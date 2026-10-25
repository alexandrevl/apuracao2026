from dataclasses import replace

import pytest

from apuracao.analysis import project
from apuracao.parse import Counts
from apuracao.regions import aggregate_regions, sorted_regions
from apuracao.sources import REGIONS, UF_REGION, UFS
from test_core import A, B, baseline, make_result


def national(ufs):
    template = make_result('br', 0, 0, 0)[1]
    counts = {key: sum(getattr(r.counts, key) for r in ufs.values())
              for key in Counts.__dataclass_fields__ if key != 'pct_secoes'}
    counts['pct_secoes'] = 100 * counts['secoes_totalizadas'] / counts['secoes_total']
    valid = sum(r.votos_validos for r in ufs.values())
    candidates = [replace(c, votos=sum(r.candidate(c.numero).votos for r in ufs.values()))
                  for c in template.candidates]
    candidates = [replace(c, pct=100 * c.votos / valid if valid else 0) for c in candidates]
    candidates.sort(key=lambda c: -c.votos)
    return replace(template, counts=Counts(**counts), votos_validos=valid, candidates=candidates)


def scenario():
    return {uf: make_result(uf, 480, 520, 0.5)[1] for uf in UFS}


def test_region_percentages_are_ratios_of_totals_not_means():
    ufs = {uf: make_result(uf, 0, 0, 0)[1] for uf in UFS}
    sp = make_result('sp', 600, 400, 0.1)[1]
    rj = make_result('rj', 40, 60, 0.9)[1]
    ufs['sp'] = replace(sp, counts=replace(sp.counts, secoes_total=9000, secoes_totalizadas=900,
                                          comparecimento=90, abstencao=10))
    ufs['rj'] = replace(rj, counts=replace(rj.counts, comparecimento=100, abstencao=100))
    base = baseline()
    regional = aggregate_regions(national(ufs), ufs, None, base)
    southeast = next(r for r in regional['regioes'] if r['regiao'] == 'Sudeste')
    assert southeast['secoes']['pct'] == pytest.approx(15)
    assert southeast['pct'][A] == pytest.approx(100 * 640 / 1100)
    assert southeast['eleitorado']['pct_comparecimento'] == pytest.approx(100 * 190 / 300)
    assert southeast['saldo_candidato_a_votos'] == 180
    assert southeast['swing_pp'] == pytest.approx(100 * 640 / 1100 - 50)


def test_complete_regions_reconcile_with_brazil_and_reuse_remaining_votes_model():
    ufs = scenario()
    br, base = national(ufs), baseline()
    regional = aggregate_regions(br, ufs, None, base)
    assert set(UF_REGION) == set(UFS)
    assert sum(len(members) for members in REGIONS.values()) == 28
    assert regional['consistencia']['status'] == 'sincronizado'
    assert sum(r['saldo_candidato_a_votos'] for r in regional['regioes']) == (
        br.candidate(A).votos - br.candidate(B).votos)
    projection = project(br, ufs, None, base)
    remaining = sum(r['validos_restantes_estimados'] for r in regional['regioes'])
    assert remaining == pytest.approx(projection.validos_projetados - br.votos_validos, abs=3)
    for row in regional['regioes']:
        assert row['pct'][A] + row['pct'][B] == pytest.approx(100)
    assert regional['regioes'][-1]['regiao'] == 'Exterior'


def test_national_ahead_of_states_is_flagged_without_changing_state_totals():
    ufs = scenario()
    br = national(ufs)
    ahead = replace(br, votos_validos=br.votos_validos + 100,
                    candidates=[replace(c, votos=c.votos + (100 if c.numero == A else 0))
                                for c in br.candidates])
    data = aggregate_regions(ahead, ufs, None, baseline())
    assert data['consistencia']['status'] == 'defasado'
    assert data['consistencia']['diferenca_validos_ufs_menos_br'] == -100
    assert data['consistencia']['diferenca_votos_ufs_menos_br'][A] == -100
    assert sum(row['votos_validos'] for row in data['regioes']) == br.votos_validos


def test_missing_state_does_not_masquerade_as_complete_regional_result():
    ufs = scenario()
    br = national(ufs)
    del ufs['sp']
    data = aggregate_regions(br, ufs, None, baseline())
    southeast = next(r for r in data['regioes'] if r['regiao'] == 'Sudeste')
    assert data['consistencia']['status'] == 'incompleto'
    assert southeast['ufs_sem_resultado'] == ['sp']
    assert southeast['pct'][A] is None and southeast['saldo_candidato_a_votos'] is None
    assert southeast['secoes']['pct'] is None
    assert 'sp' in southeast['ufs_restante_base_1t']
    assert southeast['validos_restantes_estimados'] > 0
    assert sorted_regions(data['regioes'], 'apurado')[-1]['regiao'] == 'Sudeste'


def test_finalized_regions_have_no_remaining_votes_and_sort_by_remaining_volume():
    ufs = {uf: replace(make_result(uf, 600, 400, 1)[1], andamento='f') for uf in UFS}
    data = aggregate_regions(national(ufs), ufs, None, baseline())
    for row in data['regioes']:
        assert row['validos_restantes_estimados'] == 0
        assert row['andamento'] == 'f' and row['secoes']['pct'] == 100
        assert row['ufs_finalizadas'] == len(row['ufs'])
    ufs['sp'] = make_result('sp', 100, 100, 0.1, eleitorado=30_000_000)[1]
    rows = aggregate_regions(national(ufs), ufs, None, baseline())['regioes']
    assert sorted_regions(rows, 'restantes')[0]['regiao'] == 'Sudeste'


def test_known_zero_turnout_does_not_revert_to_baseline_turnout():
    ufs = {}
    for uf in UFS:
        result = make_result(uf, 0, 0, 0.5)[1]
        ufs[uf] = replace(result, counts=replace(
            result.counts, comparecimento=0, abstencao=result.counts.eleitorado_totalizado))
    data = aggregate_regions(national(ufs), ufs, None, baseline())
    assert all(row['validos_restantes_estimados'] == 0 for row in data['regioes'])


def test_final_state_does_not_forecast_votes_in_residual_untotalized_electorate():
    ufs = {uf: replace(make_result(uf, 600, 400, 0.99)[1], andamento='f') for uf in UFS}
    data = aggregate_regions(national(ufs), ufs, None, baseline())
    assert all(row['validos_restantes_estimados'] == 0 for row in data['regioes'])
