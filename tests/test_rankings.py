"""Tests for club/player rankings (in-memory DuckDB)."""

from __future__ import annotations

import datetime as dt

from frbe_tools.analysis.rankings import (
    club_history,
    club_retention,
    latest_period,
    player_distribution,
    player_rating_evolution,
    players_in_bucket,
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


class TestDistribution:
    def test_rating_buckets_global(self) -> None:
        # 2026 members: elo 2200/1600/1900/2000 -> four bands of 1 each.
        df = player_distribution(_con(), "2026-01-01", dimension="rating")
        counts = dict(zip(df["bucket"], df["players"], strict=True))
        assert counts == {"1600-1699": 1, "1900-1999": 1, "2000-2099": 1, "2200-2299": 1}
        assert df["pct"].to_list() == [25.0, 25.0, 25.0, 25.0]

    def test_rating_custom_bin(self) -> None:
        df = player_distribution(_con(), "2026-01-01", dimension="rating", bin_size=500)
        counts = dict(zip(df["bucket"], df["players"], strict=True))
        assert counts == {"1500-1999": 2, "2000-2499": 2}  # 1600/1900 vs 2000/2200

    def test_unrated_bucket(self) -> None:
        con = _con()
        con.execute(
            f"INSERT INTO player_snapshots ({COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ["2026-01-01", 9, "No Rating", "M", "1992-01-01", True, False, False, "V", 10, 0],
        )
        df = player_distribution(con, "2026-01-01", dimension="rating")
        counts = dict(zip(df["bucket"], df["players"], strict=True))
        assert counts["unrated"] == 1
        assert df["bucket"].to_list()[0] == "unrated"  # sorted first, before the bands

    def test_hide_unrated(self) -> None:
        con = _con()
        con.execute(
            f"INSERT INTO player_snapshots ({COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ["2026-01-01", 9, "No Rating", "M", "1992-01-01", True, False, False, "V", 10, 0],
        )
        df = player_distribution(con, "2026-01-01", dimension="rating", include_unrated=False)
        buckets = df["bucket"].to_list()
        assert "unrated" not in buckets
        # pct now covers the 4 rated members only (25% each), not 5 incl. unrated.
        assert df["pct"].to_list() == [25.0, 25.0, 25.0, 25.0]

    def test_age_cohorts(self) -> None:
        # 2026 cohorts: 1980->46, 2010->16, 1995->31, 1990->36.
        df = player_distribution(_con(), "2026-01-01", dimension="age")
        counts = dict(zip(df["bucket"], df["players"], strict=True))
        assert counts == {"10-19": 1, "30-39": 2, "40-49": 1}

    def test_tenure_distribution(self) -> None:
        con = connect(":memory:")
        ins = f"INSERT INTO player_snapshots ({COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        rows = [
            # veteran: in club 10 since 2018 (with a gap) -> ~8 years tenure in 2026
            ("2018-01-01", 1, "Vet", "M", "1980-01-01", True, False, False, "V", 10, 2000),
            ("2026-01-01", 1, "Vet", "M", "1980-01-01", True, False, False, "V", 10, 2000),
            # newcomer: first appears 2026 -> 0 years
            ("2026-01-01", 2, "New", "M", "2000-01-01", True, False, False, "V", 10, 1500),
            # switcher: club 20 in 2018, club 10 now -> tenure counts the current club only
            ("2018-01-01", 3, "Switch", "M", "1980-01-01", True, False, False, "V", 20, 1800),
            ("2026-01-01", 3, "Switch", "M", "1980-01-01", True, False, False, "V", 10, 1800),
        ]
        for r in rows:
            con.execute(ins, list(r))
        df = player_distribution(con, "2026-01-01", dimension="tenure")
        counts = dict(zip(df["bucket"], df["players"], strict=True))
        assert counts == {"0-1": 2, "8-9": 1}  # newcomer + switcher young; veteran old
        assert df["bucket"].to_list()[0] == "0-1"  # ascending

    def test_club_scope(self) -> None:
        df = player_distribution(_con(), "2026-01-01", dimension="rating", idclub=10)
        assert df["players"].sum() == 3  # club 10 has 3 members

    def test_region_scope(self) -> None:
        df = player_distribution(_con(), "2026-01-01", dimension="rating", region="F")
        assert df["players"].sum() == 2  # players 3 and 4 are region F members

    def test_invalid_bin_raises(self) -> None:
        for bad in (0, -5):
            try:
                player_distribution(_con(), "2026-01-01", dimension="rating", bin_size=bad)
            except ValueError:
                continue
            raise AssertionError(f"expected ValueError for bin_size={bad}")

    def test_invalid_dimension_raises(self) -> None:
        try:
            player_distribution(_con(), "2026-01-01", dimension="height")
        except ValueError:
            return
        raise AssertionError("expected ValueError for an unknown dimension")


class TestPlayersInBucket:
    def test_rating_bucket(self) -> None:
        # The 1600-1699 band holds only Young Gun (player 2, elo 1600, born 2010).
        df = players_in_bucket(_con(), "2026-01-01", dimension="rating", bucket_low=1600)
        assert df["idplayer"].to_list() == [2]
        row = df.row(0, named=True)
        assert row["name"] == "Young Gun"
        assert row["rating"] == 1600
        assert row["age"] == 16  # 2026 - 2010
        assert "club" in df.columns

    def test_rating_bucket_excludes_neighbours(self) -> None:
        # Old Strong (2200) is in its own band, not the 1900 one.
        df = players_in_bucket(_con(), "2026-01-01", dimension="rating", bucket_low=1900)
        assert df["idplayer"].to_list() == [3]  # Foreign Fem (1900) only

    def test_strongest_first(self) -> None:
        # A wide bin lands several in one band; they come back strongest first.
        # bin 1000 -> band [1000,2000) holds 1900 (p3) and 1600 (p2).
        df = players_in_bucket(
            _con(), "2026-01-01", dimension="rating", bucket_low=1000, bin_size=1000
        )
        assert df["idplayer"].to_list() == [3, 2]  # 1900 (player3) before 1600 (player2)

    def test_unrated_bucket(self) -> None:
        con = _con()
        con.execute(
            f"INSERT INTO player_snapshots ({COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ["2026-01-01", 9, "No Rating", "M", "1992-01-01", True, False, False, "V", 10, 0],
        )
        df = players_in_bucket(con, "2026-01-01", dimension="rating", bucket_low=None)
        assert df["idplayer"].to_list() == [9]
        assert df.row(0, named=True)["rating"] is None  # unrated -> NULL rating

    def test_age_bucket(self) -> None:
        # 30-39 cohort: Foreign Fem (1995->31) and Club20 Member (1990->36).
        df = players_in_bucket(_con(), "2026-01-01", dimension="age", bucket_low=30)
        assert sorted(df["idplayer"].to_list()) == [3, 4]

    def test_club_and_region_scope(self) -> None:
        c = players_in_bucket(_con(), "2026-01-01", dimension="rating", bucket_low=1900, idclub=10)
        assert c["idplayer"].to_list() == [3]
        f = players_in_bucket(_con(), "2026-01-01", dimension="rating", bucket_low=2000, region="F")
        assert f["idplayer"].to_list() == [4]

    def test_tenure_bucket(self) -> None:
        con = connect(":memory:")
        ins = f"INSERT INTO player_snapshots ({COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        rows = [
            ("2018-01-01", 1, "Vet", "M", "1980-01-01", True, False, False, "V", 10, 2000),
            ("2026-01-01", 1, "Vet", "M", "1980-01-01", True, False, False, "V", 10, 2000),
            ("2026-01-01", 2, "New", "M", "2000-01-01", True, False, False, "V", 10, 1500),
        ]
        for r in rows:
            con.execute(ins, list(r))
        # The 0-1 tenure band is the newcomer; the veteran sits in the ~8y band.
        df = players_in_bucket(con, "2026-01-01", dimension="tenure", bucket_low=0)
        assert df["idplayer"].to_list() == [2]
        assert df.row(0, named=True)["since"] == 2026  # joined in 2026
        vet = players_in_bucket(con, "2026-01-01", dimension="tenure", bucket_low=8)
        assert vet["idplayer"].to_list() == [1]
        assert vet.row(0, named=True)["since"] == 2018

    def test_unrated_bucket_for_non_rating_raises(self) -> None:
        for dim in ("age", "tenure"):
            try:
                players_in_bucket(_con(), "2026-01-01", dimension=dim, bucket_low=None)
            except ValueError:
                continue
            raise AssertionError(f"expected ValueError for {dim} with bucket_low=None")

    def test_invalid_dimension_raises(self) -> None:
        try:
            players_in_bucket(_con(), "2026-01-01", dimension="height", bucket_low=0)
        except ValueError:
            return
        raise AssertionError("expected ValueError for an unknown dimension")


class TestRetention:
    @staticmethod
    def _retention_con():
        # Seasons 2020/21..2023/24, anchored on October snapshots (month >= 7, so
        # season-year == calendar year). 2020 is the first season -> its cohorts
        # are left-censored. Club 10 measurable cohorts: 2021/22 and 2022/23.
        con = connect(":memory:")
        ins = f"INSERT INTO player_snapshots ({COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        rows = [
            # p1 junior+rated: joins 2021/22, stays through 2023/24 -> retained +1,+2
            ("2021-10-01", 1, "P1", "M", "2010-01-01", True, False, False, "V", 10, 1500),
            ("2022-10-01", 1, "P1", "M", "2010-01-01", True, False, False, "V", 10, 1550),
            ("2023-10-01", 1, "P1", "M", "2010-01-01", True, False, False, "V", 10, 1600),
            # p2 adult+rated: joins 2021/22, leaves after -> churns at +1
            ("2021-10-01", 2, "P2", "M", "1990-01-01", True, False, False, "V", 10, 1800),
            # p3 adult+unrated: joins 2022/23, stays 2023/24 -> retained +1
            ("2022-10-01", 3, "P3", "F", "1985-01-01", True, False, False, "V", 10, 0),
            ("2023-10-01", 3, "P3", "F", "1985-01-01", True, False, False, "V", 10, 0),
            # p4: present in the first season (2020/21) -> left-censored, excluded
            ("2020-10-01", 4, "P4", "M", "2008-01-01", True, False, False, "V", 10, 0),
            ("2023-10-01", 4, "P4", "M", "2008-01-01", True, False, False, "V", 10, 1200),
            # p5 adult: joins club 10 in 2021/22, switches to club 20 -> churn for 10
            ("2021-10-01", 5, "P5", "M", "1995-01-01", True, False, False, "V", 10, 1700),
            ("2022-10-01", 5, "P5", "M", "1995-01-01", True, False, False, "V", 20, 1700),
            ("2023-10-01", 5, "P5", "M", "1995-01-01", True, False, False, "V", 20, 1700),
        ]
        for r in rows:
            con.execute(ins, list(r))
        return con

    def test_triangle_and_censoring(self) -> None:
        df = club_retention(self._retention_con(), 10)
        rows = {r["cohort"]: r for r in df.to_dicts()}
        # 2021/22: P1, P2, P5 (P4 excluded as left-censored); only P1 stays.
        assert rows["2021/22"]["size"] == 3
        assert rows["2021/22"]["+1y"] == 33.3  # 1 of 3
        assert rows["2021/22"]["+2y"] == 33.3
        # 2022/23: P3 only; +2y is right-censored (2024/25 unseen) -> null.
        assert rows["2022/23"]["size"] == 1
        assert rows["2022/23"]["+1y"] == 100.0
        assert rows["2022/23"]["+2y"] is None

    def test_season_label_collapses_quarters(self) -> None:
        # The four snapshot quarters of one season (Jul/Oct/Jan/Apr) must form a
        # single cohort, not split a Jan-joiner into the next calendar year.
        con = connect(":memory:")
        ins = f"INSERT INTO player_snapshots ({COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        rows = [
            # founder in season 2023/24 -> first season, left-censored
            ("2023-10-01", 9, "F", "M", "1980-01-01", True, False, False, "V", 10, 2000),
            # X across Oct/Jan/Apr of 2024/25 (one season!), back next season -> +1
            ("2024-10-01", 1, "X", "M", "1990-01-01", True, False, False, "V", 10, 1500),
            ("2025-01-01", 1, "X", "M", "1990-01-01", True, False, False, "V", 10, 1500),
            ("2025-04-01", 1, "X", "M", "1990-01-01", True, False, False, "V", 10, 1500),
            ("2025-10-01", 1, "X", "M", "1990-01-01", True, False, False, "V", 10, 1500),
            # Y joins same season (Jan only), gone next season -> churn
            ("2025-01-01", 2, "Y", "F", "1990-01-01", True, False, False, "V", 10, 1400),
        ]
        for r in rows:
            con.execute(ins, list(r))
        df = club_retention(con, 10)
        # One cohort labelled as a season span; X's Jan/Apr appearance does NOT
        # spawn a separate 2025/26 cohort.
        assert df["cohort"].to_list() == ["2024/25"]
        assert df.to_dicts()[0]["size"] == 2  # X and Y, one season
        assert df.to_dicts()[0]["+1y"] == 50.0  # only X returns

    def test_switcher_counts_as_churn(self) -> None:
        # P5 switched to club 20 the next season, so it must NOT count as retained.
        df = club_retention(self._retention_con(), 10)
        assert {r["cohort"]: r["+1y"] for r in df.to_dicts()}["2021/22"] == 33.3

    def test_split_by_age_groups_and_order(self) -> None:
        df = club_retention(self._retention_con(), 10, by="age")
        assert df.columns[0] == "group"
        recs = [(r["group"], r["cohort"]) for r in df.to_dicts()]
        # juniors come before adults (display order, not alphabetical).
        assert recs[0] == ("junior", "2021/22")
        by_grp = {(r["group"], r["cohort"]): r for r in df.to_dicts()}
        assert by_grp[("junior", "2021/22")]["+1y"] == 100.0  # P1 stays
        assert by_grp[("adult", "2021/22")]["+1y"] == 0.0  # P2, P5 both gone

    def test_max_horizon_caps_columns(self) -> None:
        df = club_retention(self._retention_con(), 10, max_horizon=1)
        assert [c for c in df.columns if c.startswith("+")] == ["+1y"]

    def test_empty_club_returns_empty_frame(self) -> None:
        df = club_retention(self._retention_con(), 999)
        assert df.is_empty()
        assert df.columns == ["cohort", "size"]

    def test_invalid_by_raises(self) -> None:
        try:
            club_retention(self._retention_con(), 10, by="height")
        except ValueError:
            return
        raise AssertionError("expected ValueError for an unknown by=")


class TestLatestPeriod:
    def test_latest_period(self) -> None:
        assert latest_period(_con()) == dt.date(2026, 1, 1)
