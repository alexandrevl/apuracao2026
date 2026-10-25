import copy
import json
from datetime import datetime

import httpx
import pytest
from typer.testing import CliRunner

from apuracao import cli, store as store_module
from apuracao.baseline import load_baseline
from apuracao.sources import SourceError, TseSource
from apuracao.store import Store
from apuracao.tracker import Tracker
from test_core import FakeSource, baseline, make_result


def test_installed_data_directory_uses_user_location(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "__file__", str(tmp_path / "venv/lib/python3.12/site-packages/apuracao/cli.py"))
    monkeypatch.delenv("APURACAO_DATA", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user-data"))
    assert cli.data_directory() == tmp_path / "user-data/apuracao"
    monkeypatch.setenv("APURACAO_DATA", str(tmp_path / "override"))
    assert cli.data_directory() == tmp_path / "override"


def test_checkout_keeps_existing_data_directory(tmp_path, monkeypatch):
    package = tmp_path / "src/apuracao"
    package.mkdir(parents=True)
    (tmp_path / "pyproject.toml").touch()
    monkeypatch.setattr(cli, "__file__", str(package / "cli.py"))
    monkeypatch.delenv("APURACAO_DATA", raising=False)
    assert cli.data_directory() == tmp_path / "data"


@pytest.mark.parametrize("arguments", [
    ["snapshot", "--cached", "--json"],
    ["snapshot", "-s", "sim", "--cached", "--json"],
    ["ufs", "--cached", "--json"],
    ["pesquisas", "--json"],
    ["grafico"],
    ["history", "--json"],
    ["events", "--json"],
])
def test_local_commands_never_initialize_network(tmp_path, monkeypatch, arguments):
    def forbidden(*args, **kwargs):
        raise AssertionError("local command initialized a network source")

    monkeypatch.setattr(cli, "DATA_DIR", tmp_path)
    monkeypatch.setattr("apuracao.sources.TseSource.__init__", forbidden)
    monkeypatch.setattr("apuracao.sim.SimSource.__init__", forbidden)
    result = CliRunner().invoke(cli.app, arguments)
    assert result.exit_code in (0, 2), result.output
    assert not (tmp_path / "baseline").exists()
    assert not (tmp_path / "templates").exists()


def test_missing_baseline_local_read_does_not_write(tmp_path):
    assert load_baseline(tmp_path, download=False) is None
    assert list(tmp_path.iterdir()) == []


def test_cached_official_result_survives_missing_baseline(tmp_path, monkeypatch):
    raw, result = make_result("br", 60, 40, 0.1)
    database = Store(tmp_path / "tse")
    database.save_result(result, raw, datetime(2026, 10, 25, 18))
    database.close()
    monkeypatch.setattr(cli, "DATA_DIR", tmp_path)
    response = CliRunner().invoke(cli.app, ["snapshot", "--cached", "--json"])
    assert response.exit_code == 0, response.output
    payload = json.loads(response.output)
    assert next(c for c in payload["candidatos"] if c["numero"] == "13")["votos"] == 60
    assert payload["projecao"] is None
    assert "Base do 1º turno ausente" in payload["erro"]


def test_failed_raw_write_rolls_back_database(tmp_path, monkeypatch):
    raw, result = make_result("br", 60, 40, 0.1)
    database = Store(tmp_path)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(store_module, "write_json_atomic", fail)
    with pytest.raises(OSError, match="disk full"):
        database.save_result(result, raw, datetime.now())
    assert database.history() == []
    database.close()
    reopened = Store(tmp_path)
    assert reopened.latest_results() == {}
    reopened.close()


def test_store_can_be_reopened_after_directory_move(tmp_path):
    root = tmp_path / "original"
    raw, result = make_result("br", 60, 40, 0.1)
    database = Store(root)
    database.save_result(result, raw, datetime.now())
    database.close()
    moved = tmp_path / "moved"
    root.rename(moved)
    reopened = Store(moved)
    assert reopened.latest_results()["br"].candidate("13").votos == 60
    reopened.close()


def test_etag_is_removed_when_new_response_has_none():
    requests = []
    responses = iter([
        httpx.Response(200, json={"version": 1}, headers={"ETag": '"one"'}),
        httpx.Response(200, json={"version": 2}),
        httpx.Response(200, json={"version": 3}),
    ])

    def handler(request):
        requests.append(request)
        return next(responses)

    source = TseSource(max_rps=1_000_000)
    source._client.close()
    source._client = httpx.Client(base_url="https://example.invalid/", transport=httpx.MockTransport(handler))
    try:
        for _ in range(3):
            source.progress()
        assert requests[1].headers["If-None-Match"] == '"one"'
        assert "If-None-Match" not in requests[2].headers
    finally:
        source.close()


def test_unsolicited_304_is_an_error():
    source = TseSource()
    source._client.close()
    source._client = httpx.Client(base_url="https://example.invalid/", transport=httpx.MockTransport(
        lambda request: httpx.Response(304)))
    try:
        with pytest.raises(SourceError, match="sem versão local"):
            source.progress()
    finally:
        source.close()


def test_tracker_retries_cdn_lag_even_if_progress_is_unchanged(tmp_path):
    source = FakeSource()
    tracker = Tracker(source, Store(tmp_path), baseline())
    try:
        tracker.poll()
        source.advance("sp", 60, 40, 0.1)
        current = copy.deepcopy(source.results["sp"])
        source.results["sp"] = make_result("sp", 0, 0, 0)[0]
        tracker.poll()  # EA14 avançou mas EA20 ainda responde 304.
        source.log.clear()
        source.results["sp"] = current
        tracker.poll()  # Mesmo EA14 (304): ainda precisa buscar SP.
        assert "sp" in source.log
        assert tracker.state.ufs["sp"].candidate("13").votos == 60
    finally:
        tracker.store.close()


def test_tracker_retries_national_cdn_lag(tmp_path):
    source = FakeSource()
    tracker = Tracker(source, Store(tmp_path), baseline())
    try:
        tracker.poll()
        for item in source.ab["abr"]:
            if item["cdabr"] == "br":
                item["s"]["st"] = "10"
        tracker.poll()  # BR ainda 304, acompanhamento já avançou.
        source.log.clear()
        source.results["br"] = make_result("br", 60, 40, 0.1)[0]
        tracker.poll()
        assert "br" in source.log
        assert tracker.state.br.candidate("13").votos == 60
    finally:
        tracker.store.close()


def test_sim_cached_read_preserves_existing_history(tmp_path, monkeypatch):
    raw, result = make_result("br", 60, 40, 0.1)
    database = Store(tmp_path / "sim")
    database.save_result(result, raw, datetime.now())
    database.close()
    monkeypatch.setattr(cli, "DATA_DIR", tmp_path)
    response = CliRunner().invoke(cli.app, ["snapshot", "-s", "sim", "--cached", "--json"])
    assert response.exit_code == 0, response.output
    reopened = Store(tmp_path / "sim")
    assert len(reopened.history()) == 1
    reopened.close()
    assert not (tmp_path / "tse").exists()


def test_raw_identifiers_cannot_escape_store(tmp_path):
    from dataclasses import replace

    raw, result = make_result("br", 60, 40, 0.1)
    database = Store(tmp_path)
    try:
        with pytest.raises(ValueError, match="identificador"):
            database.save_result(replace(result, idg="../../escape"), raw, datetime.now())
        assert database.history() == []
    finally:
        database.close()
