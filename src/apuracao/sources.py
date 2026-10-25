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


def progress_path(eleicao: str) -> str:
    return f"{eleicao}/dados/br/br-e{int(eleicao):06d}-ab.json"


def result_path(eleicao: str, abr: str) -> str:
    if abr != "br" and abr not in UFS:
        raise ValueError(f"abrangência desconhecida: {abr}")
    return f"{eleicao}/dados/{abr}/{abr}-c{CARGO_PRESIDENTE}-e{int(eleicao):06d}-u.json"


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
