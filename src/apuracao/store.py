"""Histórico local: SQLite (para consultas) + JSON bruto de cada versão recebida (auditoria).

Um banco por fonte (``data/<fonte>/apuracao.db``) para simulações nunca se misturarem
com o ao vivo. Um snapshot só é gravado quando o TSE gera nova versão (``idg``).
"""

from __future__ import annotations

import json
import sqlite3
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .parse import Result, parse_result
from .sources import CIDADE_UF, UFS

if TYPE_CHECKING:
    from .analysis import Projection
    from .baseline import Baseline
    from .events import Event

SCHEMA = """
create table if not exists snapshots (
    id integer primary key,
    captured_at text not null,          -- hora local da captura (ISO)
    abr text not null,                  -- br, uf (ac..to) ou zz (exterior)
    idg text not null,                  -- id de geração do arquivo no TSE
    gerado_em text,                     -- dg/hg do arquivo
    totalizado_em text,                 -- dt/ht: hora da totalização refletida
    andamento text,                     -- n, p, f
    md text,                            -- matematicamente definido (e/s/n)
    secoes_total integer,
    secoes_totalizadas integer,
    pct_secoes real,
    eleitorado integer,
    eleitorado_nao_totalizado integer,
    comparecimento integer,
    abstencao integer,
    votos_validos integer,
    brancos integer,
    nulos integer,
    total_votos integer,
    raw_path text,
    unique (abr, idg)
);
create table if not exists candidate_votes (
    snapshot_id integer not null references snapshots(id),
    numero text not null,
    nome text,
    partido text,
    votos integer,
    pct real,                           -- % dos votos válidos
    eleito integer,
    primary key (snapshot_id, numero)
);
create table if not exists projections (
    id integer primary key,
    captured_at text not null,
    totalizado_em text,
    pct_secoes_br real,
    numero text not null,
    nome text,
    pct real,
    pct_min real,
    pct_max real,
    swing_pp real,
    votos integer,                      -- votos projetados do candidato
    votos_atuais integer,               -- votos oficiais do candidato no momento
    validos_projetados integer,
    validos_atuais integer
);
create table if not exists events (
    id integer primary key,
    captured_at text not null,
    totalizado_em text,
    type text not null,
    abr text,
    message text not null,
    payload text
);
create table if not exists baseline_t1 (
    abr text not null,
    numero text not null,
    nome text,
    votos integer,
    pct real,
    primary key (abr, numero)
);
create table if not exists city_snapshots (
    id integer primary key,
    captured_at text not null,
    cd text not null,                   -- código TSE do município
    uf text not null,
    idg text not null,
    totalizado_em text,
    pct_secoes real,
    votos_validos integer,
    votos_a integer,                    -- 13
    votos_b integer,                    -- 22
    raw_path text,
    unique (cd, idg)
);
create index if not exists idx_snapshots_abr on snapshots(abr, totalizado_em);
create view if not exists v_votos as
    select s.captured_at, s.totalizado_em, s.abr, s.pct_secoes, s.andamento,
           c.numero, c.nome, c.votos, c.pct
    from snapshots s join candidate_votes c on c.snapshot_id = s.id;
"""


def write_json_atomic(path: Path, raw: dict) -> None:
    """Publica JSON completo antes de referenciá-lo em um commit SQLite."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".apuracao-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(raw, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


@dataclass(frozen=True)
class PacePoint:
    totalizado_em: datetime
    secoes_totalizadas: int


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.raw_dir = root / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = root / "apuracao.db"
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("pragma journal_mode=wal")
        self._db.executescript(SCHEMA)
        # Bancos criados antes das colunas de votos na projeção.
        cols = {r[1] for r in self._db.execute("pragma table_info(projections)")}
        for col in ("votos", "votos_atuais", "validos_projetados", "validos_atuais"):
            if col not in cols:
                self._db.execute(f"alter table projections add column {col} integer")

    def close(self) -> None:
        self._db.close()

    # ---- escrita -------------------------------------------------------------

    def save_result(self, result: Result, raw: dict, captured_at: datetime) -> bool:
        """Grava a versão se ainda não existir. Retorna True se for nova."""
        if result.abr not in ("br", *UFS) or not result.idg or any(
            char in result.idg for char in ("/", "\\", "\x00")
        ) or result.idg in (".", ".."):
            raise ValueError("Abrangência ou identificador de resultado inválido")
        raw_path = self.raw_dir / result.abr / f"{result.idg}.json"
        with self._lock, self._db:
            cur = self._db.execute(
                """insert or ignore into snapshots (captured_at, abr, idg, gerado_em, totalizado_em,
                   andamento, md, secoes_total, secoes_totalizadas, pct_secoes, eleitorado,
                   eleitorado_nao_totalizado, comparecimento, abstencao, votos_validos, brancos,
                   nulos, total_votos, raw_path)
                   values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _iso(captured_at), result.abr, result.idg, _iso(result.gerado_em),
                    _iso(result.totalizado_em), result.andamento, result.matematicamente_definido,
                    result.counts.secoes_total, result.counts.secoes_totalizadas,
                    result.counts.pct_secoes, result.counts.eleitorado,
                    result.counts.eleitorado_nao_totalizado, result.counts.comparecimento,
                    result.counts.abstencao, result.votos_validos, result.brancos, result.nulos,
                    result.total_votos, str(raw_path.relative_to(self.root)),
                ),
            )
            if cur.rowcount == 0:
                return False
            self._db.executemany(
                "insert into candidate_votes values (?,?,?,?,?,?,?)",
                [
                    (cur.lastrowid, c.numero, c.nome, c.partido, c.votos, c.pct, int(c.eleito))
                    for c in result.candidates
                ],
            )
            write_json_atomic(raw_path, raw)
        return True

    def save_city_result(self, uf: str, result: Result, raw: dict, captured_at: datetime) -> bool:
        """Município da lista ``CIDADES``: tabela própria, fora de ``snapshots`` (que é BR/UF)."""
        if CIDADE_UF.get(result.abr) != uf or not result.idg or any(
            char in result.idg for char in ("/", "\\", "\x00")
        ) or result.idg in (".", ".."):
            raise ValueError("Município ou identificador de resultado inválido")
        raw_path = self.raw_dir / "mu" / result.abr / f"{result.idg}.json"
        votes = {c.numero: c.votos for c in result.candidates}
        with self._lock, self._db:
            cur = self._db.execute(
                """insert or ignore into city_snapshots (captured_at, cd, uf, idg, totalizado_em, pct_secoes,
                   votos_validos, votos_a, votos_b, raw_path) values (?,?,?,?,?,?,?,?,?,?)""",
                (_iso(captured_at), result.abr, uf, result.idg, _iso(result.totalizado_em),
                 result.counts.pct_secoes, result.votos_validos, votes.get("13"), votes.get("22"),
                 str(raw_path.relative_to(self.root))),
            )
            if cur.rowcount == 0:
                return False
            write_json_atomic(raw_path, raw)
        return True

    def save_projection(self, projection: Projection, br: Result, captured_at: datetime) -> None:
        with self._lock, self._db:
            self._db.executemany(
                """insert into projections (captured_at, totalizado_em, pct_secoes_br, numero, nome,
                   pct, pct_min, pct_max, swing_pp, votos, votos_atuais, validos_projetados, validos_atuais)
                   values (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (_iso(captured_at), _iso(br.totalizado_em), br.counts.pct_secoes, c.numero,
                     c.nome, c.pct, c.pct_min, c.pct_max, projection.swing_pp, c.votos,
                     (br.candidate(c.numero).votos if br.candidate(c.numero) else 0),
                     projection.validos_projetados, br.votos_validos)
                    for c in projection.candidates
                ],
            )

    def timeline(self) -> tuple[list[dict], list[dict]]:
        """Série nacional: snapshots oficiais (com votos por candidato) e projeções, em ordem."""
        with self._lock:
            snaps = self._db.execute(
                """select s.id, s.totalizado_em, s.pct_secoes, s.eleitorado_nao_totalizado, s.votos_validos,
                          s.andamento, s.eleitorado, c.numero, c.votos, c.pct
                   from snapshots s join candidate_votes c on c.snapshot_id = s.id
                   where s.abr = 'br' and s.votos_validos > 0 order by s.totalizado_em, s.id"""
            ).fetchall()
            projs = self._db.execute(
                """select captured_at, totalizado_em, pct_secoes_br, numero, pct, pct_min, pct_max, swing_pp,
                          votos, votos_atuais, validos_projetados, validos_atuais
                   from projections order by id"""
            ).fetchall()
        official: dict[int, dict] = {}
        for r in snaps:
            row = official.setdefault(r["id"], {
                "totalizado_em": r["totalizado_em"], "pct_secoes": r["pct_secoes"],
                "restante": r["eleitorado_nao_totalizado"], "validos": r["votos_validos"],
                "andamento": r["andamento"], "eleitorado": r["eleitorado"],
                "votos": {}, "pct": {}})
            row["votos"][r["numero"]] = r["votos"]
            row["pct"][r["numero"]] = r["pct"]
        projections: dict[str, dict] = {}
        for r in projs:
            row = projections.setdefault(r["captured_at"], {
                "totalizado_em": r["totalizado_em"], "pct_secoes": r["pct_secoes_br"],
                "swing_pp": r["swing_pp"], "validos_projetados": r["validos_projetados"],
                "validos_atuais": r["validos_atuais"], "cand": {}})
            row["cand"][r["numero"]] = {k: r[k] for k in ("pct", "pct_min", "pct_max", "votos", "votos_atuais")}
        return list(official.values()), list(projections.values())

    def save_events(self, events: list[Event]) -> None:
        if not events:
            return
        with self._lock, self._db:
            self._db.executemany(
                """insert into events (captured_at, totalizado_em, type, abr, message, payload)
                   values (?,?,?,?,?,?)""",
                [
                    (_iso(e.captured_at), _iso(e.totalizado_em), e.type, e.abr, e.message,
                     json.dumps(e.payload, ensure_ascii=False))
                    for e in events
                ],
            )

    def save_baseline(self, baseline: Baseline) -> None:
        rows = [
            (abr, numero, baseline.nomes.get(numero, ""), votos, uf.pct.get(numero, 0.0))
            for abr, uf in {"br": baseline.br, **baseline.ufs}.items()
            for numero, votos in uf.votos.items()
        ]
        with self._lock, self._db:
            self._db.executemany("insert or replace into baseline_t1 values (?,?,?,?,?)", rows)

    # ---- leitura -------------------------------------------------------------

    def latest_results(self) -> dict[str, Result]:
        """Última versão gravada de cada abrangência, relida do JSON bruto."""
        with self._lock:
            rows = self._db.execute(
                """select abr, raw_path from snapshots s
                   where id = (select max(id) from snapshots where abr = s.abr)"""
            ).fetchall()
        return {
            row["abr"]: parse_result(json.loads((self.root / row["raw_path"]).read_text(encoding="utf-8")))
            for row in rows
        }

    def latest_city_results(self) -> dict[str, Result]:
        """Última versão gravada de cada município acompanhado (código TSE → resultado)."""
        with self._lock:
            rows = self._db.execute(
                """select cd, raw_path from city_snapshots s
                   where id = (select max(id) from city_snapshots where cd = s.cd)"""
            ).fetchall()
        return {
            row["cd"]: parse_result(json.loads((self.root / row["raw_path"]).read_text(encoding="utf-8")))
            for row in rows
        }

    def pace_points(self) -> list[PacePoint]:
        with self._lock:
            rows = self._db.execute(
                """select totalizado_em, secoes_totalizadas from snapshots
                   where abr = 'br' and totalizado_em is not null order by totalizado_em"""
            ).fetchall()
        return [
            PacePoint(datetime.fromisoformat(r["totalizado_em"]), r["secoes_totalizadas"])
            for r in rows
        ]

    def history(self, abr: str = "br", since: str | None = None) -> list[dict]:
        query = """select s.id, s.captured_at, s.totalizado_em, s.pct_secoes, s.secoes_totalizadas,
                          s.andamento, s.votos_validos, s.comparecimento
                   from snapshots s where s.abr = ?"""
        params: list = [abr]
        if since:
            query += " and s.totalizado_em >= ?"
            params.append(since)
        query += " order by s.totalizado_em, s.id"
        with self._lock:
            snaps = self._db.execute(query, params).fetchall()
            votes = self._db.execute(
                """select snapshot_id, numero, nome, votos, pct from candidate_votes
                   where snapshot_id in (select id from snapshots where abr = ?)""",
                (abr,),
            ).fetchall()
        by_snap: dict[int, list[dict]] = {}
        for v in votes:
            by_snap.setdefault(v["snapshot_id"], []).append(
                {"numero": v["numero"], "nome": v["nome"], "votos": v["votos"], "pct": v["pct"]}
            )
        return [
            {**{k: s[k] for k in s.keys() if k != "id"}, "candidatos": by_snap.get(s["id"], [])}
            for s in snaps
        ]

    def latest_date(self) -> str | None:
        """Data (AAAA-MM-DD) da totalização mais recente gravada."""
        with self._lock:
            row = self._db.execute("select max(totalizado_em) from snapshots").fetchone()
        return row[0][:10] if row and row[0] else None

    def events(self, since: str | None = None, limit: int | None = None) -> list[dict]:
        query = "select captured_at, totalizado_em, type, abr, message, payload from events"
        params: list = []
        if since:
            query += " where coalesce(totalizado_em, captured_at) >= ?"
            params.append(since)
        query += " order by id desc"
        if limit:
            query += " limit ?"
            params.append(limit)
        with self._lock:
            rows = self._db.execute(query, params).fetchall()
        return [
            {**{k: r[k] for k in r.keys() if k != "payload"}, "payload": json.loads(r["payload"] or "{}")}
            for r in reversed(rows)
        ]
