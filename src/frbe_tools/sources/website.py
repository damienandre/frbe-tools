"""Scraper for the federation manager website database dumps.

The manager site at ``GestionLogin.php`` exposes the federation databases as
SQLite and/or DBF downloads behind a login form. This module will:

1. Authenticate against ``GestionLogin.php`` using the credentials in
   :class:`frbe_tools.config.Settings` (``FRBE_USERNAME`` / ``FRBE_PASSWORD``).
2. Locate and download the requested dump(s) into a local directory.
3. Return the path(s) for downstream ingestion by :mod:`frbe_tools.db.store`.

This is a stub: the exact login flow, form fields, and dump URLs still need to
be reverse-engineered from the live site. DBF dumps will be read via ``dbfread``
(a future dependency) at ingestion time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from frbe_tools.config import Settings

LOGIN_URL = "https://www.frbe-kbsb.be/sites/manager/GestionCOMMON/GestionLogin.php"

DumpFormat = Literal["sqlite", "dbf"]


def download_dumps(
    settings: Settings,
    dest: Path,
    *,
    fmt: DumpFormat = "sqlite",
) -> Path:
    """Authenticate and download the federation database dump.

    Args:
        settings: Resolved configuration, including website credentials.
        dest: Directory to write the downloaded dump into.
        fmt: Which dump format to retrieve (``"sqlite"`` or ``"dbf"``).

    Returns:
        Path to the downloaded dump.

    Raises:
        NotImplementedError: Always, until the login + download flow is built.
    """
    raise NotImplementedError(
        "Website dump scraping is not implemented yet. "
        "It will log in at GestionLogin.php using FRBE_USERNAME/FRBE_PASSWORD "
        "and download the requested dump."
    )
