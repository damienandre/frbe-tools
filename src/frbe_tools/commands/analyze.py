"""``frbe analyze`` commands: club and player rankings over the DuckDB store."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

import polars as pl
import typer

from frbe_tools.analysis.rankings import (
    STATUS_PRESETS,
    club_history,
    latest_period,
    player_distribution,
    player_rating_evolution,
    rank_clubs,
    rank_clubs_by_growth,
    rank_clubs_by_strength,
    rank_rating_changes,
)
from frbe_tools.config import load_settings
from frbe_tools.db.store import connect, scalar

app = typer.Typer(help="Analyze consolidated federation data.", no_args_is_help=True)

PeriodOpt = Annotated[
    str | None, typer.Option("--period", "-p", help="YYYY-MM-DD or YYYYMM (default: latest).")
]
StatusOpt = Annotated[str, typer.Option("--status", help=f"One of: {', '.join(STATUS_PRESETS)}.")]
LimitOpt = Annotated[int, typer.Option("--limit", "-n", help="Number of rows to show.")]


def _resolve_period(con, raw: str | None) -> dt.date:
    if raw is None:
        return latest_period(con)
    digits = raw.replace("-", "")
    if len(digits) == 6 and digits.isdigit():
        return dt.date(int(digits[:4]), int(digits[4:6]), 1)
    return dt.date.fromisoformat(raw)


def _statuses(preset: str) -> tuple[str, ...]:
    try:
        return STATUS_PRESETS[preset]
    except KeyError as exc:
        raise typer.BadParameter(f"status must be one of {', '.join(STATUS_PRESETS)}") from exc


def _show(df: pl.DataFrame) -> None:
    with pl.Config(tbl_rows=200, tbl_hide_dataframe_shape=True, fmt_str_lengths=40):
        typer.echo(str(df))


@app.command()
def clubs(
    period: PeriodOpt = None,
    status: StatusOpt = "member",
    sex: Annotated[str | None, typer.Option("--sex", help="M or F.")] = None,
    min_age: Annotated[int | None, typer.Option("--min-age", help="Inclusive cohort age.")] = None,
    max_age: Annotated[int | None, typer.Option("--max-age", help="Inclusive cohort age.")] = None,
    foreign: Annotated[bool | None, typer.Option("--foreign/--belgian")] = None,
    region: Annotated[str | None, typer.Option("--region", help="V, F or D.")] = None,
    rated: Annotated[bool, typer.Option("--rated", help="Only rated (elo>0) players.")] = False,
    new: Annotated[bool, typer.Option("--new", help="Only players new this period.")] = False,
    limit: LimitOpt = 20,
) -> None:
    """Rank clubs by player count, filtered by status/age/gender/etc."""
    settings = load_settings()
    con = connect(settings.db_path)
    per = _resolve_period(con, period)
    df = rank_clubs(
        con,
        per,
        statuses=_statuses(status),
        sex=sex,
        min_age=min_age,
        max_age=max_age,
        foreign=foreign,
        region=region,
        rated_only=rated,
        new_only=new,
        limit=limit,
    )
    typer.echo(f"Clubs by {status} players as of {per}:")
    _show(df)


@app.command()
def strength(
    period: PeriodOpt = None,
    metric: Annotated[
        str, typer.Option("--metric", help="avg_elo, median_elo, max_elo, top_n_sum.")
    ] = "avg_elo",
    top_n: Annotated[int, typer.Option("--top-n", help="Boards for top_n_sum.")] = 4,
    status: StatusOpt = "member",
    min_players: Annotated[
        int, typer.Option("--min-players", help="Drop clubs below this many rated players.")
    ] = 4,
    limit: LimitOpt = 20,
) -> None:
    """Rank clubs by an Elo aggregate (strength)."""
    settings = load_settings()
    con = connect(settings.db_path)
    per = _resolve_period(con, period)
    try:
        df = rank_clubs_by_strength(
            con,
            per,
            metric=metric,
            top_n=top_n,
            statuses=_statuses(status),
            min_players=min_players,
            limit=limit,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Clubs by {metric} as of {per}:")
    _show(df)


@app.command()
def growth(
    baseline: Annotated[str, typer.Argument(help="Baseline period (YYYY-MM-DD or YYYYMM).")],
    period: PeriodOpt = None,
    status: StatusOpt = "member",
    limit: LimitOpt = 20,
) -> None:
    """Rank clubs by membership change between two periods."""
    settings = load_settings()
    con = connect(settings.db_path)
    per = _resolve_period(con, period)
    base = _resolve_period(con, baseline)
    df = rank_clubs_by_growth(con, per, base, statuses=_statuses(status), limit=limit)
    typer.echo(f"Club {status} growth from {base} to {per}:")
    _show(df)


@app.command()
def player(
    idplayer: Annotated[int, typer.Argument(help="Player matricule.")],
) -> None:
    """Show a player's national-Elo evolution over time."""
    settings = load_settings()
    con = connect(settings.db_path)
    df = player_rating_evolution(con, idplayer)
    if df.is_empty():
        typer.echo(f"No rating history for player {idplayer}.")
        raise typer.Exit(code=1)
    typer.echo(f"Rating evolution for player {idplayer}:")
    _show(df)


@app.command("club-history")
def club_history_cmd(
    idclub: Annotated[int, typer.Argument(help="Club id.")],
    month: Annotated[
        int | None,
        typer.Option("--month", "-m", min=1, max=12, help="Only this month, e.g. 7 (July)."),
    ] = None,
    youth_age: Annotated[int, typer.Option("--youth-age", help="Upper youth cohort age.")] = 19,
) -> None:
    """Show one club's metrics (members, youth, women, strength) over time."""
    settings = load_settings()
    con = connect(settings.db_path)
    df = club_history(con, idclub, youth_max_age=youth_age, month=month)
    if df.is_empty():
        typer.echo(f"No history for club {idclub}.")
        raise typer.Exit(code=1)
    name = scalar(
        con, "SELECT coalesce(name_short, name_long) FROM clubs WHERE idclub = ?", [idclub]
    )
    typer.echo(f"History for club {idclub}{f' ({name})' if name else ''}:")
    _show(df)


@app.command()
def distribution(
    dimension: Annotated[str, typer.Argument(help="rating, age or tenure.")] = "rating",
    period: PeriodOpt = None,
    club: Annotated[int | None, typer.Option("--club", "-c", help="Restrict to one club.")] = None,
    region: Annotated[
        str | None, typer.Option("--region", help="V, F or D (regional federation).")
    ] = None,
    status: StatusOpt = "member",
    bin_size: Annotated[
        int | None,
        typer.Option("--bin", help="Bucket width (default 100 Elo / 10 years / 2 tenure-years)."),
    ] = None,
    hide_unrated: Annotated[
        bool, typer.Option("--hide-unrated", help="Drop unrated players (rating only).")
    ] = False,
) -> None:
    """Show the distribution of players by rating or age (club/federation/global)."""
    settings = load_settings()
    con = connect(settings.db_path)
    per = _resolve_period(con, period)
    try:
        df = player_distribution(
            con,
            per,
            dimension=dimension,
            statuses=_statuses(status),
            idclub=club,
            region=region,
            bin_size=bin_size,
            include_unrated=not hide_unrated,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if club is not None:
        scope = f"club {club}"
    elif region is not None:
        scope = f"federation {region}"
    else:
        scope = "all clubs"
    typer.echo(f"{dimension.capitalize()} distribution of {status} players in {scope} as of {per}:")
    _show(df)


@app.command()
def movers(
    baseline: Annotated[str, typer.Argument(help="Baseline period (YYYY-MM-DD or YYYYMM).")],
    period: PeriodOpt = None,
    club: Annotated[
        int | None, typer.Option("--club", "-c", help="Restrict to players in this club.")
    ] = None,
    losers: Annotated[
        bool, typer.Option("--losers", help="Show biggest losers instead of gainers.")
    ] = False,
    status: StatusOpt = "member",
    limit: LimitOpt = 20,
) -> None:
    """Rank players by biggest Elo gain (or loss) between two periods."""
    settings = load_settings()
    con = connect(settings.db_path)
    per = _resolve_period(con, period)
    base = _resolve_period(con, baseline)
    df = rank_rating_changes(
        con, per, base, statuses=_statuses(status), idclub=club, limit=limit, ascending=losers
    )
    scope = f" in club {club}" if club is not None else ""
    typer.echo(f"Biggest {'losers' if losers else 'gainers'}{scope} from {base} to {per}:")
    _show(df)
