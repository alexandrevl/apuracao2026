"""Projeção por unidade (maiores cidades + resto de cada UF), comparação com o 1º turno, ritmo e meta.

Projeção (estimativa, não resultado):
- Unidades: cada uma das 80 maiores cidades acompanhadas e o "resto" de cada UF (UF − essas
  cidades). Sem arquivo de cidade, a UF inteira é uma unidade. Separar capital e interior tira a
  maior parte do viés de ordem de chegada que distorcia o swing medido por UF.
- Swing = fração do candidato A entre os finalistas agora − a mesma fração no 1º turno da unidade.
- O swing observado é encolhido pela confiabilidade ``r = apurado / (apurado + K·(1 − apurado))``:
  resto das UFs → 0 (o 1º turno); cidades → cidades da mesma UF → da região → do país → 0, cada
  nível valendo pela parte já apurada das suas cidades (pesada pelo tamanho).
- Fração do restante da unidade = fração do 1º turno + swing encolhido.
- Votos válidos restantes = eleitorado não totalizado × comparecimento × taxa de válidos
  (taxas atuais misturadas com as do 1º turno por ``w = min(1, apurado / 20%)``).
- Faixa: intervalo de ~90% de uma soma de erros de swing nos votos restantes — comum ao país
  (σ 1,5 p.p.), por região (1 p.p.) e por unidade (2,5 p.p.·√(1 − apurado)). Os σ são premissas,
  não medidas; a faixa não é intervalo de confiança calibrado.

"Matematicamente definido" usa apenas números oficiais: vantagem do líder maior que todo o
eleitorado ainda não totalizado (cada voto restante iria para o segundo colocado).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .baseline import Baseline, BaselineUf
from .parse import Counts, Progress, Result
from .sources import CIDADE_UF, REGIONS, UF_REGION
from .store import PacePoint

# Peso prévio no encolhimento do swing: r = apurado / (apurado + K·(1 − apurado)); com 5% apurado
# o dado observado vale metade. Calibrado em 25/10/2026 (por UF, centro 0): prever, a cada versão, o
# swing dos votos que entraram depois em cada UF. Centro 0 com K=0,05: RMSE 2,3 p.p., viés −0,1;
# centro regional/nacional (K=0,10): 2,7 p.p., viés +0,6; modelo original: 4,2 p.p., viés +3,2.
SWING_SHRINK_K = 0.05
SIGMA_COMMON = 0.015
SIGMA_REGION = 0.01
SIGMA_UNIT = 0.025
Z90 = 1.645
TURNOUT_FULL_WEIGHT_AT = 0.20  # comparecimento/taxa de válidos: só o dado atual a partir de 20% apurado
PROJECTION_NOTICE = ("Estimativa própria (maiores cidades + resto de cada UF vs 1º turno); "
                     "não é resultado oficial.")


def finalists(br: Result) -> tuple[str, str] | None:
    """Os dois candidatos do 2º turno em ordem fixa pelo número (13 antes de 22).

    Não use ``seq``: no arquivo do TSE ele é a classificação por votos e troca na virada
    (às 19:44:44 de 25/10/2026 o 22 passou a seq 1), o que invertia o candidato A."""
    if len(br.candidates) != 2:
        return None
    a, b = sorted(br.candidates, key=lambda c: int(c.numero))
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
    swing_pp: float  # swing estimado do resto das UFs, agregado no país (p.p.; candidato A vs 1º turno)
    swing_regioes_pp: dict[str, float]  # idem, por região
    swing_cidades_pp: float | None  # swing médio estimado das cidades no país (reserva); None sem cidades
    swing_cidades_ufs_pp: dict[str, float]  # ponto de partida das cidades de cada UF (UF → região → país)
    cidades_usadas: int  # quantas cidades entraram separadas do resto da sua UF
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


@dataclass(frozen=True)
class Pace:
    secoes_por_min: float
    janela_min: float
    restantes: int
    eta_min: float | None
    eta: datetime | None


def reliability(frac: float) -> float:
    """Peso do swing observado contra a opinião prévia, pela fração apurada (0–1): 0 → 0, 1 → 1."""
    return frac / (frac + SWING_SHRINK_K * (1 - frac)) if frac > 0 else 0.0


def _shrink(observed: float, frac: float, center: float) -> float:
    r = reliability(frac)
    return r * observed + (1 - r) * center


@dataclass(frozen=True)
class _Unit:
    """Uma cidade acompanhada ou o resto de uma UF (ou a UF inteira, sem cidades)."""

    uf: str
    cidade: bool
    result: Result | None
    base: BaselineUf
    progress_counts: Counts | None = None

    @property
    def frac(self) -> float:
        if self.result is None:
            return 0.0
        return 1.0 if self.result.andamento == "f" else min(1.0, max(0.0, self.result.counts.pct_secoes / 100))

    def votes(self, a: str, b: str) -> tuple[int, int]:
        if self.result is None:
            return 0, 0
        ca, cb = self.result.candidate(a), self.result.candidate(b)
        return (ca.votos if ca else 0), (cb.votos if cb else 0)


def _minus_counts(x: Counts, y: Counts) -> Counts:
    secoes_total = x.secoes_total - y.secoes_total
    secoes_totalizadas = x.secoes_totalizadas - y.secoes_totalizadas
    return Counts(
        secoes_total=secoes_total, secoes_totalizadas=secoes_totalizadas,
        pct_secoes=100 * secoes_totalizadas / secoes_total if secoes_total > 0 else 0.0,
        eleitorado=x.eleitorado - y.eleitorado,
        eleitorado_totalizado=x.eleitorado_totalizado - y.eleitorado_totalizado,
        eleitorado_nao_totalizado=x.eleitorado_nao_totalizado - y.eleitorado_nao_totalizado,
        comparecimento=x.comparecimento - y.comparecimento, abstencao=x.abstencao - y.abstencao,
    )


def _minus_result(x: Result, cities: list[Result]) -> Result | None:
    """UF − cidades; None se os arquivos estiverem defasados entre si (algum total negativo)."""
    counts, valid = x.counts, x.votos_validos
    votes = {c.numero: c.votos for c in x.candidates}
    for city in cities:
        counts, valid = _minus_counts(counts, city.counts), valid - city.votos_validos
        for c in city.candidates:
            votes[c.numero] = votes.get(c.numero, 0) - c.votos
    fields = (counts.secoes_total, counts.secoes_totalizadas, counts.eleitorado, counts.eleitorado_totalizado,
              counts.eleitorado_nao_totalizado, counts.comparecimento, counts.abstencao, valid, *votes.values())
    if min(fields) < 0 or counts.secoes_totalizadas > counts.secoes_total:
        return None
    candidates = [replace(c, votos=votes[c.numero]) for c in x.candidates]
    return replace(x, counts=counts, votos_validos=valid, candidates=candidates)


def _minus_base(x: BaselineUf, cities: list[BaselineUf]) -> BaselineUf:
    votes = dict(x.votos)
    for city in cities:
        for numero, v in city.votos.items():
            votes[numero] = votes.get(numero, 0) - v
    return BaselineUf(
        abr=x.abr, comparecimento=x.comparecimento - sum(c.comparecimento for c in cities),
        abstencao=x.abstencao - sum(c.abstencao for c in cities),
        votos_validos=x.votos_validos - sum(c.votos_validos for c in cities), votos=votes, pct={},
    )


def _units(ufs: dict[str, Result], cities: dict[str, Result], progress: Progress | None,
           baseline: Baseline) -> list[_Unit]:
    units: list[_Unit] = []
    for abr, base in baseline.ufs.items():
        result = ufs.get(abr)
        prog = progress.ufs[abr].counts if progress and abr in progress.ufs else None
        tracked = [cd for cd, uf in CIDADE_UF.items()
                   if uf == abr and cd in cities and cd in baseline.cidades] if result is not None else []
        rest = _minus_result(result, [cities[cd] for cd in tracked]) if tracked else None
        if rest is None:  # sem cidades, ou arquivos de cidade e UF fora de sincronia
            units.append(_Unit(abr, False, result, base, prog))
            continue
        units.extend(_Unit(abr, True, cities[cd], baseline.cidades[cd]) for cd in tracked)
        units.append(_Unit(abr, False, rest, _minus_base(base, [baseline.cidades[cd] for cd in tracked])))
    return units


def _observed_swing(unit: _Unit, a: str, b: str) -> float | None:
    va, vb = unit.votes(a, b)
    return va / (va + vb) - unit.base.two_party_share(a, b) if va + vb else None


def _pooled(units: list[_Unit], a: str, b: str, center: float) -> float:
    """Média dos swings observados pesada pela parte apurada; a parte que falta vota no centro."""
    counted = weighted = total = 0.0
    for unit in units:
        size = unit.base.votos.get(a, 0) + unit.base.votos.get(b, 0)
        total += size
        swing = _observed_swing(unit, a, b)
        if swing is not None:
            counted += size * unit.frac
            weighted += size * unit.frac * swing
    if counted <= 0 or total <= 0:
        return center
    return _shrink(weighted / counted, counted / total, center)


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
    w = min(1.0, counts.pct_secoes / 100 / TURNOUT_FULL_WEIGHT_AT)
    if not counts.comparecimento and not counts.abstencao:
        turnout, valid = base.turnout, base.valid_rate
    else:
        turnout = w * counts.pct_comparecimento / 100 + (1 - w) * base.turnout
        valid = (w * result.votos_validos / counts.comparecimento if counts.comparecimento else 0.0)
        valid += (1 - w) * base.valid_rate
    return counts.eleitorado_nao_totalizado * turnout * valid


def project(
    br: Result, ufs: dict[str, Result], progress: Progress | None, baseline: Baseline,
    cities: dict[str, Result] | None = None,
) -> Projection | None:
    pair = finalists(br)
    if pair is None:
        return None
    a, b = pair
    units = _units(ufs, cities or {}, progress, baseline)
    city_units = [u for u in units if u.cidade]
    # Centro das cidades em cascata: nacional (centro 0) → região (centro nacional) → UF (centro
    # regional). Cada nível vale pela parte já apurada das suas cidades, pesada pelo tamanho; assim a
    # capital paulista segue primeiro as cidades de SP, e Manaus/Belém só entram enquanto SP tem pouco.
    city_country = _pooled(city_units, a, b, 0.0) if city_units else 0.0
    city_region = {name: _pooled([u for u in city_units if u.uf in members], a, b, city_country)
                   for name, members in REGIONS.items()}
    city_uf = {uf: _pooled([u for u in city_units if u.uf == uf], a, b, city_region[UF_REGION[uf]])
               for uf in {u.uf for u in city_units}}

    proj = {a: 0.0, b: 0.0}
    var_common = 0.0  # Σ votos restantes (erro comum)
    var_region: dict[str, float] = {}
    var_unit = 0.0
    uf_votes: dict[str, list[float]] = {}
    for unit in units:
        va, vb = unit.votes(a, b)
        rem_valid = _remaining_valid_votes(unit.base, unit.result, unit.progress_counts)
        observed = _observed_swing(unit, a, b)
        center = city_uf[unit.uf] if unit.cidade else 0.0
        swing = center if observed is None else _shrink(observed, unit.frac, center)
        share = min(0.99, max(0.01, unit.base.two_party_share(a, b) + swing))

        proj[a] += va + rem_valid * share
        proj[b] += vb + rem_valid * (1 - share)
        var_common += rem_valid
        region = UF_REGION[unit.uf]
        var_region[region] = var_region.get(region, 0.0) + rem_valid
        var_unit += (rem_valid * SIGMA_UNIT) ** 2 * (1 - unit.frac)
        acc = uf_votes.setdefault(unit.uf, [0.0, 0.0])
        acc[0] += va + rem_valid * share
        acc[1] += vb + rem_valid * (1 - share)
    ufs_pct_a = {abr: 100 * va / (va + vb) for abr, (va, vb) in uf_votes.items() if va + vb > 0}
    sigma_votes = math.sqrt((var_common * SIGMA_COMMON) ** 2
                            + sum((v * SIGMA_REGION) ** 2 for v in var_region.values()) + var_unit)
    low_a, high_a = proj[a] - Z90 * sigma_votes, proj[a] + Z90 * sigma_votes

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
        swing_pp=100 * _pooled([u for u in units if not u.cidade and u.uf != "zz"], a, b, 0.0),
        swing_regioes_pp={name: 100 * _pooled([u for u in units if not u.cidade and u.uf in members], a, b, 0.0)
                          for name, members in REGIONS.items()},
        swing_cidades_pp=100 * city_country if city_units else None,
        swing_cidades_ufs_pp={uf: 100 * s for uf, s in sorted(city_uf.items())},
        cidades_usadas=len(city_units),
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
            )
        )
    return out


CHANCE_NOTICE = ("Chance estimada: palpite a partir da projeção (curva normal; incerteza = faixa da projeção, "
                 "no mínimo 2,5 p.p. × fração de votos que falta). Não é probabilidade calibrada.")
UNCERTAINTY_FLOOR_PP = 2.5
RECENT_WINDOW_MIN = 10.0
# Fração mínima de seções apuradas para extrapolar o ritmo em ETA (o início é lento e acelera).
PACE_ETA_MIN_FRAC = 0.10
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
    """Situação do candidato A (menor número, 13 LULA) em relação aos votos que faltam."""

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
    # No início o ritmo é baixo e acelera muito; extrapolar daí dá ETA de dias. Só estima com amostra madura.
    maduro = secoes_total > 0 and last.secoes_totalizadas / secoes_total >= PACE_ETA_MIN_FRAC
    eta_min = 0.0 if restantes == 0 else (restantes / rate if rate > 0 and maduro else None)
    return Pace(
        secoes_por_min=rate,
        janela_min=minutes,
        restantes=restantes,
        eta_min=eta_min,
        eta=last.totalizado_em + timedelta(minutes=eta_min) if eta_min is not None else None,
    )
