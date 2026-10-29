"""Refaz uma noite já gravada com o modelo atual, sem rede.

Lê as versões publicadas (``snapshots``/``city_snapshots`` + JSON bruto) da fonte de origem e as
reaplica, na ordem e nos horários originais de captura, num histórico novo. A cada rodada original
(mesmo ``captured_at``) recalcula projeção e eventos com o código atual, como o ``Tracker`` faz ao vivo,
inclusive descartando versões que retrocedem a totalização (nó atrasado do CDN).

Dois modos: ``replay`` aplica tudo de uma vez; ``ReplayTracker`` solta as rodadas conforme um relógio
virtual (``speed`` 1 = mesmo ritmo da noite) para o ``watch`` mostrar a apuração passando.

Diferenças em relação ao ao vivo: o EA14 (acompanhamento) não é gravado; o ``Progress`` é reconstruído
a partir dos resultados já aplicados. O aviso ``sem_atualizacao`` depende do relógio e não é reproduzido.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .analysis import analyze_ufs, pace, project
from .baseline import Baseline
from .events import Event, EventDetector
from .parse import Progress, Result, UfProgress, parse_result
from .polls import PollBook
from .sources import CIDADE_UF, ELEICAO_T2
from .store import Store
from .tracker import Tracker


@dataclass(frozen=True)
class ReplayStats:
    rodadas: int
    versoes: int
    ignoradas: int  # versões que retrocediam a totalização e o ao vivo também descartaria
    projecoes: int
    eventos: int


# Pastas de dados que nunca podem ser destino de reconstrução (históricos reais, base, moldes, backups).
PROTEGIDAS = frozenset({"tse", "t1", "sim", "baseline", "templates"})


def resolve_paths(data_dir: Path, origem: str, destino: str) -> tuple[Path, Path]:
    """Origem e destino como pastas simples dentro de ``data_dir``; o destino pode ser apagado e refeito.

    Recusa nomes com separador ou ``..``, destinos protegidos ou de backup, e origem igual,
    ancestral ou descendente do destino (já com ``resolve``, então links e ``./`` não escapam).
    """
    root = data_dir.resolve()
    paths = []
    for kind, name in (("origem", origem), ("destino", destino)):
        if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
            raise ValueError(f"{kind} deve ser o nome de uma pasta dentro de {root}: {name!r}")
        path = (root / name).resolve()
        if path.parent != root:
            raise ValueError(f"{kind} {name!r} resolve para fora de {root}")
        paths.append(path)
    source, target = paths
    if target.name in PROTEGIDAS or target.name.startswith("backup"):
        raise ValueError(f"destino {target.name!r} é protegido; escolha outro nome (ex.: replay, replay-secoes)")
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("origem e destino não podem ser a mesma pasta nem uma dentro da outra")
    return source, target


def _retrocede(previous: Result | None, result: Result) -> bool:
    return previous is not None and previous.totalizado_em is not None and (
        result.totalizado_em is None or result.totalizado_em < previous.totalizado_em
    )


class Replayer:
    """Aplica, rodada a rodada, as versões gravadas na origem a um ``Store`` novo."""

    def __init__(self, source_root: Path, target: Store, baseline: Baseline):
        if target.latest_date() is not None:
            raise ValueError(f"{target.root} já tem histórico; remova-o antes de refazer a noite")
        self.source_root, self.target, self.baseline = source_root, target, baseline
        origin = sqlite3.connect(f"file:{source_root / 'apuracao.db'}?mode=ro", uri=True)
        try:
            # Mesma ordem do ao vivo: UF/BR antes das cidades dentro da mesma rodada.
            versions = sorted(
                [(c, 0, i, abr, None, p) for i, c, abr, p in origin.execute(
                    "select id, captured_at, abr, raw_path from snapshots")]
                + [(c, 1, i, cd, uf, p) for i, c, cd, uf, p in origin.execute(
                    "select id, captured_at, cd, uf, raw_path from city_snapshots")]
            )
            first = origin.execute(
                "select min(captured_at) from snapshots where abr = 'br' and totalizado_em is not null"
            ).fetchone()[0]
        finally:
            origin.close()
        self.versions = len(versions)
        self._rounds: list[tuple[datetime, list[tuple]]] = []
        for version in versions:
            if not self._rounds or self._rounds[-1][0].isoformat(timespec="seconds") != version[0]:
                self._rounds.append((datetime.fromisoformat(version[0]), []))
            self._rounds[-1][1].append(version)
        # Início da apuração: o que foi gravado antes (zerado, testes) entra de uma vez no primeiro instante.
        self.start = datetime.fromisoformat(first) if first else (self._rounds[0][0] if self._rounds else None)
        self.br: Result | None = None
        self.ufs: dict[str, Result] = {}
        self.cities: dict[str, Result] = {}
        self.projection = None
        self.detector = EventDetector()
        self.rounds = self.skipped = self.projections = self.events = 0
        self._next = 0

    @property
    def done(self) -> bool:
        return self._next >= len(self._rounds)

    def progress(self) -> Progress | None:
        if self.br is None:
            return None

        def of(result: Result) -> UfProgress:
            return UfProgress(result.abr, result.andamento, result.totalizado_em, result.counts)

        return Progress(self.br.eleicao, "", None, {abr: of(r) for abr, r in self.ufs.items()}, of(self.br))

    def advance_to(self, until: datetime | None) -> list[Event]:
        """Aplica as rodadas com ``captured_at <= until`` (None = todas); devolve os eventos novos."""
        new: list[Event] = []
        while not self.done and (until is None or self._rounds[self._next][0] <= until):
            at, rows = self._rounds[self._next]
            self._next += 1
            new += self._apply(at, rows)
        return new

    def _apply(self, at: datetime, rows: list[tuple]) -> list[Event]:
        for _, kind, _, key, uf, path in rows:
            raw = json.loads((self.source_root / path).read_text(encoding="utf-8"))
            result = parse_result(raw)
            if kind == 0:
                if _retrocede(self.br if key == "br" else self.ufs.get(key), result):
                    self.skipped += 1
                    continue
                self.target.save_result(result, raw, at)
                if key == "br":
                    self.br = result
                else:
                    self.ufs[key] = result
            else:
                if CIDADE_UF.get(key) != uf or _retrocede(self.cities.get(key), result):
                    self.skipped += 1
                    continue
                self.target.save_city_result(uf, result, raw, at)
                self.cities[key] = result
        self.rounds += 1
        if self.br is None:
            return []
        progress = self.progress()
        self.projection = project(self.br, self.ufs, progress, self.baseline, self.cities)
        if self.projection and self.br.votos_validos:
            self.target.save_projection(self.projection, self.br, at)
            self.projections += 1
        new = self.detector.update(self.br, progress, self.projection, at)
        self.target.save_events(new)
        self.events += len(new)
        return new


def replay(source_root: Path, target_root: Path, baseline: Baseline) -> ReplayStats:
    """Reaplica ``source_root`` em ``target_root`` (que não pode ter histórico), de uma vez."""
    target = Store(target_root)
    try:
        replayer = Replayer(source_root, target, baseline)
        replayer.advance_to(None)
        return ReplayStats(replayer.rounds, replayer.versions, replayer.skipped,
                           replayer.projections, replayer.events)
    finally:
        target.close()


class ReplaySource:
    """Identidade da fonte: não há rede; ``ReplayTracker`` não consulta arquivos por ela."""

    name = "replay"
    eleicao = ELEICAO_T2

    def close(self) -> None:
        pass


class ReplayTracker(Tracker):
    """Tracker cujas "publicações" vêm do histórico gravado, soltas por um relógio virtual.

    O relógio começa no primeiro resultado do BR com totalização e avança ``speed`` vezes mais rápido
    que o real; ``offset`` (minutos) pula esse trecho. Cada ``poll`` aplica as rodadas que já
    "aconteceram", com o ``captured_at`` original.
    """

    def __init__(self, source_root: Path, store: Store, baseline: Baseline | None, *, speed: float = 1.0,
                 offset: float = 0.0, notify_events: bool = False, polls: PollBook | None = None,
                 clock: Callable[[], float] = time.monotonic):
        if baseline is None:
            raise ValueError("replay precisa da base do 1º turno")
        if not math.isfinite(speed) or speed <= 0 or not math.isfinite(offset):
            raise ValueError("Velocidade deve ser positiva e offset deve ser finito")
        super().__init__(ReplaySource(), store, baseline, notify_events=notify_events, polls=polls)
        self.replayer = Replayer(source_root, store, baseline)
        self._clock, self._speed, self._offset = clock, speed, timedelta(minutes=offset)
        self._t0 = clock()
        self._seen = 0

    def virtual_now(self) -> datetime | None:
        start = self.replayer.start
        if start is None:
            return None
        return start + self._offset + timedelta(seconds=(self._clock() - self._t0) * self._speed)

    def _poll(self, now: datetime, ufs) -> bool:
        until = self.virtual_now()
        if until is None:
            return False
        new = self.replayer.advance_to(until)
        r, st = self.replayer, self.state
        if r.br is None or r.rounds == self._seen:
            return False
        self._seen = r.rounds
        st.progress, st.br, st.ufs, st.cidades = r.progress(), r.br, dict(r.ufs), dict(r.cities)
        st.last_change = now
        st.projection = r.projection
        st.uf_analysis = analyze_ufs(st.br, st.ufs, st.progress, self.baseline)
        st.pace = pace(self.store.pace_points(), st.br.counts.secoes_total)
        self._refresh_timeline()
        st.new_events = new
        st.recent_events = self.store.events()
        return True
