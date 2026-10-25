"""Renderização compartilhada (Rich) pelo painel TUI e pelo modo simples, e saída JSON para agentes."""

from __future__ import annotations

from datetime import datetime

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .analysis import CHANCE_NOTICE, PROJECTION_NOTICE, finalists
from .regions import REGION_ORDER, UF_REGION, sorted_regions
from .tracker import State

COLORS = ("bright_red", "dodger_blue1")  # 1º e 2º na ordem oficial (seq): 13 LULA, 22 FLAVIO
ANDAMENTO = {"n": "não iniciada", "p": "parcial", "f": "finalizada"}


def n(value: int | float) -> str:
    return f"{int(value):,}".replace(",", ".")


def pct(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}".replace(".", ",") + "%"


def hm(value: datetime | None) -> str:
    return value.strftime("%H:%M:%S") if value else "—"


def _colors(state: State) -> dict[str, str]:
    pair = finalists(state.br) if state.br else None
    return dict(zip(pair, COLORS)) if pair else {}


def _bar(value: float, width: int, color: str) -> Text:
    filled = max(0, min(width, round(width * value / 100)))
    return Text("█" * filled, style=color) + Text("░" * (width - filled), style="grey35")


def header(state: State) -> Text:
    t = Text()
    t.append(" APURAÇÃO 2026 · PRESIDENTE · 2º TURNO ", style="bold black on bright_white")
    t.append(f"  fonte={state.source}", style="bold yellow" if state.source != "tse" else "bold green")
    if state.br:
        t.append(f"  TSE totalizou {hm(state.br.totalizado_em)}  gerado {hm(state.br.gerado_em)}")
    t.append(f"  checado {hm(state.last_check)}", style="grey62")
    if state.error:
        t.append(f"\n ⚠ {state.error}", style="bold red")
    return t


def scoreboard(state: State) -> Panel:
    br = state.br
    if br is None:
        return Panel("Aguardando primeira leitura do TSE…", title="Placar")
    colors = _colors(state)
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right")
    table.add_column()
    table.add_column(justify="right")
    table.add_column(justify="right")
    table.add_column()
    for c in br.candidates:
        color = colors.get(c.numero, "white")
        mark = " ✔ ELEITO" if c.eleito else ""
        table.add_row(
            Text(c.numero, style=f"bold {color}"),
            Text(f"{c.nome} ({c.partido}){mark}", style=f"bold {color}"),
            Text(pct(c.pct), style=f"bold {color}"),
            n(c.votos),
            _bar(c.pct, 36, color),
        )
    k = br.counts
    lines = [table, Text()]
    status = Text()
    status.append(f"Seções {pct(k.pct_secoes)} ", style="bold")
    status.append(f"({n(k.secoes_totalizadas)}/{n(k.secoes_total)})  ")
    status.append(f"andamento: {ANDAMENTO.get(br.andamento, br.andamento)}  ")
    status.append(f"comparecimento {pct(k.pct_comparecimento)}  ")
    status.append(f"válidos {n(br.votos_validos)}  brancos {n(br.brancos)}  nulos {n(br.nulos)}")
    lines.append(status)
    if len(br.candidates) >= 2 and br.votos_validos:
        lead, second = br.candidates[0], br.candidates[1]
        if lead.votos == second.votos:
            lines.append(Text("Empate em votos — sem líder isolado.", style="bold yellow"))
        else:
            diff = pct(lead.pct - second.pct).removesuffix("%")
            lines.append(Text(f"Vantagem: {lead.nome} +{n(lead.votos - second.votos)} votos ({diff} p.p.)",
                              style="bold"))
    p = state.projection
    if p and br.votos_validos:
        falta = "  ".join(f"{c.nome}: falta {n(p.falta[c.numero])}" for c in br.candidates)
        meta = Text(f"Meta (50%+1 dos válidos projetados): {n(p.meta)}  →  {falta}")
        if p.meta_simples:
            meta.append(f"   [meta 2022: {n(p.meta_simples)}]", style="grey62")
        lines.append(meta)
    return Panel(Group(*lines), title="Placar oficial (TSE)", border_style="white")


def _chance_style(chance: float) -> str:
    if chance >= 70:
        return "bold green"
    if chance >= 30:
        return "bold yellow"
    return "bold red"


def thermometer_panel(state: State, lo: float = 40.0, hi: float = 60.0, width: int = 40) -> Panel:
    t = state.thermometer
    if t is None:
        return Panel(Text("Sem termômetro (exige projeção).", style="grey62"), title="Termômetro da virada")
    nome = t.nome.split()[0].capitalize()
    rows: list = []
    head = Text()
    head.append(f"{nome}: ", style="bold")
    finalizada = state.br is not None and state.br.andamento == "f"
    if finalizada and t.rotulo == "empate":
        head.append("apuração encerrada — empate em votos", style="bold yellow")
    elif finalizada:
        head.append("apuração encerrada — " + ("venceu" if t.chance_pct == 100 else "não venceu"),
                    style="bold green" if t.chance_pct == 100 else "bold red")
    elif t.definido:
        head.append("matematicamente eleito" if t.chance_pct == 100 else "sem chance matemática",
                    style="bold green" if t.chance_pct == 100 else "bold red")
    else:
        head.append(f"chance estimada {t.chance_pct:.0f}%", style=_chance_style(t.chance_pct))
        head.append(f" — {t.rotulo}", style=_chance_style(t.chance_pct))
    rows.append(head)
    impossible = t.necessario_pct is not None and t.necessario_pct > 100
    meta_reached = t.necessario_pct is not None and t.necessario_pct < 0
    if t.definido:
        rows.append(Text("Termômetro encerrado: definição pelos números oficiais.", style="grey70"))
    elif meta_reached:
        rows.append(Text("Meta estimada já alcançada — não precisa de votos adicionais para superá-la.",
                         style="bold green"))
        rows.append(Text("Isso, sozinho, não é definição matemática.", style="yellow"))
    elif impossible:
        rows.append(Text("Virada inviável neste cenário estimado: precisaria de mais de 100% "
                         "dos votos restantes estimados.", style="yellow"))
    elif t.necessario_pct is not None:
        rows.append(Text(f"Precisa de {pct(t.necessario_pct, 1)} dos ~{n(t.validos_restantes)} votos válidos que faltam"))
    else:
        rows.append(Text("Termômetro indisponível: sem votos ainda ou dados BR/UFs incompatíveis.",
                         style="grey62"))
    if t.projetado_pct is not None and not t.definido and not impossible and not meta_reached:
        gap = t.gap_pp
        verb = "faltam" if gap > 0 else "sobram"
        line = Text(f"Projeção dá {pct(t.projetado_pct, 1)} do restante → {verb} ")
        line.append(f"{pct(abs(gap), 1).removesuffix('%')} p.p.", style="bold red" if gap > 0 else "bold green")
        rows.append(line)
    if t.recente_pct is not None:
        rows.append(Text(f"Últimos {t.recente_janela_min:.0f} min (hora do TSE): fez {pct(t.recente_pct, 1)} "
                         f"dos votos novos", style="grey85"))
    if t.necessario_pct is not None and t.projetado_pct is not None and not t.definido and not impossible and not meta_reached:
        def at(v: float) -> int:
            return max(0, min(width - 1, round((v - lo) / (hi - lo) * (width - 1))))
        offset = len(f"{lo:.0f}% ")
        bar = ["░"] * width
        bar[at(50)] = "│"
        bar[at(t.projetado_pct)] = "●"
        bar[at(t.necessario_pct)] = "▼"
        scale = Text(f"{lo:.0f}% ", style="grey62") + Text("".join(bar)) + Text(f" {hi:.0f}%", style="grey62")
        scale.stylize("yellow", offset + at(t.necessario_pct), offset + at(t.necessario_pct) + 1)
        scale.stylize("bright_red", offset + at(t.projetado_pct), offset + at(t.projetado_pct) + 1)
        rows.append(scale)
        rows.append(Text("▼ precisa  ● projeção do restante  │ 50%", style="grey62"))
    if not t.definido:
        rows.append(Text(CHANCE_NOTICE, style="italic grey50"))
    return Panel(Group(*rows), title="Termômetro da virada", border_style="red")


def projection_panel(state: State) -> Panel:
    p, br = state.projection, state.br
    if br is None or p is None:
        return Panel("Sem projeção (exige 2 finalistas e base do 1º turno).", title="Projeção")
    colors = _colors(state)
    rows: list = [Text(PROJECTION_NOTICE, style="italic yellow")]
    for c in p.candidates:
        color = colors.get(c.numero, "white")
        line = Text()
        line.append(f"{c.nome:<22}", style=f"bold {color}")
        line.append(f"{pct(c.pct, 1):>7}", style=f"bold {color}")
        line.append(f"  faixa {pct(c.pct_min, 1)} – {pct(c.pct_max, 1)}", style="grey70")
        rows.append(line)
    swing = f"{p.swing_pp:+.2f}".replace(".", ",")
    rows.append(Text(f"Swing vs 1º turno ({p.candidates[0].nome}): {swing} p.p."))
    rows.append(Text(f"Válidos projetados: {n(p.validos_projetados)}"))
    if p.definido:
        nome = next(c.nome for c in br.candidates if c.numero == p.vencedor)
        rows.append(Text(f"✔ {nome} MATEMATICAMENTE ELEITO (vantagem {n(p.vantagem)} > "
                         f"{n(p.eleitorado_restante)} eleitores restantes)", style="bold green"))
    else:
        rows.append(Text(f"Vantagem {n(p.vantagem)} vs eleitorado restante {n(p.eleitorado_restante)}",
                         style="grey70"))
    pace = state.pace
    if pace:
        eta = f"ETA 100%: {hm(pace.eta)} ({pace.eta_min:.0f} min)" if pace.eta_min is not None else "ETA: —"
        rows.append(Text(f"Ritmo: {pace.secoes_por_min:,.0f} seções/min (janela {pace.janela_min:.0f} min) · "
                         f"{n(pace.restantes)} restantes · {eta}".replace(",", ".")))
    return Panel(Group(*rows), title="Projeção · ritmo", border_style="yellow")


def uf_table(state: State, sort: str = "eleitorado") -> Table:
    br = state.br
    pair = finalists(br) if br else None
    colors = _colors(state)
    nomes = {c.numero: c.nome.split()[0] for c in br.candidates} if br else {}
    table = Table(expand=True, header_style="bold", pad_edge=False, box=None)
    table.add_column("UF")
    table.add_column("Região", no_wrap=True)
    table.add_column("Apurado", justify="right")
    table.add_column("St")
    if pair:
        for numero in pair:
            table.add_column(f"Votos {nomes[numero]}", justify="right", style=colors[numero], no_wrap=True)
            table.add_column(f"% {nomes[numero]}", justify="right", style=colors[numero], no_wrap=True)
    table.add_column("Líder")
    table.add_column("Swing", justify="right")
    table.add_column("1ºT", justify="right")
    table.add_column("Comp.", justify="right")
    table.add_column("Comp.1ºT", justify="right")

    rows = state.uf_analysis
    if sort == "apurado":
        rows = sorted(rows, key=lambda u: -u.pct_secoes)
    elif sort == "swing":
        rows = sorted(rows, key=lambda u: -(u.swing_pp if u.swing_pp is not None else -999))
    elif sort == "alfabetica":
        rows = sorted(rows, key=lambda u: u.abr)
    elif sort == "regiao":
        rows = sorted(rows, key=lambda u: (REGION_ORDER[UF_REGION[u.abr]], u.abr))
    else:
        size = {abr: r.counts.eleitorado for abr, r in state.ufs.items()}
        if state.progress:
            size |= {abr: p.counts.eleitorado for abr, p in state.progress.ufs.items()}
        rows = sorted(rows, key=lambda u: -size.get(u.abr, 0))
    for u in rows:
        cells = [
            u.abr.upper(),
            UF_REGION[u.abr],
            pct(u.pct_secoes),
            {"n": "·", "p": "…", "f": "✔"}.get(u.andamento, u.andamento),
        ]
        if pair:
            for num in pair:
                cells += [
                    n(u.votos[num]) if num in u.votos else "—",
                    pct(u.pct.get(num)),
                ]
        cells += [
            Text(nomes.get(u.lider, "—"), style=colors.get(u.lider, "")),
            "—" if u.swing_pp is None else f"{u.swing_pp:+.1f}".replace(".", ","),
            pct(u.share_1t, 1),
            pct(u.comparecimento_pct, 1),
            pct(u.comparecimento_1t_pct, 1),
        ]
        table.add_row(*cells)
    return table


def regions_panel(state: State, sort: str = "regiao") -> Group:
    """Placar agregado, peso do que falta e contribuição regional para o saldo nacional."""
    data = state.regions
    pair = finalists(state.br) if state.br else None
    if not pair or not data.get("regioes"):
        return Group(Text("Sem dados regionais: aguardando resultado nacional com dois finalistas."))
    a, b = pair
    names = {c.numero: c.nome.split()[0].capitalize() for c in state.br.candidates}
    colors = _colors(state)
    rows = sorted_regions(data["regioes"], sort)
    consistency = data["consistencia"]
    style = "green" if consistency["status"] == "sincronizado" else "bold yellow"
    warning = Panel(Text(consistency["aviso"], style=style), title="UFs × placar nacional")
    table = Table(expand=True, box=None, pad_edge=False, header_style="bold")
    table.add_column("Região", no_wrap=True)
    for label, color in (("Apurado", None), ("UFs ✔", None), (f"Votos {names[a]}", colors[a]),
                         (f"% {names[a]}", colors[a]), (f"Votos {names[b]}", colors[b]),
                         (f"% {names[b]}", colors[b]), ("Líder", None), ("Swing", None),
                         ("1ºT", None), ("Comp.", None), ("Comp.1ºT", None), ("Restam ~válidos", None)):
        table.add_column(label, justify="right", no_wrap=True, style=color or "")
    for row in rows:
        complete = row["dados_completos"]
        swing = row["swing_pp"]
        table.add_row(
            row["regiao"] + ("" if complete else " *"), pct(row["secoes"]["pct"]),
            f"{row['ufs_finalizadas']}/{len(row['ufs'])}",
            n(row["votos"][a]) if complete else "—", pct(row["pct"][a]),
            n(row["votos"][b]) if complete else "—", pct(row["pct"][b]),
            Text(names.get(row["lider"], "—"), style=colors.get(row["lider"], "")),
            "—" if swing is None else f"{swing:+.1f}".replace(".", ","),
            pct(row["share_1t_candidato_a"], 1), pct(row["eleitorado"]["pct_comparecimento"], 1),
            pct(row["pct_comparecimento_1t"], 1), n(row["validos_restantes_estimados"]),
        )
    title = Text(f"Ordem: {sort} (tecla o nesta aba) · UFs ✔ = finalizadas/total", style="bold")
    estimate_note = Text(
        f"1ºT e swing: {names[a]} entre os finalistas. Apurado = razão das somas das seções; "
        "Comp. = só seções totalizadas. * cobertura incompleta. "
        "Restam ~válidos = estimativa, usando o 1º turno onde ainda faltam arquivos.",
        style="italic grey70",
    )
    remaining = max(rows, key=lambda row: row["validos_restantes_estimados"])
    outlook = Text(
        f"Maior volume estimado por entrar: {remaining['regiao']}. "
        "Volume não determina quem será beneficiado.",
        style="bold yellow",
    ) if remaining["validos_restantes_estimados"] else Text(
        "Estimativa de votos restantes esgotada; confirme o andamento oficial.", style="grey70"
    )
    bars = Table.grid(padding=(0, 1))
    bars.add_column(min_width=12)
    bars.add_column(width=22, justify="right")
    bars.add_column()
    bars.add_column(width=22)
    bars.add_column()
    scale = max((abs(row["saldo_candidato_a_votos"] or 0) for row in rows), default=0) or 1
    for row in rows:
        balance = row["saldo_candidato_a_votos"]
        if balance is None:
            bars.add_row(row["regiao"], "", "│", "", "Sem cobertura completa")
            continue
        width = max(1, round(22 * abs(balance) / scale)) if balance else 0
        left = Text("█" * width if balance < 0 else "", style=colors[b])
        right = Text("█" * width if balance > 0 else "", style=colors[a])
        label = f"{names[a if balance > 0 else b]} +{n(abs(balance))}" if balance else "Empate"
        bars.add_row(row["regiao"], left, "│", right, label)
    contribution = Panel(
        Group(Text(f"← vantagem {names[b]} │ vantagem {names[a]} →", style="bold"), bars,
              Text("Saldos dos arquivos estaduais. Só reconciliam com o nacional quando a soma confere.",
                   style="italic grey70")),
        title="De onde vem a diferença — saldo de votos por região",
    )
    return Group(title, warning, table, estimate_note, Text(), outlook, Text(), contribution)


def events_panel(state: State, limit: int = 12) -> Panel:
    styles = {"virada": "bold red", "virada_projecao": "bold yellow", "definido": "bold green",
              "eleito": "bold green", "uf_finalizada": "cyan", "sem_atualizacao": "red",
              "projecao_definida": "bold yellow", "projecao_retirada": "bold yellow",
              "apuracao_finalizada": "bold green"}
    lines = []
    for e in state.recent_events[-limit:]:
        when = (e.get("totalizado_em") or e["captured_at"])[11:19]
        lines.append(Text(f"{when} ", style="grey62") + Text(e["message"], style=styles.get(e["type"], "")))
    return Panel(Group(*lines) if lines else Text("Nenhum evento ainda.", style="grey62"),
                 title="Eventos", border_style="cyan")


def polls_panel(state: State) -> Panel:
    title = "Pesquisas × apuração (Δ = pesquisa − referência, em p.p. do 1º candidato)"
    if state.polls_error:
        body: Table | Text = Text(f"⚠ {state.polls_error}", style="bold red")
        return Panel(body, title=title, border_style="magenta")
    if not state.polls:
        return Panel(Text("Sem comparação: nenhuma pesquisa na lista ou apuração ainda sem dados.",
                          style="grey62"), title=title, border_style="magenta")
    br = state.br
    pair = finalists(br)
    colors = _colors(state)
    nomes = {c.numero: c.nome.split()[0].capitalize() for c in br.candidates}
    a, b = pair
    table = Table(expand=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Instituto", no_wrap=True, overflow="ellipsis", ratio=3)
    for header_, kwargs in (
        ("Onde", {}), ("Campo", {}),
        (nomes[a], {"justify": "right", "style": colors[a]}),
        (nomes[b], {"justify": "right", "style": colors[b]}),
        ("±", {"justify": "right"}),
        (f"Proj.{nomes[a][0]}", {"justify": "right"}),
        (f"Ofic.{nomes[a][0]}", {"justify": "right"}),
        ("Apur.", {"justify": "right"}),
        ("Δ", {"justify": "right"}),
        ("Ref.", {}), ("Marg.", {"justify": "center"}),
    ):
        table.add_column(header_, no_wrap=True, **kwargs)
    for c in state.polls:
        p = c.poll
        day = p.campo_fim or p.divulgacao  # AAAA-MM-DD
        campo = f"{day[8:10]}/{day[5:7]}" if len(day) >= 10 else "—"
        nome = p.instituto + (" *" if p.validos_calculado else "") + ("" if p.fonte_confirmada else " †")
        diff = "—" if c.diferenca_pp is None else f"{c.diferenca_pp:+.1f}".replace(".", ",")
        margem = {True: Text("✔", style="green"), False: Text("✘", style="red"),
                  None: Text("—", style="grey62")}[c.dentro_margem]
        ref = {"final": Text("FINAL", style="bold green"), "projecao": Text("proj.", style="yellow"),
               "sem_dados": Text("—", style="grey62")}[c.referencia]
        table.add_row(
            nome, p.abr.upper(), campo,
            pct(p.validos.get(a), 1), pct(p.validos.get(b), 1),
            "—" if p.margem_pp is None else f"{p.margem_pp:.1f}".replace(".", ","),
            pct(c.projecao_a, 1), pct(c.oficial_a, 1), pct(c.pct_secoes, 0),
            Text(diff, style="bold"), ref, margem,
        )
    origem = {"pessoal": "lista pessoal (pesquisas.json no diretório de dados)",
              "projeto": "lista do projeto"}.get(state.polls_origem or "", "—")
    note = Text(f"Proj./Ofic.: {nomes[a]} na projeção e no placar parcial · Δ = pesquisa − referência "
                f"(+ = pesquisa deu mais a {nomes[a]}) · Ref.: proj. até a abrangência finalizar, depois FINAL · "
                "Marg.: |Δ| ≤ margem · * válidos calculados dos totais · † fonte secundária, números não "
                f"confirmados na fonte original · menor |Δ| primeiro · Lista: {origem}.",
                style="italic grey62")
    return Panel(Group(table, note), title=title, border_style="magenta")


def full_view(state: State, sort: str = "eleitorado") -> Group:
    return Group(header(state), scoreboard(state), projection_panel(state), thermometer_panel(state),
                 polls_panel(state),
                 Panel(uf_table(state, sort), title="UFs (swing = 1º candidato vs 1º turno, p.p.)"),
                 events_panel(state, limit=8))


# ---- JSON para agentes ---------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def to_json(state: State, uf: str | None = None) -> dict:
    """Contrato estável descrito no AGENTS.md."""
    br = state.br
    out: dict = {
        "fonte": state.source,
        "eleicao": state.eleicao,
        "capturado_em": _iso(state.last_check),
        "erro": state.error,
    }
    if br is None:
        return out | {"disponivel": False}

    def result_block(r) -> dict:
        k = r.counts
        leader = r.candidates[0] if r.votos_validos and len(r.candidates) > 1 else None
        if leader is not None and leader.votos == r.candidates[1].votos:
            leader = None
        return {
            "abrangencia": r.abr,
            "andamento": r.andamento,
            "tse": {"idg": r.idg, "gerado_em": _iso(r.gerado_em), "totalizado_em": _iso(r.totalizado_em),
                    "matematicamente_definido": r.matematicamente_definido or None},
            "secoes": {"total": k.secoes_total, "totalizadas": k.secoes_totalizadas, "pct": k.pct_secoes},
            "eleitorado": {"total": k.eleitorado, "nao_totalizado": k.eleitorado_nao_totalizado,
                           "comparecimento": k.comparecimento, "abstencao": k.abstencao,
                           "pct_comparecimento": round(k.pct_comparecimento, 4)},
            "votos": {"validos": r.votos_validos, "brancos": r.brancos, "nulos": r.nulos, "total": r.total_votos},
            "candidatos": [{"numero": c.numero, "nome": c.nome, "partido": c.partido, "votos": c.votos,
                            "pct": c.pct, "eleito": c.eleito} for c in r.candidates],
            "lider": None if leader is None else {
                "numero": leader.numero, "nome": leader.nome,
                "vantagem_votos": leader.votos - r.candidates[1].votos,
                "vantagem_pp": round(leader.pct - r.candidates[1].pct, 4)},
        }

    if uf:
        r = state.ufs.get(uf)
        ua = next((u for u in state.uf_analysis if u.abr == uf), None)
        block = result_block(r) if r else {"abrangencia": uf, "disponivel": False}
        if ua:
            block["comparacao_1t"] = {"share_1t_candidato_a": ua.share_1t, "swing_pp": ua.swing_pp,
                                      "pct_comparecimento_1t": ua.comparecimento_1t_pct}
        block["pesquisas"] = polls_json(state, uf)
        return out | block

    out |= result_block(br)
    p = state.projection
    out["projecao"] = None if p is None else {
        "aviso": PROJECTION_NOTICE,
        "candidatos": [{"numero": c.numero, "nome": c.nome, "pct": round(c.pct, 3), "min": round(c.pct_min, 3),
                        "max": round(c.pct_max, 3), "votos": c.votos} for c in p.candidates],
        "swing_nacional_pp": round(p.swing_pp, 3),
        "validos_projetados": p.validos_projetados,
        "meta_votos": p.meta,
        "falta_para_meta": p.falta,
        "meta_metodo_2022": p.meta_simples,
    }
    out["matematicamente_definido"] = None if p is None else {
        "definido": p.definido, "vencedor": p.vencedor, "vantagem": p.vantagem,
        "eleitorado_restante": p.eleitorado_restante}
    out["ritmo"] = None if state.pace is None else {
        "secoes_por_min": round(state.pace.secoes_por_min, 1), "janela_min": round(state.pace.janela_min, 1),
        "secoes_restantes": state.pace.restantes,
        "eta_min": None if state.pace.eta_min is None else round(state.pace.eta_min, 1),
        "eta": _iso(state.pace.eta)}
    out["ufs"] = [
        {"uf": u.abr, "andamento": u.andamento, "pct_secoes": u.pct_secoes, "votos": u.votos, "pct": u.pct,
         "lider": u.lider, "share_1t_candidato_a": round(u.share_1t, 3),
         "swing_pp": None if u.swing_pp is None else round(u.swing_pp, 3),
         "pct_comparecimento": None if u.comparecimento_pct is None else round(u.comparecimento_pct, 3),
         "pct_comparecimento_1t": round(u.comparecimento_1t_pct, 3)}
        for u in state.uf_analysis
    ]
    out["eventos_recentes"] = state.recent_events[-20:]
    out["regional"] = state.regions
    out["pesquisas"] = polls_json(state)
    t = state.thermometer
    out["termometro"] = None if t is None else {
        "candidato": t.numero, "nome": t.nome,
        "necessario_pct_restante": None if t.necessario_pct is None else round(t.necessario_pct, 3),
        "projecao_pct_restante": None if t.projetado_pct is None else round(t.projetado_pct, 3),
        "gap_pp": None if t.gap_pp is None else round(t.gap_pp, 3),
        "recente_pct": None if t.recente_pct is None else round(t.recente_pct, 3),
        "recente_janela_min": None if t.recente_janela_min is None else round(t.recente_janela_min, 1),
        "validos_restantes": t.validos_restantes,
        "chance_pct": round(t.chance_pct, 2), "rotulo": t.rotulo, "definido": t.definido,
        "aviso": CHANCE_NOTICE,
    }
    return out


def polls_json(state: State, abr: str | None = None) -> dict:
    return {
        "erro": state.polls_error,
        "lista": state.polls_origem,
        "nota": "diferenca_pp = pesquisa − referência (% válidos do 1º candidato na ordem oficial, 13 LULA). "
                "Referência = projeção até a abrangência finalizar; depois, resultado final oficial.",
        "comparacoes": [
            {"instituto": c.poll.instituto, "abr": c.poll.abr, "divulgacao": c.poll.divulgacao,
             "campo_fim": c.poll.campo_fim, "registro_tse": c.poll.registro_tse, "margem_pp": c.poll.margem_pp,
             "validos": c.poll.validos, "validos_calculado": c.poll.validos_calculado, "fonte": c.poll.fonte,
             "fonte_confirmada": c.poll.fonte_confirmada,
             "projecao_a": None if c.projecao_a is None else round(c.projecao_a, 3),
             "oficial_a": c.oficial_a, "pct_secoes": c.pct_secoes, "referencia": c.referencia,
             "diferenca_pp": None if c.diferenca_pp is None else round(c.diferenca_pp, 3),
             "dentro_margem": c.dentro_margem}
            for c in state.polls if abr is None or c.poll.abr == abr
        ],
    }
