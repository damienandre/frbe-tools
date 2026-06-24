"""Club and player rankings computed over the DuckDB store.

Every ranking is taken *as of a given period* (a quarterly snapshot date).
Functions return Polars DataFrames so results can be printed, exported, or
composed further.

Age uses **birth-year cohorts** (``age = year - birth_year``), the convention
used for chess youth categories: in a given year everyone born the same year is
the same "age", so "under 20 in 2026" means born 2007 or later.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import duckdb
import polars as pl

# Maps the raw flags to the affiliation status (mirrors the player_affiliations view).
_STATUS_CASE = (
    "CASE WHEN affiliated THEN 'member' "
    "WHEN free_license THEN 'free_license' ELSE 'unaffiliated' END"
)

# Convenient status groupings for callers / the CLI.
STATUS_PRESETS: dict[str, tuple[str, ...]] = {
    "member": ("member",),
    "registered": ("member", "free_license"),
    "free_license": ("free_license",),
    "unaffiliated": ("unaffiliated",),
    "all": ("member", "free_license", "unaffiliated"),
}

_STRENGTH_AGG = {
    "avg_elo": "round(avg(elo), 1)",
    "median_elo": "median(elo)",
    "max_elo": "max(elo)",
}

_CLUB_NAME = "coalesce(c.name_short, c.name_long, CAST(ps.idclub AS VARCHAR))"


def latest_period(con: duckdb.DuckDBPyConnection) -> dt.date:
    """Return the most recent loaded period."""
    row = con.execute("SELECT max(period) FROM player_snapshots").fetchone()
    if row is None or row[0] is None:
        raise ValueError("No snapshots loaded; run `frbe db build` first.")
    return row[0]


def _period_year(period: dt.date | str) -> int:
    return period.year if isinstance(period, dt.date) else int(str(period)[:4])


def _status_clause(statuses: tuple[str, ...]) -> tuple[str, list[Any]]:
    placeholders = ", ".join("?" * len(statuses))
    return f"{_STATUS_CASE} IN ({placeholders})", list(statuses)


def rank_clubs(
    con: duckdb.DuckDBPyConnection,
    period: dt.date | str,
    *,
    statuses: tuple[str, ...] = ("member",),
    sex: str | None = None,
    min_age: int | None = None,
    max_age: int | None = None,
    age_year: int | None = None,
    foreign: bool | None = None,
    region: str | None = None,
    rated_only: bool = False,
    new_only: bool = False,
    enabled_only: bool = False,
    limit: int | None = None,
) -> pl.DataFrame:
    """Rank clubs by number of players matching the given filters at ``period``.

    Covers members, registered (members + free licence), youth/age bands, gender,
    foreigners, region, rated and newly-affiliated counts via parameters. ``min_age``
    / ``max_age`` are inclusive birth-year-cohort bounds (e.g. ``max_age=19`` for
    under-20). Returns columns: rank, idclub, name, players.
    """
    status_sql, params = _status_clause(statuses)
    where = ["ps.period = ?", "ps.idclub IS NOT NULL", status_sql]
    params = [period, *params]

    if sex is not None:
        where.append("ps.sex = ?")
        params.append(sex)
    year = age_year if age_year is not None else _period_year(period)
    if min_age is not None:
        where.append("extract('year' FROM ps.birthday) <= ?")
        params.append(year - min_age)
    if max_age is not None:
        where.append("extract('year' FROM ps.birthday) >= ?")
        params.append(year - max_age)
    if foreign is not None:
        where.append("ps.foreign_ = ?")
        params.append(foreign)
    if region is not None:
        where.append("ps.region = ?")
        params.append(region)
    if rated_only:
        where.append("ps.elo > 0")
    if new_only:
        where.append(
            "ps.idplayer IN (SELECT idplayer FROM player_snapshots "
            "GROUP BY idplayer HAVING min(period) = ?)"
        )
        params.append(period)
    if enabled_only:
        where.append("c.enabled")

    sql = f"""
        SELECT ps.idclub, {_CLUB_NAME} AS name, count(*) AS players
        FROM player_snapshots ps
        LEFT JOIN clubs c ON c.idclub = ps.idclub
        WHERE {" AND ".join(where)}
        GROUP BY ALL
        ORDER BY players DESC, ps.idclub
    """
    if limit:
        sql += f"\nLIMIT {int(limit)}"
    return con.execute(sql, params).pl().with_row_index("rank", offset=1)


def rank_clubs_by_strength(
    con: duckdb.DuckDBPyConnection,
    period: dt.date | str,
    *,
    metric: str = "avg_elo",
    top_n: int = 4,
    statuses: tuple[str, ...] = ("member",),
    min_players: int = 1,
    limit: int | None = None,
) -> pl.DataFrame:
    """Rank clubs by an Elo aggregate over their rated players at ``period``.

    ``metric`` is one of ``avg_elo``, ``median_elo``, ``max_elo`` or
    ``top_n_sum`` (sum of the club's top ``top_n`` boards). Only rated players
    (elo > 0) count; ``min_players`` drops clubs with too few rated players.
    """
    status_sql, status_params = _status_clause(statuses)
    base_where = f"period = ? AND ps.idclub IS NOT NULL AND elo > 0 AND {status_sql}"

    if metric == "top_n_sum":
        sql = f"""
            WITH ranked AS (
                SELECT ps.idclub, elo,
                       row_number() OVER (PARTITION BY ps.idclub ORDER BY elo DESC) AS rn
                FROM player_snapshots ps
                WHERE {base_where}
            )
            SELECT r.idclub,
                   coalesce(c.name_short, c.name_long, CAST(r.idclub AS VARCHAR)) AS name,
                   sum(r.elo) AS score, count(*) AS players
            FROM ranked r
            LEFT JOIN clubs c ON c.idclub = r.idclub
            WHERE r.rn <= ?
            GROUP BY ALL
            HAVING count(*) >= ?
            ORDER BY score DESC, r.idclub
        """
        params = [period, *status_params, top_n, min_players]
    elif metric in _STRENGTH_AGG:
        sql = f"""
            SELECT ps.idclub, {_CLUB_NAME} AS name,
                   {_STRENGTH_AGG[metric]} AS score, count(*) AS players
            FROM player_snapshots ps
            LEFT JOIN clubs c ON c.idclub = ps.idclub
            WHERE {base_where}
            GROUP BY ALL
            HAVING count(*) >= ?
            ORDER BY score DESC, ps.idclub
        """
        params = [period, *status_params, min_players]
    else:
        raise ValueError(
            f"Unknown metric {metric!r}; expected one of "
            f"{', '.join([*_STRENGTH_AGG, 'top_n_sum'])}."
        )

    if limit:
        sql += f"\nLIMIT {int(limit)}"
    return con.execute(sql, params).pl().with_row_index("rank", offset=1)


def rank_clubs_by_growth(
    con: duckdb.DuckDBPyConnection,
    period: dt.date | str,
    baseline: dt.date | str,
    *,
    statuses: tuple[str, ...] = ("member",),
    limit: int | None = None,
) -> pl.DataFrame:
    """Rank clubs by membership change between ``baseline`` and ``period``.

    Returns columns: rank, idclub, name, count_then, count_now, delta, pct.
    Ordered by absolute delta (fastest growing first; shrinking clubs last).
    """
    status_sql, status_params = _status_clause(statuses)
    counts = (
        "SELECT idclub, count(*) AS n FROM player_snapshots "
        f"WHERE period = ? AND idclub IS NOT NULL AND {status_sql} GROUP BY idclub"
    )
    sql = f"""
        WITH cur AS ({counts}), base AS ({counts})
        SELECT
            coalesce(cur.idclub, base.idclub) AS idclub,
            coalesce(c.name_short, c.name_long,
                     CAST(coalesce(cur.idclub, base.idclub) AS VARCHAR)) AS name,
            coalesce(base.n, 0) AS count_then,
            coalesce(cur.n, 0) AS count_now,
            coalesce(cur.n, 0) - coalesce(base.n, 0) AS delta,
            round(100.0 * (coalesce(cur.n, 0) - coalesce(base.n, 0)) / nullif(base.n, 0), 1) AS pct
        FROM cur
        FULL OUTER JOIN base ON cur.idclub = base.idclub
        LEFT JOIN clubs c ON c.idclub = coalesce(cur.idclub, base.idclub)
        ORDER BY delta DESC, idclub
    """
    params = [period, *status_params, baseline, *status_params]
    if limit:
        sql += f"\nLIMIT {int(limit)}"
    return con.execute(sql, params).pl().with_row_index("rank", offset=1)


def player_rating_evolution(
    con: duckdb.DuckDBPyConnection,
    idplayer: int,
) -> pl.DataFrame:
    """Return a player's national-Elo time series (period, elo, gain, games)."""
    return con.execute(
        "SELECT period, elo, gain, games FROM player_rating_history "
        "WHERE idplayer = ? ORDER BY period",
        [idplayer],
    ).pl()


def rank_rating_changes(
    con: duckdb.DuckDBPyConnection,
    period: dt.date | str,
    baseline: dt.date | str,
    *,
    statuses: tuple[str, ...] = ("member",),
    limit: int = 20,
    ascending: bool = False,
) -> pl.DataFrame:
    """Rank players by Elo change between ``baseline`` and ``period``.

    Descending = biggest gainers; ``ascending=True`` = biggest losers. Only
    players rated (elo > 0) in both periods and matching ``statuses`` at
    ``period`` are considered. Columns: rank, idplayer, name, elo_then, elo_now, delta.
    """
    status_sql, status_params = _status_clause(statuses)
    order = "ASC" if ascending else "DESC"
    sql = f"""
        WITH cur AS (
            SELECT idplayer, elo FROM player_snapshots
            WHERE period = ? AND elo > 0 AND {status_sql}
        ),
        base AS (
            SELECT idplayer, elo FROM player_snapshots WHERE period = ? AND elo > 0
        )
        SELECT cur.idplayer,
               arg_max(ps.name, ps.period) AS name,
               base.elo AS elo_then, cur.elo AS elo_now,
               cur.elo - base.elo AS delta
        FROM cur
        JOIN base ON base.idplayer = cur.idplayer
        JOIN player_snapshots ps ON ps.idplayer = cur.idplayer
        GROUP BY cur.idplayer, base.elo, cur.elo
        ORDER BY delta {order}, cur.idplayer
        LIMIT ?
    """
    params = [period, *status_params, baseline, limit]
    return con.execute(sql, params).pl().with_row_index("rank", offset=1)
