"""Somas regionais das UFs, sem substituir o resultado oficial nacional do TSE.

Percentuais são razões de somas, nunca médias das porcentagens estaduais. As
estimativas restantes reutilizam o cálculo da projeção; não definem vencedores.
"""

from .analysis import _remaining_valid_votes, finalists
from .baseline import Baseline
from .parse import Progress, Result
from .sources import REGIONS

REGION_ORDER = {region: index for index, region in enumerate(REGIONS)}
REGION_SORTS = ("regiao", "restantes", "eleitorado", "apurado", "saldo")


def aggregate_regions(
    br: Result | None, ufs: dict[str, Result], progress: Progress | None, baseline: Baseline
) -> dict:
    out = {"regioes": [], "aviso": (
        "Agregados dos arquivos das UFs, não um arquivo regional oficial do TSE. "
        "Votos restantes são estimativas do modelo; swing parcial pode refletir a ordem das UFs. "
        "Comparecimento parcial refere-se apenas às seções totalizadas."
    )}
    if br is None or (pair := finalists(br)) is None:
        out["consistencia"] = {"status": "sem_dados", "aviso": "Sem resultado nacional com dois finalistas."}
        return out
    a, b = pair
    missing_all, delayed = [], []
    totals = {"validos": 0, "secoes": 0, "votos": {a: 0, b: 0}}
    for name, members in REGIONS.items():
        votes = {a: 0, b: 0}
        ts = st = electorate = remaining_electorate = turnout = abstention = valid = 0
        baseline_votes = {a: 0, b: 0}
        baseline_turnout = baseline_abstention = 0
        remaining_valid = 0.0
        missing, missing_counts, prior_only = [], [], []
        finalized = 0
        for uf in members:
            base = baseline.ufs[uf]
            result = ufs.get(uf)
            prog = progress.ufs.get(uf) if progress else None
            counts = result.counts if result else (prog.counts if prog else None)
            known_votes = result is not None or (prog is not None and prog.counts.secoes_totalizadas == 0)
            if not known_votes:
                missing.append(uf)
            if counts is None:
                missing_counts.append(uf)
            else:
                ts += counts.secoes_total
                st += counts.secoes_totalizadas
                electorate += counts.eleitorado
                remaining_electorate += counts.eleitorado_nao_totalizado
                turnout += counts.comparecimento
                abstention += counts.abstencao
            if result:
                valid += result.votos_validos
                for numero in pair:
                    candidate = result.candidate(numero)
                    votes[numero] += candidate.votos if candidate else 0
                finalized += result.andamento == "f"
            else:
                prior_only.append(uf)
            if prog and (result is None or result.totalizado_em != prog.totalizado_em
                         or result.counts.secoes_totalizadas != prog.counts.secoes_totalizadas):
                # Arquivo de acompanhamento pode chegar antes ou depois do resultado.
                if result is not None or prog.counts.secoes_totalizadas:
                    delayed.append(uf)
            remaining_valid += _remaining_valid_votes(base, result, prog.counts if prog else None)
            for numero in pair:
                baseline_votes[numero] += base.votos.get(numero, 0)
            baseline_turnout += base.comparecimento
            baseline_abstention += base.abstencao
        complete = not missing
        pct = {numero: (100 * votes[numero] / valid if complete and valid else None) for numero in pair}
        baseline_total = sum(baseline_votes.values())
        prior_a = 100 * baseline_votes[a] / baseline_total if baseline_total else None
        share_now = 100 * votes[a] / sum(votes.values()) if complete and sum(votes.values()) else None
        leader = max(votes, key=votes.get) if complete and votes[a] != votes[b] else None
        out["regioes"].append({
            "regiao": name, "ufs": list(members), "ufs_finalizadas": finalized,
            "ufs_sem_resultado": missing, "dados_completos": complete,
            "andamento": "f" if finalized == len(members) else ("p" if st else "n"),
            "secoes": {"total": ts if not missing_counts else None, "totalizadas": st,
                       "pct": 100 * st / ts if ts and not missing_counts else None},
            "eleitorado": {"total": electorate if not missing_counts else None,
                           "nao_totalizado": remaining_electorate if not missing_counts else None,
                           "comparecimento": turnout, "abstencao": abstention,
                           "pct_comparecimento": 100 * turnout / (turnout + abstention)
                           if complete and turnout + abstention else None},
            "votos_validos": valid, "votos": votes, "pct": pct, "lider": leader,
            "saldo_candidato_a_votos": votes[a] - votes[b] if complete else None,
            "share_1t_candidato_a": prior_a,
            "swing_pp": share_now - prior_a if share_now is not None and prior_a is not None else None,
            "pct_comparecimento_1t": 100 * baseline_turnout / (baseline_turnout + baseline_abstention)
            if baseline_turnout + baseline_abstention else None,
            "validos_restantes_estimados": round(remaining_valid),
            "ufs_restante_base_1t": prior_only,
        })
        missing_all.extend(missing)
        totals["validos"] += valid
        totals["secoes"] += st
        for numero in pair:
            totals["votos"][numero] += votes[numero]
    diff_valid = totals["validos"] - br.votos_validos
    diff_sections = totals["secoes"] - br.counts.secoes_totalizadas
    diff_votes = {numero: totals["votos"][numero] - (br.candidate(numero).votos if br.candidate(numero) else 0)
                  for numero in pair}
    if missing_all:
        status = "incompleto"
        warning = "Cobertura incompleta: faltam resultados de " + ", ".join(uf.upper() for uf in missing_all)
    elif diff_valid or diff_sections or any(diff_votes.values()) or delayed:
        status = "defasado"
        warning = (f"UFs e Brasil não sincronizados: soma das UFs − BR = {diff_valid:+,} votos válidos "
                   f"e {diff_sections:+,} seções. O placar nacional continua sendo o arquivo BR do TSE.")
        if delayed:
            warning += " Acompanhamento divergente: " + ", ".join(uf.upper() for uf in delayed) + "."
    else:
        status = "sincronizado"
        warning = "Soma das UFs confere com os votos e seções do arquivo nacional."
    out["consistencia"] = {
        "status": status, "aviso": warning, "ufs_sem_resultado": missing_all,
        "ufs_divergentes_acompanhamento": delayed,
        "diferenca_validos_ufs_menos_br": diff_valid,
        "diferenca_secoes_ufs_menos_br": diff_sections,
        "diferenca_votos_ufs_menos_br": diff_votes,
    }
    return out


def sorted_regions(rows: list[dict], sort: str) -> list[dict]:
    if sort == "restantes":
        return sorted(rows, key=lambda row: -row["validos_restantes_estimados"])
    if sort == "eleitorado":
        return sorted(rows, key=lambda row: (row["eleitorado"]["total"] is None,
                                            -(row["eleitorado"]["total"] or 0)))
    if sort == "apurado":
        return sorted(rows, key=lambda row: (row["secoes"]["pct"] is None, -(row["secoes"]["pct"] or 0)))
    if sort == "saldo":
        return sorted(rows, key=lambda row: (row["saldo_candidato_a_votos"] is None,
                                            -abs(row["saldo_candidato_a_votos"] or 0)))
    return sorted(rows, key=lambda row: REGION_ORDER[row["regiao"]])
