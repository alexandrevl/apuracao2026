"""Projeção por UF ajustada pelo 1º turno, comparação com o 1º turno, ritmo e meta.

Projeção (estimativa, não resultado):
- Prior da UF = fração do candidato entre os dois finalistas no 1º turno da UF,
  deslocada pelo *swing* nacional observado (média ponderada por votos válidos já apurados).
- Peso do dado atual ``w = min(1, apurado_uf / 20%)``.
- Votos válidos restantes = eleitorado não totalizado × comparecimento × taxa de válidos
  (taxas atuais da UF misturadas com as do 1º turno pelo mesmo ``w``).
- Fração do restante = ``w·atual + (1−w)·prior``.
- Faixa de sensibilidade: ±(2 p.p.·(1−w) + 0,5 p.p.·w·(1−apurado)) sobre a fração do restante.
  É uma faixa de cenário, não um intervalo de confiança estatístico.

"Matematicamente definido" usa apenas números oficiais: vantagem do líder maior que todo o
eleitorado ainda não totalizado (cada voto restante iria para o segundo colocado).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from .baseline import Baseline, BaselineUf
from .parse import Counts, Progress, Result
from .store import PacePoint

FULL_WEIGHT_AT = 0.20  # fração apurada a partir da qual a UF usa só o dado atual
BAND_PRIOR = 0.02
BAND_CURRENT = 0.005
PROJECTION_NOTICE = "Estimativa própria baseada no 1º turno e no ritmo por UF; não é resultado oficial."


def finalists(br: Result) -> tuple[str, str] | None:
    """Os dois candidatos do 2º turno, na ordem oficial do TSE."""
    if len(br.candidates) != 2:
        return None
    a, b = sorted(br.candidates, key=lambda c: c.seq)
    return a.numero, b.numero


def _share(result: Result, a: str, b: str) -> float | None:
    ca, cb = result.candidate(a), result.candidate(b)
    va, vb = (ca.votos if ca else 0), (cb.votos if cb else 0)
    return va / (va + vb) if va + vb else None


@dataclass(frozen=True)
class CandidateProjection:
    numero: str
    nome: str
    votos: int
    pct: float
    pct_min: float
    pct_max: float


@dataclass(frozen=True)
class Projection:
    candidates: list[CandidateProjection]
    swing_pp: float  # deslocamento médio do candidato A em relação ao 1º turno (p.p.)
    validos_projetados: int
    meta: int  # maioria dos válidos projetados
    falta: dict[str, int]  # votos que cada candidato ainda precisa (pela meta projetada)
    meta_simples: int | None  # método de 2022: válidos atuais extrapolados pelo % apurado
    definido: bool
    vencedor: str | None
    vantagem: int
    eleitorado_restante: int
    ufs_pct_a: dict[str, float]  # % final projetado do candidato A em cada UF

    @property
    def leader(self) -> CandidateProjection:
        return max(self.candidates, key=lambda c: c.pct)


@dataclass(frozen=True)
class UfAnalysis:
    abr: str
    andamento: str
    pct_secoes: float
    votos: dict[str, int]
    pct: dict[str, float]
    lider: str | None
    share_1t: float  # fração do candidato A entre os finalistas no 1º turno (%)
    swing_pp: float | None  # A atual − A no 1º turno (p.p.); None sem votos
    comparecimento_pct: float | None
    comparecimento_1t_pct: float
    peso: float


@dataclass(frozen=True)
class Pace:
    secoes_por_min: float
    janela_min: float
    restantes: int
    eta_min: float | None
    eta: datetime | None


def national_swing(ufs: dict[str, Result], baseline: Baseline, a: str, b: str) -> float:
    num = den = 0.0
    for abr, result in ufs.items():
        base = baseline.ufs.get(abr)
        share = _share(result, a, b)
        if base is None or share is None:
            continue
        weight = result.votos_validos
        num += (share - base.two_party_share(a, b)) * weight
        den += weight
    return num / den if den else 0.0


def _remaining_valid_votes(
    base: BaselineUf, result: Result | None, progress_counts: Counts | None = None
) -> float:
    """Mesma estimativa de votos restantes para a projeção nacional e os agregados regionais."""
    if result is None:
        electorate = (progress_counts.eleitorado if progress_counts
                      else base.comparecimento + base.abstencao)
        return electorate * base.turnout * base.valid_rate
    if result.andamento == "f":
        return 0.0
    counts = result.counts
    w = min(1.0, counts.pct_secoes / 100 / FULL_WEIGHT_AT)
    if not counts.comparecimento and not counts.abstencao:
        turnout, valid = base.turnout, base.valid_rate
    else:
        turnout = w * counts.pct_comparecimento / 100 + (1 - w) * base.turnout
        valid = (w * result.votos_validos / counts.comparecimento if counts.comparecimento else 0.0)
        valid += (1 - w) * base.valid_rate
    return counts.eleitorado_nao_totalizado * turnout * valid


def project(
    br: Result, ufs: dict[str, Result], progress: Progress | None, baseline: Baseline
) -> Projection | None:
    pair = finalists(br)
    if pair is None:
        return None
    a, b = pair
    swing = national_swing(ufs, baseline, a, b)

    proj = {a: 0.0, b: 0.0}
    low_a = high_a = 0.0
    ufs_pct_a: dict[str, float] = {}
    for abr, base in baseline.ufs.items():
        result = ufs.get(abr)
        if result is not None:
            counts = result.counts
            va = (result.candidate(a).votos if result.candidate(a) else 0)
            vb = (result.candidate(b).votos if result.candidate(b) else 0)
            frac = counts.pct_secoes / 100
        else:
            # Sem arquivo da UF ainda: nada apurado, todo o eleitorado é restante.
            va = vb = 0
            frac = 0.0

        w = min(1.0, frac / FULL_WEIGHT_AT)
        rem_valid = _remaining_valid_votes(
            base, result, progress.ufs[abr].counts if progress and abr in progress.ufs else None
        )

        prior = min(0.99, max(0.01, base.two_party_share(a, b) + swing))
        current = va / (va + vb) if va + vb else prior
        share = w * current + (1 - w) * prior
        band = BAND_PRIOR * (1 - w) + BAND_CURRENT * w * (1 - frac)

        proj[a] += va + rem_valid * share
        proj[b] += vb + rem_valid * (1 - share)
        low_a += va + rem_valid * max(0.0, share - band)
        high_a += va + rem_valid * min(1.0, share + band)
        uf_a, uf_b = va + rem_valid * share, vb + rem_valid * (1 - share)
        if uf_a + uf_b > 0:
            ufs_pct_a[abr] = 100 * uf_a / (uf_a + uf_b)

    # O encerramento nacional prevalece sobre arquivos estaduais ainda defasados.
    if br.andamento == "f":
        proj = {n: float(br.candidate(n).votos) for n in pair}
        low_a = high_a = proj[a]

    total = proj[a] + proj[b]
    if total <= 0:
        return None
    pct_a = 100 * proj[a] / total
    pct_a_min, pct_a_max = 100 * low_a / total, 100 * high_a / total
    nomes = {c.numero: c.nome for c in br.candidates}
    candidates = [
        CandidateProjection(a, nomes[a], round(proj[a]), pct_a, pct_a_min, pct_a_max),
        CandidateProjection(b, nomes[b], round(proj[b]), 100 - pct_a, 100 - pct_a_max, 100 - pct_a_min),
    ]

    meta = int(total // 2) + 1
    counted = {c.numero: c.votos for c in br.candidates}
    pct_secoes = br.counts.pct_secoes
    meta_simples = (
        int(br.votos_validos * 100 / pct_secoes / 2) + 1 if pct_secoes > 0 and br.votos_validos else None
    )
    leader, trailer = sorted(br.candidates, key=lambda c: -c.votos)
    vantagem = leader.votos - trailer.votos
    restante = br.counts.eleitorado_nao_totalizado
    definido = vantagem > restante and br.votos_validos > 0
    return Projection(
        candidates=candidates,
        swing_pp=100 * swing,
        validos_projetados=round(total),
        meta=meta,
        falta={n: max(0, meta - v) for n, v in counted.items()},
        meta_simples=meta_simples,
        definido=definido,
        vencedor=leader.numero if definido else None,
        vantagem=vantagem,
        eleitorado_restante=restante,
        ufs_pct_a=ufs_pct_a,
    )


def analyze_ufs(
    br: Result, ufs: dict[str, Result], progress: Progress | None, baseline: Baseline
) -> list[UfAnalysis]:
    pair = finalists(br)
    out: list[UfAnalysis] = []
    for abr, base in baseline.ufs.items():
        result = ufs.get(abr)
        prog = progress.ufs.get(abr) if progress else None
        share_1t = base.two_party_share(*pair) if pair else 0.0
        if result is not None:
            counts = result.counts
            votos = {c.numero: c.votos for c in result.candidates}
            pct = {c.numero: c.pct for c in result.candidates}
            share = _share(result, *pair) if pair else None
            leader = max(result.candidates, key=lambda c: c.votos, default=None)
            tied = leader is not None and sum(c.votos == leader.votos for c in result.candidates) > 1
            lider = leader.numero if leader and leader.votos and not tied else None
            comp = counts.pct_comparecimento if counts.comparecimento + counts.abstencao else None
            andamento, pct_secoes = result.andamento, counts.pct_secoes
        else:
            votos, pct, share, lider, comp = {}, {}, None, None, None
            andamento = prog.andamento if prog else "n"
            pct_secoes = prog.counts.pct_secoes if prog else 0.0
        out.append(
            UfAnalysis(
                abr=abr,
                andamento=andamento,
                pct_secoes=pct_secoes,
                votos=votos,
                pct=pct,
                lider=lider,
                share_1t=100 * share_1t,
                swing_pp=None if share is None else 100 * (share - share_1t),
                comparecimento_pct=comp,
                comparecimento_1t_pct=100 * base.turnout,
                peso=min(1.0, pct_secoes / 100 / FULL_WEIGHT_AT),
            )
        )
    return out


CHANCE_NOTICE = ("Chance estimada: palpite a partir da projeção (curva normal; incerteza = faixa da projeção, "
                 "no mínimo 2,5 p.p. × fração de votos que falta). Não é probabilidade calibrada.")
UNCERTAINTY_FLOOR_PP = 2.5
RECENT_WINDOW_MIN = 10.0
# Abaixo disso a amostra é pequena e enviesada pela ordem de chegada (exterior, UFs pequenas):
# a normal centrada na projeção daria "100%" sem base. Não vale para definição oficial.
MIN_PCT_SECOES_CHANCE = 5.0


def chance_above_half(pct: float, pct_min: float, pct_max: float, remaining_frac: float) -> float:
    """P(% final > 50): normal centrada na projeção; faixa min–max lida como intervalo de ~90%,
    com piso de incerteza proporcional à fração dos votos válidos que ainda falta."""
    sigma = max((pct_max - pct_min) / 2 / 1.645, UNCERTAINTY_FLOOR_PP * remaining_frac, 0.03)
    return 100 * 0.5 * (1 + math.erf((pct - 50) / (sigma * math.sqrt(2))))


def needed_share(validos_projetados: int, validos_atuais: int, votos_atuais_a: int) -> float | None:
    """% dos votos válidos que ainda faltam que o candidato precisa para chegar a 50%."""
    restante = validos_projetados - validos_atuais
    if restante <= 0:
        return None
    return 100 * (validos_projetados / 2 - votos_atuais_a) / restante


def _remaining_shares(
    projected_valid: int, official_valid: int, projected_votes_a: int, official_votes_a: int
) -> tuple[float | None, float | None]:
    """Necessidade bruta e share projetado; dados incompatíveis não viram percentuais fictícios."""
    remaining = projected_valid - official_valid
    if official_valid <= 0 or remaining <= 0:
        return None, None
    projected = 100 * (projected_votes_a - official_votes_a) / remaining
    if not 0 <= projected <= 100:
        return None, None
    return needed_share(projected_valid, official_valid, official_votes_a), projected


def chance_label(chance: float) -> str:
    for limit, label in ((1, "muito improvável"), (10, "difícil"), (30, "possível, mas atrás"),
                         (70, "aberto"), (90, "favorável"), (99, "muito favorável")):
        if chance < limit:
            return label
    return "praticamente garantido"


@dataclass(frozen=True)
class Thermometer:
    """Situação do candidato A (1º na ordem oficial, 13 LULA) em relação aos votos que faltam."""

    numero: str
    nome: str
    necessario_pct: float | None  # quanto precisa do restante para chegar a 50%
    projetado_pct: float | None  # quanto a projeção estima que ele fará do restante
    recente_pct: float | None  # quanto fez dos votos apurados nos últimos RECENT_WINDOW_MIN min
    recente_janela_min: float | None
    validos_restantes: int
    chance_pct: float | None  # None: seções apuradas insuficientes para o palpite
    rotulo: str
    definido: bool

    @property
    def gap_pp(self) -> float | None:
        """Necessário − projetado: positivo = falta ritmo; negativo = sobra."""
        if self.necessario_pct is None or self.projetado_pct is None:
            return None
        return self.necessario_pct - self.projetado_pct


def recent_share(official: list[dict], numero: str, window_min: float = RECENT_WINDOW_MIN
                 ) -> tuple[float, float] | None:
    """Fração do candidato nos votos válidos que entraram na janela recente (hora do TSE)."""
    if len(official) < 2:
        return None
    last = official[-1]
    t_last = datetime.fromisoformat(last["totalizado_em"])
    start = next((r for r in official[:-1]
                  if datetime.fromisoformat(r["totalizado_em"]) >= t_last - timedelta(minutes=window_min)),
                 official[-2])
    d_valid = last["validos"] - start["validos"]
    minutes = (t_last - datetime.fromisoformat(start["totalizado_em"])).total_seconds() / 60
    if d_valid <= 0 or minutes <= 0:
        return None
    d_a = last["votos"].get(numero, 0) - start["votos"].get(numero, 0)
    if not 0 <= d_a <= d_valid:
        return None
    return 100 * d_a / d_valid, minutes


def thermometer(br: Result, projection: Projection | None, official: list[dict]) -> Thermometer | None:
    pair = finalists(br)
    if pair is None or projection is None:
        return None
    a = pair[0]
    cand = next(c for c in projection.candidates if c.numero == a)
    counted = br.candidate(a).votos if br.candidate(a) else 0
    restante = projection.validos_projetados - br.votos_validos
    definido = br.andamento == "f" or projection.definido
    needed, projected = (None, None) if definido else _remaining_shares(
        projection.validos_projetados, br.votos_validos, cand.votos, counted
    )
    recent = recent_share(official, a)

    # Definição só com números oficiais: totalização finalizada ou vantagem > eleitorado não totalizado.
    if definido:
        leader = max(br.candidates, key=lambda c: c.votos)
        tied = sum(c.votos == leader.votos for c in br.candidates) > 1
        winner = projection.vencedor or (leader.numero if leader.votos and not tied else None)
        chance = (100.0 if winner == a else 0.0) if winner is not None else 50.0
    else:
        k = br.counts
        remaining_frac = k.eleitorado_nao_totalizado / k.eleitorado if k.eleitorado else 1.0
        chance = (chance_above_half(cand.pct, cand.pct_min, cand.pct_max, remaining_frac)
                  if k.pct_secoes >= MIN_PCT_SECOES_CHANCE else None)
    if definido:
        rotulo = "empate" if chance == 50 else ("eleito" if chance == 100 else "sem chance matemática")
    elif chance is None:
        rotulo = "dados insuficientes"
    else:
        rotulo = chance_label(chance)
    return Thermometer(
        numero=a, nome=cand.nome, necessario_pct=needed, projetado_pct=projected,
        recente_pct=recent[0] if recent else None, recente_janela_min=recent[1] if recent else None,
        validos_restantes=max(0, restante), chance_pct=chance, rotulo=rotulo, definido=definido,
    )


def pace(points: list[PacePoint], secoes_total: int, window_min: float = 10.0) -> Pace | None:
    """Seções por minuto (hora de totalização do TSE) na janela recente e previsão de término."""
    if len(points) < 2:
        return None
    last = points[-1]
    start = next((p for p in points if p.totalizado_em >= last.totalizado_em - timedelta(minutes=window_min)), points[0])
    if start is last:
        start = points[-2]
    minutes = (last.totalizado_em - start.totalizado_em).total_seconds() / 60
    if minutes <= 0:
        return None
    rate = (last.secoes_totalizadas - start.secoes_totalizadas) / minutes
    if rate < 0:
        return None  # Retotalização não é ritmo negativo de entrada de seções.
    restantes = max(0, secoes_total - last.secoes_totalizadas)
    eta_min = 0.0 if restantes == 0 else (restantes / rate if rate > 0 else None)
    return Pace(
        secoes_por_min=rate,
        janela_min=minutes,
        restantes=restantes,
        eta_min=eta_min,
        eta=last.totalizado_em + timedelta(minutes=eta_min) if eta_min is not None else None,
    )
