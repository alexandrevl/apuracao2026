"""Resultado final do 1º turno (eleição 6257) por UF, usado como referência da projeção.

Os arquivos são finais e imutáveis; ficam em ``data/baseline/<abr>.json`` depois do
primeiro download.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .parse import Result, parse_result
from .sources import CIDADES, ELEICAO_T1, UFS, SourceError, TseSource
from .store import write_json_atomic


@dataclass(frozen=True)
class BaselineUf:
    abr: str
    comparecimento: int
    abstencao: int
    votos_validos: int
    votos: dict[str, int]
    pct: dict[str, float]

    @property
    def turnout(self) -> float:
        base = self.comparecimento + self.abstencao
        return self.comparecimento / base if base else 0.0

    @property
    def valid_rate(self) -> float:
        return self.votos_validos / self.comparecimento if self.comparecimento else 0.0

    def two_party_share(self, a: str, b: str) -> float:
        """Fração de ``a`` entre os dois finalistas (demais redistribuídos proporcionalmente)."""
        va, vb = self.votos.get(a, 0), self.votos.get(b, 0)
        return va / (va + vb) if va + vb else 0.5


@dataclass(frozen=True)
class Baseline:
    br: BaselineUf
    ufs: dict[str, BaselineUf]
    nomes: dict[str, str]
    cidades: dict[str, BaselineUf] = field(default_factory=dict)  # código TSE → 1º turno do município


def _from_result(result: Result) -> BaselineUf:
    return BaselineUf(
        abr=result.abr,
        comparecimento=result.counts.comparecimento,
        abstencao=result.counts.abstencao,
        votos_validos=result.votos_validos,
        votos={c.numero: c.votos for c in result.candidates},
        pct={c.numero: c.pct for c in result.candidates},
    )


def load_baseline(data_dir: Path, *, download: bool = True) -> Baseline | None:
    """Lê o cache; com download=False nunca acessa a rede nem cria arquivos."""
    cache = data_dir / "baseline"
    missing = [abr for abr in ("br", *UFS) if not (cache / f"{abr}.json").exists()]
    if missing and not download:
        return None
    if missing:
        source = TseSource(eleicao=ELEICAO_T1, name="t1")
        try:
            for abr in missing:
                raw = source.result(abr).raw
                result = parse_result(raw)
                if result.abr != abr or result.eleicao != ELEICAO_T1 or result.andamento != "f":
                    raise SourceError(f"1º turno de {abr} ainda não está finalizado no TSE")
                write_json_atomic(cache / f"{abr}.json", raw)
        finally:
            source.close()

    results = {
        abr: parse_result(json.loads((cache / f"{abr}.json").read_text(encoding="utf-8")))
        for abr in ("br", *UFS)
    }
    for abr, result in results.items():
        if result.abr != abr or result.eleicao != ELEICAO_T1 or result.andamento != "f":
            raise SourceError(f"Base de {abr} não é um resultado final do 1º turno (and={result.andamento})")
    return Baseline(
        br=_from_result(results["br"]),
        ufs={abr: _from_result(results[abr]) for abr in UFS},
        nomes={c.numero: c.nome for c in results["br"].candidates},
        cidades=load_city_baseline(data_dir, download=download),
    )


def load_city_baseline(data_dir: Path, *, download: bool = True) -> dict[str, BaselineUf]:
    """1º turno das maiores cidades. Opcional: falha de rede deixa a projeção só por UF."""
    cache = data_dir / "baseline" / "mu"
    missing = [(uf, cd) for uf, cd, _ in CIDADES if not (cache / f"{cd}.json").exists()]
    if missing and download:
        source = TseSource(eleicao=ELEICAO_T1, name="t1")
        try:
            for uf, cd in missing:
                raw = source.city_result(uf, cd).raw
                result = parse_result(raw)
                if result.abr != cd or result.eleicao != ELEICAO_T1 or result.andamento != "f":
                    raise SourceError(f"1º turno de {uf}{cd} não é resultado final")
                write_json_atomic(cache / f"{cd}.json", raw)
        except SourceError:
            pass
        finally:
            source.close()
    out: dict[str, BaselineUf] = {}
    for _, cd, _ in CIDADES:
        path = cache / f"{cd}.json"
        if path.exists():
            result = parse_result(json.loads(path.read_text(encoding="utf-8")))
            if result.abr == cd and result.eleicao == ELEICAO_T1 and result.andamento == "f":
                out[cd] = _from_result(result)
    return out
