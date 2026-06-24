"""Data records returned by the API, plus defensive parsers.

The federation API is loosely typed, so parsing favours resilience: missing or
malformed fields degrade to sensible defaults rather than raising.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Column order for club CSV exports. Keep this in sync with ``ClubRow`` and
# ``ClubRow.as_dict`` — it is the single source of truth for the schema.
CLUB_CSV_FIELDS: tuple[str, ...] = (
    "idclub",
    "name_long",
    "name_short",
    "enabled",
    "email_main",
    "responsible_emails",
)


@dataclass(frozen=True, slots=True)
class ClubRow:
    """A single club row as exported to CSV."""

    idclub: int
    name_long: str
    name_short: str
    enabled: bool
    email_main: str
    responsible_emails: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "idclub": self.idclub,
            "name_long": self.name_long,
            "name_short": self.name_short,
            "enabled": self.enabled,
            "email_main": self.email_main,
            "responsible_emails": self.responsible_emails,
        }


def extract_responsible_emails(detail: dict[str, Any]) -> str:
    """Return a deduplicated, comma-separated list of board-member emails.

    Order is preserved (first occurrence wins) so the output is stable. Emails
    are normalized to lowercase and stripped; entries without an email or that
    are not dict-shaped are ignored.
    """
    boardmembers = detail.get("boardmembers") or {}
    seen: dict[str, None] = {}
    for member in boardmembers.values():
        if not isinstance(member, dict):
            continue
        email = member.get("email")
        if not email:
            continue
        normalized = email.strip().lower()
        if normalized and normalized not in seen:
            seen[normalized] = None
    return ",".join(seen)


def build_club_row(summary: dict[str, Any], detail: dict[str, Any] | None) -> ClubRow:
    """Assemble a :class:`ClubRow` from an index summary and optional detail.

    ``detail`` is preferred when available; otherwise the index ``summary`` is
    used and ``responsible_emails`` is left empty. Raises ``KeyError`` /
    ``TypeError`` / ``ValueError`` on irrecoverably malformed input so callers
    can skip the offending entry.
    """
    source = detail or summary
    return ClubRow(
        idclub=int(source.get("idclub", summary["idclub"])),
        name_long=source.get("name_long") or "",
        name_short=source.get("name_short") or "",
        enabled=bool(source.get("enabled", False)),
        email_main=source.get("email_main") or "",
        responsible_emails=extract_responsible_emails(detail) if detail else "",
    )
