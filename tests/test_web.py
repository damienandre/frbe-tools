"""Tests for the local web UI (FastAPI routes over a seeded temp DuckDB)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from frbe_tools.config import Settings
from frbe_tools.db.store import connect
from frbe_tools.web.app import create_app

COLS = (
    "period, idplayer, name, sex, birthday, affiliated, free_license, foreign_, region, idclub, elo"
)

ROWS = [
    ("2026-01-01", 1, "Old Strong", "M", "1980-05-01", True, False, False, "V", 10, 2200),
    ("2026-01-01", 2, "Young Gun", "M", "2010-03-01", True, False, False, "V", 10, 1600),
    ("2026-01-01", 3, "Foreign Fem", "F", "1995-02-01", True, False, True, "F", 10, 1900),
    ("2026-01-01", 4, "Club20 Member", "M", "1990-01-01", True, False, False, "F", 20, 2000),
    ("2025-01-01", 1, "Old Strong", "M", "1980-05-01", True, False, False, "V", 10, 2100),
    ("2025-01-01", 4, "Club20 Member", "M", "1990-01-01", True, False, False, "F", 20, 2050),
]


def _seeded_db(path: Path) -> None:
    con = connect(path)
    insert = f"INSERT INTO player_snapshots ({COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
    for r in ROWS:
        con.execute(insert, list(r))
    con.execute("INSERT INTO clubs (idclub, name_short) VALUES (10, 'Alpha'), (20, 'Beta')")
    con.close()  # release the write lock so the app can open read-only


def _client(tmp_path: Path) -> TestClient:
    db = tmp_path / "frbe.duckdb"
    _seeded_db(db)
    return TestClient(create_app(Settings(db_path=db)))


def test_index_shows_summary(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert "latest period" in r.text


def test_clubs_page_and_table(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/clubs").status_code == 200
    r = client.get("/clubs/table", params={"status": "member"})
    assert r.status_code == 200
    assert "Alpha" in r.text  # club 10 name from the dim
    assert "row(s)" in r.text


def test_clubs_table_youth_filter(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/clubs/table", params={"max_age": 19, "period": "202601"})
    assert r.status_code == 200
    assert "Alpha" in r.text  # only club 10 has a youth member
    assert "Beta" not in r.text


def test_strength_growth_movers_tables(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/strength/table", params={"min_players": 1}).status_code == 200
    assert client.get("/growth/table", params={"baseline": "202501"}).status_code == 200
    r = client.get("/movers/table", params={"baseline": "202501", "period": "202601"})
    assert r.status_code == 200
    assert "Old Strong" in r.text  # +100 gainer


def test_movers_club_filter(tmp_path: Path) -> None:
    r = _client(tmp_path).get(
        "/movers/table", params={"baseline": "202501", "period": "202601", "club": 10}
    )
    assert r.status_code == 200
    assert "Old Strong" in r.text
    assert "Club20 Member" not in r.text  # club 20 excluded


def test_club_and_player_detail(tmp_path: Path) -> None:
    client = _client(tmp_path)
    rc = client.get("/clubs/10")
    assert rc.status_code == 200
    assert "Alpha" in rc.text and "countsChart" in rc.text
    rp = client.get("/players/1")
    assert rp.status_code == 200
    assert "Old Strong" in rp.text and "eloChart" in rp.text


def test_missing_database_returns_503(tmp_path: Path) -> None:
    client = TestClient(
        create_app(Settings(db_path=tmp_path / "nope.duckdb")), raise_server_exceptions=False
    )
    r = client.get("/")
    assert r.status_code == 503
    assert "frbe db build" in r.text
