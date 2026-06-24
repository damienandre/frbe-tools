"""``frbe db`` commands: build and inspect the local DuckDB store."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated

import httpx
import typer

from frbe_tools.api.clubs import collect_club_rows
from frbe_tools.config import load_settings
from frbe_tools.db.store import connect, ingest_player_dir, load_clubs, scalar

logger = logging.getLogger(__name__)

app = typer.Typer(help="Build and inspect the local analytical database.", no_args_is_help=True)


@app.command()
def build(
    source: Annotated[
        Path | None,
        typer.Option("--source", "-s", help="Player files directory (default: <data_dir>/player)."),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Re-ingest periods already present."),
    ] = False,
    clubs: Annotated[
        bool,
        typer.Option("--clubs/--no-clubs", help="Also fetch + load clubs from the API."),
    ] = True,
) -> None:
    """Ingest the player snapshots (and clubs) into the DuckDB store."""
    settings = load_settings()
    src = source or (settings.data_dir / "player")
    if not src.is_dir():
        typer.echo(f"No player directory at {src} (run `frbe scrape players` first).", err=True)
        raise typer.Exit(code=1)

    con = connect(settings.db_path)
    summary = ingest_player_dir(con, src, overwrite=overwrite)
    ingested = sum(1 for n in summary.values() if n)
    total_rows = sum(summary.values())
    typer.echo(
        f"Player snapshots: {ingested} periods ingested "
        f"({len(summary) - ingested} skipped), {total_rows} rows."
    )

    if clubs:
        try:
            rows = asyncio.run(collect_club_rows(base_url=settings.api_base))
            n = load_clubs(con, rows)
            typer.echo(f"Clubs: loaded {n} from the API.")
        except httpx.HTTPError as exc:
            logger.warning("Skipped clubs (API error): %s", exc)
            typer.echo("Clubs: skipped (API unreachable).", err=True)

    con.close()
    typer.echo(f"Database ready at {settings.db_path}")


@app.command()
def info() -> None:
    """Show a summary of the database contents."""
    settings = load_settings()
    con = connect(settings.db_path)
    snapshots = scalar(con, "SELECT count(*) FROM player_snapshots")
    players = scalar(con, "SELECT count(*) FROM players")
    clubs = scalar(con, "SELECT count(*) FROM clubs")
    periods = con.execute(
        "SELECT count(*), min(period), max(period) FROM source_files"
    ).fetchone() or (0, None, None)

    typer.echo(f"Database: {settings.db_path}")
    typer.echo(f"  periods:          {periods[0]} ({periods[1]} .. {periods[2]})")
    typer.echo(f"  player_snapshots: {snapshots} rows")
    typer.echo(f"  distinct players: {players}")
    typer.echo(f"  clubs:            {clubs}")
    con.close()
