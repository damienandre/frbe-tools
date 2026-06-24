"""Tests for the Players Manager dump parsing (no network)."""

from __future__ import annotations

from frbe_tools.sources.website import PlayerDump, parse_player_dumps

PAGE_URL = "https://www.frbe-kbsb.be/sites/manager/ELO/database.php"

SAMPLE_HTML = """
<h4>Player databases</h4>
<a href="players_202607.zip">Player SQLite</a>
<a href="PLAYER_202607.ZIP">Player dbf</a>
<h4>ARCHIVES</h4>
2026-04: <a href="players_202604.zip">Player SQLite</a> - <a href="PLAYER_202604.ZIP">Player</a>
2018-10: <a href="PLAYER_201810.ZIP">Player</a> - <a href="PLAYER_201810-v3.ZIP">Player-v3</a>
<a href="Tournois%20ELO%202020-01.pdf">Tournois</a>
<a href="fide.sqlite.zip">Fide</a>
"""


class TestParsePlayerDumps:
    def test_prefers_sqlite_and_resolves_urls(self) -> None:
        dumps = parse_player_dumps(SAMPLE_HTML, PAGE_URL)
        by_period = {d.period: d for d in dumps}

        assert by_period["202607"].kind == "sqlite"
        assert by_period["202607"].url == (
            "https://www.frbe-kbsb.be/sites/manager/ELO/players_202607.zip"
        )
        assert by_period["202604"].kind == "sqlite"

    def test_falls_back_to_dbf_when_no_sqlite(self) -> None:
        dumps = parse_player_dumps(SAMPLE_HTML, PAGE_URL)
        by_period = {d.period: d for d in dumps}

        assert by_period["201810"].kind == "dbf"
        assert by_period["201810"].url.endswith("/ELO/PLAYER_201810.ZIP")

    def test_ignores_v3_variants_and_unrelated_files(self) -> None:
        dumps = parse_player_dumps(SAMPLE_HTML, PAGE_URL)
        periods = {d.period for d in dumps}
        assert periods == {"202607", "202604", "201810"}
        # the -v3 file must not create a separate entry or override the dbf url
        assert "-v3" not in by_url(dumps)

    def test_sorted_newest_first(self) -> None:
        dumps = parse_player_dumps(SAMPLE_HTML, PAGE_URL)
        assert [d.period for d in dumps] == ["202607", "202604", "201810"]


class TestPlayerDumpFilename:
    def test_sqlite_filename(self) -> None:
        assert PlayerDump("202607", "sqlite", "x").filename == "player202607.sqlite"

    def test_dbf_filename(self) -> None:
        assert PlayerDump("201810", "dbf", "x").filename == "player201810.dbf"


def by_url(dumps: list[PlayerDump]) -> str:
    return " ".join(d.url for d in dumps)
