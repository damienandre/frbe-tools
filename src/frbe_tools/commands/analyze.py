"""``frbe analyze`` commands (analyses over the consolidated store). Stub."""

from __future__ import annotations

import typer

from frbe_tools.analysis.rankings import club_evolution, club_rankings
from frbe_tools.config import load_settings
from frbe_tools.db.store import connect

app = typer.Typer(help="Analyze consolidated federation data.", no_args_is_help=True)


@app.command()
def rankings() -> None:
    """Show club rankings from the most recent snapshot."""
    settings = load_settings()
    try:
        con = connect(settings.db_path)
        frame = club_rankings(con)
    except NotImplementedError as exc:
        typer.echo(f"Not implemented yet: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(frame)


@app.command()
def evolution(
    idclub: int | None = typer.Option(None, help="Restrict to a single club id."),
) -> None:
    """Show how clubs evolve across snapshots over time."""
    settings = load_settings()
    try:
        con = connect(settings.db_path)
        frame = club_evolution(con, idclub=idclub)
    except NotImplementedError as exc:
        typer.echo(f"Not implemented yet: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(frame)
