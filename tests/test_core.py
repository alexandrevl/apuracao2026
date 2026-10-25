import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from apuracao.analysis import pace, project
from apuracao.baseline import Baseline, BaselineUf
from apuracao.events import EventDetector
from apuracao.parse import parse_progress, parse_result
from apuracao.sources import UFS, Fetched
from apuracao.store import PacePoint, Store
from apuracao.tracker import Tracker

FIX = Path(__file__).parent / "fixtures"
A, B = "13", "22"  # ordem oficial (seq) no arquivo do 2º turno


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def make_result(abr: str, va: int, vb: int, frac: float, eleitorado: int = 1_000_000) -> tuple[dict, object]:
    """EA20 do 2º turno com votos e fração apurada dados (comparecimento 80%, válidos 95%)."""
    raw = copy.deepcopy(load("t2-br-u.json"))
    raw["cdabr"], raw["tpabr"], raw["idg"] = abr, "br" if abr == "br" else "uf", f"{abr}-{va}-{vb}-{frac}"
    est = round(eleitorado * frac)
    raw["s"].update(ts="1000", st=str(round(1000 * frac)), pstn=str(100 * frac).replace(".", ","))
    raw["e"].update(te=str(eleitorado), est=str(est), esnt=str(eleitorado - est),
                    c=str(round(est * 0.8)), a=str(est - round(est * 0.8)))
    raw["v"].update(vv=str(va + vb))
    for agr in raw["carg"][0]["agr"]:
        for par in agr["par"]:
            for c in par["cand"]:
                v = va if c["n"] == A else vb
                c["vap"] = str(v)
                c["pvapn"] = str(100 * v / (va + vb) if va + vb else 0).replace(".", ",")
    return raw, parse_result(raw)


def baseline(share_a: float = 0.5) -> Baseline:
    votes = {A: round(1_000_000 * share_a), B: round(1_000_000 * (1 - share_a))}
    uf = lambda abr: BaselineUf(abr, 800_000, 200_000, 760_000, votes, {})
    return Baseline(br=uf("br"), ufs={abr: uf(abr) for abr in UFS}, nomes={A: "LULA", B: "FLAVIO"})


def test_parse_real_first_round_matches_tse_totals():
    r = parse_result(load("t1-br-u.json"))
    assert r.andamento == "f" and r.counts.pct_secoes == 100.0
    assert r.candidates[0].nome == "FLAVIO BOLSONARO"
    assert r.candidates[0].votos == 56_104_503
    assert r.candidates[0].pct == pytest.approx(47.027772356)
    assert sum(c.votos for c in r.candidates) == r.votos_validos


def test_parse_ties_follow_official_sequence():
    r = parse_result(load("t2-br-u.json"))  # tudo zerado
    assert [c.numero for c in r.candidates] == ["13", "22"]


def test_parse_progress_has_every_uf_and_brazil():
    p = parse_progress(load("t2-br-ab.json"))
    assert set(p.ufs) == set(UFS) and p.br is not None


def test_projection_at_full_count_equals_official_result_with_no_band():
    ufs = {abr: make_result(abr, 600, 400, 1.0)[1] for abr in UFS}
    _, br = make_result("br", 600 * 28, 400 * 28, 1.0)
    p = project(br, ufs, None, baseline(0.3))
    a = next(c for c in p.candidates if c.numero == A)
    assert a.pct == pytest.approx(60.0)
    assert a.pct_min == pytest.approx(60.0) and a.pct_max == pytest.approx(60.0)


def test_projection_uses_first_round_where_nothing_counted_and_swing_where_counted():
    # Metade das UFs apurada 100% com A 5 p.p. acima do 1º turno; resto sem votos.
    counted = UFS[:14]
    ufs = {abr: make_result(abr, 550, 450, 1.0)[1] for abr in counted}
    _, br = make_result("br", 550 * 14, 450 * 14, 0.5)
    p = project(br, ufs, None, baseline(0.5))
    assert p.swing_pp == pytest.approx(5.0)
    a = next(c for c in p.candidates if c.numero == A)
    assert 54.0 < a.pct < 55.5  # UFs abertas recebem prior 50% + swing 5 p.p.
    assert a.pct_min < a.pct < a.pct_max


def test_mathematically_defined_only_when_lead_exceeds_remaining_electorate():
    ufs = {}
    _, br = make_result("br", 500_000, 300_000, 0.8, eleitorado=1_200_000)
    remaining = br.counts.eleitorado_nao_totalizado
    assert project(br, ufs, None, baseline()).definido is (200_000 > remaining)

    _, br_tie = make_result("br", 300_000 + remaining, 300_000, 0.8, eleitorado=1_200_000)
    assert project(br_tie, ufs, None, baseline()).definido is False
    _, br_win = make_result("br", 300_001 + remaining, 300_000, 0.8, eleitorado=1_200_000)
    p = project(br_win, ufs, None, baseline())
    assert p.definido is True and p.vencedor == A


def test_events_prime_silently_then_fire_once_on_lead_change_and_milestone():
    det = EventDetector()
    now = datetime(2026, 10, 25, 18)
    _, r1 = make_result("br", 520, 480, 0.30)
    assert det.update(r1, None, None, now) == []  # reinício no meio não repete alertas
    _, r2 = make_result("br", 490, 510, 0.52)
    types = [e.type for e in det.update(r2, None, None, now)]
    assert types.count("virada") == 1 and "marco" in types
    _, r3 = make_result("br", 489, 511, 0.53)
    assert det.update(r3, None, None, now) == []


def test_pace_uses_tse_totalization_clock():
    t0 = datetime(2026, 10, 25, 18)
    pts = [PacePoint(t0 + timedelta(minutes=m), s) for m, s in [(0, 0), (5, 1000), (10, 2000)]]
    p = pace(pts, secoes_total=5000)
    assert p.secoes_por_min == pytest.approx(200)
    assert p.eta_min == pytest.approx(15) and p.eta == t0 + timedelta(minutes=25)


class FakeSource:
    name, eleicao = "fake", "6258"

    def __init__(self):
        self.ab = copy.deepcopy(load("t2-br-ab.json"))
        self.results = {abr: make_result(abr, 0, 0, 0)[0] for abr in ("br", *UFS)}
        self.served: dict[str, str] = {}
        self.log: list[str] = []

    def _serve(self, key, raw):
        self.log.append(key)
        sig = json.dumps(raw, sort_keys=True)
        if self.served.get(key) == sig:
            return Fetched(None, key)
        self.served[key] = sig
        return Fetched(copy.deepcopy(raw), key)

    def progress(self):
        return self._serve("ab", self.ab)

    def result(self, abr):
        return self._serve(abr, self.results[abr])

    def advance(self, abr, va, vb, frac):
        raw, _ = make_result(abr, va, vb, frac)
        self.results[abr] = raw
        for item in self.ab["abr"]:
            if item["cdabr"] == abr:
                item["s"]["st"] = raw["s"]["st"]
                item["dt"], item["ht"], item["and"] = "25/10/2026", f"18:{len(self.log) % 60:02d}:00", "p"


def test_tracker_downloads_only_what_changed(tmp_path):
    src = FakeSource()
    tracker = Tracker(src, Store(tmp_path), baseline())
    tracker.poll()
    src.log.clear()

    tracker.poll()
    assert src.log == ["ab"]  # nada mudou: só o acompanhamento

    src.log.clear()
    src.advance("sp", 60, 40, 0.1)
    src.results["br"] = make_result("br", 60, 40, 0.01)[0]
    tracker.poll()
    assert src.log == ["ab", "br", "sp"]  # acompanhamento + Brasil + só a UF que avançou
    assert tracker.state.ufs["sp"].candidate(A).votos == 60


def test_thermometer_never_declares_result_from_estimated_remaining():
    # UFs defasadas (totais pequenos) e BR já bem à frente: o restante estimado fica negativo,
    # mas o TSE ainda tem eleitorado não totalizado e a apuração não terminou.
    from apuracao.analysis import thermometer
    ufs = {abr: make_result(abr, 600, 400, 1.0)[1] for abr in UFS}
    _, br = make_result("br", 300_000, 200_000, 0.6, eleitorado=10_000_000)
    p = project(br, ufs, None, baseline())
    assert p.validos_projetados < br.votos_validos and not p.definido
    t = thermometer(br, p, [])
    assert t.definido is False and t.rotulo not in ("eleito", "sem chance matemática")
    assert t.necessario_pct is None and t.projetado_pct is None


def test_thermometer_needed_share_and_chance_floor():
    from apuracao.analysis import thermometer
    # Metade apurada, A com 48%: precisa de ~52% do restante; com 50% faltando a chance não é ~0.
    ufs = {abr: make_result(abr, 480, 520, 0.5)[1] for abr in UFS}
    _, br = make_result("br", 480 * 28, 520 * 28, 0.5)
    t = thermometer(br, project(br, ufs, None, baseline(0.48)), [])
    assert t.necessario_pct == pytest.approx(52.0, abs=0.1)
    assert t.gap_pp == pytest.approx(4.0, abs=0.1)
    assert 0.5 < t.chance_pct < 20
