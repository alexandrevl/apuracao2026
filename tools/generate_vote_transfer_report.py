from __future__ import annotations

import json
import math
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from apuracao.parse import parse_result
from apuracao.sources import BASE_URL, ELEICAO_T1, ELEICAO_T2, UFS, result_path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "transferencia_votos_2026.html"
AREAS = ("br", *UFS)
AREA_NAMES = {
    "ac": "Acre", "al": "Alagoas", "am": "Amazonas", "ap": "Amapá",
    "ba": "Bahia", "ce": "Ceará", "df": "Distrito Federal",
    "es": "Espírito Santo", "go": "Goiás", "ma": "Maranhão",
    "mg": "Minas Gerais", "ms": "Mato Grosso do Sul", "mt": "Mato Grosso",
    "pa": "Pará", "pb": "Paraíba", "pe": "Pernambuco", "pi": "Piauí",
    "pr": "Paraná", "rj": "Rio de Janeiro", "rn": "Rio Grande do Norte",
    "ro": "Rondônia", "rr": "Roraima", "rs": "Rio Grande do Sul",
    "sc": "Santa Catarina", "se": "Sergipe", "sp": "São Paulo",
    "to": "Tocantins", "zz": "Exterior",
}
COLORS = ("#226d67", "#c66d43", "#7864aa", "#c59b36", "#4979a2", "#b5546a", "#6f8f58", "#8b7768")


def fetch_result(election: str, area: str):
    url = f"{BASE_URL}/{result_path(election, area)}"
    response = subprocess.run(
        [
            "curl.exe", "--fail", "--silent", "--show-error", "--location",
            "--max-time", "20", "--user-agent", "apuracao-transfer-report/1.0", url,
        ],
        capture_output=True,
        timeout=25,
    )
    if response.returncode:
        error = response.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"TSE fetch failed for {election}/{area}: {error}")
    raw = json.loads(response.stdout.decode("utf-8"))
    result = parse_result(raw)
    if result.eleicao != election or result.abr != area or result.andamento != "f":
        raise RuntimeError(f"Unexpected or non-final TSE result for {election}/{area}")
    return result, raw


def fit_ecological_model(rows: list[dict], candidate_ids: list[str], target_id: str,
                         national_share: float) -> list[float]:
    """Bounded, ridge-stabilized ecological regression, weighted by runoff valid votes."""
    weights = [row["valid2"] for row in rows]
    weight_total = sum(weights)
    weights = [weight / weight_total for weight in weights]
    features = [
        [row["votes1"].get(number, 0) / row["valid1"] for number in candidate_ids]
        for row in rows
    ]
    outcomes = [row["votes2"].get(target_id, 0) / row["valid2"] for row in rows]
    coefficients = [national_share] * len(candidate_ids)
    penalty = 0.01
    step = 0.25

    for _ in range(30000):
        gradients = [2 * penalty * (value - national_share) for value in coefficients]
        for x, y, weight in zip(features, outcomes, weights, strict=True):
            residual = sum(value * coefficient for value, coefficient in zip(x, coefficients, strict=True)) - y
            for index, value in enumerate(x):
                gradients[index] += 2 * weight * value * residual
        updated = [
            min(1.0, max(0.0, value - step * gradient))
            for value, gradient in zip(coefficients, gradients, strict=True)
        ]
        if max(abs(a - b) for a, b in zip(updated, coefficients, strict=True)) < 1e-11:
            coefficients = updated
            break
        coefficients = updated
    return coefficients


def calibrate_national_share(coefficients: list[float], votes: list[int],
                             target_share: float) -> list[float]:
    """Shift model probabilities so first-round candidate flows match the national margin."""
    total = sum(votes)
    if total <= 0:
        raise RuntimeError("Cannot calibrate the model without first-round valid votes")
    if target_share <= 0 or target_share >= 1:
        raise RuntimeError("The runoff winner share must be between 0 and 100 percent")

    epsilon = 1e-12
    logits = [
        math.log(min(1 - epsilon, max(epsilon, value)) /
                 (1 - min(1 - epsilon, max(epsilon, value))))
        for value in coefficients
    ]
    low, high = -40.0, 40.0
    for _ in range(100):
        offset = (low + high) / 2
        share = sum(
            vote / total / (1 + math.exp(-max(-700, min(700, logit + offset))))
            for vote, logit in zip(votes, logits, strict=True)
        )
        if share < target_share:
            low = offset
        else:
            high = offset
    offset = (low + high) / 2
    return [
        1 / (1 + math.exp(-max(-700, min(700, logit + offset))))
        for logit in logits
    ]


def build_data() -> dict:
    t1, t2, raw_t1, raw_t2 = {}, {}, {}, {}
    request_count = 0
    for election, target, raw_target in (
        (ELEICAO_T1, t1, raw_t1),
        (ELEICAO_T2, t2, raw_t2),
    ):
        for area in AREAS:
            result, raw = fetch_result(election, area)
            target[area], raw_target[area] = result, raw
            request_count += 1
            if request_count < len(AREAS) * 2:
                time.sleep(0.25)

    first_candidates = {
        candidate.numero: {
            "id": candidate.numero,
            "name": candidate.nome,
            "party": candidate.partido,
            "votes": candidate.votos,
        }
        for candidate in t1["br"].candidates
    }
    finalist_ids = {candidate.numero for candidate in t2["br"].candidates}
    if len(finalist_ids) != 2 or not finalist_ids.issubset(first_candidates):
        raise RuntimeError("Could not identify exactly two runoff candidates from TSE results")
    winner = next((candidate.numero for candidate in t2["br"].candidates if candidate.eleito), None)
    if winner not in finalist_ids:
        raise RuntimeError("The TSE result does not identify the elected finalist")
    other = next(number for number in finalist_ids if number != winner)
    target_share = t2["br"].candidate(winner).votos / t2["br"].votos_validos
    candidate_ids = list(first_candidates)

    rows = []
    for area in UFS:
        first, second = t1[area], t2[area]
        if first.votos_validos <= 0 or second.votos_validos <= 0:
            raise RuntimeError(f"Missing valid-vote totals for {AREA_NAMES[area]}")
        rows.append({
            "id": area,
            "name": AREA_NAMES[area],
            "valid1": first.votos_validos,
            "valid2": second.votos_validos,
            "votes1": {candidate.numero: candidate.votos for candidate in first.candidates},
            "votes2": {candidate.numero: candidate.votos for candidate in second.candidates},
        })

    coefficients = fit_ecological_model(rows, candidate_ids, winner, target_share)
    for row in rows:
        row["actualWinnerShare"] = row["votes2"].get(winner, 0) / row["valid2"]
        row["estimatedWinnerShare"] = sum(
            row["votes1"].get(number, 0) / row["valid1"] * coefficient
            for number, coefficient in zip(candidate_ids, coefficients, strict=True)
        )

    weighted_sse = sum(
        row["valid2"] * (row["actualWinnerShare"] - row["estimatedWinnerShare"]) ** 2
        for row in rows
    )
    weighted_mean = sum(
        row["valid2"] * row["actualWinnerShare"] for row in rows
    ) / sum(row["valid2"] for row in rows)
    weighted_sst = sum(
        row["valid2"] * (row["actualWinnerShare"] - weighted_mean) ** 2 for row in rows
    )

    candidates = []
    for number, candidate in first_candidates.items():
        if number in finalist_ids:
            continue
        share_to_winner = coefficients[candidate_ids.index(number)]
        candidates.append({
            **candidate,
            "color": COLORS[len(candidates) % len(COLORS)],
            "toWinner": share_to_winner,
            "toOther": 1 - share_to_winner,
            "flowWinner": round(candidate["votes"] * share_to_winner),
            "flowOther": round(candidate["votes"] * (1 - share_to_winner)),
        })
    candidates.sort(key=lambda candidate: candidate["votes"], reverse=True)

    first_candidate_results = {candidate.numero: candidate for candidate in t1["br"].candidates}
    first_valid = t1["br"].votos_validos
    second_valid = t2["br"].votos_validos
    net_valid_increase = second_valid - first_valid
    if net_valid_increase < 0:
        raise RuntimeError(
            f"Second-round valid votes ({second_valid}) are below first-round valid votes ({first_valid}); "
            "the requested increase-flow chart cannot be reconciled."
        )
    second_winner_votes = t2["br"].candidate(winner).votos
    second_other_votes = t2["br"].candidate(other).votos
    net_to_winner = round(net_valid_increase * second_winner_votes / second_valid)
    net_to_other = net_valid_increase - net_to_winner
    first_winner_target = second_winner_votes - net_to_winner
    first_winner_share = first_winner_target / first_valid
    calibrated_shares = calibrate_national_share(
        coefficients,
        [first_candidate_results[number].votos for number in candidate_ids],
        first_winner_share,
    )
    scale_candidates = []
    for index, number in enumerate(candidate_ids):
        candidate = first_candidates[number]
        probability = calibrated_shares[index]
        flow_winner = round(candidate["votes"] * probability)
        scale_candidates.append({
            **candidate,
            "color": (
                "#155d53" if number == winner
                else "#394b66" if number == other
                else COLORS[(index - 2) % len(COLORS)]
            ),
            "toWinner": probability,
            "flowWinner": flow_winner,
            "flowOther": candidate["votes"] - flow_winner,
        })
    rounding_difference = first_winner_target - sum(c["flowWinner"] for c in scale_candidates)
    for candidate in sorted(scale_candidates, key=lambda item: item["votes"], reverse=True):
        if rounding_difference == 0:
            break
        if rounding_difference > 0:
            adjustment = min(rounding_difference, candidate["flowOther"])
        else:
            adjustment = -min(-rounding_difference, candidate["flowWinner"])
        candidate["flowWinner"] += adjustment
        candidate["flowOther"] -= adjustment
        rounding_difference -= adjustment
    if rounding_difference:
        raise RuntimeError("Could not reconcile rounded first-round candidate flows")
    if sum(c["flowWinner"] for c in scale_candidates) + net_to_winner != second_winner_votes:
        raise RuntimeError("Winner flow total does not match the official second-round result")
    if sum(c["flowOther"] for c in scale_candidates) + net_to_other != second_other_votes:
        raise RuntimeError("Other finalist flow total does not match the official second-round result")
    first_winner_votes = first_candidate_results[winner].votos
    first_other_votes = first_candidate_results[other].votos
    eliminated_votes = first_valid - first_winner_votes - first_other_votes

    def national_result(result):
        return {
            "generated": result.gerado_em.isoformat() if result.gerado_em else None,
            "validVotes": result.votos_validos,
            "totalVotes": result.total_votos,
            "blankVotes": result.brancos,
            "nullVotes": result.nulos,
            "turnout": result.counts.comparecimento,
            "abstention": result.counts.abstencao,
            "candidates": [
                {"id": c.numero, "name": c.nome, "votes": c.votos, "share": c.pct, "elected": c.eleito}
                for c in result.candidates
            ],
        }

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "elections": {"first": ELEICAO_T1, "second": ELEICAO_T2},
        "candidateIds": candidate_ids,
        "winnerId": winner,
        "otherId": other,
        "winner": {
            "id": winner,
            "name": t2["br"].candidate(winner).nome,
            "party": t2["br"].candidate(winner).partido,
        },
        "other": {
            "id": other,
            "name": t2["br"].candidate(other).nome,
            "party": t2["br"].candidate(other).partido,
        },
        "candidates": candidates,
        "scaleCandidates": scale_candidates,
        "netValidIncrease": net_valid_increase,
        "netIncreaseToWinner": net_to_winner,
        "netIncreaseToOther": net_to_other,
        "crossCandidateModel": {
            "otherToWinner": next(c["flowWinner"] for c in scale_candidates if c["id"] == other),
            "winnerToOther": next(c["flowOther"] for c in scale_candidates if c["id"] == winner),
        },
        "noCrossSwitchScenario": {
            "eliminatedVotes": eliminated_votes,
            "winnerFirstVotes": first_winner_votes,
            "otherFirstVotes": first_other_votes,
            "winnerNetGain": second_winner_votes - first_winner_votes,
            "otherNetGain": second_other_votes - first_other_votes,
            "availablePool": eliminated_votes + net_valid_increase,
        },
        "firstRound": national_result(t1["br"]),
        "secondRound": national_result(t2["br"]),
        "areas": rows,
        "model": {
            "weightedRmsePp": (weighted_sse / sum(row["valid2"] for row in rows)) ** 0.5 * 100,
            "weightedR2": 1 - weighted_sse / weighted_sst if weighted_sst else None,
            "areaCount": len(rows),
            "penalty": 0.01,
        },
        "sources": {
            "first": f"{BASE_URL}/{result_path(ELEICAO_T1, 'br')}",
            "second": f"{BASE_URL}/{result_path(ELEICAO_T2, 'br')}",
        },
    }


HTML = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Estimación ecológica de posibles flujos de voto entre las dos vueltas presidenciales de Brasil en 2026, basada en resultados oficiales agregados del TSE.">
<link rel="icon" href="data:,">
<title>Flujos estimados de voto | Brasil 2026</title>
<style>
:root{color-scheme:light;--ink:#172623;--muted:#64716e;--paper:#f5f7f5;--card:#fff;--line:#dfe7e2;--green:#155d53;--lime:#c7e3a0;--accent:#ebf3e9;--shadow:0 12px 32px #173e3210}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.top{background:#102c28;color:#fff;padding:22px max(24px,calc((100vw - 1180px)/2));display:flex;justify-content:space-between;align-items:center;gap:20px}.brand{display:flex;align-items:center}.eyebrow{font-size:11px;letter-spacing:.15em;text-transform:uppercase;color:#bdd4c9}.top h1{font-size:20px;margin:3px 0 0;line-height:1.2}.languages{display:flex;gap:6px}.languages button{background:transparent;color:#d5e5dd;border:1px solid #ffffff40;border-radius:999px;padding:7px 11px;font:600 12px system-ui,sans-serif;cursor:pointer}.languages button.active,.languages button:hover{background:#c7e3a0;color:#19352c;border-color:#c7e3a0}
main{max-width:1180px;margin:32px auto;padding:0 24px 36px}.intro{display:flex;justify-content:space-between;align-items:flex-end;gap:24px;margin-bottom:22px}.intro h2{font-size:clamp(25px,4vw,38px);line-height:1.13;margin:0 0 10px;letter-spacing:-.035em}.intro p{max-width:790px;color:var(--muted);margin:0}.badge{white-space:nowrap;border:1px solid #d7e5d9;background:#edf5ec;color:#285e43;border-radius:999px;padding:8px 12px;font-size:12px;font-weight:700}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:24px 0}.card,.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow)}.card{padding:17px 19px}.card .label{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}.card .value{font-size:23px;font-weight:750;margin-top:4px;letter-spacing:-.025em}.card .sub{font-size:12px;color:var(--muted);margin-top:2px}
.panel{padding:24px;margin:16px 0}.panelhead{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:16px}.panel h3{margin:0;font-size:19px;letter-spacing:-.02em}.panelhead p{margin:4px 0 0;color:var(--muted);font-size:13px;max-width:780px}.graphwrap{overflow-x:auto}.graph{display:block;width:100%;min-width:760px;height:auto}.node-label{font-size:12px;fill:var(--ink);font-weight:650}.node-meta{font-size:11px;fill:var(--muted)}.target-label{font-size:15px;fill:var(--ink);font-weight:750}.flow{fill-opacity:.3;stroke:none}.source-node{stroke:#fff;stroke-width:1}.target-node{stroke:#fff;stroke-width:2}.graph-note{display:flex;gap:8px;align-items:flex-start;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:13px;margin-top:8px}
.scenario-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:16px 0 10px}.scenario-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 18px}.scenario-row{display:grid;grid-template-columns:minmax(150px,1fr) minmax(180px,1.3fr);gap:10px;align-items:center;padding:11px 12px;border:1px solid var(--line);border-radius:10px}.scenario-label strong{display:block;font-size:12px;line-height:1.35}.scenario-label small{color:var(--muted);font-size:11px}.scenario-control{min-width:0}.scenario-inputs{display:grid;grid-template-columns:minmax(70px,1fr) 82px;gap:8px;align-items:center}.scenario-range{width:100%;accent-color:var(--green)}.scenario-number{width:100%;padding:5px 6px;border:1px solid var(--line);border-radius:6px;color:var(--ink);font:inherit;font-size:12px}.scenario-split{display:block;text-align:right;color:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}.scenario-reset{border:1px solid #b8cdbf;border-radius:8px;background:#edf5ec;color:#285e43;padding:7px 11px;font:600 12px system-ui,sans-serif;cursor:pointer}.scenario-reset:hover{background:#dcebdc}.scenario-note{margin-top:12px}
.tablewrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}th{text-align:left;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.055em;font-weight:700}th,td{padding:11px 10px;border-bottom:1px solid var(--line);white-space:nowrap}td.numeric,th.numeric{text-align:right;font-variant-numeric:tabular-nums}.candidate{display:flex;align-items:center;gap:8px}.dot{width:9px;height:9px;border-radius:50%;display:inline-block;flex:none}.rate{font-weight:750}.subtle{color:var(--muted)}.modelgrid{display:grid;grid-template-columns:1fr 1fr;gap:14px}#scaleSummary{grid-template-columns:repeat(4,minmax(0,1fr));margin-top:16px}.switchgrid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.modelbox{background:#f4f7f4;border-radius:12px;padding:16px}.modelbox strong{font-size:20px}.modelbox small{display:block;color:var(--muted);margin-top:4px}.method{background:#edf3ef;border-left:4px solid #639b70;border-radius:8px;padding:14px 16px;color:#35443e;font-size:13px}.foot{margin-top:26px;padding:20px 4px 0;border-top:1px solid var(--line);color:var(--muted);font-size:12px}.foot p{margin:8px 0}.foot a{color:var(--green)}.disclaimer{padding:13px 15px;background:#fff;border:1px solid var(--line);border-radius:10px;margin:10px 0}
.sr{position:absolute;left:-9999px}@media(max-width:760px){.top{align-items:flex-start;padding:18px 18px}.top h1{font-size:16px}.languages{flex-wrap:wrap;justify-content:flex-end}.languages button{padding:6px 8px}.intro{display:block}.badge{display:inline-block;margin-top:13px}.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.card{padding:14px}.card .value{font-size:19px}.panel{padding:17px}.modelgrid,#scaleSummary,.switchgrid{grid-template-columns:repeat(2,minmax(0,1fr))}.scenario-grid{grid-template-columns:1fr}}@media(max-width:520px){.switchgrid{grid-template-columns:1fr}.scenario-row{grid-template-columns:1fr;gap:6px}.scenario-head{align-items:flex-start}}@media print{.top{background:#102c28!important;print-color-adjust:exact}.languages,.scenario-reset{display:none}body{background:#fff}.panel,.card{box-shadow:none;break-inside:avoid}}
</style>
</head>
<body>
<header class="top">
  <div class="brand">
    <div><div class="eyebrow" data-i="eyebrow">ANÁLISIS ELECTORAL · BRASIL 2026</div><h1 data-i="header">Estimación de flujos entre vueltas</h1></div>
  </div>
  <nav class="languages" aria-label="Language">
    <button class="active" data-lang="es">ES</button><button data-lang="en">EN</button><button data-lang="de">DE</button>
  </nav>
</header>
<main>
  <section class="intro">
    <div><h2 data-i="title">¿Cómo podrían haberse repartido los votos de los candidatos eliminados?</h2><p data-i="intro">Una estimación estadística a partir de resultados oficiales por estado. Los datos agregados no permiten observar ni demostrar cómo votó cada persona.</p></div>
    <span class="badge" data-i="badge">ESTIMACIÓN ECOLÓGICA · NO ES UN CONTEO INDIVIDUAL</span>
  </section>
  <section class="cards" id="summary"></section>
  <section class="panel">
    <div class="panelhead"><div><h3 data-i="graphTitle">Flujos estimados desde los candidatos eliminados</h3><p data-i="graphSub">El grosor representa la asignación modelada de los votos válidos de primera vuelta de cada candidatura eliminada. No son transferencias observadas.</p></div></div>
    <div class="graphwrap"><svg class="graph" id="flowGraph" role="img" aria-labelledby="graphTitle graphDesc"></svg></div>
    <p class="sr" id="graphDesc"></p>
    <div class="graph-note"><span aria-hidden="true">ⓘ</span><span data-i="graphNote">Solo se muestran las candidaturas que no pasaron a segunda vuelta. Los dos bloques de destino suman asignaciones estimadas de esos votos; no representan el total oficial obtenido por los finalistas.</span></div>
  </section>
  <section class="panel">
    <div class="panelhead"><div><h3 data-i="scenarioTitle">Probar escenarios de transferencia</h3><p data-i="scenarioSub"></p></div></div>
    <div class="scenario-head"><strong data-i="scenarioColumn"></strong><button class="scenario-reset" id="resetScenario" type="button" data-i="scenarioReset"></button></div>
    <div class="scenario-grid" id="transferControls"></div>
    <div class="graph-note scenario-note"><span aria-hidden="true">ⓘ</span><span data-i="scenarioNote"></span></div>
  </section>
  <section class="panel">
    <div class="panelhead"><div><h3 data-i="scaleGraphTitle">Todos los votos válidos: primera vuelta frente a segunda vuelta</h3><p data-i="scaleGraphSub">Compara el volumen total de votos válidos de cada vuelta. Las candidaturas eliminadas y las dos finalistas de primera vuelta se estiman con el modelo estatal; el aumento neto de votos válidos se muestra aparte.</p></div></div>
    <div class="graphwrap"><svg class="graph" id="scaleFlowGraph" role="img" aria-labelledby="scaleGraphTitle scaleGraphDesc"></svg></div>
    <p class="sr" id="scaleGraphDesc"></p>
    <div class="graph-note"><span aria-hidden="true">ⓘ</span><span data-i="scaleGraphNote">El grosor está en escala común: cada flujo se mide en votos. El bloque adicional representa la diferencia neta entre los totales oficiales de votos válidos de las vueltas, no un recuento de personas nuevas. Los flujos desde candidaturas individuales son estimaciones ecológicas y no transferencias observadas.</span></div>
    <div class="modelgrid" id="scaleSummary"></div>
  </section>
  <section class="panel">
    <div class="panelhead"><div><h3 data-i="switchTitle">¿Hubo cambios entre Lula y Flávio?</h3><p data-i="switchSub">El modelo ecológico puede sugerir cambios entre grupos; los resultados agregados no revelan los cambios individuales.</p></div></div>
    <div class="switchgrid" id="switchScenarios"></div>
  </section>
  <section class="panel">
    <div class="panelhead"><div><h3 data-i="tableTitle">Detalle de los votos de primera vuelta</h3><p data-i="tableSub">Porcentaje estimado de cada grupo que habría votado por cada finalista, con el tamaño del grupo oficial de primera vuelta.</p></div></div>
    <div class="tablewrap"><table><thead id="flowHead"></thead><tbody id="flowRows"></tbody></table></div>
  </section>
  <section class="panel">
    <div class="panelhead"><div><h3 data-i="fitTitle">Comprobación del modelo por estado</h3><p data-i="fitSub">Compara el porcentaje oficial de la candidatura ganadora en segunda vuelta con el valor que predice el modelo a partir de las mezclas de voto de la primera vuelta.</p></div></div>
    <div class="modelgrid" id="modelSummary"></div>
    <div class="tablewrap" style="margin-top:15px"><table><thead id="areaHead"></thead><tbody id="areaRows"></tbody></table></div>
  </section>
  <section class="panel">
    <div class="panelhead"><div><h3 data-i="methodTitle">Método y límites</h3></div></div>
    <div class="method" data-i="methodBody"></div>
  </section>
  <footer class="foot">
    <p><strong data-i="sourceLabel">Fuentes oficiales:</strong> <a id="source1" target="_blank" rel="noopener">TSE · 1.ª vuelta</a> · <a id="source2" target="_blank" rel="noopener">TSE · 2.ª vuelta</a>. <span id="generated"></span></p>
    <div class="disclaimer" data-i="aiDisclaimer"></div>
  </footer>
</main>
<script>
const D = __DATA__;
const TEXT = {
 es:{
  eyebrow:"ANÁLISIS ELECTORAL · BRASIL 2026",header:"Estimación de flujos entre vueltas",
  title:"¿Cómo podrían haberse repartido los votos de los candidatos eliminados?",
  intro:"Una estimación estadística a partir de resultados oficiales por estado. Los datos agregados no permiten observar ni demostrar cómo votó cada persona.",
  badge:"ESTIMACIÓN ECOLÓGICA · NO ES UN CONTEO INDIVIDUAL",
  graphTitle:"Flujos estimados desde los candidatos eliminados",
  graphSub:"El grosor representa la asignación modelada de los votos válidos de primera vuelta de cada candidatura eliminada. No son transferencias observadas.",
  graphNote:"Solo se muestran las candidaturas que no pasaron a segunda vuelta. Los dos bloques de destino suman asignaciones estimadas de esos votos; no representan el total oficial obtenido por los finalistas.",
  tableTitle:"Detalle de los votos de primera vuelta",tableSub:"Porcentaje estimado de cada grupo que habría votado por cada finalista, con el tamaño del grupo oficial de primera vuelta.",
  colCandidate:"Candidatura eliminada (1.ª vuelta)",colVotes:"Votos 1.ª vuelta",colWinner:"Estimado a",colOther:"Estimado a",colFlow:"Reparto estimado",
  fitTitle:"Comprobación del modelo por estado",fitSub:"Compara el porcentaje oficial de la candidatura ganadora en segunda vuelta con el valor que predice el modelo a partir de las mezclas de voto de la primera vuelta.",
  modelError:"Error cuadrático medio ponderado",modelR2:"R² ponderado",modelAreas:"Unidades geográficas usadas",
  colArea:"Estado / exterior",colActual:"Resultado oficial 2.ª vuelta",colPred:"Predicción del modelo",colResidual:"Diferencia",
  methodTitle:"Método y límites",
  methodBody:"<strong>Modelo:</strong> regresión ecológica lineal con restricciones (cada coeficiente entre 0 % y 100 %), ajustada sobre la proporción de votos de primera vuelta por candidatura en cada una de las 27 UFs y el exterior. Se pondera por votos válidos de segunda vuelta y se aplica una regularización leve hacia el resultado nacional para reducir inestabilidad. La proporción estimada de cada candidatura eliminada se multiplica por sus votos nacionales de primera vuelta para dibujar los flujos.<br><br><strong>Interpretación:</strong> es una asociación entre patrones geográficos, no un seguimiento de electores. El modelo no puede identificar cambios individuales, abstención, votos blancos/nulos, nuevos votantes ni cambios dentro de los estados. Las asignaciones no son conteos oficiales ni una encuesta; no deben presentarse como hechos observados.",
  sourceLabel:"Fuentes oficiales:",generated:"Datos consultados desde los archivos públicos del TSE.",
  aiDisclaimer:"Aviso de IA: el modelo y la presentación se prepararon con asistencia de IA. Las estimaciones pueden contener errores; compruebe los resultados y el método en las fuentes oficiales.",
  firstRound:"Vuelta 1 · votos válidos",secondRound:"Vuelta 2 · votos válidos",winnerResult:"Votos oficiales de la candidatura ganadora",otherResult:"Votos oficiales de la otra candidatura",
  estimated:"Estimación modelada",to:"A",of:"de",official:"oficial",estimate:"estimado",flowSummary:"asignados por el modelo",
  graphDescription:"Diagrama de flujos modelados desde los votos de candidaturas eliminadas en la primera vuelta hacia las dos candidaturas de segunda vuelta.",
  scenarioTitle:"Ajustar porcentajes para probar escenarios",
  scenarioSub:"Cambia qué porcentaje de cada grupo de votos válidos de la primera vuelta se asigna a Lula. El porcentaje restante se asigna automáticamente a Flávio; el gráfico y los resultados se recalculan al instante.",
  scenarioColumn:"Porcentaje hacia Lula (el resto irá a Flávio)",
  scenarioReset:"Restaurar estimación del modelo",
  scenarioVotes:"votos de primera vuelta",
  scenarioAria:"Porcentaje de este grupo asignado a",
  scenarioNote:"Los valores iniciales reproducen el modelo calibrado. Los controles modifican el Sankey completo de abajo; el gráfico de candidaturas eliminadas de arriba permanece como referencia del modelo. El aumento neto es una diferencia entre totales, no un grupo observado de votantes. Los escenarios no son predicciones ni evidencia de cambios individuales.",
  scenarioWinnerResult:"Votos del escenario hacia",
  scenarioOtherResult:"Votos del escenario hacia",
  scenarioWinnerDifference:"Diferencia frente al resultado oficial de",
  scenarioOtherDifference:"Diferencia frente al resultado oficial de",
  scaleGraphTitle:"Todos los votos válidos: primera vuelta frente a segunda vuelta",
  scaleGraphSub:"Compara el volumen de votos válidos en ambas vueltas. Los controles permiten cambiar el reparto supuesto de cada grupo entre los dos finalistas; los destinos muestran el total resultante del escenario y lo comparan con el resultado oficial.",
  scaleGraphNote:"El grosor está en escala común: cada flujo se mide en votos. El aumento neto es una diferencia entre totales, no un recuento de personas nuevas. Las asignaciones son parámetros de escenario, no transferencias observadas ni predicciones.",
  scaleGraphDescription:"Sankey de escenario con todos los votos válidos: los votos de primera vuelta y el aumento neto se distribuyen entre los dos finalistas según los porcentajes ajustables.",
  scaleSecond:"Resultado del escenario",scaleIncrease:"Aumento neto de votos válidos",
  modeledGroup:"grupo del escenario"
  ,switchTitle:"¿Hubo cambios entre Lula y Flávio?",
  switchSub:"El modelo ecológico puede sugerir cambios entre grupos; los resultados agregados no revelan cambios individuales.",
  modelSwitchTitle:"Lo que estima el modelo estatal",
  noSwitchTitle:"Un escenario sin cambios entre finalistas también cuadra",
  modelSwitchText:"El modelo asigna <strong>{{otherToWinner}}</strong> votos del primer turno de Flávio a Lula ({{otherToWinnerPct}} del voto inicial de Flávio), y <strong>{{winnerToOther}}</strong> votos de Lula a Flávio. Son inferencias ecológicas basadas en patrones por estado, no personas rastreadas ni transferencias comprobadas.",
  noSwitchText:"Si los votos iniciales de ambos finalistas se mantienen con su candidato, los <strong>{{eliminated}}</strong> votos de candidaturas eliminadas más el aumento neto de <strong>{{increase}}</strong> votos válidos suman <strong>{{available}}</strong>: exactamente las ganancias combinadas de Lula ({{winnerGain}}) y Flávio ({{otherGain}}). Es una posibilidad aritmética, no evidencia de lo que hicieron los votantes.",
  voterCaution:"El aumento neto de votos válidos no equivale necesariamente a personas nuevas: puede haber entradas y salidas, abstención y cambios desde votos blancos o nulos."
 },
 en:{
  eyebrow:"ELECTION ANALYSIS · BRAZIL 2026",header:"Estimated flows between rounds",
  title:"How might first-round votes for eliminated candidates have split?",
  intro:"A statistical estimate based on official state-level results. Aggregate data cannot show or prove how any individual voted.",
  badge:"ECOLOGICAL ESTIMATE · NOT AN INDIVIDUAL COUNT",
  graphTitle:"Estimated flows from eliminated candidates",
  graphSub:"Width represents the model's allocation of each eliminated candidate's valid first-round votes. These are not observed transfers.",
  graphNote:"Only candidates who did not advance are shown. The two destination blocks sum estimated allocations from those ballots; they are not the finalists' official totals.",
  tableTitle:"First-round vote detail",tableSub:"Estimated share of each group assigned to each finalist, alongside its official first-round size.",
  colCandidate:"Eliminated candidate (round 1)",colVotes:"Round 1 votes",colWinner:"Estimated to",colOther:"Estimated to",colFlow:"Estimated split",
  fitTitle:"Model check by state",fitSub:"Compares the official second-round share for the winner with the model's prediction from first-round vote mixes.",
  modelError:"Weighted root mean squared error",modelR2:"Weighted R²",modelAreas:"Geographic units used",
  colArea:"State / abroad",colActual:"Official round 2 result",colPred:"Model prediction",colResidual:"Difference",
  methodTitle:"Method and limitations",
  methodBody:"<strong>Model:</strong> constrained linear ecological regression (each coefficient between 0% and 100%), fitted to first-round candidate vote shares in each of Brazil's 27 states plus abroad. It is weighted by second-round valid votes and mildly regularized toward the national result to reduce instability. The estimated share for each eliminated candidate is multiplied by that candidate's national first-round votes to draw the flows.<br><br><strong>Interpretation:</strong> this is an association between geographic patterns, not voter tracking. The model cannot identify individual changes, abstention, blank/null votes, new voters, or within-state variation. Allocations are neither official counts nor a poll and must not be presented as observed facts.",
  sourceLabel:"Official sources:",generated:"Data retrieved from the TSE public files.",
  aiDisclaimer:"AI disclaimer: the model and presentation were prepared with AI assistance. Estimates may be wrong; verify the results and method against the official sources.",
  firstRound:"Round 1 · valid votes",secondRound:"Round 2 · valid votes",winnerResult:"Official votes for the winning candidate",otherResult:"Official votes for the other candidate",
  estimated:"Model estimate",to:"To",of:"of",official:"official",estimate:"estimated",flowSummary:"allocated by the model",
  graphDescription:"Diagram of model-estimated flows from eliminated first-round candidates to the two runoff candidates.",
  scenarioTitle:"Adjust transfer percentages to test scenarios",
  scenarioSub:"Change the share of each first-round valid-vote group assigned to Lula. The remainder is automatically assigned to Flávio; the chart and totals update instantly.",
  scenarioColumn:"Share to Lula (the remainder goes to Flávio)",
  scenarioReset:"Restore model estimates",
  scenarioVotes:"first-round votes",
  scenarioAria:"Share of this group assigned to",
  scenarioNote:"The initial values reproduce the calibrated model. These controls affect the full Sankey below; the eliminated-candidates chart above remains the model reference. The net increase is a difference between totals, not an observed group of voters. Scenarios are not predictions or evidence of individual switching.",
  scenarioWinnerResult:"Scenario votes to",
  scenarioOtherResult:"Scenario votes to",
  scenarioWinnerDifference:"Difference from official result for",
  scenarioOtherDifference:"Difference from official result for",
  scaleGraphTitle:"All valid votes: first round compared with runoff",
  scaleGraphSub:"Compares valid-vote volume across rounds. Adjust the assumed split of each group between the finalists; destinations show scenario totals alongside the official result.",
  scaleGraphNote:"All flows use the same scale: each width represents votes. The net increase is a difference between totals, not a count of new people. Allocations are scenario inputs, not observed transfers or predictions.",
  scaleGraphDescription:"Scenario Sankey of all valid votes: first-round votes and the net increase are split between the finalists using adjustable percentages.",
  scaleSecond:"Scenario result",scaleIncrease:"Net increase in valid votes",
  modeledGroup:"scenario group"
  ,switchTitle:"Did voters switch between Lula and Flávio?",
  switchSub:"The ecological model can suggest group-level flows; aggregate results cannot reveal individual voter changes.",
  modelSwitchTitle:"What the state-level model estimates",
  noSwitchTitle:"A no-switch scenario also fits the totals",
  modelSwitchText:"The model allocates <strong>{{otherToWinner}}</strong> first-round Flávio votes to Lula ({{otherToWinnerPct}} of Flávio's initial vote), and <strong>{{winnerToOther}}</strong> Lula votes to Flávio. These are ecological inferences from state patterns—not tracked people or verified transfers.",
  noSwitchText:"If both finalists' first-round votes stay with their candidate, the <strong>{{eliminated}}</strong> votes for eliminated candidates plus the net increase of <strong>{{increase}}</strong> valid votes total <strong>{{available}}</strong>—exactly the combined gains by Lula ({{winnerGain}}) and Flávio ({{otherGain}}). This is arithmetically possible, not evidence of what voters did.",
  voterCaution:"The net increase in valid votes is not necessarily the number of new voters: there can be entries and exits, abstention, and changes from blank or null votes."
 },
 de:{
  eyebrow:"WAHLANALYSE · BRASILIEN 2026",header:"Geschätzte Stimmenflüsse zwischen den Wahlgängen",
  title:"Wie könnten sich die Stimmen für ausgeschiedene Kandidaten verteilt haben?",
  intro:"Eine statistische Schätzung auf Grundlage amtlicher Ergebnisse nach Bundesstaat. Aggregierte Daten zeigen oder beweisen nicht, wie eine einzelne Person gewählt hat.",
  badge:"ÖKOLOGISCHE SCHÄTZUNG · KEINE INDIVIDUELLE AUSZÄHLUNG",
  graphTitle:"Geschätzte Flüsse von ausgeschiedenen Kandidaten",
  graphSub:"Die Breite zeigt die modellierte Verteilung der gültigen Stimmen aus dem ersten Wahlgang für ausgeschiedene Kandidaten. Es sind keine beobachteten Wechsel.",
  graphNote:"Angezeigt werden nur Kandidaten, die nicht in die Stichwahl kamen. Die beiden Zielblöcke umfassen die geschätzten Zuteilungen dieser Stimmen, nicht die amtlichen Gesamtergebnisse der Finalisten.",
  tableTitle:"Details der Stimmen im ersten Wahlgang",tableSub:"Geschätzter Anteil jeder Gruppe für jeden Finalisten sowie die amtliche Gruppengröße im ersten Wahlgang.",
  colCandidate:"Ausgeschiedener Kandidat (1. Wahlgang)",colVotes:"Stimmen 1. Wahlgang",colWinner:"Geschätzt für",colOther:"Geschätzt für",colFlow:"Geschätzte Aufteilung",
  fitTitle:"Modellprüfung nach Bundesstaat",fitSub:"Vergleicht den amtlichen Stimmenanteil des Siegers im zweiten Wahlgang mit der Modellprognose aus den Stimmenmustern des ersten Wahlgangs.",
  modelError:"Gewichteter quadratischer Mittelfehler",modelR2:"Gewichtetes R²",modelAreas:"Verwendete geografische Einheiten",
  colArea:"Bundesstaat / Ausland",colActual:"Amtliches Ergebnis 2. Wahlgang",colPred:"Modellprognose",colResidual:"Abweichung",
  methodTitle:"Methode und Grenzen",
  methodBody:"<strong>Modell:</strong> beschränkte lineare ökologische Regression (jeder Koeffizient zwischen 0 % und 100 %), angepasst an die Stimmenanteile der Kandidaten im ersten Wahlgang in den 27 Bundesstaaten Brasiliens und im Ausland. Gewichtet nach gültigen Stimmen des zweiten Wahlgangs und leicht zum nationalen Ergebnis regularisiert, um Instabilität zu mindern. Der geschätzte Anteil jeder ausgeschiedenen Kandidatur wird mit deren nationalen Stimmen im ersten Wahlgang multipliziert, um die Flüsse darzustellen.<br><br><strong>Interpretation:</strong> Dies ist ein Zusammenhang zwischen geografischen Mustern, keine Nachverfolgung von Wählern. Das Modell kann individuelle Wechsel, Nichtwahl, ungültige/leere Stimmen, neue Wähler oder Unterschiede innerhalb der Bundesstaaten nicht bestimmen. Die Zuteilungen sind weder amtliche Zählungen noch eine Umfrage und dürfen nicht als beobachtete Tatsachen dargestellt werden.",
  sourceLabel:"Amtliche Quellen:",generated:"Daten aus öffentlichen TSE-Dateien abgerufen.",
  aiDisclaimer:"KI-Hinweis: Modell und Darstellung wurden mit KI-Unterstützung erstellt. Schätzungen können fehlerhaft sein; überprüfen Sie Ergebnisse und Methode anhand der amtlichen Quellen.",
  firstRound:"1. Wahlgang · gültige Stimmen",secondRound:"2. Wahlgang · gültige Stimmen",winnerResult:"Amtliche Stimmen für den gewählten Kandidaten",otherResult:"Amtliche Stimmen für den anderen Kandidaten",
  estimated:"Modellschätzung",to:"Für",of:"von",official:"amtlich",estimate:"geschätzt",flowSummary:"vom Modell zugeordnet",
  graphDescription:"Diagramm geschätzter Modellflüsse von ausgeschiedenen Kandidaten des ersten Wahlgangs zu den beiden Kandidaten der Stichwahl.",
  scenarioTitle:"Transferanteile anpassen und Szenarien testen",
  scenarioSub:"Ändern Sie den Anteil jeder gültigen Erstwahl-Stimmengruppe, der Lula zugeordnet wird. Der Rest wird automatisch Flávio zugeordnet; Diagramm und Summen werden sofort aktualisiert.",
  scenarioColumn:"Anteil für Lula (der Rest entfällt auf Flávio)",
  scenarioReset:"Modellschätzung wiederherstellen",
  scenarioVotes:"Stimmen im ersten Wahlgang",
  scenarioAria:"Anteil dieser Gruppe, der zugeordnet wird an",
  scenarioNote:"Die Ausgangswerte bilden das kalibrierte Modell ab. Diese Regler ändern das vollständige Sankey-Diagramm darunter; das Diagramm der ausgeschiedenen Kandidaten oben bleibt die Modellreferenz. Der Nettozuwachs ist eine Differenz zwischen Summen, keine beobachtete Wählergruppe. Szenarien sind weder Vorhersagen noch Belege für individuelles Wechselverhalten.",
  scenarioWinnerResult:"Szenariostimmen für",
  scenarioOtherResult:"Szenariostimmen für",
  scenarioWinnerDifference:"Abweichung vom amtlichen Ergebnis für",
  scenarioOtherDifference:"Abweichung vom amtlichen Ergebnis für",
  scaleGraphTitle:"Alle gültigen Stimmen: erster Wahlgang im Vergleich zur Stichwahl",
  scaleGraphSub:"Vergleicht die gültigen Stimmen beider Wahlgänge. Passen Sie die angenommene Aufteilung jeder Gruppe zwischen den Finalisten an; die Zielwerte zeigen das Szenario und den Vergleich mit dem amtlichen Ergebnis.",
  scaleGraphNote:"Alle Flüsse verwenden denselben Maßstab: Jede Breite steht für Stimmen. Der Nettozuwachs ist eine Differenz zwischen Summen, keine Zahl neuer Personen. Die Zuteilungen sind Szenariowerte, keine beobachteten Wechsel oder Vorhersagen.",
  scaleGraphDescription:"Szenario-Sankey aller gültigen Stimmen: Stimmen des ersten Wahlgangs und der Nettozuwachs werden anhand anpassbarer Prozentsätze auf die beiden Finalisten verteilt.",
  scaleSecond:"Szenarioergebnis",scaleIncrease:"Nettozuwachs gültiger Stimmen",
  modeledGroup:"Szenariogruppe"
  ,switchTitle:"Wechselten Wähler zwischen Lula und Flávio?",
  switchSub:"Das ökologische Modell kann Gruppenflüsse schätzen; aggregierte Ergebnisse zeigen keine individuellen Wahlentscheidungen.",
  modelSwitchTitle:"Schätzung des Modells nach Bundesstaat",
  noSwitchTitle:"Auch ein Szenario ohne Wechsel zwischen Finalisten passt",
  modelSwitchText:"Das Modell ordnet <strong>{{otherToWinner}}</strong> Stimmen von Flávio aus dem ersten Wahlgang Lula zu ({{otherToWinnerPct}} von Flávios ursprünglichen Stimmen) und <strong>{{winnerToOther}}</strong> Stimmen von Lula Flávio. Dies sind ökologische Schlüsse aus regionalen Mustern—keine nachverfolgten Personen oder bestätigten Wechsel.",
  noSwitchText:"Wenn die Stimmen beider Finalisten aus dem ersten Wahlgang bei ihrem Kandidaten bleiben, ergeben die <strong>{{eliminated}}</strong> Stimmen für ausgeschiedene Kandidaten plus der Nettozuwachs von <strong>{{increase}}</strong> gültigen Stimmen insgesamt <strong>{{available}}</strong>—genau die zusammengezählten Zuwächse von Lula ({{winnerGain}}) und Flávio ({{otherGain}}). Das ist rechnerisch möglich, aber kein Beleg für tatsächliches Wählerverhalten.",
  voterCaution:"Der Nettozuwachs gültiger Stimmen entspricht nicht zwingend der Zahl neuer Wähler: Es kann Zu- und Abgänge, Wahlenthaltung sowie Änderungen bei ungültigen oder leeren Stimmen geben."
 }
};
const fmt = n => new Intl.NumberFormat(document.documentElement.lang === "de" ? "de-DE" : document.documentElement.lang === "en" ? "en-US" : "es-ES").format(n);
const pct = n => new Intl.NumberFormat(document.documentElement.lang === "de" ? "de-DE" : document.documentElement.lang === "en" ? "en-US" : "es-ES",{style:"percent",maximumFractionDigits:1}).format(n);
const esc = s => String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const scenarioShares = new Map([
 ...D.scaleCandidates.map(c=>[c.id,c.flowWinner/c.votes]),
 ["net-increase",D.netValidIncrease?D.netIncreaseToWinner/D.netValidIncrease:D.secondRound.candidates.find(c=>c.id===D.winner.id).share/100]
]);
const scenarioPercent = n => new Intl.NumberFormat(document.documentElement.lang === "de" ? "de-DE" : document.documentElement.lang === "en" ? "en-US" : "es-ES",{style:"percent",minimumFractionDigits:2,maximumFractionDigits:2}).format(n);
function renderScenarioControls(lang){
 const t=TEXT[lang], items=[...D.scaleCandidates].sort((a,b)=>b.votes-a.votes);
 items.push({id:"net-increase",name:t.scaleIncrease,votes:D.netValidIncrease});
 document.getElementById("transferControls").innerHTML=items.map(item=>{
  const share=scenarioShares.get(item.id), percentage=Number((share*100).toFixed(4));
  const aria=`${t.scenarioAria} ${D.winner.name}`;
  return `<div class="scenario-row" data-scenario-id="${esc(item.id)}">
   <div class="scenario-label"><strong>${esc(item.name)}</strong><small>${fmt(item.votes)} ${t.scenarioVotes}</small></div>
   <div class="scenario-control"><div class="scenario-inputs">
    <input class="scenario-range" type="range" min="0" max="100" step="0.01" value="${percentage}" aria-label="${esc(aria)} ${esc(item.name)}">
    <input class="scenario-number" type="number" min="0" max="100" step="0.01" value="${percentage}" aria-label="${esc(aria)} ${esc(item.name)}">
   </div><span class="scenario-split">${scenarioPercent(share)} ${esc(D.winner.name)} · ${scenarioPercent(1-share)} ${esc(D.other.name)}</span></div>
  </div>`;
 }).join("");
 document.querySelectorAll(".scenario-row").forEach(row=>{
  const id=row.dataset.scenarioId, range=row.querySelector(".scenario-range"), number=row.querySelector(".scenario-number");
  range.addEventListener("input",()=>updateScenarioShare(row,id,range.value,lang));
  number.addEventListener("change",()=>updateScenarioShare(row,id,number.value,lang));
 });
 document.getElementById("resetScenario").onclick=()=>{
  for(const candidate of D.scaleCandidates) scenarioShares.set(candidate.id,candidate.flowWinner/candidate.votes);
  scenarioShares.set("net-increase",D.netValidIncrease?D.netIncreaseToWinner/D.netValidIncrease:D.secondRound.candidates.find(c=>c.id===D.winner.id).share/100);
  renderScenarioControls(document.documentElement.lang);
  drawScaleGraph(document.documentElement.lang);
 };
}
function updateScenarioShare(row,id,value,lang){
 const percentage=Number(value);
 if(value.trim()===""||!Number.isFinite(percentage)){
  const current=scenarioShares.get(id)*100;
  row.querySelector(".scenario-range").value=String(current);
  row.querySelector(".scenario-number").value=current.toFixed(4);
  return;
 }
 const bounded=Math.min(100,Math.max(0,percentage));
 const share=bounded/100;
 scenarioShares.set(id,share);
 row.querySelector(".scenario-range").value=String(bounded);
 row.querySelector(".scenario-number").value=bounded.toFixed(4);
 row.querySelector(".scenario-split").textContent=`${scenarioPercent(share)} ${D.winner.name} · ${scenarioPercent(1-share)} ${D.other.name}`;
 drawScaleGraph(lang);
}
function render(lang){
 document.documentElement.lang=lang;
 document.querySelectorAll("[data-i]").forEach(el=>el.innerHTML=TEXT[lang][el.dataset.i]);
 document.querySelectorAll("[data-lang]").forEach(b=>b.classList.toggle("active",b.dataset.lang===lang));
 const t=TEXT[lang], winner=D.winner, other=D.other;
 const candidate=(id,round)=>round.candidates.find(c=>c.id===id);
 const w1=candidate(winner.id,D.firstRound), w2=candidate(winner.id,D.secondRound), o2=candidate(other.id,D.secondRound);
 document.getElementById("summary").innerHTML=[
  [t.firstRound,fmt(D.firstRound.validVotes),`${D.firstRound.candidates.length} ${lang==="es"?"candidaturas":lang==="de"?"Kandidaturen":"candidates"}`],
  [t.secondRound,fmt(D.secondRound.validVotes),`${D.areas.length} ${lang==="es"?"unidades geográficas":lang==="de"?"geografische Einheiten":"geographic units"}`],
  [t.winnerResult,fmt(w2.votes),`${esc(winner.name)} · ${pct(w2.share/100)}`],
  [t.otherResult,fmt(o2.votes),`${esc(other.name)} · ${pct(o2.share/100)}`]
 ].map(c=>`<article class="card"><div class="label">${c[0]}</div><div class="value">${c[1]}</div><div class="sub">${c[2]}</div></article>`).join("");
 const scenario=D.noCrossSwitchScenario;
 const firstOther=candidate(other.id,D.firstRound);
 const modelValues={
  otherToWinner:fmt(D.crossCandidateModel.otherToWinner),
  otherToWinnerPct:pct(D.crossCandidateModel.otherToWinner/firstOther.votes),
  winnerToOther:fmt(D.crossCandidateModel.winnerToOther),
  eliminated:fmt(scenario.eliminatedVotes),
  increase:fmt(D.netValidIncrease),
  available:fmt(scenario.availablePool),
  winnerGain:fmt(scenario.winnerNetGain),
  otherGain:fmt(scenario.otherNetGain)
 };
 const fill=(template)=>template.replace(/\{\{(\w+)\}\}/g,(_,key)=>modelValues[key]??"");
 document.getElementById("switchScenarios").innerHTML=`
  <div class="modelbox"><strong>${t.modelSwitchTitle}</strong><p>${fill(t.modelSwitchText)}</p></div>
  <div class="modelbox"><strong>${t.noSwitchTitle}</strong><p>${fill(t.noSwitchText)}</p></div>
  <p class="method" style="grid-column:1/-1">${t.voterCaution}</p>`;
 document.getElementById("flowHead").innerHTML=`<tr><th>${t.colCandidate}</th><th class="numeric">${t.colVotes}</th><th class="numeric">${t.colWinner} ${esc(winner.name)}</th><th class="numeric">${t.colOther} ${esc(other.name)}</th><th class="numeric">${t.colFlow}</th></tr>`;
 document.getElementById("flowRows").innerHTML=D.candidates.map(c=>`<tr><td><span class="candidate"><i class="dot" style="background:${c.color}"></i><span>${esc(c.name)} <span class="subtle">(${esc(c.party)})</span></span></span></td><td class="numeric">${fmt(c.votes)}</td><td class="numeric"><span class="rate">${pct(c.toWinner)}</span><br><span class="subtle">${fmt(c.flowWinner)}</span></td><td class="numeric"><span class="rate">${pct(c.toOther)}</span><br><span class="subtle">${fmt(c.flowOther)}</span></td><td class="numeric">${pct(c.votes/D.firstRound.validVotes)}</td></tr>`).join("");
 const rmse=new Intl.NumberFormat(lang==="de"?"de-DE":lang==="en"?"en-US":"es-ES",{maximumFractionDigits:2}).format(D.model.weightedRmsePp)+" pp";
 const r2=D.model.weightedR2===null?"—":new Intl.NumberFormat(lang==="de"?"de-DE":lang==="en"?"en-US":"es-ES",{style:"percent",maximumFractionDigits:1}).format(D.model.weightedR2);
 document.getElementById("modelSummary").innerHTML=`<div class="modelbox"><strong>${rmse}</strong><small>${t.modelError}</small></div><div class="modelbox"><strong>${r2}</strong><small>${t.modelR2}</small></div><div class="modelbox"><strong>${D.model.areaCount}</strong><small>${t.modelAreas}</small></div>`;
 document.getElementById("areaHead").innerHTML=`<tr><th>${t.colArea}</th><th class="numeric">${t.colActual} · ${esc(winner.name)}</th><th class="numeric">${t.colPred}</th><th class="numeric">${t.colResidual}</th></tr>`;
 document.getElementById("areaRows").innerHTML=D.areas.map(a=>`<tr><td>${esc(a.name)}</td><td class="numeric">${pct(a.actualWinnerShare)}</td><td class="numeric">${pct(a.estimatedWinnerShare)}</td><td class="numeric">${(a.actualWinnerShare>=a.estimatedWinnerShare?"+":"")+new Intl.NumberFormat(lang==="de"?"de-DE":lang==="en"?"en-US":"es-ES",{maximumFractionDigits:2}).format((a.actualWinnerShare-a.estimatedWinnerShare)*100)} pp</td></tr>`).join("");
 drawGraph(lang);
 renderScenarioControls(lang);
 drawScaleGraph(lang);
 document.getElementById("source1").href=D.sources.first;document.getElementById("source2").href=D.sources.second;
 document.getElementById("generated").textContent=`${t.generated} ${new Date(D.generatedAt).toLocaleString(lang==="de"?"de-DE":lang==="en"?"en-US":"es-ES")}.`;
}
function drawGraph(lang){
 const svg=document.getElementById("flowGraph"), candidates=D.candidates, W=1000, H=Math.max(430,candidates.length*58+40), gap=15, top=22;
 const total=candidates.reduce((s,c)=>s+c.flowWinner+c.flowOther,0)||1, scale=(H-2*top-gap*Math.max(0,candidates.length-1))/total;
 const sourceX=55, sourceW=15, targetX=720, targetW=18, startX=sourceX+sourceW, endX=targetX;
 const sources=[];let y=top;
 for(const c of candidates){const h=(c.flowWinner+c.flowOther)*scale;sources.push({c,y,h});y+=h+gap;}
 const sumWinner=candidates.reduce((s,c)=>s+c.flowWinner,0),sumOther=candidates.reduce((s,c)=>s+c.flowOther,0);
 const winH=sumWinner*scale,otherH=sumOther*scale;
 let winStart=top, otherStart=top+winH;
 const pieces=[];
 for(const s of sources){
  const hA=s.c.flowWinner*scale,hB=s.c.flowOther*scale;
  const sourceMidA=s.y+hA/2,sourceMidB=s.y+hA+hB/2;
  const targetMidA=winStart+hA/2,targetMidB=otherStart+hB/2;
  const curve=(y1,y2,h)=>`M ${startX} ${y1-h/2} C ${startX+170} ${y1-h/2}, ${endX-170} ${y2-h/2}, ${endX} ${y2-h/2} L ${endX} ${y2+h/2} C ${endX-170} ${y2+h/2}, ${startX+170} ${y1+h/2}, ${startX} ${y1+h/2} Z`;
  if(hA>0){pieces.push(`<path class="flow" fill="${s.c.color}" d="${curve(sourceMidA,targetMidA,hA)}"><title>${esc(s.c.name)} → ${esc(D.winner.name)}: ${fmt(s.c.flowWinner)} (${pct(s.c.toWinner)})</title></path>`);winStart+=hA;}
  if(hB>0){pieces.push(`<path class="flow" fill="${s.c.color}" d="${curve(sourceMidB,otherStart+hB/2,hB)}"><title>${esc(s.c.name)} → ${esc(D.other.name)}: ${fmt(s.c.flowOther)} (${pct(s.c.toOther)})</title></path>`);otherStart+=hB;}
 }
 const sourceNodes=sources.map(s=>{const mid=s.y+s.h/2;return `<rect class="source-node" x="${sourceX}" y="${s.y}" width="${sourceW}" height="${s.h}" rx="4" fill="${s.c.color}"><title>${esc(s.c.name)}: ${fmt(s.c.votes)} first-round votes</title></rect><text class="node-label" x="${sourceX+26}" y="${mid-4}">${esc(s.c.name)}</text><text class="node-meta" x="${sourceX+26}" y="${mid+13}">${fmt(s.c.votes)} · ${pct(s.c.toWinner)} → ${esc(D.winner.name)}</text>`}).join("");
 const wShare=D.secondRound.candidates.find(c=>c.id===D.winner.id).share/100,oShare=D.secondRound.candidates.find(c=>c.id===D.other.id).share/100;
 const targetNodes=`<rect class="target-node" x="${targetX}" y="${top}" width="${targetW}" height="${winH}" rx="5" fill="#155d53"><title>${esc(D.winner.name)}: estimated contribution from eliminated groups ${fmt(sumWinner)}</title></rect>
 <text class="target-label" x="${targetX+30}" y="${top+winH/2-5}">${esc(D.winner.name)}</text><text class="node-meta" x="${targetX+30}" y="${top+winH/2+13}">${fmt(sumWinner)} ${TEXT[lang].estimate} · ${pct(wShare)} ${TEXT[lang].official}</text>
 <rect class="target-node" x="${targetX}" y="${top+winH}" width="${targetW}" height="${otherH}" rx="5" fill="#394b66"><title>${esc(D.other.name)}: estimated contribution from eliminated groups ${fmt(sumOther)}</title></rect>
 <text class="target-label" x="${targetX+30}" y="${top+winH+otherH/2-5}">${esc(D.other.name)}</text><text class="node-meta" x="${targetX+30}" y="${top+winH+otherH/2+13}">${fmt(sumOther)} ${TEXT[lang].estimate} · ${pct(oShare)} ${TEXT[lang].official}</text>`;
 svg.setAttribute("viewBox",`0 0 ${W} ${H}`);svg.setAttribute("height",H);
 svg.innerHTML=`<text class="node-meta" x="${sourceX}" y="14">${lang==="es"?"VOTOS VÁLIDOS · 1.ª VUELTA":lang==="de"?"GÜLTIGE STIMMEN · 1. WAHLGANG":"VALID VOTES · ROUND 1"}</text><text class="node-meta" x="${targetX}" y="14">${lang==="es"?"DESTINO MODELADO":lang==="de"?"MODELLIERTES ZIEL":"MODELED DESTINATION"}</text>${pieces.join("")}${sourceNodes}${targetNodes}`;
 document.getElementById("graphDesc").textContent=TEXT[lang].graphDescription;
}
function drawScaleGraph(lang){
 const svg=document.getElementById("scaleFlowGraph"),t=TEXT[lang];
 const candidates=D.scaleCandidates.map(c=>{
  const toWinner=Math.min(c.votes,Math.max(0,Math.round(c.votes*scenarioShares.get(c.id))));
  return {...c,toWinner:scenarioShares.get(c.id),flowWinner:toWinner,flowOther:c.votes-toWinner};
 }).sort((a,b)=>b.votes-a.votes);
 const netToWinner=Math.min(D.netValidIncrease,Math.max(0,Math.round(D.netValidIncrease*scenarioShares.get("net-increase"))));
 const additional={
  id:"net-increase",name:t.scaleIncrease,votes:D.netValidIncrease,
  toWinner:scenarioShares.get("net-increase"),flowWinner:netToWinner,
  flowOther:D.netValidIncrease-netToWinner,color:"#c59b36"
 };
 const sources=[...candidates,additional],W=1060,top=28,gap=5,scale=0.0000055;
 const H=Math.ceil(2*top+D.secondRound.validVotes*scale+gap*(sources.length-1));
 const sourceX=320,sourceW=14,targetX=735,targetW=18,startX=sourceX+sourceW,endX=targetX;
 const scenarioWinner=sources.reduce((sum,item)=>sum+item.flowWinner,0);
 const scenarioOther=sources.reduce((sum,item)=>sum+item.flowOther,0);
 let sourceY=top, winnerY=top, otherY=top+scenarioWinner*scale;
 const pieces=[],sourceNodes=[];
 const curve=(y1,y2,h)=>`M ${startX} ${y1-h/2} C ${startX+120} ${y1-h/2}, ${endX-120} ${y2-h/2}, ${endX} ${y2-h/2} L ${endX} ${y2+h/2} C ${endX-120} ${y2+h/2}, ${startX+120} ${y1+h/2}, ${startX} ${y1+h/2} Z`;
 for(const item of sources){
  const winnerHeight=item.flowWinner*scale,otherHeight=item.flowOther*scale,totalHeight=item.votes*scale;
  const sourceMidWinner=sourceY+winnerHeight/2,sourceMidOther=sourceY+winnerHeight+otherHeight/2;
  if(winnerHeight>0){
   const targetMid=winnerY+winnerHeight/2;
   pieces.push(`<path class="flow" fill="${item.color}" d="${curve(sourceMidWinner,targetMid,winnerHeight)}"><title>${esc(item.name)} → ${esc(D.winner.name)}: ${fmt(item.flowWinner)} (${scenarioPercent(item.toWinner)})</title></path>`);
   winnerY+=winnerHeight;
  }
  if(otherHeight>0){
   const targetMid=otherY+otherHeight/2;
   pieces.push(`<path class="flow" fill="${item.color}" d="${curve(sourceMidOther,targetMid,otherHeight)}"><title>${esc(item.name)} → ${esc(D.other.name)}: ${fmt(item.flowOther)} (${scenarioPercent(1-item.toWinner)})</title></path>`);
   otherY+=otherHeight;
  }
  sourceNodes.push(`<rect class="source-node" x="${sourceX}" y="${sourceY}" width="${sourceW}" height="${totalHeight}" rx="3" fill="${item.color}"><title>${esc(item.name)}: ${fmt(item.votes)} ${t.modeledGroup}</title></rect>`);
  if(totalHeight>=42){
   const mid=sourceY+totalHeight/2;
   const label=item.name.length>27?`${item.name.slice(0,26)}…`:item.name;
   sourceNodes.push(`<text class="node-label" x="${sourceX-12}" y="${mid-4}" text-anchor="end">${esc(label)}</text><text class="node-meta" x="${sourceX-12}" y="${mid+13}" text-anchor="end">${fmt(item.votes)}</text>`);
  }
  sourceY+=totalHeight+gap;
 }
 const winnerResult=D.secondRound.candidates.find(c=>c.id===D.winner.id);
 const otherResult=D.secondRound.candidates.find(c=>c.id===D.other.id);
 const winnerHeight=scenarioWinner*scale,otherHeight=scenarioOther*scale;
 const targets=`<rect class="target-node" x="${targetX}" y="${top}" width="${targetW}" height="${winnerHeight}" rx="5" fill="#155d53"><title>${esc(D.winner.name)}: ${fmt(scenarioWinner)} · ${t.official}: ${fmt(winnerResult.votes)}</title></rect>
 <text class="target-label" x="${targetX+30}" y="${top+winnerHeight/2-5}">${esc(D.winner.name)}</text><text class="node-meta" x="${targetX+30}" y="${top+winnerHeight/2+13}">${fmt(scenarioWinner)} · ${pct(scenarioWinner/D.secondRound.validVotes)} · ${t.official}: ${fmt(winnerResult.votes)}</text>
 <rect class="target-node" x="${targetX}" y="${top+winnerHeight}" width="${targetW}" height="${otherHeight}" rx="5" fill="#394b66"><title>${esc(D.other.name)}: ${fmt(scenarioOther)} · ${t.official}: ${fmt(otherResult.votes)}</title></rect>
 <text class="target-label" x="${targetX+30}" y="${top+winnerHeight+otherHeight/2-5}">${esc(D.other.name)}</text><text class="node-meta" x="${targetX+30}" y="${top+winnerHeight+otherHeight/2+13}">${fmt(scenarioOther)} · ${pct(scenarioOther/D.secondRound.validVotes)} · ${t.official}: ${fmt(otherResult.votes)}</text>`;
 const sourceHeading=lang==="es"?"VOTOS VÁLIDOS · PRIMERA VUELTA":lang==="de"?"GÜLTIGE STIMMEN · ERSTER WAHLGANG":"VALID VOTES · FIRST ROUND";
 svg.setAttribute("viewBox",`0 0 ${W} ${H}`);
 svg.setAttribute("height",H);
 svg.innerHTML=`<text class="node-meta" x="20" y="14">${sourceHeading}</text><text class="node-meta" x="${targetX}" y="14">${t.scaleSecond.toLocaleUpperCase()}</text>${pieces.join("")}${sourceNodes.join("")}${targets}`;
 document.getElementById("scaleGraphDesc").textContent=t.scaleGraphDescription;
 const delta=value=>`${value>0?"+":""}${fmt(value)}`;
 document.getElementById("scaleSummary").innerHTML=[
  [`${t.scenarioWinnerResult} ${esc(D.winner.name)}`,`${fmt(scenarioWinner)} · ${pct(scenarioWinner/D.secondRound.validVotes)}`],
  [`${t.scenarioOtherResult} ${esc(D.other.name)}`,`${fmt(scenarioOther)} · ${pct(scenarioOther/D.secondRound.validVotes)}`],
  [`${t.scenarioWinnerDifference} ${esc(D.winner.name)}`,delta(scenarioWinner-winnerResult.votes)],
  [`${t.scenarioOtherDifference} ${esc(D.other.name)}`,delta(scenarioOther-otherResult.votes)]
 ].map(c=>`<div class="modelbox"><strong>${c[1]}</strong><small>${c[0]}</small></div>`).join("");
}
document.querySelectorAll("[data-lang]").forEach(b=>b.addEventListener("click",()=>render(b.dataset.lang)));
render("es");
</script>
</body></html>"""


def main() -> None:
    data = build_data()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(HTML.replace("__DATA__", payload), encoding="utf-8")
    print(f"Created {OUTPUT}")
    print(
        f"Official totals: round 1={data['firstRound']['validVotes']:,} valid votes, "
        f"round 2={data['secondRound']['validVotes']:,} valid votes; "
        f"state model RMSE={data['model']['weightedRmsePp']:.2f} pp"
    )


if __name__ == "__main__":
    main()
