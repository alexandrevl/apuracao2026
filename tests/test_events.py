from dataclasses import replace
from datetime import datetime, timedelta

from apuracao.analysis import project
from apuracao.events import EventDetector
from apuracao.sources import UFS
from test_core import baseline, make_result

NOW = datetime(2026, 10, 25, 19)


def scenario(share):
    ufs = {abr: make_result(abr, share, 1000 - share, 0.8)[1] for abr in UFS}
    br = replace(make_result('br', share * 28, (1000 - share) * 28, 0.8)[1], andamento='p')
    return br, project(br, ufs, None, baseline())


def test_model_call_is_symmetric_reversible_and_separate_from_mathematical_result():
    detector = EventDetector()
    br, projection = scenario(500)
    assert detector.update(br, None, projection, NOW) == []
    for share, winner in ((540, '13'), (460, '22')):
        br, projection = scenario(share)
        events = detector.update(br, None, projection, NOW)
        calls = [event for event in events if event.type == 'projecao_definida']
        assert len(calls) == 1 and calls[0].payload['vencedor'] == winner
        assert calls[0].payload['chance_pct'] >= 99
        assert not any(event.type == 'definido' for event in events)
        assert not any(event.type == 'projecao_definida'
                       for event in detector.update(br, None, projection, NOW))
        br, projection = scenario(500)
        withdrawals = [event for event in detector.update(br, None, projection, NOW)
                       if event.type == 'projecao_retirada']
        assert len(withdrawals) == 1 and withdrawals[0].payload['anterior'] == winner


def test_stale_estimated_totals_withdraw_model_call_without_declaring_winner():
    detector = EventDetector()
    br, projection = scenario(500)
    detector.update(br, None, projection, NOW)
    br, projection = scenario(540)
    detector.update(br, None, projection, NOW)
    stale = replace(projection, validos_projetados=br.votos_validos - 1)
    events = detector.update(br, None, stale, NOW)
    assert any(event.type == 'projecao_retirada' for event in events)
    assert not any(event.type in ('projecao_definida', 'definido', 'apuracao_finalizada') for event in events)


def test_official_mathematical_and_finished_events_are_independent_and_not_repeated():
    detector = EventDetector()
    br, projection = scenario(500)
    detector.update(br, None, projection, NOW)
    br, projection = scenario(540)
    br = replace(br, counts=replace(br.counts, eleitorado_nao_totalizado=1))
    projection = project(br, {}, None, baseline())
    events = detector.update(br, None, projection, NOW)
    assert any(event.type == 'definido' for event in events)
    assert not any(event.type in ('projecao_definida', 'apuracao_finalizada') for event in events)
    final = replace(br, andamento='f')
    events = detector.update(final, None, projection, NOW)
    assert sum(event.type == 'apuracao_finalizada' for event in events) == 1
    assert not any(event.type == 'apuracao_finalizada'
                   for event in detector.update(final, None, projection, NOW))
    restarted = EventDetector()
    assert restarted.update(final, None, projection, NOW) == []


def test_repeated_identical_results_do_not_postpone_stale_warning():
    detector = EventDetector()
    br, projection = scenario(500)
    detector.update(br, None, projection, NOW)
    detector.update(br, None, projection, NOW + timedelta(minutes=9))
    warning = detector.check_stale(br, NOW + timedelta(minutes=10))
    assert warning is not None and warning.type == 'sem_atualizacao'
    detector.update(br, None, projection, NOW + timedelta(minutes=11))
    assert detector.check_stale(br, NOW + timedelta(minutes=12)) is None


def test_stale_warning_is_stamped_with_the_moment_it_fired_not_the_last_tse_data():
    detector = EventDetector()
    br, projection = scenario(500)
    br = replace(br, totalizado_em=datetime(2026, 10, 25, 17, 6, 27))
    detector.update(br, None, projection, NOW)
    warning = detector.check_stale(br, NOW + timedelta(minutes=12))
    assert warning.captured_at == NOW + timedelta(minutes=12) and warning.totalizado_em is None
    assert warning.payload['ultimo_totalizado_em'] == '2026-10-25T17:06:27'


def test_no_model_call_below_minimum_sections():
    detector = EventDetector()
    br, projection = scenario(500)
    detector.update(br, None, projection, NOW)
    br, projection = scenario(540)
    early = replace(br, counts=replace(br.counts, secoes_totalizadas=br.counts.secoes_total // 100, pct_secoes=1.0))
    assert not any(e.type == 'projecao_definida' for e in detector.update(early, None, projection, NOW))


def test_not_defined_tse_flag_does_not_emit_definition():
    detector = EventDetector()
    br, _ = scenario(500)
    detector.update(replace(br, matematicamente_definido=''), None, None, NOW)
    events = detector.update(replace(br, matematicamente_definido='n'), None, None, NOW)
    assert not any(e.type == 'tse_md' for e in events)
    events = detector.update(replace(br, matematicamente_definido='e'), None, None, NOW)
    assert sum(e.type == 'tse_md' for e in events) == 1


def test_tie_does_not_create_false_lead_change_or_leading_milestone():
    detector = EventDetector()
    br = make_result('br', 400, 600, 0.4)[1]
    detector.update(br, None, None, NOW)
    tied = make_result('br', 600, 600, 0.5)[1]
    events = detector.update(tied, None, None, NOW)
    assert not any(e.type in ('virada', 'lideranca') for e in events)
    milestone = next(e for e in events if e.type == 'marco')
    assert milestone.payload['lider'] is None and 'sem liderança isolada' in milestone.message
