"""Detecção de eventos (viradas, marcos, UFs finalizadas, definição) e notificação macOS.

Na primeira atualização o detector apenas memoriza o estado atual: reiniciar o ``watch``
no meio da apuração não repete alertas antigos.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime

from .analysis import Projection, finalists, thermometer
from .parse import Progress, Result

MILESTONES = (1, 10, 25, 50, 75, 90, 95, 99, 100)
STALE_AFTER_MIN = 10
MODEL_CALL_THRESHOLD = 99.0


@dataclass(frozen=True)
class Event:
    type: str
    message: str
    captured_at: datetime
    totalizado_em: datetime | None = None
    abr: str = "br"
    payload: dict = field(default_factory=dict)


class EventDetector:
    def __init__(self) -> None:
        self._primed = False
        self._leader: str | None = None
        self._proj_leader: str | None = None
        self._milestones: set[int] = set()
        self._ufs_final: set[str] = set()
        self._definido = False
        self._md = ""
        self._eleitos: set[str] = set()
        self._last_change: datetime | None = None
        self._stale_sent = False
        self._model_winner: str | None = None
        self._finalizada = False
        self._last_result: Result | None = None

    def update(
        self,
        br: Result,
        progress: Progress | None,
        projection: Projection | None,
        now: datetime,
    ) -> list[Event]:
        events: list[Event] = []
        t = br.totalizado_em
        names = {c.numero: c.nome for c in br.candidates}

        def emit(type_: str, message: str, abr: str = "br", **payload) -> None:
            events.append(Event(type_, message, now, t, abr, payload))

        pct = br.counts.pct_secoes
        top = max(br.candidates, key=lambda c: c.votos, default=None)
        tied = top is not None and sum(c.votos == top.votos for c in br.candidates) > 1
        leader = top.numero if top and top.votos and not tied else None
        proj_leader = projection.leader.numero if projection else None
        ufs_final = {abr for abr, p in (progress.ufs.items() if progress else []) if p.andamento == "f"}
        eleitos = {c.numero for c in br.candidates if c.eleito}
        reached = {m for m in MILESTONES if pct >= m}
        finalizada = br.andamento == "f"
        model_winner = None
        model_chance = None
        model = thermometer(br, projection, [])
        pair = finalists(br)
        model_available = (
            model is not None and pair is not None and br.votos_validos > 0
            and br.andamento == "p" and not model.definido
            and model.necessario_pct is not None and model.projetado_pct is not None
        )
        if model_available:
            if model.chance_pct >= MODEL_CALL_THRESHOLD:
                model_winner, model_chance = pair[0], model.chance_pct
            elif model.chance_pct <= 100 - MODEL_CALL_THRESHOLD:
                model_winner, model_chance = pair[1], 100 - model.chance_pct

        if br != self._last_result:
            self._last_change = now
            self._stale_sent = False
            self._last_result = br

        if not self._primed:
            self._primed = True
            self._leader, self._proj_leader = leader, proj_leader
            self._milestones, self._ufs_final = reached, ufs_final
            self._definido = bool(projection and projection.definido)
            self._md, self._eleitos = br.matematicamente_definido, eleitos
            self._model_winner = model_winner
            self._finalizada = finalizada
            return events

        for m in sorted(reached - self._milestones):
            if leader:
                lead = br.candidate(leader)
                message = f"{m}% das seções totalizadas — {lead.nome} lidera com {lead.pct:.2f}%"
            else:
                message = f"{m}% das seções totalizadas — sem liderança isolada"
            emit("marco", message, pct=m, lider=leader)
        if leader and self._leader and leader != self._leader:
            c = br.candidates[0]
            emit("virada", f"VIRADA: {c.nome} assume a liderança ({c.pct:.2f}%, {pct:.2f}% apurado)",
                 lider=leader, anterior=self._leader)
        elif leader and not self._leader:
            c = br.candidates[0]
            emit("lideranca", f"Primeiros votos: {c.nome} à frente ({c.pct:.2f}%)", lider=leader)
        if proj_leader and self._proj_leader and proj_leader != self._proj_leader:
            emit("virada_projecao", f"Projeção vira: {names[proj_leader]} passa à frente "
                 f"({projection.leader.pct:.1f}% estimado)", lider=proj_leader)
        # Indicação estatística reversível, nunca tratada como definição matemática.
        if not finalizada and not (projection and projection.definido):
            if self._model_winner and model_winner != self._model_winner:
                emit("projecao_retirada",
                     f"MODELO: retirada a indicação de vitória de {names[self._model_winner]}; "
                     "a estimativa mudou ou ficou indisponível. Não é resultado oficial.",
                     anterior=self._model_winner)
            if model_winner and model_winner != self._model_winner:
                emit("projecao_definida",
                     f"MODELO indica vitória de {names[model_winner]} "
                     f"({model_chance:.1f}% de chance estimada). "
                     "Estimativa não calibrada e sujeita a revisão; NÃO é definição matemática.",
                     vencedor=model_winner, chance_pct=model_chance,
                     limiar_pct=MODEL_CALL_THRESHOLD)
            self._model_winner = model_winner
        for abr in sorted(ufs_final - self._ufs_final):
            emit("uf_finalizada", f"{abr.upper()} finalizou a totalização", abr=abr)
        if projection and projection.definido and not self._definido:
            w = projection.vencedor
            emit("definido", f"{names[w]} matematicamente eleito: vantagem de "
                 f"{projection.vantagem:,} votos > {projection.eleitorado_restante:,} eleitores restantes"
                 .replace(",", "."), vencedor=w)
        if br.matematicamente_definido == "e" and br.matematicamente_definido != self._md:
            emit("tse_md", f"TSE sinaliza matematicamente definido (md={br.matematicamente_definido})",
                 md=br.matematicamente_definido)
        for numero in sorted(eleitos - self._eleitos):
            emit("eleito", f"TSE: {names[numero]} ELEITO", eleito=numero)
        if finalizada and not self._finalizada:
            emit("apuracao_finalizada", "TSE: apuração nacional finalizada (and=f).",
                 pct_secoes=pct)

        self._leader = leader or self._leader
        self._proj_leader = proj_leader or self._proj_leader
        self._milestones |= reached
        self._ufs_final |= ufs_final
        self._definido = self._definido or bool(projection and projection.definido)
        self._md = br.matematicamente_definido or self._md
        self._eleitos |= eleitos
        self._finalizada = finalizada
        return events

    def check_stale(self, br: Result | None, now: datetime) -> Event | None:
        """Avisa uma vez se a apuração está em andamento e nada muda há STALE_AFTER_MIN."""
        if br is None or br.andamento != "p" or self._last_change is None or self._stale_sent:
            return None
        idle = (now - self._last_change).total_seconds() / 60
        if idle < STALE_AFTER_MIN:
            return None
        self._stale_sent = True
        return Event("sem_atualizacao", f"Sem atualização do TSE há {idle:.0f} min", now, br.totalizado_em)


NOTIFY_TYPES = {"virada", "virada_projecao", "marco", "uf_finalizada", "definido", "tse_md", "eleito",
                "lideranca", "sem_atualizacao", "projecao_definida", "projecao_retirada",
                "apuracao_finalizada"}


def notify(event: Event) -> None:
    """Notificação do macOS sem bloquear e sem interpolar texto em AppleScript."""
    if sys.platform != "darwin" or event.type not in NOTIFY_TYPES:
        return
    subprocess.Popen(
        ["osascript", "-e", "on run argv", "-e",
         "display notification (item 2 of argv) with title (item 1 of argv)", "-e", "end run",
         "Apuração 2026", event.message],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
