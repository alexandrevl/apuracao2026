"""Painel em tela cheia (Textual) com abas. A consulta roda numa thread; a tela só redesenha.

Teclas: 1 Painel · 2/g Gráficos · 3/p Pesquisas · 4/u UFs · o ordem das UFs · r atualizar · q sair.
Cabeçalho e placar ficam fixos acima das abas.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable

from rich.console import Group
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Static, TabbedContent, TabPane
from textual.worker import get_current_worker
from textual_plotext import PlotextPlot

from . import charts, render
from .tracker import State, Tracker, next_delay
from .regions import REGION_SORTS

SORTS = ("eleitorado", "apurado", "swing", "alfabetica", "regiao")


class Chart(PlotextPlot):
    """Redesenha antes de cada render: plotext transforma as séries in-place no build."""

    def __init__(self, draw: Callable, **kwargs):
        super().__init__(**kwargs)
        self._draw = draw
        self._chart_state: State | None = None

    def show(self, state: State) -> None:
        self._chart_state = state
        self.refresh()

    def render(self):
        # build() aplica log10 aos dados in-place. Reutilizar o desenho num repaint
        # (notificação ou resize) aplicaria log10 duas vezes sobre as mesmas séries.
        if self._chart_state is not None:
            self._draw(self.plt, self._chart_state)
        return super().render()


class ApuracaoApp(App):
    TITLE = "Apuração 2026"
    CSS = """
    Screen { layout: vertical; }
    #header { height: auto; }
    #score { height: auto; }
    TabbedContent { height: 1fr; }
    TabPane { padding: 0; }
    #painel-top { height: auto; }
    #timeline-painel { height: 1fr; min-height: 12; }
    #events-scroll { height: 9; overflow-y: scroll; }
    #events { height: auto; }
    #proj { width: 1fr; }
    #thermo { width: 1fr; }
    #graficos { grid-size: 3 2; grid-columns: 1fr 1fr 1fr; grid-rows: 3fr 2fr; }
    #timeline { column-span: 2; }
    #swing-box { border: round $secondary; }
    Chart { border: round $secondary; }
    """
    BINDINGS = [
        ("q", "quit", "Sair"),
        ("r", "refresh_now", "Atualizar"),
        ("1", "tab('painel')", "Painel"),
        ("2,g", "tab('tab-graficos')", "Gráficos"),
        ("3,p", "tab('pesquisas')", "Pesquisas"),
        ("4,u", "tab('ufs')", "UFs"),
        ("5", "tab('regioes')", "Regiões"),
        ("o", "cycle_sort", "Ordenar"),
    ]

    def __init__(self, tracker: Tracker, interval: float):
        super().__init__()
        self.tracker = tracker
        self.interval = interval
        self.sort = SORTS[0]
        self.region_sort = REGION_SORTS[0]
        self._wake = threading.Event()
        self._state: State | None = None
        self._preferences_path = tracker.store.root.parent / "preferences.json"
        try:
            preferences = json.loads(self._preferences_path.read_text())
        except (FileNotFoundError, ValueError):
            preferences = {}
        saved_theme = preferences.get("theme") if isinstance(preferences, dict) else None
        self.theme = saved_theme if saved_theme in self.available_themes else "dracula"

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        yield Static(id="score")
        with TabbedContent(initial="painel"):
            with TabPane("1 Painel", id="painel"):
                with Vertical():
                    with Horizontal(id="painel-top"):
                        yield Static(id="proj")
                        yield Static(id="thermo")
                    yield Chart(charts.draw_timeline, id="timeline-painel")
                    with VerticalScroll(id="events-scroll"):
                        yield Static(id="events")
            with TabPane("2 Gráficos", id="tab-graficos"):
                with Grid(id="graficos"):
                    yield Chart(charts.draw_timeline, id="timeline")
                    with VerticalScroll(id="swing-box"):
                        yield Static(id="swing")
                    yield Chart(charts.draw_need, id="need")
                    yield Chart(charts.draw_chance, id="chance")
                    yield Chart(charts.draw_margin, id="margin")
            with TabPane("3 Pesquisas", id="pesquisas"):
                with VerticalScroll():
                    yield Static(id="polls")
            with TabPane("4 UFs", id="ufs"):
                with VerticalScroll():
                    yield Static(id="uftable")
            with TabPane("5 Regiões", id="regioes"):
                with VerticalScroll():
                    yield Static(id="regiontable")
        yield Footer()

    def on_mount(self) -> None:
        self.theme_changed_signal.subscribe(self, self._save_theme)
        self.run_worker(self._poll_forever, thread=True, exclusive=True)

    def _save_theme(self, theme) -> None:
        """Persiste imediatamente; preferência compartilhada entre todas as fontes."""
        path = self._preferences_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"theme": theme.name}) + "\n")
            temporary.replace(path)
        except OSError as exc:
            self.notify(f"Não foi possível salvar o tema: {exc}", severity="error")

    def _poll_forever(self) -> None:
        worker = get_current_worker()
        errors = 0
        while not worker.is_cancelled:
            state = self.tracker.poll()
            errors = errors + 1 if state.error else 0
            self.call_from_thread(self._show, state)
            self._wake.wait(next_delay(state, self.interval, errors))
            self._wake.clear()

    def _show(self, state: State) -> None:
        self._state = state
        self.query_one("#header", Static).update(render.header(state))
        self.query_one("#score", Static).update(render.scoreboard(state))
        self.query_one("#proj", Static).update(render.projection_panel(state))
        self.query_one("#thermo", Static).update(render.thermometer_panel(state))
        events_scroll = self.query_one("#events-scroll", VerticalScroll)
        follow_latest = events_scroll.is_vertical_scroll_end
        self.query_one("#events", Static).update(render.events_panel(state, limit=0))
        if follow_latest:
            self.call_after_refresh(events_scroll.scroll_end, animate=False, immediate=True)
        for chart in self.query(Chart):
            chart.show(state)
        self.query_one("#swing", Static).update(charts.swing_bars(state))
        parts = [charts.polls_ruler(state)]
        if (accuracy := charts.polls_accuracy(state)) is not None:
            parts.append(accuracy)
        parts.append(render.polls_panel(state))
        self.query_one("#polls", Static).update(Group(*parts))
        self._show_ufs()
        self.query_one("#regiontable", Static).update(render.regions_panel(state, self.region_sort))
        for event in state.new_events:
            self.notify(event.message, title="Apuração", timeout=12,
                        severity="warning" if event.type in ("virada", "sem_atualizacao") else "information")


    def action_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

    def _show_ufs(self) -> None:
        title = Text(f"Ordem: {self.sort} (tecla o) · swing em p.p. vs 1º turno · Comp. = só seções já apuradas",
                     style="bold")
        self.query_one("#uftable", Static).update(Group(title, render.uf_table(self._state, self.sort)))

    def action_cycle_sort(self) -> None:
        if self.query_one(TabbedContent).active == "regioes":
            self.region_sort = REGION_SORTS[(REGION_SORTS.index(self.region_sort) + 1) % len(REGION_SORTS)]
            if self._state:
                self.query_one("#regiontable", Static).update(
                    render.regions_panel(self._state, self.region_sort)
                )
        else:
            self.sort = SORTS[(SORTS.index(self.sort) + 1) % len(SORTS)]
            if self._state:
                self._show_ufs()
