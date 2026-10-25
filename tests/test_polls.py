import json
import os

import pytest

from apuracao.analysis import project
from apuracao.polls import BUNDLED_POLLS, PollBook, compare
from apuracao.sources import UFS
from test_core import A, B, baseline, make_result


def poll(abr="br", a=50.0, margem=2.0, instituto="X"):
    return {"instituto": instituto, "abr": abr, "divulgacao": "2026-10-22", "margem_pp": margem,
            "validos": {A: a, B: 100 - a}}


def book(tmp_path, items):
    path = tmp_path / "pesquisas.json"
    path.write_text(json.dumps(items))
    return PollBook(path)


def test_before_final_reference_is_projection_and_ranking_by_error(tmp_path):
    ufs = {abr: make_result(abr, 520, 480, 0.5)[1] for abr in UFS}
    _, br = make_result("br", 520 * 28, 480 * 28, 0.5)
    proj = project(br, ufs, None, baseline(0.5))
    proj_a = next(c.pct for c in proj.candidates if c.numero == A)
    polls = book(tmp_path, [poll(a=45.0, instituto="Longe"), poll(a=round(proj_a, 1), instituto="Perto")]).refresh()

    out = compare(polls, br, ufs, proj)
    assert [c.poll.instituto for c in out] == ["Perto", "Longe"]
    assert all(c.referencia == "projecao" for c in out)
    assert out[1].diferenca_pp == pytest.approx(45.0 - proj_a)
    assert out[1].dentro_margem is False and out[0].dentro_margem is True


def test_after_final_reference_is_official_result_and_margin_is_inclusive(tmp_path):
    ufs = {abr: make_result(abr, 520, 480, 1.0)[1] for abr in UFS}
    raw, _ = make_result("br", 520 * 28, 480 * 28, 1.0)
    raw["and"] = "f"
    from apuracao.parse import parse_result
    br = parse_result(raw)
    proj = project(br, ufs, None, baseline(0.5))
    polls = book(tmp_path, [poll(a=50.0, margem=2.0), poll("sp", a=55.0, margem=3.0)]).refresh()

    nat, sp = compare(polls, br, ufs, proj)
    assert nat.referencia == "final" and nat.diferenca_pp == pytest.approx(-2.0)
    assert nat.dentro_margem is True  # exatamente na margem conta como dentro
    assert sp.referencia == "projecao"  # UF ainda sem and=f
    assert sp.projecao_a == pytest.approx(52.0)


def test_bad_edit_keeps_last_valid_polls_and_reports_error(tmp_path):
    pb = book(tmp_path, [poll()])
    assert len(pb.refresh()) == 1 and pb.error is None
    pb.path.write_text(json.dumps([{"instituto": "Y", "abr": "xx", "validos": {A: 50, B: 50}}]))
    os.utime(pb.path, (1, 1))  # garante mtime diferente
    assert len(pb.refresh()) == 1
    assert "abr inválida" in pb.error


def test_personal_list_replaces_project_list_and_switches_back_when_removed(tmp_path):
    project_file = tmp_path / "projeto.json"
    project_file.write_text(json.dumps({"pesquisas": [poll(instituto="Projeto A"), poll(instituto="Projeto B")]}))
    personal = tmp_path / "pesquisas.json"
    pb = PollBook(personal, fallback=project_file)
    assert [p.instituto for p in pb.refresh()] == ["Projeto A", "Projeto B"] and pb.origem == "projeto"
    personal.write_text(json.dumps([poll(instituto="Minha")]))
    assert [p.instituto for p in pb.refresh()] == ["Minha"] and pb.origem == "pessoal"
    personal.unlink()
    assert len(pb.refresh()) == 2 and pb.origem == "projeto"


def test_project_poll_list_is_valid_national_and_sourced():
    raw = json.loads(BUNDLED_POLLS.read_text(encoding="utf-8"))
    polls = PollBook(BUNDLED_POLLS).refresh()
    assert polls and len(polls) == len(raw["pesquisas"])
    institutes = [p.instituto for p in polls]
    assert len(institutes) == len(set(institutes))  # uma rodada (a mais recente) por instituto
    for p, item in zip(polls, raw["pesquisas"], strict=True):
        assert p.abr == "br" and p.fonte and p.fonte.startswith("https://")
        assert item.get("campo_fim") and p.margem_pp is not None
        assert abs(sum(p.validos.values()) - 100) <= 0.2
