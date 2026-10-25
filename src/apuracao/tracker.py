"""Ciclo de atualização: acompanhamento (EA14) como gatilho, resultados (EA20) sob demanda.

1. Lê o EA14 com ETag; se não mudou, nada é baixado.
2. Se mudou, identifica as UFs cuja totalização avançou e baixa só esses EA20 + o do Brasil.
3. Grava versões novas, recalcula projeção/comparação/ritmo e detecta eventos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .analysis import Pace, Projection, Thermometer, UfAnalysis, analyze_ufs, pace, project, thermometer
from .baseline import Baseline
from .events import Event, EventDetector, notify
from .parse import Progress, Result, parse_progress, parse_result
from .polls import PollBook, PollComparison, compare
from .regions import aggregate_regions
from .sources import UFS, Source, SourceBlocked, SourceError
from .store import Store

# O TSE publica em horário de Brasília (UTC-3 fixo desde que o Brasil aboliu o horário de verão).
# Todo carimbo gerado aqui usa esse relógio, para ser comparável a totalizado_em/gerado_em
# independentemente de onde o `watch` roda.
BRT = timezone(timedelta(hours=-3), "BRT")


def now_brt() -> datetime:
    return datetime.now(BRT).replace(tzinfo=None)


@dataclass
class State:
    source: str
    eleicao: str
    last_check: datetime | None = None
    last_change: datetime | None = None
    br: Result | None = None
    progress: Progress | None = None
    ufs: dict[str, Result] = field(default_factory=dict)
    projection: Projection | None = None
    uf_analysis: list[UfAnalysis] = field(default_factory=list)
    pace: Pace | None = None
    new_events: list[Event] = field(default_factory=list)
    recent_events: list[dict] = field(default_factory=list)
    error: str | None = None
    blocked: bool = False
    requests: int = 0
    polls: list[PollComparison] = field(default_factory=list)
    polls_error: str | None = None
    polls_origem: str | None = None  # "pessoal" | "projeto"
    timeline_official: list[dict] = field(default_factory=list)
    timeline_projections: list[dict] = field(default_factory=list)
    thermometer: Thermometer | None = None
    regions: dict = field(default_factory=dict)


BLOCKED_WAIT_S = 660  # bloqueio do TSE dura 10 min e reinicia se houver nova tentativa
MAX_ERROR_WAIT_S = 30


def next_delay(state: State, interval: float, consecutive_errors: int) -> float:
    """Intervalo até a próxima rodada: normal, backoff em erro, espera longa em bloqueio."""
    if state.blocked:
        return BLOCKED_WAIT_S
    if state.error:
        return min(MAX_ERROR_WAIT_S, interval + 5 * consecutive_errors)
    return interval


class Tracker:
    def __init__(self, source: Source, store: Store, baseline: Baseline | None, notify_events: bool = False,
                 polls: PollBook | None = None):
        self.source = source
        self.store = store
        self.baseline = baseline
        self.notify_events = notify_events
        self.detector = EventDetector()
        self.state = State(source=source.name, eleicao=source.eleicao)
        self._signatures: dict[str, tuple] = {}
        self._dirty = False
        self.polls = polls

    def poll(self, ufs: str | list[str] = "changed") -> State:
        """Uma rodada. ``ufs``: "changed" (só UFs que avançaram), "all", ou lista explícita."""
        st = self.state
        now = now_brt()
        st.last_check = now
        st.new_events = []
        st.requests = 0
        try:
            changed = self._poll(now, ufs)
            st.error, st.blocked = None, False
        except SourceBlocked as exc:
            st.error, st.blocked, changed = str(exc), True, False
        except SourceError as exc:
            st.error, changed = str(exc), False

        if not changed and (stale := self.detector.check_stale(st.br, now)) and not self._already_warned(stale):
            st.new_events = [stale]
            self.store.save_events(st.new_events)
        if st.new_events:
            st.recent_events = self.store.events()
            if self.notify_events:
                for event in st.new_events:
                    notify(event)
        self._compare_polls()
        return st

    def _already_warned(self, stale: Event) -> bool:
        """Reabrir o ``watch`` não repete o aviso para o mesmo dado parado do TSE."""
        last = stale.payload.get("ultimo_totalizado_em")
        return any(e["type"] == "sem_atualizacao" and e["payload"].get("ultimo_totalizado_em") == last
                   for e in self.store.events())

    def _compare_polls(self) -> None:
        """Barato e sem rede: roda toda rodada para pegar edições da lista de pesquisas."""
        if self.polls is None:
            return
        st = self.state
        st.polls = compare(self.polls.refresh(), st.br, st.ufs, st.projection)
        st.polls_error, st.polls_origem = self.polls.error, self.polls.origem

    def _fetch(self, kind: str, abr: str = "br"):
        self.state.requests += 1
        return self.source.progress() if kind == "progress" else self.source.result(abr)

    def _poll(self, now: datetime, ufs: str | list[str]) -> bool:
        st = self.state
        fetched = self._fetch("progress")
        if fetched.changed:
            st.progress = parse_progress(fetched.raw)
        elif st.progress is None:
            return False

        if isinstance(ufs, list):
            targets = ufs
        else:
            targets = [
                abr for abr in UFS
                if (p := st.progress.ufs.get(abr)) is not None
                and p.counts.secoes_totalizadas > 0
                and (ufs == "all" or self._signatures.get(abr) != p.signature)
            ]
        br_pending = st.br is None or (
            st.progress.br is not None and self._signatures.get("br") != st.progress.br.signature
        )
        if not fetched.changed and not targets and not br_pending and not self._dirty:
            return False

        changed = self._dirty
        for abr in (["br"] if fetched.changed or br_pending else []) + targets:
            res = self._fetch("result", abr)
            if not res.changed:
                continue
            result = parse_result(res.raw)
            if result.abr != abr or result.eleicao != self.source.eleicao:
                raise SourceError(f"Resultado de {result.abr}/eleição {result.eleicao} recebido para {abr}/eleição {self.source.eleicao}")
            previous = st.br if abr == "br" else st.ufs.get(abr)
            if previous is not None and previous.totalizado_em is not None and (
                result.totalizado_em is None or result.totalizado_em < previous.totalizado_em
            ):
                # Um nó atrasado do CDN não deve fazer o painel/histórico retroceder.
                continue
            self.store.save_result(result, res.raw, now)
            if abr == "br":
                st.br = result
            else:
                st.ufs[abr] = result
            p = st.progress.br if abr == "br" else st.progress.ufs.get(abr)
            if p is not None and (
                result.counts.secoes_totalizadas >= p.counts.secoes_totalizadas
                and (p.totalizado_em is None or (
                    result.totalizado_em is not None and result.totalizado_em >= p.totalizado_em
                ))
                and (p.andamento != "f" or result.andamento == "f")
            ):
                self._signatures[abr] = p.signature
            changed = self._dirty = True

        if not changed or st.br is None:
            return False
        st.last_change = now
        if self.baseline is not None:
            st.projection = project(st.br, st.ufs, st.progress, self.baseline)
            st.uf_analysis = analyze_ufs(st.br, st.ufs, st.progress, self.baseline)
        st.pace = pace(self.store.pace_points(), st.br.counts.secoes_total)
        if st.projection and st.br.votos_validos:
            self.store.save_projection(st.projection, st.br, now)
        self._refresh_timeline()
        st.new_events = self.detector.update(st.br, st.progress, st.projection, now)
        self.store.save_events(st.new_events)
        st.recent_events = self.store.events()
        self._dirty = False
        return True

    def _refresh_timeline(self) -> None:
        st = self.state
        st.timeline_official, st.timeline_projections = self.store.timeline()
        st.thermometer = thermometer(st.br, st.projection, st.timeline_official) if st.br else None
        st.regions = aggregate_regions(st.br, st.ufs, st.progress, self.baseline) if self.baseline else {}

    def load_cached(self) -> State:
        """Monta o estado só com o que já está no histórico local (sem rede)."""
        st = self.state
        latest = self.store.latest_results()
        st.br = latest.pop("br", None)
        st.ufs = latest
        if st.br is not None:
            if self.baseline is not None:
                st.projection = project(st.br, st.ufs, None, self.baseline)
                st.uf_analysis = analyze_ufs(st.br, st.ufs, None, self.baseline)
            st.pace = pace(self.store.pace_points(), st.br.counts.secoes_total)
            self._refresh_timeline()
            if self.baseline is None:
                st.error = "Base do 1º turno ausente: mostrando só dados oficiais; execute apuracao baseline para habilitar estimativas."
        else:
            st.error = "Sem dados no histórico local para essa fonte. Inicie apuracao watch para coletar dados."
        st.recent_events = self.store.events()
        self._compare_polls()
        return st
