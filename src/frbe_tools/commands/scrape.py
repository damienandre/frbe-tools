"""``frbe scrape`` commands (website database dumps). Stub."""

from __future__ import annotations

from typing import Annotated

import typer

from frbe_tools.config import load_settings
from frbe_tools.sources.website import DumpFormat, download_dumps

app = typer.Typer(help="Scrape database dumps from the manager website.", no_args_is_help=True)


@app.command()
def dumps(
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help="Dump format: sqlite or dbf."),
    ] = "sqlite",
) -> None:
    """Download the federation database dump (requires website credentials)."""
    settings = load_settings()
    if not settings.has_credentials:
        typer.echo(
            "Missing credentials: set FRBE_USERNAME and FRBE_PASSWORD in your .env.",
            err=True,
        )
        raise typer.Exit(code=2)

    dump_format: DumpFormat = "dbf" if fmt == "dbf" else "sqlite"
    try:
        path = download_dumps(settings, settings.data_dir, fmt=dump_format)
    except NotImplementedError as exc:
        typer.echo(f"Not implemented yet: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Downloaded dump to {path}")
