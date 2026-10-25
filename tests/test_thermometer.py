from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from apuracao.analysis import analyze_ufs, pace, project, recent_share, thermometer
from apuracao.charts import projection_series
from apuracao.store import PacePoint, Store
from apuracao.tracker import State
from test_core import A, baseline, make_result


def scenario(frac=0.9, total=1100, projected_a=630):
    br = replace(make_result('br', 600, 400, frac, eleitorado=100_000)[1], andamento='p')
    projection = project(br, {}, None, baseline())
    candidates = [replace(c, votos=projected_a if c.numero == A else total - projected_a,
                          pct=100 * (projected_a if c.numero == A else total - projected_a) / total)
                  for c in projection.candidates]
    return br, replace(projection, validos_projetados=total, candidates=candidates)


def test_estimated_meta_can_be_reached_without_official_mathematical_definition():
    br, projection = scenario()
    t = thermometer(br, projection, [])
    assert t.necessario_pct == pytest.approx(-50)
    assert t.projetado_pct == pytest.approx(30)
    assert not t.definido


@pytest.mark.parametrize('projected_a', [599, 701])
def test_impossible_remaining_projection_is_unavailable_not_clamped(projected_a):
    br, projection = scenario(projected_a=projected_a)
    t = thermometer(br, projection, [])
    assert t.necessario_pct is None and t.projetado_pct is None
    assert t.gap_pp is None and not t.definido


def test_official_definition_and_final_count_stop_the_remaining_vote_calculation():
    br, projection = scenario(frac=0.9999, total=1001, projected_a=600)
    assert projection.definido  # vantagem de 200 votos > 10 eleitores não totalizados
    t = thermometer(br, projection, [])
    assert t.definido and t.chance_pct == 100
    assert t.necessario_pct is None and t.projetado_pct is None and t.gap_pp is None
    final_br = replace(br, andamento='f')
    t = thermometer(final_br, replace(projection, definido=False, vencedor=None), [])
    assert t.definido and t.necessario_pct is None and t.projetado_pct is None


def test_historical_thermometer_stops_at_official_definition_and_ignores_incompatible_data(tmp_path):
    store = Store(tmp_path)
    start = datetime(2026, 10, 25, 19)
    scenarios = [scenario(0.5, 2000, 1020), scenario(0.7, 1100, 599),
                 scenario(0.9), scenario(0.9999, 1001, 600), scenario(1, 1000, 600)]
    for i, (br, projection) in enumerate(scenarios):
        now = start + timedelta(minutes=i)
        br = replace(br, totalizado_em=now)
        if i == len(scenarios) - 1:
            br = replace(br, andamento='f')
        # O histórico de gráficos usa as colunas tipadas do SQLite, não precisa de leitura de brutos.
        store.save_result(br, {}, now)
        store.save_projection(projection, br, now)
    state = State(source='sim', eleicao='6258', br=br)
    state.timeline_official, state.timeline_projections = store.timeline()
    series = projection_series(state)
    assert series['x_need'] == [50, 90]
    assert series['need'] == pytest.approx([40, -50])  # bruto preservado para interpretação, exibição limita a 0
    assert series['proj_rest'] == pytest.approx([42, 30])
    assert series['chance'][-2:] == [100, 100]
    store.close()


def test_finalized_history_stops_even_if_the_vote_count_is_tied(tmp_path):
    store = Store(tmp_path)
    start = datetime(2026, 10, 25, 19)
    _, projection = scenario(0.5, 2000, 1000)
    for index, frac in enumerate((0.5, 1.0)):
        br = make_result('br', 500, 500, frac, eleitorado=100_000)[1]
        br = replace(br, totalizado_em=start + timedelta(minutes=index),
                     andamento='f' if index else 'p')
        store.save_result(br, {}, br.totalizado_em)
        store.save_projection(projection, br, br.totalizado_em)
    state = State(source='sim', eleicao='6258', br=br)
    state.timeline_official, state.timeline_projections = store.timeline()
    assert projection_series(state)['x_need'] == [50]
    store.close()


def test_final_national_count_overrides_delayed_state_projection():
    br = replace(make_result('br', 400, 600, 1)[1], andamento='f')
    projection = project(br, {}, None, baseline(0.8))
    assert projection.validos_projetados == br.votos_validos
    for candidate in projection.candidates:
        assert candidate.votos == br.candidate(candidate.numero).votos
        assert candidate.pct_min == candidate.pct == candidate.pct_max
    t = thermometer(br, projection, [])
    assert t.definido and t.chance_pct == 0


def test_final_tie_does_not_elect_first_candidate_in_official_sequence():
    br = replace(make_result('br', 500, 500, 1)[1], andamento='f')
    projection = project(br, {}, None, baseline())
    t = thermometer(br, projection, [])
    assert t.definido and t.chance_pct == 50 and t.rotulo == 'empate'
    assert not projection.definido and projection.vencedor is None


def test_tied_state_has_no_isolated_leader():
    br = make_result('br', 500, 500, 0.5)[1]
    state = make_result('sp', 500, 500, 0.5)[1]
    row = next(r for r in analyze_ufs(br, {'sp': state}, None, baseline()) if r.abr == 'sp')
    assert row.lider is None


@pytest.mark.parametrize('votes,minutes', [(90, 1), (250, 1), (150, 0)])
def test_recent_share_rejects_retotalization_or_same_clock(votes, minutes):
    start = datetime(2026, 10, 25, 19)
    official = [
        {'totalizado_em': start.isoformat(), 'validos': 200, 'votos': {A: 100}},
        {'totalizado_em': (start + timedelta(minutes=minutes)).isoformat(),
         'validos': 300, 'votos': {A: votes}},
    ]
    assert recent_share(official, A) is None


def test_pace_retotalization_is_not_reported_as_negative_arrival_rate():
    start = datetime(2026, 10, 25, 19)
    points = [PacePoint(start, 200), PacePoint(start + timedelta(minutes=1), 100)]
    assert pace(points, 1000) is None


def test_pace_at_complete_count_has_zero_eta_even_without_new_sections():
    start = datetime(2026, 10, 25, 19)
    points = [PacePoint(start, 1000), PacePoint(start + timedelta(minutes=1), 1000)]
    result = pace(points, 1000)
    assert result.secoes_por_min == 0 and result.restantes == 0
    assert result.eta_min == 0 and result.eta == points[-1].totalizado_em


def test_known_zero_turnout_is_displayed_as_zero_not_missing():
    br = make_result('br', 0, 0, 0.5)[1]
    state = make_result('sp', 0, 0, 0.5)[1]
    state = replace(state, counts=replace(state.counts, comparecimento=0, abstencao=500_000))
    row = next(r for r in analyze_ufs(br, {'sp': state}, None, baseline()) if r.abr == 'sp')
    assert row.comparecimento_pct == 0
