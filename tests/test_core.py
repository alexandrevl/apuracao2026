import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from apuracao.analysis import pace, project
from apuracao.baseline import Baseline, BaselineUf
from apuracao.events import Event, EventDetector
from apuracao.parse import parse_progress, parse_result
from apuracao.sources import UFS, Fetched, SourceError
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

    def uf(abr: str) -> BaselineUf:
        return BaselineUf(abr, 800_000, 200_000, 760_000, votes, {})

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


def test_candidate_a_stays_13_when_tse_seq_flips_after_lead_change():
    # O seq do TSE é a classificação: na virada de 25/10 o 22 passou a seq 1.
    from apuracao.analysis import finalists, thermometer
    raw, _ = make_result("br", 490, 510, 0.5)
    for agr in raw["carg"][0]["agr"]:
        for par in agr["par"]:
            for c in par["cand"]:
                c["seq"] = "1" if c["n"] == B else "2"
    br = parse_result(raw)
    assert finalists(br) == (A, B)
    p = project(br, {}, None, baseline(0.5))
    assert p.candidates[0].numero == A
    assert thermometer(br, p, []).numero == A


def test_early_swing_is_shrunk_and_converges_to_observed_at_full_count():
    # Todas as UFs do país com A 10 p.p. acima do 1º turno: o swing estimado cresce com o % apurado.
    domestic = [abr for abr in UFS if abr != "zz"]
    swings = []
    for frac in (0.05, 0.5, 1.0):
        ufs = {abr: make_result(abr, 600, 400, frac)[1] for abr in domestic}
        _, br = make_result("br", 600 * 27, 400 * 27, frac)
        swings.append(project(br, ufs, None, baseline(0.5)).swing_pp)
    assert 0 < swings[0] < swings[1] < swings[2]
    assert swings[0] < 6  # com 5% apurado o swing observado vale ~metade
    assert swings[2] == pytest.approx(10.0)


def test_capital_counted_first_does_not_fake_a_statewide_swing():
    # SP: capital (60% A no 1º turno) e interior (40%) do mesmo tamanho; nenhum voto mudou,
    # mas só metade da capital foi apurada. Por UF isso parece +10 p.p. de swing; por unidade, não.
    def base_uf(abr, va, vb, k=1):
        return BaselineUf(abr, 800_000 * k, 200_000 * k, 760_000 * k, {A: va, B: vb}, {})

    base = baseline(0.5)
    base.ufs["sp"] = base_uf("sp", 1_000_000, 1_000_000, 2)
    base.cidades["71072"] = base_uf("71072", 600_000, 400_000)
    _, city = make_result("71072", 228_000, 152_000, 0.5)
    raw_uf, _ = make_result("sp", 228_000, 152_000, 0.25, eleitorado=2_000_000)
    raw_uf["s"].update(ts="2000", st="500")
    uf = parse_result(raw_uf)
    _, br = make_result("br", 228_000, 152_000, 0.01)

    by_uf = project(br, {"sp": uf}, None, base)
    by_unit = project(br, {"sp": uf}, None, base, {"71072": city})
    assert by_uf.ufs_pct_a["sp"] > 52
    assert by_unit.cidades_usadas == 1
    assert by_unit.ufs_pct_a["sp"] == pytest.approx(50.0, abs=0.01)
    a = next(c for c in by_unit.candidates if c.numero == A)
    assert a.pct_min < a.pct < a.pct_max


def test_city_without_data_follows_cities_of_its_own_state_not_other_regions():
    # Guarulhos (SP) quase toda apurada sem swing; Manaus quase toda apurada com +10 p.p.
    # A capital paulista, sem votos ainda, deve partir de SP (~0), não de Manaus.
    base = baseline(0.5)
    city_base = BaselineUf("c", 800_000, 200_000, 760_000, {A: 500_000, B: 500_000}, {})
    for cd in ("71072", "64777", "02550"):
        base.cidades[cd] = city_base
    for abr in ("sp", "am"):
        base.ufs[abr] = BaselineUf(abr, 2_400_000, 600_000, 2_280_000, {A: 1_500_000, B: 1_500_000}, {})

    def uf_file(abr, va, vb):
        raw, _ = make_result(abr, va, vb, 0.34, eleitorado=3_000_000)
        raw["s"].update(ts="3000", st="1000")
        return parse_result(raw)

    cities = {"71072": make_result("71072", 0, 0, 0.0)[1], "64777": make_result("64777", 450, 450, 0.9)[1],
              "02550": make_result("02550", 540, 360, 0.9)[1]}
    ufs = {"sp": uf_file("sp", 550, 550), "am": uf_file("am", 640, 460)}
    _, br = make_result("br", 1190, 1010, 0.05)
    p = project(br, ufs, None, base, cities)
    assert p.cidades_usadas == 3
    assert p.swing_cidades_ufs_pp["am"] > 8
    assert abs(p.swing_cidades_ufs_pp["sp"]) < 1


def test_city_files_out_of_sync_with_uf_fall_back_to_whole_uf():
    base = baseline(0.5)
    base.cidades["71072"] = BaselineUf("71072", 800_000, 200_000, 760_000, {A: 600_000, B: 400_000}, {})
    _, uf = make_result("sp", 100, 100, 0.1)
    _, city = make_result("71072", 5_000, 5_000, 0.5)  # cidade à frente do arquivo da UF
    _, br = make_result("br", 100, 100, 0.01)
    assert project(br, {"sp": uf}, None, base, {"71072": city}).cidades_usadas == 0


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


class CityFakeSource(FakeSource):
    """Publica São Paulo capital; ``city_fails`` simula 404/timeout só do arquivo municipal."""

    def __init__(self):
        super().__init__()
        self.city = make_result("71072", 30, 20, 0.05, eleitorado=500_000)[0]
        self.city_fails = True

    def city_result(self, uf, cd):
        if self.city_fails:
            self.log.append(f"{cd}!")
            raise SourceError(f"{cd}: HTTP 404")
        return self._serve(cd, self.city)


def _city_tracker(tmp_path, monkeypatch):
    clock = [datetime(2026, 10, 25, 19, 0)]
    monkeypatch.setattr("apuracao.tracker.now_brt", lambda: clock[0])
    base = baseline(0.5)
    base.cidades["71072"] = BaselineUf("71072", 400_000, 100_000, 380_000, {A: 300_000, B: 200_000}, {})
    src = CityFakeSource()
    src.advance("sp", 60, 40, 0.1)
    src.results["sp"]["s"]["ts"] = "2000"  # UF maior que a capital
    src.results["sp"].update(dt="25/10/2026", ht="18:00:00")  # igual ao acompanhamento: UF em dia
    src.results["br"] = make_result("br", 60, 40, 0.01)[0]
    return src, Tracker(src, Store(tmp_path), base), clock


def test_city_failure_does_not_block_official_update(tmp_path, monkeypatch):
    src, tracker, _ = _city_tracker(tmp_path, monkeypatch)
    st = tracker.poll()
    assert st.error is None and "71072!" in src.log
    assert st.br.candidate(A).votos == 60 and st.projection is not None
    assert st.projection.cidades_usadas == 0  # cai para a UF inteira


def test_pending_city_is_retried_even_when_progress_is_unchanged(tmp_path, monkeypatch):
    src, tracker, clock = _city_tracker(tmp_path, monkeypatch)
    tracker.poll()
    src.city_fails = False
    clock[0] += timedelta(seconds=30)
    src.log.clear()
    st = tracker.poll()  # EA14 igual (304): a cidade pendente ainda é buscada
    assert src.log == ["ab", "71072"]
    assert st.projection.cidades_usadas == 1


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


def test_stale_warning_uses_brasilia_clock_and_survives_restart_without_repeating(tmp_path, monkeypatch):
    clock = [datetime(2026, 10, 25, 18, 0)]
    monkeypatch.setattr("apuracao.tracker.now_brt", lambda: clock[0])
    src = FakeSource()
    src.advance("sp", 60, 40, 0.1)
    src.results["br"] = make_result("br", 60, 40, 0.01)[0]
    src.results["br"].update({"and": "p", "dt": "25/10/2026", "ht": "17:06:27"})

    def warnings(store):
        return [e for e in store.events() if e["type"] == "sem_atualizacao"]

    store = Store(tmp_path)
    tracker = Tracker(src, store, baseline())
    tracker.poll()
    clock[0] = datetime(2026, 10, 25, 18, 11)
    tracker.poll()
    assert tracker.state.last_check == datetime(2026, 10, 25, 18, 11)
    assert [w["captured_at"] for w in warnings(store)] == ["2026-10-25T18:11:00"]

    restarted = Tracker(src, store, baseline())
    restarted.poll()
    clock[0] = datetime(2026, 10, 25, 18, 30)
    restarted.poll()
    assert len(warnings(store)) == 1


def test_legacy_stale_warnings_without_payload_still_block_a_repeat(tmp_path, monkeypatch):
    clock = [datetime(2026, 10, 25, 18, 0)]
    monkeypatch.setattr("apuracao.tracker.now_brt", lambda: clock[0])
    src = FakeSource()
    src.advance("sp", 60, 40, 0.1)
    src.results["br"] = make_result("br", 60, 40, 0.01)[0]
    src.results["br"].update({"and": "p", "dt": "25/10/2026", "ht": "17:06:27"})
    store = Store(tmp_path)
    legacy = Event("sem_atualizacao", "Sem atualização do TSE há 10 min", datetime(2026, 10, 25, 22, 16),
                   datetime(2026, 10, 25, 17, 6, 27))
    store.save_events([legacy])
    tracker = Tracker(src, store, baseline())
    tracker.poll()
    clock[0] = datetime(2026, 10, 25, 18, 30)
    tracker.poll()
    assert [e["type"] for e in store.events()] == ["sem_atualizacao"]


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
