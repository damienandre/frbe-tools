"""Local analytical store backed by DuckDB.

Consolidates data from every source into a single DuckDB database. Records are
stored as dated *snapshots* (each load tagged with a ``snapshot_date``) so that
analyses can track the evolution of clubs and members over time.

DuckDB can read source SQLite databases, CSV, and Parquet directly, which keeps
ingestion of website dumps and API exports cheap.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

    from frbe_tools.api.models import ClubRow


def connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Open (creating if needed) the DuckDB database at ``db_path``.

    Raises:
        NotImplementedError: Always, until the schema is defined.
    """
    raise NotImplementedError(
        "DuckDB store is not implemented yet. It will open the database, "
        "ensure the snapshot-oriented schema exists, and return the connection."
    )


def load_clubs_snapshot(
    con: duckdb.DuckDBPyConnection,
    rows: Sequence[ClubRow],
    *,
    snapshot_date: date,
) -> int:
    """Insert a dated snapshot of club rows. Returns the number inserted.

    Raises:
        NotImplementedError: Always, until ingestion is built.
    """
    raise NotImplementedError("Club snapshot ingestion is not implemented yet.")


def load_sqlite_dump(
    con: duckdb.DuckDBPyConnection,
    dump_path: Path,
    *,
    snapshot_date: date,
) -> None:
    """Ingest a scraped SQLite dump as a dated snapshot.

    Raises:
        NotImplementedError: Always, until ingestion is built.
    """
    raise NotImplementedError("SQLite dump ingestion is not implemented yet.")
