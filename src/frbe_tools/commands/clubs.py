"""``frbe clubs`` commands."""

from __future__ import annotations

import asyncio
import csv
import logging
from pathlib import Path
from typing import Annotated

import httpx
import typer

from frbe_tools.api.client import DEFAULT_CONCURRENCY, DEFAULT_TIMEOUT
from frbe_tools.api.clubs import collect_club_rows
from frbe_tools.api.models import CLUB_CSV_FIELDS, ClubRow
from frbe_tools.config import load_settings

logger = logging.getLogger(__name__)

app = typer.Typer(help="Access club administrative data.", no_args_is_help=True)

# Default into the gitignored data/ directory: the export contains personal
# emails and must never be committed.
DEFAULT_OUTPUT = Path("data/clubs.csv")


def write_csv(rows: list[ClubRow], output: Path) -> None:
    """Write club rows to ``output`` using the canonical column order."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CLUB_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_dict())


@app.command()
def export(
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Path to the output CSV."),
    ] = DEFAULT_OUTPUT,
    concurrency: Annotated[
        int,
        typer.Option("--concurrency", "-c", min=1, help="Parallel HTTP requests."),
    ] = DEFAULT_CONCURRENCY,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="HTTP request timeout in seconds."),
    ] = DEFAULT_TIMEOUT,
) -> None:
    """Export all clubs (with board-member emails) to a CSV file."""
    settings = load_settings()
    try:
        rows = asyncio.run(
            collect_club_rows(
                base_url=settings.api_base,
                concurrency=concurrency,
                timeout=timeout,
            )
        )
    except httpx.HTTPError as exc:
        logger.error("Failed to fetch club index: %s", exc)
        raise typer.Exit(code=1) from exc

    write_csv(rows, output)
    logger.info("Wrote %d clubs to %s", len(rows), output)
