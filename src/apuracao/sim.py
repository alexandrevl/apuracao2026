"""Simulação da noite de apuração gerando arquivos no formato real do TSE (EA14/EA20).

Parte da estrutura real do 2º turno (candidatos, seções, eleitorado por UF) e do
resultado do 1º turno por UF. Cada UF começa e termina em horários diferentes,
atualiza em lotes, e tem um viés inicial que se dissipa — para exercitar viradas,
marcos, projeção e alertas. O relógio simulado começa às 17:00 de 25/10/2026 e
anda ``speed`` vezes mais rápido que o real.
"""

from __future__ import annotations

import copy
import json
import math
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .baseline import Baseline
from .sources import ELEICAO_T2, UFS, Fetched, TseSource, progress_path, result_path
from .store import write_json_atomic

SIM_START = datetime(2026, 10, 25, 17, 0, 0)


def _pct2(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def _pct9(x: float) -> str:
    if x in (0, 100):
        return str(int(x))
    return f"{x:.9f}".replace(".", ",")


def load_templates(data_dir: Path) -> tuple[dict, dict]:
    """Arquivos reais do 2º turno (zerados) usados como molde; cacheados em data/templates."""
    cache = data_dir / "templates"
    cache.mkdir(parents=True, exist_ok=True)
    files = {"br-u": result_path(ELEICAO_T2, "br"), "br-ab": progress_path(ELEICAO_T2)}
    out = {}
    missing = [k for k in files if not (cache / f"{k}.json").exists()]
    if missing:
        source = TseSource(ELEICAO_T2, name="templates")
        try:
            for key in missing:
                fetched = source.result("br") if key == "br-u" else source.progress()
                write_json_atomic(cache / f"{key}.json", fetched.raw)
        finally:
            source.close()
    for key in files:
        out[key] = json.loads((cache / f"{key}.json").read_text(encoding="utf-8"))
    return out["br-u"], out["br-ab"]


@dataclass
class _UfPlan:
    abr: str
    secoes: int
    eleitorado: int
    start_min: float
    duration_min: float
    cadence_min: float
    phase_min: float
    share_final: float  # fração final do candidato A
    early_bias: float
    turnout: float
    valid_rate: float
    blank_frac: float


@dataclass
class _UfState:
    frac: float
    at: datetime | None


class SimSource:
    def __init__(self, data_dir: Path, baseline: Baseline, speed: float = 20.0, seed: int | None = None,
                 start_offset_min: float = 0.0):
        if not math.isfinite(speed) or speed <= 0 or not math.isfinite(start_offset_min):
            raise ValueError("Velocidade deve ser positiva e offset deve ser finito")
        self.name = "sim"
        self.eleicao = ELEICAO_T2
        self._br_tpl, self._ab_tpl = load_templates(data_dir)
        self._speed = speed
        self._t0 = time.monotonic()
        self._offset = timedelta(minutes=start_offset_min)
        self._rng = random.Random(seed)
        self._idg = 9_000_000
        self._signatures: dict[str, tuple] = {}
        self._idgs: dict[str, str] = {}

        cands = sorted(
            (c for agr in self._br_tpl["carg"][0]["agr"] for par in agr["par"] for c in par["cand"]),
            key=lambda c: int(c["seq"]),
        )
        self._a, self._b = cands[0]["n"], cands[1]["n"]
        swing = self._rng.gauss(0, 0.025)
        ab = {item["cdabr"]: item for item in self._ab_tpl["abr"]}
        self._plans: dict[str, _UfPlan] = {}
        for abr in UFS:
            base = baseline.ufs[abr]
            big = int(ab[abr]["e"]["te"]) > 10_000_000
            start = self._rng.uniform(25, 60) if abr == "zz" else self._rng.uniform(0, 12)
            duration = self._rng.uniform(150, 240) if big or abr == "zz" else self._rng.uniform(70, 170)
            final = min(0.95, max(0.05, base.two_party_share(self._a, self._b) + swing + self._rng.gauss(0, 0.012)))
            self._plans[abr] = _UfPlan(
                abr=abr,
                secoes=int(ab[abr]["s"]["ts"]),
                eleitorado=int(ab[abr]["e"]["te"]),
                start_min=start,
                duration_min=duration,
                cadence_min=self._rng.uniform(0.5, 2.5),
                phase_min=self._rng.uniform(0, 2.5),
                share_final=final,
                early_bias=self._rng.gauss(0, 0.05),
                turnout=max(0.5, base.turnout + self._rng.gauss(-0.01, 0.01)),
                valid_rate=base.valid_rate,
                # Brancos ≈ 38,5% dos votos não válidos no 1º turno nacional (2,30 mi de 5,98 mi).
                blank_frac=0.385,
            )

    # ---- relógio e estado ----------------------------------------------------

    def now(self) -> datetime:
        elapsed = (time.monotonic() - self._t0) * self._speed
        return SIM_START + self._offset + timedelta(seconds=elapsed)

    def _uf_state(self, plan: _UfPlan, now: datetime) -> _UfState:
        minutes = (now - SIM_START).total_seconds() / 60
        # A UF só publica em lotes: o tempo efetivo é quantizado pela cadência dela.
        q = math.floor((minutes - plan.phase_min) / plan.cadence_min) * plan.cadence_min + plan.phase_min
        x = (q - plan.start_min) / plan.duration_min
        if x <= 0:
            return _UfState(0.0, None)
        if x >= 1:
            return _UfState(1.0, SIM_START + timedelta(minutes=plan.start_min + plan.duration_min))
        frac = 1 - (1 - x) ** 1.6  # rápido no começo, cauda longa no fim
        return _UfState(frac, SIM_START + timedelta(minutes=q))

    def _uf_numbers(self, plan: _UfPlan, state: _UfState) -> dict:
        f = state.frac
        st = round(plan.secoes * f)
        est = plan.eleitorado if st == plan.secoes else round(plan.eleitorado * f)
        c = round(est * plan.turnout)
        vv = round(c * plan.valid_rate)
        vb = round((c - vv) * plan.blank_frac)
        share = min(0.97, max(0.03, plan.share_final + plan.early_bias * (1 - f) ** 2))
        va = round(vv * share)
        return {"ts": plan.secoes, "st": st, "te": plan.eleitorado, "est": est, "c": c, "a": est - c,
                "vv": vv, "vb": vb, "tvn": c - vv - vb, "va": va, "vbb": vv - va,
                "frac": f, "at": state.at}

    def _all_numbers(self) -> tuple[datetime, dict[str, dict]]:
        now = self.now()
        return now, {abr: self._uf_numbers(p, self._uf_state(p, now)) for abr, p in self._plans.items()}

    @staticmethod
    def _sum(nums: dict[str, dict]) -> dict:
        keys = ("ts", "st", "te", "est", "c", "a", "vv", "vb", "tvn", "va", "vbb")
        total = {k: sum(n[k] for n in nums.values()) for k in keys}
        ats = [n["at"] for n in nums.values() if n["at"]]
        total["at"] = max(ats) if ats else None
        total["frac"] = total["st"] / total["ts"] if total["ts"] else 0
        return total

    # ---- montagem dos arquivos -----------------------------------------------

    @staticmethod
    def _fill_counts(s: dict, e: dict, n: dict) -> None:
        pst = 100 * n["st"] / n["ts"] if n["ts"] else 0
        s.update(ts=str(n["ts"]), st=str(n["st"]), pst=_pct2(pst), pstn=_pct9(pst),
                 snt=str(n["ts"] - n["st"]), psnt=_pct2(100 - pst), psntn=_pct9(100 - pst))
        pest = 100 * n["est"] / n["te"] if n["te"] else 0
        pc = 100 * n["c"] / n["est"] if n["est"] else 0
        e.update(te=str(n["te"]), est=str(n["est"]), pest=_pct2(pest), pestn=_pct9(pest),
                 esnt=str(n["te"] - n["est"]), pesnt=_pct2(100 - pest), pesntn=_pct9(100 - pest),
                 c=str(n["c"]), pc=_pct2(pc), pcn=_pct9(pc), a=str(n["a"]),
                 pa=_pct2(100 - pc if n["est"] else 0), pan=_pct9(100 - pc if n["est"] else 0))

    @staticmethod
    def _andamento(n: dict) -> str:
        return "n" if n["st"] == 0 else ("f" if n["st"] == n["ts"] else "p")

    def _next_idg(self, path: str, signature: tuple) -> str | None:
        """Novo idg se o conteúdo mudou; None se é igual ao último entregue (como um 304)."""
        if self._signatures.get(path) == signature:
            return None
        self._signatures[path] = signature
        self._idg += 1
        self._idgs[path] = str(self._idg)
        return self._idgs[path]

    def _stamp(self, raw: dict, idg: str, gen: datetime, at: datetime | None, andamento: str) -> None:
        raw.update(f="s", idg=idg, dg=gen.strftime("%d/%m/%Y"), hg=gen.strftime("%H:%M:%S"),
                   dt=at.strftime("%d/%m/%Y") if at else "", ht=at.strftime("%H:%M:%S") if at else "")
        raw["and"] = andamento

    def progress(self) -> Fetched:
        path = progress_path(self.eleicao)
        now, nums = self._all_numbers()
        idg = self._next_idg(path, tuple((abr, n["st"]) for abr, n in nums.items()))
        if idg is None:
            return Fetched(None, path)
        raw = copy.deepcopy(self._ab_tpl)
        total = self._sum(nums)
        all_final = all(self._andamento(n) == "f" for n in nums.values())
        for item in raw["abr"]:
            n = total if item["tpabr"] == "br" else nums[item["cdabr"]]
            self._fill_counts(item["s"], item["e"], n)
            at = n["at"]
            item["and"] = ("f" if all_final else ("n" if n["st"] == 0 else "p")) if item["tpabr"] == "br" \
                else self._andamento(n)
            item["dt"] = at.strftime("%d/%m/%Y") if at else ""
            item["ht"] = at.strftime("%H:%M:%S") if at else ""
        self._stamp(raw, idg, now, total["at"], "p")
        return Fetched(raw, path)

    def result(self, abr: str) -> Fetched:
        path = result_path(self.eleicao, abr)
        now, nums = self._all_numbers()
        total = self._sum(nums)
        n = total if abr == "br" else nums[abr]
        idg = self._next_idg(path, (n["st"],))
        if idg is None:
            return Fetched(None, path)

        raw = copy.deepcopy(self._br_tpl)
        raw["tpabr"] = "br" if abr == "br" else "uf"
        raw["cdabr"] = abr
        self._fill_counts(raw["s"], raw["e"], n)
        all_final = all(self._andamento(x) == "f" for x in nums.values())
        andamento = ("f" if all_final else self._andamento(n)) if abr == "br" else self._andamento(n)
        vv = n["vv"]
        votes = {self._a: n["va"], self._b: n["vbb"]}
        winner = max(votes, key=votes.get) if abr == "br" and all_final else None
        for agr in raw["carg"][0]["agr"]:
            for par in agr["par"]:
                for c in par["cand"]:
                    v = votes[c["n"]]
                    p = 100 * v / vv if vv else 0
                    c.update(vap=str(v), pvap=_pct2(p), pvapn=_pct9(p))
                    if winner:
                        c.update(e="s" if c["n"] == winner else "n",
                                 st="Eleito" if c["n"] == winner else "Não eleito")
        v = raw["v"]
        tv = n["c"]
        v.update(tv=str(tv), vvc=str(vv), vv=str(vv), vnom=str(vv), vb=str(n["vb"]),
                 tvn=str(n["tvn"]), vn=str(n["tvn"]))
        if abr == "br":
            raw["tf"] = "s" if all_final else "n"
            lead = abs(n["va"] - n["vbb"])
            raw["md"] = "e" if vv and lead > n["te"] - n["est"] else "n"
        self._stamp(raw, idg, now, n["at"], andamento)
        return Fetched(raw, path)

    def close(self) -> None:
        pass
