"""Gráficos no terminal: séries com plotext (painel e ``apuracao grafico``) e barras/réguas em Rich.

As funções ``draw_*`` recebem o objeto de desenho do plotext (o módulo ``plotext`` ou o
``plt`` de um ``PlotextPlot`` do Textual) e só desenham; o eixo X é sempre % de seções apuradas.
"""

from __future__ import annotations

from rich.console import Group
from rich.table import Table
from rich.text import Text

from .analysis import _remaining_shares, chance_above_half, finalists
from .tracker import State

PLT_COLORS = ("red", "blue")  # mesma ordem das cores do painel (1º e 2º na ordem oficial)
RICH_COLORS = ("bright_red", "dodger_blue1")


def _pair(state: State) -> tuple[str, str] | None:
    return finalists(state.br) if state.br else None


def _names(state: State) -> dict[str, str]:
    return {c.numero: c.nome.split()[0].capitalize() for c in state.br.candidates} if state.br else {}


def _empty(plt, title: str) -> None:
    plt.title(f"{title} — aguardando votos")
    plt.xlim(0, 100)


def _base(plt, title: str) -> None:
    plt.clear_figure()
    plt.title(title)
    plt.xlim(0, 100)
    plt.xticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    plt.xlabel("% seções apuradas")


def _ylim(plt, values: list[float], center: float = 50.0, pad: float = 0.8, min_span: float = 4.0) -> None:
    lo, hi = min([*values, center]), max([*values, center])
    span = max(min_span, hi - lo + 2 * pad)
    mid = (hi + lo) / 2
    plt.ylim(mid - span / 2, mid + span / 2)


def projection_series(state: State) -> dict[str, list[float]]:
    """Por linha de projeção: x, % projetado de A/B, faixa de A, necessário, ritmo projetado, chance."""
    pair = _pair(state)
    out = {k: [] for k in ("x", "a", "b", "a_min", "a_max", "chance", "x_need", "need", "proj_rest", "need_breaks")}
    if pair is None:
        return out
    a, b = pair
    official_by_time = {row["totalizado_em"]: row for row in state.timeline_official}
    closed_at = next((row["totalizado_em"] for row in state.timeline_official
                      if row.get("andamento") == "f" or _official_winner(row) is not None), None)
    for row in state.timeline_projections:
        ca, cb = row["cand"].get(a), row["cand"].get(b)
        if not ca or not cb:
            continue
        x = row["pct_secoes"]
        out["x"].append(x)
        out["a"].append(ca["pct"])
        out["b"].append(cb["pct"])
        out["a_min"].append(ca["pct_min"])
        out["a_max"].append(ca["pct_max"])
        vp, va = row["validos_projetados"], row["validos_atuais"]
        official = official_by_time.get(row["totalizado_em"])
        winner = _official_winner(official) if official else None
        frac = (official["restante"] / official["eleitorado"]
                if official and official.get("eleitorado") else 1 - x / 100)
        if official and official.get("andamento") == "f" and winner is None:
            out["chance"].append(50.0)
        else:
            out["chance"].append(
                (100.0 if winner == a else 0.0) if winner else
                chance_above_half(ca["pct"], ca["pct_min"], ca["pct_max"], frac)
            )
        closed = closed_at is not None and row["totalizado_em"] >= closed_at
        need = projected = None
        if not closed and vp is not None and va is not None and ca["votos_atuais"] is not None and ca["votos"] is not None:
            need, projected = _remaining_shares(vp, va, ca["votos"], ca["votos_atuais"])
            if need is not None and projected is not None:
                out["x_need"].append(x)
                out["need"].append(need)
                out["proj_rest"].append(projected)
        if not closed and need is None and out["x_need"]:
            boundary = len(out["x_need"])
            if not out["need_breaks"] or out["need_breaks"][-1] != boundary:
                out["need_breaks"].append(boundary)
    return out


def _official_winner(row: dict) -> str | None:
    votes = row["votos"]
    ordered = sorted(votes, key=votes.get, reverse=True)
    if len(ordered) != 2 or not row["validos"] or votes[ordered[0]] == votes[ordered[1]]:
        return None
    if row.get("andamento") == "f" or votes[ordered[0]] - votes[ordered[1]] > row["restante"]:
        return ordered[0]
    return None


def draw_timeline(plt, state: State) -> None:
    pair = _pair(state)
    _base(plt, "Timeline · linha = placar oficial · ◆ = projeção do resultado final · ── 50%")
    if pair is None or not state.timeline_official:
        _empty(plt, "Timeline")
        return
    names = _names(state)
    ox = [r["pct_secoes"] for r in state.timeline_official]
    values: list[float] = []
    for numero, color in zip(pair, PLT_COLORS):
        oy = [r["pct"].get(numero, 0.0) for r in state.timeline_official]
        values += oy
        plt.plot(ox, oy, color=color, marker="braille", label=names[numero])
    s = projection_series(state)
    if s["x"]:
        idx, last_x = [], -100.0
        for i, x in enumerate(s["x"]):  # um marcador a cada ~4 p.p. apurados, sempre o último
            if x - last_x >= 4.0 or i == len(s["x"]) - 1:
                idx.append(i)
                last_x = x
        for key, color in (("a", PLT_COLORS[0]), ("b", PLT_COLORS[1])):
            plt.scatter([s["x"][i] for i in idx], [s[key][i] for i in idx], color=color, marker="◆")
        values += s["a"] + s["b"]
    plt.hline(50, "white")
    _ylim(plt, values)


def draw_need(plt, state: State) -> None:
    pair = _pair(state)
    name = _names(state).get(pair[0], "") if pair else ""
    title = (f"{name} no restante · encerrado (oficial)"
             if state.thermometer and state.thermometer.definido else
             f"{name} no restante: precisa × projeção")
    _base(plt, title)
    s = projection_series(state)
    if not s["x_need"]:
        if state.thermometer and state.thermometer.definido:
            plt.title("Termômetro encerrado (definição oficial)")
        else:
            _empty(plt, "Termômetro")
        plt.ylim(0, 100)
        return
    need = [max(0.0, min(100.0, v)) for v in s["need"]]  # fora de 0–100 só no cenário estimado
    boundaries = [0, *s["need_breaks"], len(s["x_need"])]
    first = True
    for start, end in zip(boundaries, boundaries[1:]):
        if start == end:
            continue
        # "precisa" por último: se as curvas coincidirem, ela fica visível por cima.
        plt.plot(s["x_need"][start:end], s["proj_rest"][start:end], color=PLT_COLORS[0], marker="braille",
                 label="projeção do restante" if first else None)
        plt.plot(s["x_need"][start:end], need[start:end], color="yellow", marker="braille",
                 label="precisa" if first else None)
        first = False
    plt.hline(50, "white")
    # Escala ajustada aos dados (sempre com 50%), limitada a 0–100: com escala fixa 0–100,
    # cada linha do terminal vale ~10 p.p. e as curvas perto de 50% se sobrepõem.
    values = need + s["proj_rest"]
    lo, hi = min([*values, 50.0]), max([*values, 50.0])
    span = max(8.0, hi - lo + 2.0)
    mid = min(max((lo + hi) / 2, span / 2), 100 - span / 2)
    plt.ylim(max(0.0, mid - span / 2), min(100.0, mid + span / 2))


def draw_chance(plt, state: State) -> None:
    pair = _pair(state)
    name = _names(state).get(pair[0], "") if pair else ""
    _base(plt, f"Chance de {name} (palpite do modelo)")
    s = projection_series(state)
    if not s["x"]:
        _empty(plt, "Chance")
        return
    plt.plot(s["x"], s["chance"], color=PLT_COLORS[0], marker="braille")
    plt.hline(50, "white")
    plt.ylim(0, 100)
    plt.yticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])


def draw_margin(plt, state: State) -> None:
    _base(plt, "Vantagem × eleitores restantes (mi, log)")
    rows = state.timeline_official
    if not rows:
        _empty(plt, "Vantagem")
        return
    x = [r["pct_secoes"] for r in rows]
    lead = [max(abs(_diff(r)), 1) / 1e6 for r in rows]
    rest = [max(r["restante"], 1) / 1e6 for r in rows]
    plt.plot(x, rest, color="gray", marker="braille", label="eleitores não apurados")
    plt.plot(x, lead, color="green", marker="braille", label="vantagem do líder")
    plt.yscale("log")


def _diff(row: dict) -> int:
    v = sorted(row["votos"].values(), reverse=True)
    return v[0] - v[1] if len(v) > 1 else 0


# ---- Rich -----------------------------------------------------------------------


def swing_bars(state: State, width: int = 18) -> Group:
    """Barras divergentes do swing por UF (p.p. para o 1º candidato vs 1º turno)."""
    pair = _pair(state)
    rows = [u for u in state.uf_analysis if u.swing_pp is not None]
    name = _names(state).get(pair[0], "?") if pair else "?"
    title = Text(f"Swing por UF vs 1º turno (+ = melhor para {name}) · ✔ finalizada", style="bold")
    if not rows:
        return Group(title, Text("Sem votos ainda.", style="grey62"))
    scale = max(1.0, max(abs(u.swing_pp) for u in rows))
    table = Table.grid(padding=(0, 1))
    for col in ("uf", "neg", "pos", "val", "st"):
        table.add_column(justify="right" if col in ("uf", "neg", "val") else "left")
    for u in sorted(rows, key=lambda u: -u.swing_pp):
        n = round(width * abs(u.swing_pp) / scale)
        bar = "█" * n
        neg = Text(bar if u.swing_pp < 0 else "", style=RICH_COLORS[1])
        pos = Text(bar if u.swing_pp >= 0 else "", style=RICH_COLORS[0])
        table.add_row(u.abr.upper(), Text(" " * (width - len(neg.plain))) + neg, pos,
                      f"{u.swing_pp:+.1f}".replace(".", ","), "✔" if u.andamento == "f" else "")
    finals = [u for u in rows if u.andamento == "f"]
    lines = [title, table]
    if finals:
        better = sum(1 for u in finals if u.swing_pp > 0)
        lines.append(Text(f"Finalizadas: {name} melhor que no 1º turno em {better} de {len(finals)}.",
                          style="italic"))
    return Group(*lines)


def polls_ruler(state: State, width: int = 60, lo: float = 45.0, hi: float = 55.0) -> Group:
    """Régua do % do 1º candidato: cada pesquisa como inicial, projeção (▲) e resultado final (★)."""
    pair = _pair(state)
    if pair is None or not state.polls:
        return Group(Text("Régua: sem pesquisas ou sem dados.", style="grey62"))
    name = _names(state)[pair[0]]

    def pos(v: float) -> int:
        return max(0, min(width - 1, round((v - lo) / (hi - lo) * (width - 1))))

    national = [c for c in state.polls if c.poll.abr == "br"]
    lines = [Text(f"Pesquisas nacionais na régua ({name}, % válidos)", style="bold")]
    axis = ["─"] * width
    axis[pos(50)] = "┼"
    lines.append(Text(f"{lo:>4.0f}% " + "".join(axis) + f" {hi:.0f}%"))
    for c in sorted(national, key=lambda c: c.pesquisa_a):
        row = [" "] * width
        row[pos(c.pesquisa_a)] = "●"
        text = Text("      " + "".join(row))
        text.stylize(RICH_COLORS[0], 6 + pos(c.pesquisa_a), 7 + pos(c.pesquisa_a))
        text.append(f" {c.poll.instituto} {c.pesquisa_a:.1f}".replace(".", ","), style="grey70")
        lines.append(text)
    marks = [" "] * width
    ref = national[0] if national else None
    legend = []
    if ref and ref.projecao_a is not None:
        marks[pos(ref.projecao_a)] = "▲"
        legend.append(f"▲ projeção {ref.projecao_a:.1f}%".replace(".", ","))
    if ref and ref.final and ref.oficial_a is not None:
        marks[pos(ref.oficial_a)] = "★"
        legend.append(f"★ resultado final {ref.oficial_a:.2f}%".replace(".", ","))
    lines.append(Text("      " + "".join(marks), style="bold yellow"))
    lines.append(Text("      " + "  ".join(legend) + "   │ = 50%", style="yellow"))
    return Group(*lines)


def polls_accuracy(state: State) -> Text | None:
    """Resumo de acerto das pesquisas nacionais (definitivo só com resultado final)."""
    national = [c for c in state.polls if c.poll.abr == "br" and c.diferenca_pp is not None]
    if not national:
        return None
    pair = _pair(state)
    name = _names(state)[pair[0]]
    final = national[0].final
    mae = sum(abs(c.diferenca_pp) for c in national) / len(national)
    bias = sum(c.diferenca_pp for c in national) / len(national)
    best = min(national, key=lambda c: abs(c.diferenca_pp))
    inside = sum(1 for c in national if c.dentro_margem)
    direction = "superestimaram" if bias > 0 else "subestimaram"
    head = "RESULTADO FINAL" if final else "Parcial (contra a projeção)"
    def dec(v: float, sign: bool = False) -> str:
        return (f"{v:+.1f}" if sign else f"{v:.1f}").replace(".", ",")

    return Text(f"{head}: erro médio {dec(mae)} p.p. · em média {direction} {name} em {dec(abs(bias))} p.p. · "
                f"mais perto: {best.poll.instituto} ({dec(best.diferenca_pp, True)}) · "
                f"{inside}/{len(national)} dentro da margem",
                style="bold green" if final else "bold yellow")

