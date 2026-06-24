"""Runtime configuration loaded from the environment (and an optional ``.env``).

Settings are intentionally small and explicit. Credentials for the website
dumps live only in the environment / ``.env`` file and are never committed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_API_BASE = "https://www.frbe-kbsb-ksb.be/api/v1"
DEFAULT_DATA_DIR = Path("data")
DEFAULT_DB_PATH = Path("data/frbe.duckdb")


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved configuration for a single invocation."""

    api_base: str = DEFAULT_API_BASE
    data_dir: Path = DEFAULT_DATA_DIR
    db_path: Path = DEFAULT_DB_PATH
    username: str | None = None
    password: str | None = None

    @property
    def has_credentials(self) -> bool:
        """True when both website credentials are present."""
        return bool(self.username and self.password)


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
    )
