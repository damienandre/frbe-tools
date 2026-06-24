"""Local analytical store backed by DuckDB.

Consolidates the quarterly player snapshots (and club data) into a single
DuckDB database. The model is a small star schema:

- ``player_snapshots`` -- the core fact, one row per (period, player). It
  mirrors each source file faithfully: the full descriptive record (name, sex,
  birthday, nationality, fide_id, died) lives here, because all of these can
  vary over time, alongside the club relationship and rating measures.
- ``clubs`` -- club dimension (names/details sourced from the public API).
- ``players`` -- a *derived view* exposing each player's latest identity plus
  lifecycle dates (first/last seen, deceased_since).
- ``player_affiliations`` / ``player_rating_history`` -- views answering the
  time-dependent club-membership and rating-evolution questions.

Source files come in two eras with different field names (DBF 2007-2018,
SQLite 2018+); :func:`canonical_from_sqlite` / :func:`canonical_from_dbf` map
both onto one canonical schema. Parsing is deliberately tolerant: malformed
numerics and dates degrade to NULL rather than failing the ingest.
"""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
from dbfread import DBF, FieldParser

logger = logging.getLogger(__name__)

# Canonical column order; the player_snapshots table and the insert frame must
# both follow it.
SNAPSHOT_COLUMNS: tuple[str, ...] = (
    "period",
    "idplayer",
    "name",
    "sex",
    "birthday",
    "nationality",
    "fide_id",
    "died",
    "idclub",
    "affiliated",
    "free_license",
    "fed",
    "region",
    "foreign_",
    "elo",
    "elo_previous",
    "gain",
    "games",
    "games_previous",
    "performance",
    "opponents",
    "last_game",
    "title",
    "arbiter",
)

# Polars dtypes for the snapshot insert frame (column order = SNAPSHOT_COLUMNS).
_SNAPSHOT_SCHEMA: dict[str, Any] = {
    "period": pl.Date,
    "idplayer": pl.Int64,
    "name": pl.Utf8,
    "sex": pl.Utf8,
    "birthday": pl.Date,
    "nationality": pl.Utf8,
    "fide_id": pl.Int64,
    "died": pl.Boolean,
    "idclub": pl.Int64,
    "affiliated": pl.Boolean,
    "free_license": pl.Boolean,
    "fed": pl.Utf8,
    "region": pl.Utf8,
    "foreign_": pl.Boolean,
    "elo": pl.Int64,
    "elo_previous": pl.Int64,
    "gain": pl.Int64,
    "games": pl.Int64,
    "games_previous": pl.Int64,
    "performance": pl.Float64,
    "opponents": pl.Int64,
    "last_game": pl.Date,
    "title": pl.Utf8,
    "arbiter": pl.Utf8,
}

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS source_files (
    period       DATE PRIMARY KEY,
    period_code  VARCHAR NOT NULL,
    format       VARCHAR NOT NULL,
    filename     VARCHAR NOT NULL,
    row_count    INTEGER,
    ingested_at  TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS clubs (
    idclub     INTEGER PRIMARY KEY,
    name_long  VARCHAR,
    name_short VARCHAR,
    region     VARCHAR,
    email_main VARCHAR,
    enabled    BOOLEAN
);

CREATE TABLE IF NOT EXISTS player_snapshots (
    period         DATE    NOT NULL,
    idplayer       INTEGER NOT NULL,
    name           VARCHAR,
    sex            VARCHAR,
    birthday       DATE,
    nationality    VARCHAR,
    fide_id        INTEGER,
    died           BOOLEAN,
    idclub         INTEGER,
    affiliated     BOOLEAN NOT NULL,
    free_license   BOOLEAN NOT NULL,
    fed            VARCHAR,
    region         VARCHAR,
    foreign_       BOOLEAN,
    elo            INTEGER,
    elo_previous   INTEGER,
    gain           INTEGER,
    games          INTEGER,
    games_previous INTEGER,
    performance    DOUBLE,
    opponents      INTEGER,
    last_game      DATE,
    title          VARCHAR,
    arbiter        VARCHAR,
    PRIMARY KEY (period, idplayer)
);

CREATE OR REPLACE VIEW players AS
SELECT
    idplayer,
    arg_max(name, period)        AS name,
    arg_max(sex, period)         AS sex,
    arg_max(birthday, period)    AS birthday,
    arg_max(nationality, period) AS nationality,
    max(fide_id)                 AS fide_id,
    bool_or(died)                AS deceased,
    min(period) FILTER (WHERE died) AS deceased_since,
    min(period)                  AS first_seen,
    max(period)                  AS last_seen
FROM player_snapshots
GROUP BY idplayer;

CREATE OR REPLACE VIEW player_affiliations AS
SELECT
    period,
    idplayer,
    idclub,
    CASE WHEN affiliated   THEN 'member'
         WHEN free_license THEN 'free_license'
         ELSE 'unaffiliated' END AS status,
    region,
    foreign_
FROM player_snapshots;

CREATE OR REPLACE VIEW player_rating_history AS
SELECT period, idplayer, elo, elo_previous, gain, games, games_previous, performance
FROM player_snapshots
WHERE elo IS NOT NULL AND elo > 0;
"""


# --------------------------------------------------------------------------- #
# Tolerant value parsing
# --------------------------------------------------------------------------- #
def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return int(value) if isinstance(value, bool) else None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    s = str(value).strip()
    if not s or s.lower() == "none":
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return None


def _nonzero(value: int | None) -> int | None:
    """Map 0 -> None (used for idclub and fide_id, where 0 means 'none')."""
    return value or None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "none":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in {"1", "true", "t", "yes", "y"}:
        return True
    if s in {"0", "false", "f", "no", "n", "", "none"}:
        return False
    return None


def _to_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _to_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    s = str(value).strip()
    if not s or s.lower() == "none":
        return None
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _split_fed(fed: str | None) -> tuple[str | None, bool]:
    """Split the raw Fed value into (region, is_foreign).

    The '*' marks a foreign player; the remainder ('V'/'F'/'D' or '') is the
    regional federation.
    """
    raw = fed or ""
    is_foreign = "*" in raw
    region = raw.replace("*", "").strip() or None
    return region, is_foreign


# --------------------------------------------------------------------------- #
# Era -> canonical mapping
# --------------------------------------------------------------------------- #
def canonical_from_sqlite(row: dict[str, Any]) -> dict[str, Any]:
    """Map a SQLite-era ``players`` row to the canonical schema."""
    region, foreign = _split_fed(_to_str(row.get("Fed")))
    return {
        "idplayer": _to_int(row.get("IdNumber")),
        "name": _to_str(row.get("Name")),
        "sex": _to_str(row.get("Sex")),
        "birthday": _to_date(row.get("Birthday")),
        "nationality": _to_str(row.get("NatPlayer")),
        "fide_id": _nonzero(_to_int(row.get("FideId"))),
        "died": _to_bool(row.get("Died")),
        "idclub": _nonzero(_to_int(row.get("Club"))),
        "affiliated": bool(_to_bool(row.get("Affiliated"))),
        "free_license": bool(_to_bool(row.get("G"))),
        "fed": _to_str(row.get("Fed")),
        "region": region,
        "foreign_": foreign,
        "elo": _to_int(row.get("Elo")),
        "elo_previous": _to_int(row.get("EloPrevious")),
        "gain": _to_int(row.get("Gain")),
        "games": _to_int(row.get("Games")),
        "games_previous": _to_int(row.get("GamesPrevious")),
        "performance": _to_float(row.get("Performance")),
        "opponents": _to_int(row.get("Opponents")),
        "last_game": _to_date(row.get("LastGames")),
        "title": None,  # SQLite era dropped the title field
        "arbiter": _to_str(row.get("Arbiter")),
    }


def canonical_from_dbf(row: dict[str, Any]) -> dict[str, Any]:
    """Map a DBF-era record to the canonical schema.

    The DBF era has no affiliation flag (derived as ``NOT SUPPRESS``) and no
    free-license concept (always False).
    """
    region, foreign = _split_fed(_to_str(row.get("FEDERATION")))
    suppressed = _to_bool(row.get("SUPPRESS"))
    return {
        "idplayer": _to_int(row.get("MATRICULE")),
        "name": _to_str(row.get("NOM_PRENOM")),
        "sex": _to_str(row.get("SEXE")),
        "birthday": _to_date(row.get("DATE_NAISS")),
        "nationality": _to_str(row.get("NOUV_MOD")),
        "fide_id": _nonzero(_to_int(row.get("FIDE"))),
        "died": _to_bool(row.get("DECEDE")),
        "idclub": _nonzero(_to_int(row.get("CLUB"))),
        "affiliated": not bool(suppressed),
        "free_license": False,
        "fed": _to_str(row.get("FEDERATION")),
        "region": region,
        "foreign_": foreign,
        "elo": _to_int(row.get("ELO_CALCUL")),
        "elo_previous": _to_int(row.get("ELO_PRECE")),
        "gain": _to_int(row.get("GAIN")),
        "games": _to_int(row.get("PART_CALCU")),
        "games_previous": _to_int(row.get("PART_PRECE")),
        "performance": _to_float(row.get("PERFORMAN")),
        "opponents": _to_int(row.get("ADVERSAIRE")),
        "last_game": _to_date(row.get("DER_JEUX")),
        "title": _to_str(row.get("TITRE")),
        "arbiter": _to_str(row.get("ARBITRE")),
    }


class _TolerantFieldParser(FieldParser):
    """DBF field parser that yields NULL instead of raising on bad data."""

    def parse(self, field: Any, data: Any) -> Any:
        try:
            return super().parse(field, data)
        except Exception:
            return None


def _read_sqlite_rows(path: Path) -> Iterator[dict[str, Any]]:
    con = sqlite3.connect(path)
    try:
        con.row_factory = sqlite3.Row
        for row in con.execute("SELECT * FROM players"):
            yield dict(row)
    finally:
        con.close()


def _read_dbf_rows(path: Path) -> Iterator[dict[str, Any]]:
    table = DBF(
        path,
        load=False,
        parserclass=_TolerantFieldParser,
        char_decode_errors="ignore",
    )
    for record in table:
        yield dict(record)


# --------------------------------------------------------------------------- #
# Connection + schema
# --------------------------------------------------------------------------- #
def connect(db_path: Path | str) -> duckdb.DuckDBPyConnection:
    """Open (creating if needed) the DuckDB database and ensure the schema."""
    if isinstance(db_path, Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(_SCHEMA_DDL)
    return con


def scalar(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> Any:
    """Run a query and return the first column of the first row (or None)."""
    row = con.execute(sql, params or []).fetchone()
    return row[0] if row is not None else None


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #
def _period_from_filename(path: Path) -> tuple[dt.date, str]:
    """player202607.sqlite -> (date(2026, 7, 1), '202607')."""
    stem = path.stem  # 'player202607'
    code = stem.replace("player", "")
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"Cannot parse period from {path.name!r}")
    return dt.date(int(code[:4]), int(code[4:6]), 1), code


def ingest_player_file(
    con: duckdb.DuckDBPyConnection,
    path: Path,
    *,
    overwrite: bool = False,
) -> int:
    """Ingest one player snapshot file. Returns the number of rows inserted.

    Already-loaded periods are skipped unless ``overwrite`` is set. Rows without
    a player id are dropped; duplicate ids within a file keep the first.
    """
    period, code = _period_from_filename(path)
    fmt = "sqlite" if path.suffix.lower() == ".sqlite" else "dbf"

    already = scalar(con, "SELECT count(*) FROM source_files WHERE period = ?", [period])
    if already:
        if not overwrite:
            logger.info("Skipping %s (period %s already loaded)", path.name, code)
            return 0
        con.execute("DELETE FROM player_snapshots WHERE period = ?", [period])
        con.execute("DELETE FROM source_files WHERE period = ?", [period])

    mapper = canonical_from_sqlite if fmt == "sqlite" else canonical_from_dbf
    reader = _read_sqlite_rows if fmt == "sqlite" else _read_dbf_rows

    seen: set[int] = set()
    records: list[dict[str, Any]] = []
    for raw in reader(path):
        rec = mapper(raw)
        pid = rec["idplayer"]
        if pid is None or pid in seen:
            continue
        seen.add(pid)
        rec["period"] = period
        records.append(rec)

    if not records:
        logger.warning("No usable rows in %s", path.name)
        return 0

    # Bulk insert via a single Arrow-backed columnar append (fast; row-by-row
    # executemany is pathologically slow against the primary-key index).
    frame = pl.DataFrame(records, schema=_SNAPSHOT_SCHEMA)
    cols = ", ".join(SNAPSHOT_COLUMNS)
    con.register("incoming_snapshot", frame)
    try:
        con.execute(f"INSERT INTO player_snapshots ({cols}) SELECT {cols} FROM incoming_snapshot")
    finally:
        con.unregister("incoming_snapshot")

    con.execute(
        "INSERT INTO source_files (period, period_code, format, filename, row_count, ingested_at) "
        "VALUES (?, ?, ?, ?, ?, now())",
        [period, code, fmt, path.name, len(records)],
    )
    logger.info("Ingested %s: %d rows (%s)", path.name, len(records), fmt)
    return len(records)


def ingest_player_dir(
    con: duckdb.DuckDBPyConnection,
    directory: Path,
    *,
    overwrite: bool = False,
) -> dict[str, int]:
    """Ingest every player file in ``directory`` (sorted by period).

    Returns a mapping of period_code -> rows inserted (0 = skipped).
    """
    files = sorted(directory.glob("player*.sqlite")) + sorted(directory.glob("player*.dbf"))
    files.sort(key=lambda p: p.stem.replace("player", ""))
    summary: dict[str, int] = {}
    for path in files:
        _, code = _period_from_filename(path)
        summary[code] = ingest_player_file(con, path, overwrite=overwrite)
    return summary


def load_clubs(con: duckdb.DuckDBPyConnection, rows: Iterable[Any]) -> int:
    """Populate the clubs dimension from API ``ClubRow`` objects."""
    payload = [
        (
            r.idclub,
            r.name_long or None,
            r.name_short or None,
            None,  # region: not provided by the API
            r.email_main or None,
            bool(r.enabled),
        )
        for r in rows
    ]
    if not payload:
        return 0
    con.executemany(
        "INSERT OR REPLACE INTO clubs "
        "(idclub, name_long, name_short, region, email_main, enabled) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        payload,
    )
    return len(payload)
