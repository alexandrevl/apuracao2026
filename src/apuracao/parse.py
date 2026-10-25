"""Conversão dos arquivos EA14 (acompanhamento) e EA20 (resultado unificado) em objetos tipados.

Os campos do TSE vêm como strings; percentuais usam vírgula decimal ("47,027772356").
Usamos sempre a variante de 9 casas (sufixo ``n``) para cálculos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


def to_int(value: str | None) -> int:
    return int(value) if value else 0


def to_pct(value: str | None) -> float:
    return float(value.replace(",", ".")) if value else 0.0


def to_dt(date: str | None, time: str | None) -> datetime | None:
    if not date or not time:
        return None
    return datetime.strptime(f"{date} {time}", "%d/%m/%Y %H:%M:%S")


@dataclass(frozen=True)
class Counts:
    """Seções e eleitorado de uma abrangência (comum a EA14 e EA20)."""

    secoes_total: int
    secoes_totalizadas: int
    pct_secoes: float
    eleitorado: int
    eleitorado_totalizado: int
    eleitorado_nao_totalizado: int
    comparecimento: int
    abstencao: int

    @property
    def pct_comparecimento(self) -> float:
        base = self.comparecimento + self.abstencao
        return 100.0 * self.comparecimento / base if base else 0.0


def _counts(s: dict, e: dict) -> Counts:
    return Counts(
        secoes_total=to_int(s.get("ts")),
        secoes_totalizadas=to_int(s.get("st")),
        pct_secoes=to_pct(s.get("pstn")),
        eleitorado=to_int(e.get("te")),
        eleitorado_totalizado=to_int(e.get("est")),
        eleitorado_nao_totalizado=to_int(e.get("esnt")),
        comparecimento=to_int(e.get("c")),
        abstencao=to_int(e.get("a")),
    )


@dataclass(frozen=True)
class UfProgress:
    abr: str
    andamento: str  # n: não iniciado, p: parcial, f: finalizado
    totalizado_em: datetime | None
    counts: Counts

    @property
    def signature(self) -> tuple:
        """Muda sempre que a UF recebe nova totalização."""
        return (self.andamento, self.totalizado_em, self.counts.secoes_totalizadas)


@dataclass(frozen=True)
class Progress:
    """EA14: acompanhamento de todas as UFs (inclui ``zz`` exterior e ``br``)."""

    eleicao: str
    idg: str
    gerado_em: datetime | None
    ufs: dict[str, UfProgress]
    br: UfProgress | None


def parse_progress(raw: dict) -> Progress:
    ufs: dict[str, UfProgress] = {}
    br: UfProgress | None = None
    for item in raw.get("abr", []):
        p = UfProgress(
            abr=item["cdabr"],
            andamento=item.get("and", "n"),
            totalizado_em=to_dt(item.get("dt"), item.get("ht")),
            counts=_counts(item.get("s", {}), item.get("e", {})),
        )
        if item.get("tpabr") == "br":
            br = p
        else:
            ufs[p.abr] = p
    return Progress(
        eleicao=raw["ele"],
        idg=raw.get("idg", ""),
        gerado_em=to_dt(raw.get("dg"), raw.get("hg")),
        ufs=ufs,
        br=br,
    )


@dataclass(frozen=True)
class Candidate:
    numero: str
    nome: str
    partido: str
    votos: int
    pct: float  # % dos votos válidos
    eleito: bool
    situacao: str
    seq: int = 0


@dataclass(frozen=True)
class Result:
    """EA20 de um cargo numa abrangência."""

    eleicao: str
    turno: str
    abr: str
    idg: str
    gerado_em: datetime | None
    totalizado_em: datetime | None
    andamento: str
    matematicamente_definido: str  # e: eleito, s: 2º turno, n: não definido, "": ausente
    divulga: bool
    counts: Counts
    votos_validos: int
    brancos: int
    nulos: int
    total_votos: int
    candidates: list[Candidate] = field(default_factory=list)

    def candidate(self, numero: str) -> Candidate | None:
        return next((c for c in self.candidates if c.numero == numero), None)


def parse_result(raw: dict) -> Result:
    candidates: list[Candidate] = []
    for cargo in raw.get("carg", []):
        for agr in cargo.get("agr", []):
            for par in agr.get("par", []):
                for c in par.get("cand", []):
                    candidates.append(
                        Candidate(
                            numero=c["n"],
                            nome=c.get("nmu") or c.get("nm", ""),
                            partido=par.get("sg", ""),
                            votos=to_int(c.get("vap")),
                            pct=to_pct(c.get("pvapn")),
                            eleito=c.get("e") == "s" and c.get("st") != "2º turno",
                            situacao=c.get("st", ""),
                            seq=to_int(c.get("seq")),
                        )
                    )
    # Empate em votos (ex.: tudo zerado) segue o seq do TSE (classificação; no zero, ordem da urna).
    candidates.sort(key=lambda c: (-c.votos, c.seq))
    v = raw.get("v", {})
    return Result(
        eleicao=raw["ele"],
        turno=raw.get("t", ""),
        abr=raw["cdabr"],
        idg=raw.get("idg", ""),
        gerado_em=to_dt(raw.get("dg"), raw.get("hg")),
        totalizado_em=to_dt(raw.get("dt"), raw.get("ht")),
        andamento=raw.get("and", "n"),
        matematicamente_definido=raw.get("md", ""),
        divulga=raw.get("dv", "s") == "s",
        counts=_counts(raw.get("s", {}), raw.get("e", {})),
        votos_validos=to_int(v.get("vv")),
        brancos=to_int(v.get("vb")),
        nulos=to_int(v.get("tvn")),
        total_votos=to_int(v.get("tv")),
        candidates=candidates,
    )
