"""Factory for the shared :class:`httpx.AsyncClient` used by API calls."""

from __future__ import annotations

import httpx

from frbe_tools.config import DEFAULT_API_BASE

DEFAULT_CONCURRENCY = 10
DEFAULT_TIMEOUT = 30.0


def create_client(
    *,
    base_url: str = DEFAULT_API_BASE,
    timeout: float = DEFAULT_TIMEOUT,
    concurrency: int = DEFAULT_CONCURRENCY,
    token: str | None = None,
) -> httpx.AsyncClient:
    """Create an ``AsyncClient`` configured for the federation API.

    The connection pool is sized to ``concurrency`` so it matches the number of
    in-flight requests the callers allow. Pass ``token`` to authenticate against
    the ``clb``/``mgmt`` endpoint tiers (unused by the public endpoints today).
    """
    limits = httpx.Limits(
        max_connections=concurrency,
        max_keepalive_connections=concurrency,
    )
    headers = {"Authorization": f"Bearer {token}"} if token else None
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=timeout,
        limits=limits,
        headers=headers,
    )
