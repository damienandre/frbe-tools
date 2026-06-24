"""Tests for club/player rankings (in-memory DuckDB)."""

from __future__ import annotations

import datetime as dt

from frbe_tools.analysis.rankings import (
    club_history,
    latest_period,
    player_rating_evolution,
    rank_clubs,
    rank_clubs_by_growth,
    rank_clubs_by_strength,
    rank_rating_changes,
)
from frbe_tools.db.store import connect

COLS = (
    "period, idplayer, name, sex, birthday, affiliated, free_license, foreign_, region, idclub, elo"
)


def _seed(con) -> None:
    # club 10: two members (one youth, one foreign); club 20: one member, one free-license
    rows = [
        # period, id, name, sex, birthday, aff, free, foreign, region, club, elo
        ("2026-01-01", 1, "Old Strong", "M", "1980-05-01", True, False, False, "V", 10, 2200),
        ("2026-01-01", 2, "Young Gun", "M", "2010-03-01", True, False, False, "V", 10, 1600),
        ("2026-01-01", 3, "Foreign Fem", "F", "1995-02-01", True, False, True, "F", 10, 1900),
        ("2026-01-01", 4, "Club20 Member", "M", "1990-01-01", True, False, False, "F", 20, 2000),
        ("2026-01-01", 5, "Free Licensee", "F", "2008-01-01", False, True, False, "F", 20, 1400),
        # baseline period for growth/movers
        ("2025-01-01", 1, "Old Strong", "M", "1980-05-01", True, False, False, "V", 10, 2100),
        ("2025-01-01", 4, "Club20 Member", "M", "1990-01-01", True, False, False, "F", 20, 2050),
    ]
    for r in rows:
        con.execute(
            f"INSERT INTO player_snapshots ({COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)", list(r)
        )


def _con():
    con = connect(":memory:")
    _seed(con)
    return con


class TestRankClubs:
    def test_members_count(self) -> None:
        df = rank_clubs(_con(), "2026-01-01", statuses=("member",))
        counts = dict(zip(df["idclub"], df["players"], strict=True))
        assert counts == {10: 3, 20: 1}  # club 20's free-licence player is not a member

    def test_registered_includes_free_license(self) -> None:
        df = rank_clubs(_con(), "2026-01-01", statuses=("member", "free_license"))
        counts = dict(zip(df["idclub"], df["players"], strict=True))
        assert counts[20] == 2  # member + free licence

    def test_youth_cohort_under_20(self) -> None:
        # 2026 cohort: under 20 => born >= 2007. Only player 2 (2010) is a member youth.
        df = rank_clubs(_con(), "2026-01-01", statuses=("member",), max_age=19)
        assert dict(zip(df["idclub"], df["players"], strict=True)) == {10: 1}

    def test_gender_filter(self) -> None:
        df = rank_clubs(_con(), "2026-01-01", statuses=("member",), sex="F")
        assert dict(zip(df["idclub"], df["players"], strict=True)) == {10: 1}

    def test_foreign_filter(self) -> None:
        df = rank_clubs(_con(), "2026-01-01", statuses=("member",), foreign=True)
        assert dict(zip(df["idclub"], df["players"], strict=True)) == {10: 1}

    def test_rank_column_orders_desc(self) -> None:
        df = rank_clubs(_con(), "2026-01-01", statuses=("member",))
        assert df["rank"].to_list() == [1, 2]
        assert df["idclub"][0] == 10  # club 10 has more members


class TestStrengthAndGrowth:
    def test_avg_elo_min_players(self) -> None:
        df = rank_clubs_by_strength(_con(), "2026-01-01", metric="avg_elo", min_players=2)
        # club 20 has only 1 rated member -> dropped; club 10 avg of 2200,1600,1900
        assert df["idclub"].to_list() == [10]
        assert abs(df["score"][0] - 1900.0) < 0.01

    def test_top_n_sum(self) -> None:
        df = rank_clubs_by_strength(
            _con(), "2026-01-01", metric="top_n_sum", top_n=2, min_players=1
        )
        scores = dict(zip(df["idclub"], df["score"], strict=True))
        assert scores[10] == 2200 + 1900  # top 2 of club 10

    def test_growth(self) -> None:
        df = rank_clubs_by_growth(_con(), "2026-01-01", "2025-01-01", statuses=("member",))
        by_club = {r["idclub"]: r for r in df.to_dicts()}
        assert by_club[10]["count_then"] == 1 and by_club[10]["count_now"] == 3
        assert by_club[10]["delta"] == 2


class TestClubHistory:
    def test_history_per_period(self) -> None:
        df = club_history(_con(), 10)
        rows = {r["period"]: r for r in df.to_dicts()}
        assert rows[dt.date(2025, 1, 1)]["members"] == 1
        now = rows[dt.date(2026, 1, 1)]
        assert now["members"] == 3
        assert now["women"] == 1  # player 3
        assert now["youth"] == 1  # player 2 (born 2010, under 20 in 2026)
        assert now["foreign"] == 1  # player 3
        assert abs(now["avg_elo"] - 1900.0) < 0.01

    def test_month_filter_keeps_only_that_month(self) -> None:
        # seed has Jan periods only; month=7 -> empty, month=1 -> both periods
        assert club_history(_con(), 10, month=7).is_empty()
        assert club_history(_con(), 10, month=1).height == 2


class TestPlayerRankings:
    def test_rating_evolution(self) -> None:
        df = player_rating_evolution(_con(), 1)
        assert df["period"].to_list() == [dt.date(2025, 1, 1), dt.date(2026, 1, 1)]
        assert df["elo"].to_list() == [2100, 2200]

    def test_movers_gainers(self) -> None:
        df = rank_rating_changes(_con(), "2026-01-01", "2025-01-01", statuses=("member",))
        top = df.row(0, named=True)
        assert top["idplayer"] == 1 and top["delta"] == 100  # +100; player 4 lost 50

    def test_movers_losers(self) -> None:
        df = rank_rating_changes(_con(), "2026-01-01", "2025-01-01", ascending=True)
        assert df.row(0, named=True)["idplayer"] == 4  # -50

    def test_movers_club_filter(self) -> None:
        # club 10 only has player 1 rated in both periods; player 4 (club 20) excluded
        df = rank_rating_changes(_con(), "2026-01-01", "2025-01-01", idclub=10)
        assert df["idplayer"].to_list() == [1]


class TestLatestPeriod:
    def test_latest_period(self) -> None:
        assert latest_period(_con()) == dt.date(2026, 1, 1)
