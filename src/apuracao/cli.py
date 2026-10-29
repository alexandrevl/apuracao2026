"""Comando ``apuracao``: watch (TUI ou simples), snapshot único, UFs, histórico, eventos, base."""

from __future__ import annotations

import json
import math
import os
import shutil
import sqlite3
import sys
import time
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from . import render
from .baseline import load_baseline
from .polls import BUNDLED_POLLS, PollBook
from .sources import ELEICAO_T1, ELEICAO_T2, UFS, CachedSource, SourceError, TseSource
from .store import Store
from .tracker import Tracker, next_delay

def data_directory() -> Path:
    """APURACAO_DATA > checkout editável > diretório de dados do usuário."""
    if configured := os.environ.get("APURACAO_DATA"):
        return Path(configured).expanduser()
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / "pyproject.toml").is_file() and (checkout / "src" / "apuracao").is_dir():
        return checkout / "data"
    xdg = os.environ.get("XDG_DATA_HOME")
    home = Path(xdg).expanduser() if xdg and Path(xdg).expanduser().is_absolute() else Path.home() / ".local" / "share"
    return home / "apuracao"


DATA_DIR = data_directory()

app = typer.Typer(no_args_is_help=True, add_completion=False, rich_markup_mode=None,
                  help="Apuração do 2º turno de 2026 (Presidente) direto dos arquivos do TSE.")
console = Console()
err = Console(stderr=True)


def _errors(command):
    @wraps(command)
    def wrapped(*args, **kwargs):
        try:
            return command(*args, **kwargs)
        except (SourceError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
            err.print(f"Erro: {exc}", markup=False)
            err.print(f"Diretório de dados: {DATA_DIR}", markup=False)
            raise typer.Exit(2) from exc
    return wrapped


class SourceName(str, Enum):
    tse = "tse"
    sim = "sim"
    t1 = "t1"
    replay = "replay"


class Sort(str, Enum):
    eleitorado = "eleitorado"
    apurado = "apurado"
    swing = "swing"
    alfabetica = "alfabetica"
    regiao = "regiao"


SourceOpt = Annotated[SourceName, typer.Option("--source", "-s", help="tse (ao vivo), sim (simulação), t1 (1º turno real), replay (noite refeita; só leitura)")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Só JSON em stdout (para agentes)")]


def _tracker(source: SourceName, *, notify: bool = False, fresh_sim: bool = False, speed: float = 20.0,
             seed: int | None = None, offset: float = 0.0, cached: bool = False) -> Tracker:
    if source is SourceName.replay and not cached:
        raise typer.BadParameter("replay só se acompanha com `watch -s replay`; para consultar o histórico use --cached")
    baseline = load_baseline(DATA_DIR, download=not cached)
    root = DATA_DIR / source.value
    if cached:
        src = CachedSource(source.value, ELEICAO_T1 if source is SourceName.t1 else ELEICAO_T2)
    elif source is SourceName.sim:
        from .sim import SimSource

        if any((root / "raw").glob("*/*.json")) and not fresh_sim:
            raise typer.BadParameter("Já existe histórico simulado; consulte com --cached ou use --reset-sim para apagá-lo")
        src = SimSource(DATA_DIR, baseline, speed=speed, seed=seed, start_offset_min=offset)
        if fresh_sim and root.exists():
            shutil.rmtree(root)
    elif source is SourceName.t1:
        src = TseSource(ELEICAO_T1, name="t1")
    else:
        src = TseSource(ELEICAO_T2, name="tse")
    store = None
    try:
        store = Store(root)
        if baseline is not None:
            store.save_baseline(baseline)
        tracker = Tracker(src, store, baseline, notify_events=notify,
                          polls=PollBook(DATA_DIR / "pesquisas.json", fallback=BUNDLED_POLLS))
        if not cached:
            tracker.load_cached()
        return tracker
    except Exception:
        src.close()
        if store is not None:
            store.close()
        raise


def _replay_tracker(*, notify: bool, reset: bool, speed: float, offset: float) -> Tracker:
    """`watch -s replay`: refaz a noite gravada em `tse` no ritmo dela (speed 1) ou acelerada, em `replay`."""
    from .replay import ReplayTracker

    baseline = load_baseline(DATA_DIR, download=False)
    if baseline is None:
        raise typer.BadParameter("base do 1º turno ausente; execute apuracao baseline antes")
    root = DATA_DIR / SourceName.replay.value
    if any((root / "raw").glob("*/*.json")) and not reset:
        raise typer.BadParameter("Já existe um replay; consulte com --cached ou use --reset-sim para refazê-lo")
    if reset and root.exists():
        shutil.rmtree(root)
    store = Store(root)
    try:
        store.save_baseline(baseline)
        return ReplayTracker(DATA_DIR / SourceName.tse.value, store, baseline, speed=speed, offset=offset,
                             notify_events=notify, polls=PollBook(DATA_DIR / "pesquisas.json", fallback=BUNDLED_POLLS))
    except Exception:
        store.close()
        raise



def _since(store: Store, since: str | None) -> str | None:
    """Aceita 'HH:MM' (dia da última totalização gravada) ou ISO completo."""
    if since and len(since) <= 5:
        day = store.latest_date() or time.strftime("%Y-%m-%d")
        return f"{day}T{since}"
    return since


def _print_json(data) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


@app.command()
@_errors
def replay(
    origem: Annotated[str, typer.Option("--from", help="Pasta de dados a reprocessar (padrão: tse)")] = "tse",
    destino: Annotated[str, typer.Option("--to", help="Pasta de dados do resultado (padrão: replay)")] = "replay",
    reset: Annotated[bool, typer.Option("--reset", help="Apaga o destino anterior antes de refazer")] = False,
) -> None:
    """Refaz a noite gravada com o modelo atual (sem rede); `-s replay --cached` consulta o destino padrão."""
    from .replay import replay as run_replay
    from .replay import resolve_paths

    try:
        source_root, target = resolve_paths(DATA_DIR, origem, destino)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not (source_root / "apuracao.db").exists():
        raise typer.BadParameter(f"sem histórico em {source_root}")
    baseline = load_baseline(DATA_DIR, download=False)
    if baseline is None:
        raise typer.BadParameter("base do 1º turno ausente; execute apuracao baseline antes")
    if reset and target.exists():
        shutil.rmtree(target)
    stats = run_replay(source_root, target, baseline)
    console.print(f"{stats.rodadas} rodadas, {stats.versoes} versões ({stats.ignoradas} descartadas por retroceder), "
                  f"{stats.projecoes} projeções, {stats.eventos} eventos gravados em {target}")


@app.command()
@_errors
def watch(
    source: SourceOpt = SourceName.tse,
    plain: Annotated[bool, typer.Option("--plain", help="Saída simples (Rich) em vez do painel")] = False,
    interval: Annotated[float, typer.Option(help="Segundos entre consultas ao acompanhamento")] = 5.0,
    notify: Annotated[bool, typer.Option(help="Notificações do macOS")] = True,
    sort: Annotated[Sort, typer.Option(help="Ordem das UFs no modo simples")] = Sort.eleitorado,
    speed: Annotated[float, typer.Option(help="sim: velocidade do relógio simulado")] = 20.0,
    seed: Annotated[int | None, typer.Option(help="sim: semente (repete a mesma noite)")] = None,
    offset: Annotated[float, typer.Option(help="sim: começa N minutos após 17:00")] = 0.0,
    reset_sim: Annotated[bool, typer.Option("--reset-sim", help="Apaga o histórico simulado anterior antes de iniciar outra noite")] = False,
) -> None:
    """Acompanha ao vivo. Só baixa resultados quando o TSE publica algo novo."""
    if interval < 1:
        raise typer.BadParameter("intervalo mínimo de 1 s")
    if not math.isfinite(interval):
        raise typer.BadParameter("intervalo deve ser finito")
    if not math.isfinite(speed) or speed <= 0 or not math.isfinite(offset):
        raise typer.BadParameter("speed deve ser positivo e offset deve ser finito")
    if source is SourceName.replay:
        tracker = _replay_tracker(notify=notify, reset=reset_sim, speed=speed, offset=offset)
    else:
        tracker = _tracker(source, notify=notify, fresh_sim=reset_sim, speed=speed, seed=seed, offset=offset)
    try:
        if plain:
            _watch_plain(tracker, interval, sort.value)
        else:
            from .tui import ApuracaoApp

            ApuracaoApp(tracker, interval).run()
    except KeyboardInterrupt:
        pass
    finally:
        tracker.source.close()
        tracker.store.close()


def _watch_plain(tracker: Tracker, interval: float, sort: str) -> None:
    errors = 0
    while True:
        state = tracker.poll()
        errors = errors + 1 if state.error else 0
        if console.is_terminal:
            console.clear()
            console.print(render.full_view(state, sort))
        else:
            # Saída redirecionada (log/agente): uma linha inteira por mudança + eventos, com flush.
            stamp = f"{state.last_check:%H:%M:%S}"
            if state.br is not None and state.last_change == state.last_check:
                b = state.br
                cands = " | ".join(f"{c.numero} {c.nome} {c.pct:.2f}% {c.votos}" for c in b.candidates)
                proj = ""
                if state.projection:
                    proj = " | proj " + " ".join(f"{c.numero}={c.pct:.1f}%" for c in state.projection.candidates)
                print(f"{stamp} TSE {render.hm(b.totalizado_em)} {b.counts.pct_secoes:.2f}% | {cands}{proj}",
                      flush=True)
            for e in state.new_events:
                print(f"{stamp} EVENTO {e.type}: {e.message}", flush=True)
            if state.error:
                print(f"{stamp} ERRO {state.error}", flush=True)
        time.sleep(next_delay(state, interval, errors))


@app.command()
@_errors
def snapshot(
    source: SourceOpt = SourceName.tse,
    as_json: JsonOpt = False,
    uf: Annotated[str | None, typer.Option(help="Só uma UF (ac..to, zz = exterior)")] = None,
    cached: Annotated[bool, typer.Option(help="Não consulta o TSE; usa o histórico local")] = False,
) -> None:
    """Consulta única (sem polling): Brasil + todas as UFs com apuração, análise completa."""
    if uf is not None:
        uf = uf.lower()
        if uf not in UFS:
            raise typer.BadParameter(f"UF inválida: {uf}")
    if source is SourceName.sim and not cached:
        raise typer.BadParameter("a simulação só existe dentro de um watch; use --cached")
    tracker = _tracker(source, cached=cached)
    try:
        state = tracker.load_cached() if cached else tracker.poll(ufs="all")
    finally:
        tracker.source.close()
        tracker.store.close()
    if as_json:
        _print_json(render.to_json(state, uf))
    elif uf:
        console.print(render.header(state))
        console.print_json(data=render.to_json(state, uf))
    else:
        console.print(render.full_view(state))
    if state.br is None:
        raise typer.Exit(2)


@app.command()
@_errors
def ufs(
    source: SourceOpt = SourceName.tse,
    as_json: JsonOpt = False,
    cached: Annotated[bool, typer.Option(help="Usa o histórico local")] = False,
    sort: Annotated[Sort, typer.Option()] = Sort.eleitorado,
) -> None:
    """Tabela de todas as UFs (apurado, votos, líder, swing e comparecimento vs 1º turno)."""
    tracker = _tracker(source, cached=cached or source is SourceName.sim)
    try:
        state = tracker.load_cached() if cached or source is SourceName.sim else tracker.poll(ufs="all")
    finally:
        tracker.source.close()
        tracker.store.close()
    if as_json:
        _print_json(render.to_json(state).get("ufs", []))
    else:
        console.print(render.header(state))
        console.print(render.uf_table(state, sort.value))
    if state.br is None:
        raise typer.Exit(2)


@app.command()
@_errors
def history(
    source: SourceOpt = SourceName.tse,
    uf: Annotated[str, typer.Option(help="br ou UF")] = "br",
    since: Annotated[str | None, typer.Option(help="HH:MM ou ISO (hora de totalização do TSE)")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Evolução gravada (uma linha por versão publicada pelo TSE)."""
    if uf.lower() not in ("br", *UFS):
        raise typer.BadParameter(f"UF inválida: {uf}")
    store = Store(DATA_DIR / source.value)
    try:
        rows = store.history(uf.lower(), _since(store, since))
    finally:
        store.close()
    if as_json:
        _print_json(rows)
        return
    if not rows:
        console.print("Nenhum resultado gravado para essa fonte e filtro. Inicie apuracao watch para coletar dados.")
        return
    from rich.table import Table

    table = Table(box=None, header_style="bold")
    for col in ("totalizado", "apurado", "seções", *[c["nome"] for c in (rows[0]["candidatos"] if rows else [])]):
        table.add_column(col, justify="right")
    order = [c["numero"] for c in rows[0]["candidatos"]] if rows else []
    for r in rows:
        by = {c["numero"]: c for c in r["candidatos"]}
        table.add_row((r["totalizado_em"] or "")[11:19], render.pct(r["pct_secoes"]),
                      render.n(r["secoes_totalizadas"]), *[render.pct(by[n]["pct"]) for n in order if n in by])
    console.print(table)


@app.command()
@_errors
def events(
    source: SourceOpt = SourceName.tse,
    since: Annotated[str | None, typer.Option(help="HH:MM ou ISO")] = None,
    limit: Annotated[int | None, typer.Option(min=1)] = None,
    as_json: JsonOpt = False,
) -> None:
    """Eventos detectados (viradas, marcos, UFs finalizadas, definição)."""
    store = Store(DATA_DIR / source.value)
    try:
        rows = store.events(_since(store, since), limit)
    finally:
        store.close()
    if as_json:
        _print_json(rows)
        return
    if not rows:
        console.print("Nenhum evento gravado para essa fonte e filtro.")
        return
    for e in rows:
        console.print(f"{(e['totalizado_em'] or e['captured_at'])[11:19]}  [{e['type']}] {e['message']}",
                      highlight=False)


@app.command()
@_errors
def pesquisas(
    source: SourceOpt = SourceName.tse,
    as_json: JsonOpt = False,
    uf: Annotated[str | None, typer.Option(help="Só uma abrangência (br, sp, …)")] = None,
) -> None:
    """Pesquisas × apuração: Δ contra a projeção (ou o final), dentro/fora da margem. Usa o histórico local."""
    tracker = _tracker(source, cached=True)
    try:
        state = tracker.load_cached()
    finally:
        tracker.source.close()
        tracker.store.close()
    abr = uf.lower() if uf else None
    if as_json:
        _print_json(render.polls_json(state, abr))
        return
    if abr:
        state.polls = [c for c in state.polls if c.poll.abr == abr]
    console.print(render.polls_panel(state))


@app.command()
@_errors
def grafico(
    source: SourceOpt = SourceName.tse,
    width: Annotated[int | None, typer.Option(help="Largura (padrão: terminal)")] = None,
    height: Annotated[int, typer.Option(help="Altura de cada gráfico")] = 18,
) -> None:
    """Gráficos da noite (timeline, termômetro, chance, vantagem, swing, pesquisas). Usa o histórico local."""
    import plotext as plt
    from rich.text import Text

    from . import charts

    tracker = _tracker(source, cached=True)
    try:
        state = tracker.load_cached()
    finally:
        tracker.source.close()
        tracker.store.close()
    if state.br is None:
        console.print("Sem dados no histórico local para essa fonte.")
        raise typer.Exit(2)
    w = width or console.width
    console.print(render.header(state))
    console.print(render.thermometer_panel(state))
    for draw in (charts.draw_timeline, charts.draw_need, charts.draw_chance, charts.draw_margin):
        draw(plt, state)
        plt.plotsize(w, height)
        plt.theme("clear")
        console.print(Text.from_ansi(plt.build()))
    console.print(charts.swing_bars(state))
    console.print(charts.polls_ruler(state))
    if (accuracy := charts.polls_accuracy(state)) is not None:
        console.print(accuracy)


@app.command()
@_errors
def baseline(as_json: JsonOpt = False) -> None:
    """Baixa (uma vez) e mostra o resultado final do 1º turno por UF, base da projeção."""
    base = load_baseline(DATA_DIR)
    data = {
        abr: {"pct": uf.pct, "comparecimento_pct": round(100 * uf.turnout, 3), "votos_validos": uf.votos_validos}
        for abr, uf in {"br": base.br, **base.ufs}.items()
    }
    if as_json:
        _print_json({"nomes": base.nomes, "ufs": data})
        return
    top = sorted(base.br.votos, key=lambda k: -base.br.votos[k])[:2]
    from rich.table import Table

    table = Table(box=None, header_style="bold", title="1º turno (final)")
    table.add_column("UF")
    for num in top:
        table.add_column(base.nomes[num], justify="right")
    table.add_column("Comp.", justify="right")
    for abr, d in data.items():
        table.add_row(abr.upper(), *[render.pct(d["pct"].get(num)) for num in top],
                      render.pct(d["comparecimento_pct"], 1))
    console.print(table)


if __name__ == "__main__":
    app()
