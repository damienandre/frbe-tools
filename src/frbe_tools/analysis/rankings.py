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


def club_history(
    con: duckdb.DuckDBPyConnection,
    idclub: int,
    *,
    youth_max_age: int = 19,
    month: int | None = None,
) -> pl.DataFrame:
    """Return one club's metrics across periods (a time series).

    Columns: period, members, registered, free_license, women, youth (members in
    the under-``youth_max_age+1`` birth-year cohort for that period's year),
    foreign, avg_elo. Ordered oldest first.

    Pass ``month`` (1-12) to keep only periods in that calendar month — e.g.
    ``month=7`` compares every July snapshot, removing seasonal effects.
    """
    where = ["idclub = ?"]
    params: list[Any] = [youth_max_age, idclub]
    if month is not None:
        where.append("extract('month' FROM period) = ?")
        params.append(month)
    return con.execute(
        f"""
        SELECT
            period,
            count(*) FILTER (WHERE affiliated) AS members,
            count(*) FILTER (WHERE affiliated OR free_license) AS registered,
            count(*) FILTER (WHERE free_license AND NOT affiliated) AS free_license,
            count(*) FILTER (WHERE affiliated AND sex = 'F') AS women,
            count(*) FILTER (
                WHERE affiliated
                AND extract('year' FROM birthday) >= extract('year' FROM period) - ?
            ) AS youth,
            count(*) FILTER (WHERE affiliated AND foreign_) AS foreign,
            round(avg(elo) FILTER (WHERE affiliated AND elo > 0), 1) AS avg_elo
        FROM player_snapshots
        WHERE {" AND ".join(where)}
        GROUP BY period
        ORDER BY period
        """,
        params,
    ).pl()


def _tenure_sql(
    period: dt.date | str,
    status_sql: str,
    status_params: list[Any],
    idclub: int | None,
    region: str | None,
    width: int,
) -> tuple[str, list[Any]]:
    """Build the club-tenure histogram query (helper for ``player_distribution``).

    ``at_period`` is each in-scope player and the club they belong to at
    ``period``; ``joined`` finds the earliest snapshot they share that club
    (lapses ignored), and the outer query buckets the elapsed years into
    ``width``-wide bands.
    """
    scope = ["ps.period = ?", status_sql, "ps.idclub IS NOT NULL"]
    params: list[Any] = [period, *status_params]
    if idclub is not None:
        scope.append("ps.idclub = ?")
        params.append(idclub)
    if region is not None:
        scope.append("ps.region = ?")
        params.append(region)
    params += [period, period]  # joined.h.period <= ? ; date_diff end date
    # SECURITY: width is interpolated, not bound — safe only because it is an int
    # (int(bin_size) in the caller). Keep it an int if refactoring.
    sql = f"""
        WITH at_period AS (
            SELECT idplayer, idclub
            FROM player_snapshots ps
            WHERE {" AND ".join(scope)}
        ),
        joined AS (
            SELECT a.idplayer, min(h.period) AS joined_period
            FROM at_period a
            JOIN player_snapshots h
              ON h.idplayer = a.idplayer AND h.idclub = a.idclub AND h.period <= ?
            GROUP BY a.idplayer
        )
        SELECT CAST(floor(date_diff('day', joined_period, CAST(? AS DATE))::DOUBLE
                          / 365.0 / {width}) * {width} AS INTEGER) AS low,
               count(*) AS players
        FROM joined
        GROUP BY low
        ORDER BY low NULLS FIRST
    """
    return sql, params


def player_distribution(
    con: duckdb.DuckDBPyConnection,
    period: dt.date | str,
    *,
    dimension: str = "rating",
    statuses: tuple[str, ...] = ("member",),
    idclub: int | None = None,
    region: str | None = None,
    bin_size: int | None = None,
    age_year: int | None = None,
    include_unrated: bool = True,
) -> pl.DataFrame:
    """Bucket players into a histogram by rating, age, or club tenure at ``period``.

    ``dimension`` is ``rating``, ``age``, or ``tenure``. Scope is the whole
    federation by default; pass ``idclub`` to restrict to one club, or ``region``
    (``V``/``F``/``D``) to one regional federation. ``bin_size`` is the bucket
    width (default 100 Elo / 10 years / 2 tenure-years).

    Rating buckets are Elo bands; unrated players (``elo = 0``) collapse into a
    single ``unrated`` bucket sorted *first* (so it doesn't visually crowd the
    low rating bands) and still count toward the total. Pass
    ``include_unrated=False`` to drop them entirely, so ``pct`` reflects the
    distribution of *rated* players only (only meaningful for ``rating``). Age
    uses birth-year cohorts (``year - birth_year``); players with an unknown
    birthday are skipped.

    Tenure is the elapsed years since a player first joined their *current* club
    — the earliest snapshot (at or before ``period``) in which they appear with
    the club they belong to at ``period``. Lapses are *not* subtracted: a member
    who left and rejoined the same club counts from the original join, while a
    club switch resets tenure to the new club's first snapshot. Only players in a
    club at ``period`` are counted. Tenure is left-censored by the earliest
    loaded snapshot, so the top band lumps together everyone present since the
    data begins.

    Unlike ``rank_clubs`` & co this intentionally does *not* require
    ``idclub IS NOT NULL`` — the histogram counts everyone in scope, so for
    ``statuses`` that include club-less players (``all``/``unaffiliated``) the
    global/region total won't equal the sum of the per-club distributions.

    Returns columns: bucket (label, e.g. ``1600-1699`` / ``10-19`` / ``0-1`` /
    ``unrated``), players, pct (share of the total, rounded to 0.1%). Ordered low
    band first.
    """
    if dimension not in ("rating", "age", "tenure"):
        raise ValueError(f"Unknown dimension {dimension!r}; expected 'rating', 'age', or 'tenure'.")

    status_sql, status_params = _status_clause(statuses)
    _defaults = {"rating": 100, "age": 10, "tenure": 2}
    width = int(bin_size) if bin_size is not None else _defaults[dimension]
    if width <= 0:
        raise ValueError("bin_size must be a positive integer.")

    if dimension == "tenure":
        sql, params = _tenure_sql(period, status_sql, status_params, idclub, region, width)
    else:
        where = ["ps.period = ?", status_sql]
        params = [period, *status_params]
        if idclub is not None:
            where.append("ps.idclub = ?")
            params.append(idclub)
        if region is not None:
            where.append("ps.region = ?")
            params.append(region)

        if dimension == "rating":
            if include_unrated:
                # elo <= 0 -> NULL low, i.e. the "unrated" bucket; keep them counted.
                low_sql = (
                    "CASE WHEN ps.elo > 0 "
                    "THEN CAST(floor(ps.elo::DOUBLE / {w}) * {w} AS INTEGER) END"
                )
            else:
                where.append("ps.elo > 0")
                low_sql = "CAST(floor(ps.elo::DOUBLE / {w}) * {w} AS INTEGER)"
        else:
            where.append("ps.birthday IS NOT NULL")
            year = age_year if age_year is not None else _period_year(period)
            low_sql = (
                f"CAST(floor(({year} - extract('year' FROM ps.birthday))::DOUBLE / {{w}}) "
                f"* {{w}} AS INTEGER)"
            )
        # SECURITY: width and year are interpolated into the SQL string, not bound
        # as parameters. This is injection-safe only because both are guaranteed
        # ints (int(bin_size) above; _period_year -> int). Keep them ints.
        sql = f"""
            SELECT {low_sql.format(w=width)} AS low, count(*) AS players
            FROM player_snapshots ps
            WHERE {" AND ".join(where)}
            GROUP BY low
            ORDER BY low NULLS FIRST
        """

    df = con.execute(sql, params).pl()
    total = int(df["players"].sum()) if df.height else 0
    return df.select(
        bucket=pl.when(pl.col("low").is_null())
        .then(pl.lit("unrated"))
        .otherwise(pl.format("{}-{}", pl.col("low"), pl.col("low") + (width - 1))),
        players="players",
        pct=(100.0 * pl.col("players") / total).round(1) if total else pl.lit(0.0),
    )


def rank_rating_changes(
    con: duckdb.DuckDBPyConnection,
    period: dt.date | str,
    baseline: dt.date | str,
    *,
    statuses: tuple[str, ...] = ("member",),
    idclub: int | None = None,
    limit: int = 20,
    ascending: bool = False,
) -> pl.DataFrame:
    """Rank players by Elo change between ``baseline`` and ``period``.

    Descending = biggest gainers; ``ascending=True`` = biggest losers. Only
    players rated (elo > 0) in both periods and matching ``statuses`` at
    ``period`` are considered. Pass ``idclub`` to restrict to players in that
    club at ``period``. Columns: rank, idplayer, name, elo_then, elo_now, delta.
    """
    status_sql, status_params = _status_clause(statuses)
    order = "ASC" if ascending else "DESC"
    cur_where = ["period = ?", "elo > 0", status_sql]
    cur_params: list[Any] = [period, *status_params]
    if idclub is not None:
        cur_where.append("idclub = ?")
        cur_params.append(idclub)
    sql = f"""
        WITH cur AS (
            SELECT idplayer, elo FROM player_snapshots
            WHERE {" AND ".join(cur_where)}
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
    params = [*cur_params, baseline, limit]
    return con.execute(sql, params).pl().with_row_index("rank", offset=1)
