"""Root Typer application for the ``frbe`` command-line tool."""

from __future__ import annotations

import logging
from typing import Annotated

import typer

from frbe_tools import __version__
from frbe_tools.commands import analyze, clubs, db, scrape

app = typer.Typer(
    name="frbe",
    help="Access and analyze data of the Belgian chess federation (FRBE/KBSB/KSB).",
    no_args_is_help=True,
)

app.add_typer(clubs.app, name="clubs")
app.add_typer(scrape.app, name="scrape")
app.add_typer(db.app, name="db")
app.add_typer(analyze.app, name="analyze")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"frbe-tools {__version__}")
        raise typer.Exit


@app.callback()
def main(
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable debug logging."),
    ] = False,
    _version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Configure logging shared by all subcommands."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


if __name__ == "__main__":
    app()
