"""Tests for club parsing and API fetching."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from frbe_tools.api.clubs import fetch_club_detail, fetch_club_list
from frbe_tools.api.models import build_club_row, extract_responsible_emails


def _boardmembers(*emails: str | None) -> dict[str, object]:
    return {f"role{i}": {"email": e} for i, e in enumerate(emails)}


class TestExtractResponsibleEmails:
    def test_dedupes_and_preserves_first_seen_order(self) -> None:
        detail = {"boardmembers": _boardmembers("a@x.be", "b@x.be", "a@x.be")}
        assert extract_responsible_emails(detail) == "a@x.be,b@x.be"

    def test_normalizes_case_and_whitespace(self) -> None:
        detail = {"boardmembers": _boardmembers("  A@X.be ", "a@x.BE")}
        assert extract_responsible_emails(detail) == "a@x.be"

    def test_skips_missing_and_empty_emails(self) -> None:
        detail = {"boardmembers": _boardmembers(None, "", "c@x.be")}
        assert extract_responsible_emails(detail) == "c@x.be"

    def test_ignores_non_dict_members(self) -> None:
        detail = {"boardmembers": {"a": "not-a-dict", "b": {"email": "d@x.be"}}}
        assert extract_responsible_emails(detail) == "d@x.be"

    def test_missing_boardmembers_key(self) -> None:
        assert extract_responsible_emails({}) == ""


class TestBuildClubRow:
    def test_prefers_detail_over_summary(self) -> None:
        summary = {"idclub": 101, "name_long": "old"}
        detail = {
            "idclub": 101,
            "name_long": "Club 101",
            "name_short": "C101",
            "enabled": True,
            "email_main": "main@x.be",
            "boardmembers": _boardmembers("a@x.be"),
        }
        row = build_club_row(summary, detail)
        assert row.idclub == 101
        assert row.name_long == "Club 101"
        assert row.enabled is True
        assert row.responsible_emails == "a@x.be"

    def test_falls_back_to_summary_when_detail_missing(self) -> None:
        summary = {"idclub": 7, "name_long": "S", "enabled": True}
        row = build_club_row(summary, None)
        assert row.idclub == 7
        assert row.name_long == "S"
        assert row.responsible_emails == ""

    def test_malformed_entry_raises(self) -> None:
        with pytest.raises((KeyError, TypeError, ValueError)):
            build_club_row({}, None)


class TestFetchClubList:
    def test_returns_list_payload(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[{"idclub": 1}, {"idclub": 2}])

        async def run() -> list[dict]:
            async with httpx.AsyncClient(
                base_url="https://example.test", transport=httpx.MockTransport(handler)
            ) as client:
                return await fetch_club_list(client)

        assert asyncio.run(run()) == [{"idclub": 1}, {"idclub": 2}]

    def test_rejects_non_list_payload(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"not": "a list"})

        async def run() -> None:
            async with httpx.AsyncClient(
                base_url="https://example.test", transport=httpx.MockTransport(handler)
            ) as client:
                await fetch_club_list(client)

        with pytest.raises(RuntimeError):
            asyncio.run(run())


class TestFetchClubDetail:
    def test_returns_json_on_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"idclub": 5})

        async def run() -> dict | None:
            async with httpx.AsyncClient(
                base_url="https://example.test", transport=httpx.MockTransport(handler)
            ) as client:
                return await fetch_club_detail(client, 5, asyncio.Semaphore(1))

        assert asyncio.run(run()) == {"idclub": 5}

    def test_returns_none_on_http_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        async def run() -> dict | None:
            async with httpx.AsyncClient(
                base_url="https://example.test", transport=httpx.MockTransport(handler)
            ) as client:
                return await fetch_club_detail(client, 5, asyncio.Semaphore(1))

        assert asyncio.run(run()) is None
