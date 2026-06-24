"""Tests for the DuckDB store: parsing, era mapping, schema + views."""

from __future__ import annotations

import datetime as dt

from frbe_tools.db.store import (
    canonical_from_dbf,
    canonical_from_sqlite,
    connect,
    ingest_player_file,
)


class TestCanonicalMapping:
    def test_sqlite_member_with_foreign_and_free_license(self) -> None:
        rec = canonical_from_sqlite(
            {
                "IdNumber": "27",
                "Name": "Renet, Jack",
                "Sex": "M",
                "Birthday": "1956-04-24",
                "Fed": "V*",
                "Club": "703",
                "Affiliated": "1",
                "G": "0",
                "Elo": "2171",
                "FideId": "200875",
                "Died": "0",
                "NatPlayer": "NED",
            }
        )
        assert rec["idplayer"] == 27
        assert rec["affiliated"] is True
        assert rec["free_license"] is False
        assert rec["region"] == "V"
        assert rec["foreign_"] is True
        assert rec["birthday"] == dt.date(1956, 4, 24)
        assert rec["fide_id"] == 200875

    def test_sqlite_free_license_and_zero_club(self) -> None:
        rec = canonical_from_sqlite(
            {"IdNumber": "5", "Affiliated": "0", "G": "1", "Club": "0", "Fed": "", "FideId": "0"}
        )
        assert rec["affiliated"] is False
        assert rec["free_license"] is True
        assert rec["idclub"] is None  # 0 -> None
        assert rec["fide_id"] is None  # 0 -> None
        assert rec["region"] is None
        assert rec["foreign_"] is False

    def test_dbf_affiliated_is_not_suppress_and_no_free_license(self) -> None:
        member = canonical_from_dbf({"MATRICULE": "27", "SUPPRESS": False, "FEDERATION": "F"})
        lapsed = canonical_from_dbf({"MATRICULE": "28", "SUPPRESS": True, "FEDERATION": "F*"})
        assert member["affiliated"] is True
        assert lapsed["affiliated"] is False
        assert member["free_license"] is False  # never a free license in the DBF era
        assert lapsed["foreign_"] is True
        assert lapsed["region"] == "F"

    def test_tolerant_numeric_parsing(self) -> None:
        rec = canonical_from_dbf({"MATRICULE": "9", "SUPPRESS": False, "ELO_CALCUL": "-55.-"})
        assert rec["idplayer"] == 9
        assert rec["elo"] is None  # malformed -> NULL, not a crash


class TestSchemaAndViews:
    def _seed(self, con) -> None:
        # period 2025-01: id 1 member of club 10; id 2 free license; id 3 unaffiliated
        # period 2025-04: id 1 changed to club 20; id 2 became member; id 3 died
        rows = [
            # (period, id, name, club, affiliated, free_license, elo, died)
            ("2025-01-01", 1, "Alice", 10, True, False, 2000, False),
            ("2025-01-01", 2, "Bob", 11, False, True, 1500, False),
            ("2025-01-01", 3, "Cara", 12, False, False, 0, False),
            ("2025-04-01", 1, "Alice", 20, True, False, 2010, False),
            ("2025-04-01", 2, "Bob", 11, True, False, 1520, False),
            ("2025-04-01", 3, "Cara", 12, False, False, 0, True),
        ]
        for period, pid, name, club, aff, free, elo, died in rows:
            con.execute(
                "INSERT INTO player_snapshots "
                "(period, idplayer, name, affiliated, free_license, idclub, elo, died) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [period, pid, name, aff, free, club, elo, died],
            )

    def test_affiliation_status_enum(self) -> None:
        con = connect(":memory:")
        self._seed(con)
        statuses = dict(
            con.execute(
                "SELECT idplayer, status FROM player_affiliations WHERE period = '2025-01-01' "
                "ORDER BY idplayer"
            ).fetchall()
        )
        assert statuses == {1: "member", 2: "free_license", 3: "unaffiliated"}

    def test_players_view_latest_and_lifecycle(self) -> None:
        con = connect(":memory:")
        self._seed(con)
        row = con.execute(
            "SELECT deceased, deceased_since, first_seen, last_seen FROM players WHERE idplayer = 3"
        ).fetchone()
        assert row is not None
        assert row[0] is True
        assert row[1] == dt.date(2025, 4, 1)
        assert row[2] == dt.date(2025, 1, 1)
        assert row[3] == dt.date(2025, 4, 1)

    def test_rating_history_excludes_unrated(self) -> None:
        con = connect(":memory:")
        self._seed(con)
        ids = {
            r[0]
            for r in con.execute("SELECT DISTINCT idplayer FROM player_rating_history").fetchall()
        }
        assert ids == {1, 2}  # id 3 has elo 0 -> excluded


class TestIngestFile:
    def test_ingest_sqlite_file_roundtrip(self, tmp_path) -> None:
        import sqlite3

        src = tmp_path / "player202501.sqlite"
        sc = sqlite3.connect(src)
        sc.execute(
            "CREATE TABLE players (IdNumber INT, Name TEXT, Affiliated INT, G INT, Club INT, "
            "Fed TEXT, Elo INT, Birthday TEXT)"
        )
        sc.execute("INSERT INTO players VALUES (1, 'Alice', 1, 0, 10, 'V', 2000, '1990-01-01')")
        sc.commit()
        sc.close()

        con = connect(":memory:")
        n = ingest_player_file(con, src)
        assert n == 1
        row = con.execute(
            "SELECT period, idplayer, name, affiliated, region FROM player_snapshots"
        ).fetchone()
        assert row == (dt.date(2025, 1, 1), 1, "Alice", True, "V")
        # second ingest is skipped (idempotent)
        assert ingest_player_file(con, src) == 0
