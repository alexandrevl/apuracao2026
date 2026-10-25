"""Fontes de dados: TSE ao vivo (com ETag) e interface comum usada pelo simulador.

Regras do TSE respeitadas aqui: no máximo ``max_rps`` requisições por segundo
(o limite oficial é 100/s por IP), URLs montadas só a partir da lista fixa de UFs,
nenhuma descoberta por tentativa (404 repetido pode bloquear o IP).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import httpx

BASE_URL = "https://resultados.tse.jus.br/oficial/ele2026"
ELEICAO_T1 = "6257"  # Eleição Ordinária Federal 2026, 1º turno
ELEICAO_T2 = "6258"  # Eleição Ordinária Federal 2026, 2º turno
CARGO_PRESIDENTE = "0001"

# 27 UFs + exterior. Ordem alfabética, como no arquivo de acompanhamento.
UFS = (
    "ac", "al", "am", "ap", "ba", "ce", "df", "es", "go", "ma", "mg", "ms", "mt", "pa",
    "pb", "pe", "pi", "pr", "rj", "rn", "ro", "rr", "rs", "sc", "se", "sp", "to", "zz",
)
REGIONS = {
    "Nordeste": ("al", "ba", "ce", "ma", "pb", "pe", "pi", "rn", "se"),
    "Sudeste": ("es", "mg", "rj", "sp"),
    "Centro-Oeste": ("df", "go", "ms", "mt"),
    "Norte": ("ac", "am", "ap", "pa", "ro", "rr", "to"),
    "Sul": ("pr", "rs", "sc"),
    "Exterior": ("zz",),
}
UF_REGION = {uf: region for region, ufs in REGIONS.items() for uf in ufs}

# 80 maiores eleitorados municipais do 1º turno de 2026 (~35% do país), códigos TSE do arquivo
# oficial ``6258/config/mun-e006258-cm.json``; ranking pelos arquivos finais do 1º turno de 108
# candidatas. Brasília não entra: o DF já é uma abrangência.
CIDADES = (
    ("sp", "71072", "São Paulo"), ("rj", "60011", "Rio de Janeiro"), ("mg", "41238", "Belo Horizonte"),
    ("ba", "38490", "Salvador"), ("ce", "13897", "Fortaleza"), ("am", "02550", "Manaus"),
    ("pr", "75353", "Curitiba"), ("pe", "25313", "Recife"), ("rs", "88013", "Porto Alegre"),
    ("pa", "04278", "Belém"), ("go", "93734", "Goiânia"), ("sp", "64777", "Guarulhos"),
    ("sp", "62910", "Campinas"), ("ma", "09210", "São Luís"), ("rj", "58971", "São Gonçalo"),
    ("al", "27855", "Maceió"), ("ms", "90514", "Campo Grande"), ("rj", "58335", "Duque de Caxias"),
    ("sp", "70750", "São Bernardo do Campo"), ("rj", "58696", "Nova Iguaçu"), ("pi", "12190", "Teresina"),
    ("pb", "20516", "João Pessoa"), ("sp", "67890", "Osasco"), ("sp", "70572", "Santo André"),
    ("rn", "17612", "Natal"), ("sp", "70998", "São José dos Campos"), ("mg", "54038", "Uberlândia"),
    ("sp", "71455", "Sorocaba"), ("pe", "24570", "Jaboatão dos Guararapes"),
    ("sp", "69698", "Ribeirão Preto"), ("mg", "43710", "Contagem"), ("mt", "90670", "Cuiabá"),
    ("sc", "81795", "Joinville"), ("ba", "35157", "Feira de Santana"), ("sc", "81051", "Florianópolis"),
    ("se", "31054", "Aracaju"), ("rj", "58653", "Niterói"), ("mg", "47333", "Juiz de Fora"),
    ("pr", "76678", "Londrina"), ("rj", "58190", "Campos dos Goytacazes"), ("ro", "00035", "Porto Velho"),
    ("es", "56995", "Serra"), ("pa", "04154", "Ananindeua"), ("sp", "70718", "Santos"),
    ("es", "57037", "Vila Velha"), ("rj", "58041", "Belford Roxo"), ("sp", "70971", "São José do Rio Preto"),
    ("rs", "85995", "Caxias do Sul"), ("go", "92274", "Aparecida de Goiânia"),
    ("rj", "59013", "São João de Meriti"), ("sp", "66192", "Jundiaí"), ("sp", "67130", "Mogi das Cruzes"),
    ("sp", "63770", "Diadema"), ("ap", "06050", "Macapá"), ("sp", "68756", "Piracicaba"),
    ("sp", "66893", "Mauá"), ("pb", "19810", "Campina Grande"), ("pr", "76910", "Maringá"),
    ("pe", "24910", "Olinda"), ("mg", "41335", "Betim"), ("sp", "62138", "Barueri"),
    ("sp", "63134", "Carapicuíba"), ("go", "92215", "Anápolis"), ("mg", "48658", "Montes Claros"),
    ("sp", "62197", "Bauru"), ("es", "56251", "Cariacica"), ("ac", "01392", "Rio Branco"),
    ("sc", "80470", "Blumenau"), ("es", "57053", "Vitória"), ("ba", "39659", "Vitória da Conquista"),
    ("sp", "69213", "Praia Grande"), ("pr", "77771", "Ponta Grossa"), ("sp", "65633", "Itaquaquecetuba"),
    ("sp", "71218", "São Vicente"), ("pe", "23817", "Caruaru"), ("pa", "05355", "Santarém"),
    ("rs", "85898", "Canoas"), ("ce", "13730", "Caucaia"), ("pe", "25216", "Petrolina"),
    ("rs", "87912", "Pelotas"),
)
CIDADE_UF = {cd: uf for uf, cd, _ in CIDADES}  # código TSE do município (único no país, = cdabr do arquivo) → UF


def progress_path(eleicao: str) -> str:
    return f"{eleicao}/dados/br/br-e{int(eleicao):06d}-ab.json"


def result_path(eleicao: str, abr: str) -> str:
    if abr != "br" and abr not in UFS:
        raise ValueError(f"abrangência desconhecida: {abr}")
    return f"{eleicao}/dados/{abr}/{abr}-c{CARGO_PRESIDENTE}-e{int(eleicao):06d}-u.json"


def city_result_path(eleicao: str, uf: str, cd: str) -> str:
    if CIDADE_UF.get(cd) != uf:
        raise ValueError(f"município fora da lista: {uf}{cd}")
    return f"{eleicao}/dados/{uf}/{uf}{cd}-c{CARGO_PRESIDENTE}-e{int(eleicao):06d}-u.json"


class SourceError(Exception):
    """Falha temporária ao obter um arquivo (rede, 5xx, 404)."""


class SourceBlocked(SourceError):
    """O servidor recusou o acesso (403/429): é preciso esperar antes de tentar de novo."""


@dataclass(frozen=True)
class Fetched:
    raw: dict | None  # None quando o arquivo não mudou desde a última leitura
    path: str

    @property
    def changed(self) -> bool:
        return self.raw is not None


class Source(Protocol):
    name: str
    eleicao: str

    def progress(self) -> Fetched: ...

    def result(self, abr: str) -> Fetched: ...

    def close(self) -> None: ...


class CachedSource:
    """Identidade da fonte para consultas locais; não cria cliente HTTP nem simulador."""

    def __init__(self, name: str, eleicao: str):
        self.name, self.eleicao = name, eleicao

    def progress(self) -> Fetched:
        raise SourceError("Consulta local: acesso à rede desabilitado")

    def result(self, abr: str) -> Fetched:
        raise SourceError("Consulta local: acesso à rede desabilitado")

    def close(self) -> None:
        pass


class TseSource:
    def __init__(self, eleicao: str = ELEICAO_T2, name: str = "tse", max_rps: float = 10.0):
        if max_rps <= 0:
            raise ValueError("max_rps deve ser positivo")
        self.name = name
        self.eleicao = eleicao
        self._interval = 1.0 / max_rps
        self._last_request = 0.0
        self._etags: dict[str, str] = {}
        self._client = httpx.Client(
            base_url=BASE_URL,
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={"User-Agent": "apuracao-pessoal/0.1", "Accept": "application/json"},
            follow_redirects=True,
        )

    def progress(self) -> Fetched:
        return self._get(progress_path(self.eleicao))

    def result(self, abr: str) -> Fetched:
        return self._get(result_path(self.eleicao, abr))

    def city_result(self, uf: str, cd: str) -> Fetched:
        return self._get(city_result_path(self.eleicao, uf, cd))

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str) -> Fetched:
        wait = self._last_request + self._interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

        headers = {"If-None-Match": self._etags[path]} if path in self._etags else {}
        try:
            response = self._client.get(path, headers=headers)
        except httpx.HTTPError as exc:
            raise SourceError(f"{path}: {exc.__class__.__name__}: {exc}") from exc

        if response.status_code == 304:
            if path not in self._etags:
                raise SourceError(f"{path}: HTTP 304 sem versão local")
            return Fetched(None, path)
        if response.status_code in (403, 429):
            raise SourceBlocked(f"{path}: HTTP {response.status_code} (possível bloqueio do TSE)")
        if response.status_code != 200:
            raise SourceError(f"{path}: HTTP {response.status_code}")
        try:
            raw = response.json()
        except ValueError as exc:
            raise SourceError(f"{path}: JSON inválido") from exc
        if not isinstance(raw, dict):
            raise SourceError(f"{path}: JSON não é um objeto")
        if etag := response.headers.get("etag"):
            self._etags[path] = etag
        else:
            self._etags.pop(path, None)
        return Fetched(raw, path)
