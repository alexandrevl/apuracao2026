"""Comparação das pesquisas de 2º turno com a apuração.

Lista do projeto (``pesquisas.json`` dentro do pacote, conferida e com fontes) ou, se existir,
a lista pessoal ``pesquisas.json`` do diretório de dados, que a substitui por inteiro. O arquivo
ativo é relido quando muda. Para cada pesquisa compara-se o % de votos válidos do candidato A
(13 LULA, o de menor número) com:

- a projeção da abrangência (Brasil ou UF) — referência enquanto a apuração não terminou;
- o placar oficial parcial — mostrado, mas enganoso no começo (ordem de chegada das UFs);
- o resultado final, quando a abrangência está finalizada (``and=f``): aí vira o ranking oficial.

``diferenca_pp`` > 0 significa que a pesquisa deu mais votos ao candidato A do que a referência.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .analysis import Projection, finalists
from .parse import Result
from .sources import UFS

BUNDLED_POLLS = Path(__file__).with_name("pesquisas.json")


@dataclass(frozen=True)
class Poll:
    instituto: str
    abr: str
    divulgacao: str
    campo_fim: str | None
    registro_tse: str | None
    margem_pp: float | None
    validos: dict[str, float]
    validos_calculado: bool
    fonte: str | None
    fonte_confirmada: bool = True  # False: números só vistos em fonte secundária


@dataclass(frozen=True)
class PollComparison:
    poll: Poll
    pesquisa_a: float
    projecao_a: float | None
    oficial_a: float | None
    pct_secoes: float
    final: bool
    referencia: str  # "final" | "projecao" | "sem_dados"
    diferenca_pp: float | None
    dentro_margem: bool | None

    @property
    def erro(self) -> float | None:
        return None if self.diferenca_pp is None else abs(self.diferenca_pp)


def _parse_poll(item: dict, index: int) -> Poll:
    where = f"pesquisa #{index + 1} ({item.get('instituto', '?')})"
    abr = str(item.get("abr", "")).lower()
    if abr != "br" and abr not in UFS:
        raise ValueError(f"{where}: abr inválida {item.get('abr')!r}")
    validos = {str(k): float(v) for k, v in (item.get("validos") or {}).items()}
    if len(validos) != 2 or not 99.0 <= sum(validos.values()) <= 101.0:
        raise ValueError(f"{where}: 'validos' precisa ter os 2 candidatos somando ~100")
    margem = item.get("margem_pp")
    return Poll(
        instituto=str(item["instituto"]),
        abr=abr,
        divulgacao=str(item.get("divulgacao") or ""),
        campo_fim=item.get("campo_fim"),
        registro_tse=item.get("registro_tse"),
        margem_pp=None if margem is None else float(margem),
        validos=validos,
        validos_calculado=bool(item.get("validos_calculado")),
        fonte=item.get("fonte"),
        fonte_confirmada=item.get("fonte_confirmada", True) is not False,
    )


class PollBook:
    """Lê a lista pessoal (``path``) ou, na falta dela, a do projeto (``fallback``)."""

    def __init__(self, path: Path, fallback: Path | None = None):
        self.path = path
        self.fallback = fallback
        self._key: tuple[Path, float] | None = None
        self.polls: list[Poll] = []
        self.error: str | None = None
        self.origem: str | None = None  # "pessoal" | "projeto" | None

    def _active(self) -> tuple[Path, str] | None:
        if self.path.exists():
            return self.path, "pessoal"
        if self.fallback is not None and self.fallback.exists():
            return self.fallback, "projeto"
        return None

    def refresh(self) -> list[Poll]:
        active = self._active()
        if active is None:
            self.polls, self.error, self._key, self.origem = [], None, None, None
            return self.polls
        path, origem = active
        try:
            key = (path, path.stat().st_mtime)
        except FileNotFoundError:
            return self.polls
        if key == self._key:
            return self.polls
        self._key = key
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            items = raw["pesquisas"] if isinstance(raw, dict) else raw
            self.polls = [_parse_poll(item, i) for i, item in enumerate(items)]
            self.error, self.origem = None, origem
        except (ValueError, KeyError, TypeError) as exc:
            # Mantém a última versão válida; o erro aparece no painel.
            self.error = f"{path.name} ({origem}): {exc}"
        return self.polls


def compare(polls: list[Poll], br: Result | None, ufs: dict[str, Result],
            projection: Projection | None) -> list[PollComparison]:
    if br is None or (pair := finalists(br)) is None:
        return []
    a = pair[0]
    out: list[PollComparison] = []
    for poll in polls:
        if a not in poll.validos:
            continue
        result = br if poll.abr == "br" else ufs.get(poll.abr)
        oficial = None
        if result is not None and result.votos_validos and (c := result.candidate(a)):
            oficial = c.pct
        if projection is None:
            proj = None
        elif poll.abr == "br":
            proj = next(c.pct for c in projection.candidates if c.numero == a)
        else:
            proj = projection.ufs_pct_a.get(poll.abr)
        final = result is not None and result.andamento == "f" and oficial is not None
        ref_value, ref = (oficial, "final") if final else (proj, "projecao")
        if ref_value is None:
            ref = "sem_dados"
        diff = None if ref_value is None else poll.validos[a] - ref_value
        out.append(PollComparison(
            poll=poll,
            pesquisa_a=poll.validos[a],
            projecao_a=proj,
            oficial_a=oficial,
            pct_secoes=result.counts.pct_secoes if result else 0.0,
            final=final,
            referencia=ref,
            diferenca_pp=diff,
            dentro_margem=None if diff is None or poll.margem_pp is None else abs(diff) <= poll.margem_pp,
        ))
    order = {abr: i for i, abr in enumerate(("br", *UFS))}
    out.sort(key=lambda c: (order[c.poll.abr], c.erro is None, c.erro or 0.0))
    return out
