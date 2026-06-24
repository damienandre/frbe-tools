"""Club endpoints of the public API (``/clubs/anon/club``).

Fetches the public club index and, for each club, the detailed record (which
carries the board members and their emails).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from frbe_tools.api.client import (
    DEFAULT_CONCURRENCY,
    DEFAULT_TIMEOUT,
    create_client,
)
from frbe_tools.api.models import ClubRow, build_club_row
from frbe_tools.config import DEFAULT_API_BASE

logger = logging.getLogger(__name__)

CLUBS_PATH = "/clubs/anon/club"


async def fetch_club_list(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fetch the public club index. Raises on transport/HTTP errors."""
    response = await client.get(CLUBS_PATH)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected club list payload type: {type(payload).__name__}")
    return payload


async def fetch_club_detail(
    client: httpx.AsyncClient,
    idclub: int,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any] | None:
    """Fetch one club's detail record.

    A failure here is non-fatal: it is logged and ``None`` is returned so the
    caller can fall back to the index summary.
    """
    async with semaphore:
        try:
            response = await client.get(f"{CLUBS_PATH}/{idclub}")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            logger.warning("Failed to fetch club %s: %s", idclub, exc)
            return None


async def collect_club_rows(
    *,
    base_url: str = DEFAULT_API_BASE,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[ClubRow]:
    """Fetch all clubs and return rows sorted by ``idclub``.

    A failed index fetch propagates (fatal); failed per-club details degrade to
    the index summary; malformed entries are logged and skipped.
    """
    semaphore = asyncio.Semaphore(concurrency)
    async with create_client(base_url=base_url, timeout=timeout, concurrency=concurrency) as client:
        clubs = await fetch_club_list(client)
        logger.info("Fetched %d clubs from index", len(clubs))
        details = await asyncio.gather(
            *(fetch_club_detail(client, club["idclub"], semaphore) for club in clubs)
        )

    rows: list[ClubRow] = []
    for summary, detail in zip(clubs, details, strict=True):
        try:
            rows.append(build_club_row(summary, detail))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Skipping malformed club entry %r: %s", summary, exc)

    rows.sort(key=lambda row: row.idclub)
    return rows
