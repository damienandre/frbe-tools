"""Runtime configuration loaded from the environment (and an optional ``.env``).

Settings are intentionally small and explicit. Credentials for the website
dumps live only in the environment / ``.env`` file and are never committed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://www.frbe-kbsb-ksb.be/api/v1"
DEFAULT_DATA_DIR = Path("data")
DEFAULT_DB_PATH = Path("data/frbe.duckdb")
DEFAULT_WEB_HOST = "127.0.0.1"
# 8080, not 8000, to avoid clashing with a continuously-running chesstide server.
DEFAULT_WEB_PORT = 8080


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved configuration for a single invocation."""

    api_base: str = DEFAULT_API_BASE
    data_dir: Path = DEFAULT_DATA_DIR
    db_path: Path = DEFAULT_DB_PATH
    username: str | None = None
    password: str | None = None
    web_host: str = DEFAULT_WEB_HOST
    web_port: int = DEFAULT_WEB_PORT

    @property
    def has_credentials(self) -> bool:
        """True when both website credentials are present."""
        return bool(self.username and self.password)


def _int_env(name: str, default: int) -> int:
    """Read an integer env var, falling back to ``default`` on a malformed value.

    Defensive on purpose: a bad ``FRBE_WEB_PORT`` must not crash every command
    (``load_settings`` is called by all of them), only ignore the typo.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %d.", name, raw, default)
        return default


def load_settings(*, env_file: str | os.PathLike[str] | None = None) -> Settings:
    """Build :class:`Settings` from environment variables.

    Reads an optional ``.env`` file first (without overriding values already
    set in the real environment), then maps the ``FRBE_*`` variables onto the
    dataclass fields.
    """
    load_dotenv(dotenv_path=env_file, override=False)

    return Settings(
        api_base=os.getenv("FRBE_API_BASE", DEFAULT_API_BASE).rstrip("/"),
        data_dir=Path(os.getenv("FRBE_DATA_DIR", str(DEFAULT_DATA_DIR))),
        db_path=Path(os.getenv("FRBE_DB_PATH", str(DEFAULT_DB_PATH))),
        username=os.getenv("FRBE_USERNAME") or None,
        password=os.getenv("FRBE_PASSWORD") or None,
        web_host=os.getenv("FRBE_WEB_HOST", DEFAULT_WEB_HOST),
        web_port=_int_env("FRBE_WEB_PORT", DEFAULT_WEB_PORT),
    )
