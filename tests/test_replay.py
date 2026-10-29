from datetime import datetime

import pytest
from typer.testing import CliRunner

from apuracao import cli
from apuracao.parse import parse_result
from apuracao.replay import ReplayTracker, replay, resolve_paths
from apuracao.store import Store
from test_core import baseline, make_result


def stamped(abr, va, vb, frac, hora):
    raw, _ = make_result(abr, va, vb, frac)
    raw["dt"], raw["ht"] = ("25/10/2026", hora) if hora else ("", "")
    return raw, parse_result(raw)


def night(root):
    """BR 10% → 30% → 50%; uma UF junto do 30%; uma cópia velha (CDN atrasado) chegando por último."""
    store = Store(root)
    for captured, args in [
        ("2026-10-25T17:00:00", ("br", 0, 0, 0.0, None)),  # antes da apuração: zerado, sem totalização
        ("2026-10-25T19:00:00", ("br", 100, 90, 0.1, "18:59:00")),
        ("2026-10-25T19:05:00", ("br", 300, 280, 0.3, "19:04:00")),
        ("2026-10-25T19:05:00", ("sp", 150, 100, 0.3, "19:04:00")),
        ("2026-10-25T19:10:00", ("br", 500, 470, 0.5, "19:09:00")),
        ("2026-10-25T19:12:00", ("br", 250, 240, 0.2, "18:30:00")),  # retrocede: o ao vivo descartaria
    ]:
        raw, result = stamped(*args)
        store.save_result(result, raw, datetime.fromisoformat(captured))
    store.close()


def test_clock_releases_rounds_at_original_pace_and_keeps_original_timestamps(tmp_path):
    night(tmp_path / "tse")
    now = [0.0]
    store = Store(tmp_path / "replay")
    tracker = ReplayTracker(tmp_path / "tse", store, baseline(0.5), speed=1.0, clock=lambda: now[0])

    tracker.poll()  # 19:00:00 → o zerado de 17:00 entra junto, a apuração começa em 10%
    assert tracker.state.br.counts.pct_secoes == 10.0
    now[0] = 299.0  # 19:04:59
    tracker.poll()
    assert tracker.state.br.counts.pct_secoes == 10.0
    assert "sp" not in tracker.state.ufs
    now[0] = 300.0  # 19:05:00: BR e SP são da mesma rodada
    tracker.poll()
    assert tracker.state.br.counts.pct_secoes == 30.0
    assert "sp" in tracker.state.ufs
    now[0] = 10_000.0
    tracker.poll()
    assert tracker.state.br.counts.pct_secoes == 50.0  # a cópia velha de 19:12 não retrocede o painel

    rows = store.history("br")
    assert [r["captured_at"] for r in rows] == [
        "2026-10-25T17:00:00", "2026-10-25T19:00:00", "2026-10-25T19:05:00", "2026-10-25T19:10:00"]
    assert tracker.replayer.skipped == 1 and tracker.replayer.done
    store.close()


def test_offset_and_speed_skip_ahead(tmp_path):
    night(tmp_path / "tse")
    store = Store(tmp_path / "replay")
    tracker = ReplayTracker(tmp_path / "tse", store, baseline(0.5), speed=60.0, offset=5.0, clock=lambda: 0.0)
    tracker.poll()
    assert tracker.state.br.counts.pct_secoes == 30.0  # 19:00 + 5 min
    store.close()


def test_batch_replay_matches_paced_result_and_refuses_existing_history(tmp_path):
    night(tmp_path / "tse")
    stats = replay(tmp_path / "tse", tmp_path / "replay", baseline(0.5))
    assert (stats.rodadas, stats.ignoradas) == (5, 1)
    assert stats.projecoes == 4  # a rodada zerada de 17:00 não tem votos válidos
    try:
        replay(tmp_path / "tse", tmp_path / "replay", baseline(0.5))
    except ValueError as exc:
        assert "já tem histórico" in str(exc)
    else:
        raise AssertionError("replay deveria recusar histórico existente")


@pytest.mark.parametrize("destino", [
    "tse", "./tse", "../data/tse", "t1", "sim", "baseline", "templates", "backup-2026-10-29",
    "tse/raw", "", "..", "sub/dir", "tse-secoes",  # a última é a própria origem
])
def test_destinations_that_could_destroy_real_data_are_refused(tmp_path, destino):
    (tmp_path / "tse").mkdir()
    (tmp_path / "tse-secoes").mkdir()
    with pytest.raises(ValueError):
        resolve_paths(tmp_path, "tse-secoes", destino)


def test_origin_and_destination_may_not_nest_even_through_symlinks(tmp_path):
    (tmp_path / "tse").mkdir()
    (tmp_path / "atalho").symlink_to(tmp_path / "tse")
    with pytest.raises(ValueError):
        resolve_paths(tmp_path, "tse", "atalho")
    with pytest.raises(ValueError):
        resolve_paths(tmp_path, "origem/..", "replay")
    assert resolve_paths(tmp_path, "tse", "replay-secoes")[1] == (tmp_path / "replay-secoes").resolve()


def test_cli_replay_reset_never_deletes_the_live_history(tmp_path, monkeypatch):
    night(tmp_path / "tse")
    monkeypatch.setattr(cli, "DATA_DIR", tmp_path)
    for destino in ("tse", "./tse", "backup-x"):
        result = CliRunner().invoke(cli.app, ["replay", "--from", "tse", "--to", destino, "--reset"])
        assert result.exit_code != 0
    assert (tmp_path / "tse" / "apuracao.db").exists()
    assert (tmp_path / "tse" / "raw").exists()
