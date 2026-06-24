"""``frbe scrape`` commands (Players Manager database dumps)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from frbe_tools.config import load_settings
from frbe_tools.sources.website import (
    DEFAULT_REQUEST_DELAY,
    LoginError,
    download_player_dumps,
)

app = typer.Typer(help="Scrape database dumps from the Players Manager site.", no_args_is_help=True)


@app.command()
def players(
    dest: Annotated[
        Path | None,
        typer.Option("--dest", "-d", help="Output directory (default: <data_dir>/player)."),
    ] = None,
    delay: Annotated[
        float,
        typer.Option("--delay", min=0.0, help="Seconds between downloads (server politeness)."),
    ] = DEFAULT_REQUEST_DELAY,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Re-download files that already exist."),
    ] = False,
) -> None:
    """Download every available player database (SQLite preferred, DBF fallback).

    Each period is saved, unzipped and renamed, as player<YYYYMM>.{sqlite,dbf}.
    Requires FRBE_USERNAME / FRBE_PASSWORD in your .env.
    """
    settings = load_settings()
    try:
        saved = download_player_dumps(settings, dest_dir=dest, delay=delay, overwrite=overwrite)
    except LoginError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(f"Saved {len(saved)} player files to {saved[0].parent if saved else dest}")
