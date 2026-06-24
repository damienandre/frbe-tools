"""Club-level analyses computed from the consolidated store.

Functions return Polars DataFrames so results can be displayed, exported, or
composed further. Computation runs in DuckDB where possible; Polars handles the
final shaping.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import polars as pl


def club_rankings(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Rank clubs from the most recent snapshot.

    Raises:
        NotImplementedError: Always, until the metric is defined.
    """
    raise NotImplementedError("Club rankings are not implemented yet.")


def club_evolution(
    con: duckdb.DuckDBPyConnection,
    *,
    idclub: int | None = None,
) -> pl.DataFrame:
    """Track a club's (or all clubs') metrics across snapshots over time.

    Raises:
        NotImplementedError: Always, until the metric is defined.
    """
    raise NotImplementedError("Club evolution analysis is not implemented yet.")
