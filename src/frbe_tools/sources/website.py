"""Scraper for the FRBE/KBSB "Players Manager" database dumps.

The manager site exposes per-period player databases behind a login. The flow,
reverse-engineered from the live site, is:

1. GET ``GestionLogin.php`` to obtain a ``PHPSESSID`` cookie, then POST the
   ``Matricule`` / ``Password`` credentials (the ``Login`` submit button).
2. POST ``Database`` to ``Gestion.php``; the session redirects to
   ``ELO/database.php``, which lists every player period.
3. Each period offers a SQLite zip (``players_<YYYYMM>.zip`` containing
   ``players.sqlite``) and/or a DBF zip (``PLAYER_<YYYYMM>.ZIP`` containing
   ``PLAYER.DBF``). SQLite is preferred; DBF is the fallback for older periods.

Downloads are deliberately **synchronous** with a small delay between requests
to avoid overloading the federation's server.

Note: the federation has announced this system will be replaced by a new
membership platform around August 2026, so treat these endpoints as volatile.
"""

from __future__ import annotations

import io
import logging
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from frbe_tools.config import Settings

logger = logging.getLogger(__name__)

LOGIN_URL = "https://www.frbe-kbsb.be/sites/manager/GestionCOMMON/GestionLogin.php"
GESTION_URL = "https://www.frbe-kbsb.be/sites/manager/GestionCOMMON/Gestion.php"

DEFAULT_TIMEOUT = 60.0
# Seconds to wait between server requests; keep the scraper polite.
DEFAULT_REQUEST_DELAY = 1.0

# Server-side filenames: lowercase ``players_*`` = SQLite, uppercase
# ``PLAYER_*`` = DBF. The case distinction also excludes the ``-v3`` variants.
_SQLITE_RE = re.compile(r"players_(\d{6})\.zip$")
_DBF_RE = re.compile(r"PLAYER_(\d{6})\.ZIP$")
_ANCHOR_HREF_RE = re.compile(r'<a\s+[^>]*href=["\']([^"\']+)["\']', re.IGNORECASE)


class LoginError(RuntimeError):
    """Raised when authentication against the Players Manager fails."""


@dataclass(frozen=True, slots=True)
class PlayerDump:
    """A downloadable player database for one period."""

    period: str  # "YYYYMM", e.g. "202607"
    kind: str  # "sqlite" or "dbf"
    url: str  # absolute download URL of the zip

    @property
    def filename(self) -> str:
        """Local filename after extraction, e.g. ``player202607.sqlite``."""
        ext = "sqlite" if self.kind == "sqlite" else "dbf"
        return f"player{self.period}.{ext}"


def login(client: httpx.Client, settings: Settings) -> None:
    """Authenticate the client's session against the Players Manager.

    Raises:
        LoginError: if credentials are missing or rejected.
    """
    if not settings.has_credentials:
        raise LoginError("Missing FRBE_USERNAME / FRBE_PASSWORD (set them in .env).")

    client.get(LOGIN_URL)  # prime the PHPSESSID cookie
    resp = client.post(
        LOGIN_URL,
        data={
            "Matricule": settings.username,
            "Password": settings.password,
            "Login": "Login",
        },
    )
    resp.raise_for_status()
    low = resp.text.lower()
    if 'name="password"' in low and 'name="matricule"' in low:
        # The login form is served again -> credentials were rejected.
        raise LoginError("Login failed: the Players Manager rejected the credentials.")
    logger.info("Logged in to the Players Manager as %s", settings.username)


def fetch_database_page(client: httpx.Client) -> httpx.Response:
    """Open the database listing (follows the redirect to ELO/database.php)."""
    resp = client.post(GESTION_URL, data={"Database": "Database"})
    resp.raise_for_status()
    return resp


def parse_player_dumps(html: str, page_url: str) -> list[PlayerDump]:
    """Parse the database page into one preferred dump per period.

    SQLite is chosen when available, otherwise DBF. URLs are resolved against
    ``page_url`` (the database page lives in the ``ELO/`` directory). Periods are
    returned newest first.
    """
    base = httpx.URL(page_url)
    by_period: dict[str, dict[str, str]] = {}
    for href in _ANCHOR_HREF_RE.findall(html):
        name = href.rsplit("/", 1)[-1]
        if m := _SQLITE_RE.search(name):
            by_period.setdefault(m.group(1), {})["sqlite"] = str(base.join(href))
        elif m := _DBF_RE.search(name):
            by_period.setdefault(m.group(1), {})["dbf"] = str(base.join(href))

    dumps: list[PlayerDump] = []
    for period in sorted(by_period, reverse=True):
        urls = by_period[period]
        if "sqlite" in urls:
            dumps.append(PlayerDump(period, "sqlite", urls["sqlite"]))
        elif "dbf" in urls:
            dumps.append(PlayerDump(period, "dbf", urls["dbf"]))
    return dumps


def _extract_dump(content: bytes, dump: PlayerDump, dest: Path) -> Path:
    """Extract the single data file from a downloaded zip and save it renamed."""
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        members = [n for n in zf.namelist() if not n.endswith("/")]
        if not members:
            raise RuntimeError(f"Empty zip for {dump.period}")
        wanted = ".sqlite" if dump.kind == "sqlite" else ".dbf"
        candidates = [n for n in members if n.lower().endswith(wanted)]
        member = (
            candidates[0] if candidates else max(members, key=lambda n: zf.getinfo(n).file_size)
        )
        data = zf.read(member)

    out = dest / dump.filename
    out.write_bytes(data)
    return out


def download_player_dumps(
    settings: Settings,
    dest_dir: Path | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    delay: float = DEFAULT_REQUEST_DELAY,
    overwrite: bool = False,
) -> list[Path]:
    """Download and extract every available player database.

    For each period the SQLite dump is preferred, falling back to the DBF dump.
    Each zip is unzipped and its data file saved as ``player<YYYYMM>.<ext>`` in
    ``dest_dir`` (default: ``<data_dir>/player``). Runs synchronously with a
    delay between requests. Existing files are skipped unless ``overwrite``.

    Returns the list of saved file paths (newest period first).
    """
    dest = dest_dir or (settings.data_dir / "player")
    dest.mkdir(parents=True, exist_ok=True)

    saved: list[Path] = []
    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        login(client, settings)
        page = fetch_database_page(client)
        dumps = parse_player_dumps(page.text, str(page.url))
        logger.info(
            "Found %d player periods (newest: %s)", len(dumps), dumps[0].period if dumps else "none"
        )

        downloaded = 0
        for dump in dumps:
            out = dest / dump.filename
            if out.exists() and not overwrite:
                logger.info("Skipping %s (already present)", out.name)
                saved.append(out)
                continue

            if downloaded:
                time.sleep(delay)  # be polite between downloads
            downloaded += 1
            logger.info("Downloading %s (%s) ...", dump.period, dump.kind)
            try:
                resp = client.get(dump.url)
                resp.raise_for_status()
                saved.append(_extract_dump(resp.content, dump, dest))
            except (httpx.HTTPError, zipfile.BadZipFile, RuntimeError) as exc:
                logger.warning("Failed to fetch player dump %s: %s", dump.period, exc)

    return saved
