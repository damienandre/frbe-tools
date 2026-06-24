# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Objective

`frbe-tools` is a CLI + library to access and analyze data of the Belgian chess
federation (FRBE/KBSB/KSB/RBCF) from multiple sources, consolidate it into a
local analytical database, and produce analyses (club rankings, evolution over
time, etc.). Today only the public-API clubs export is implemented; the scraper,
DuckDB store, and analyses are working stubs awaiting implementation.

## Commands

Uses [uv](https://docs.astral.sh/uv/) with Python 3.14.

```bash
uv sync                                # install runtime + dev deps
uv run frbe clubs export               # export clubs -> data/clubs.csv
uv run frbe scrape players             # download player dumps -> data/player/
uv run frbe db build                   # ingest player dumps + clubs into DuckDB
uv run frbe db info                    # summarize the database
uv run frbe analyze clubs --max-age 19 # rank clubs (here: youth); also strength/growth/player/movers
uv run python -m frbe_tools --help     # module entry point
uv run ruff check                      # lint
uv run ruff format                     # format
uv run ty check                        # type check (Astral's ty)
uv run pytest                          # all tests
uv run pytest tests/test_clubs.py::TestBuildClubRow   # a single test class
```

CI (`.github/workflows/ci.yml`) runs ruff check, ruff format --check, ty, and
pytest on Python 3.14 — match it locally before pushing.

**iCloud / venv gotcha (this repo lives under `~/Documents`, which iCloud syncs):**
an in-tree `.venv` breaks intermittently — iCloud re-applies the macOS `hidden`
flag to its files, and Python ≥3.12 ignores hidden `.pth` files, so the editable
install silently stops resolving (`ModuleNotFoundError: No module named
'frbe_tools'`). Fix is to keep the venv outside the synced tree via the
(gitignored) `.envrc`, which sets `UV_PROJECT_ENVIRONMENT=$HOME/.venvs/frbe-tools`
(needs `direnv`; run `direnv allow` once). If `uv run` ever fails with that
import error, the venv got re-hidden — recreate it: `rm -rf "$UV_PROJECT_ENVIRONMENT" && uv sync`.

## Architecture

`src/` layout package `frbe_tools`. Layers, in dependency order:

- **`config.py`** — `Settings` dataclass built by `load_settings()` from
  `FRBE_*` env vars (and an optional `.env` via python-dotenv). The single place
  that reads the environment.
- **`api/`** — typed access to the public REST API. `client.create_client()` is
  the one `httpx.AsyncClient` factory (sizes the pool to `concurrency`, accepts a
  future bearer `token`). `clubs.py` holds endpoint calls; `models.py` holds the
  records (`ClubRow`) and defensive parsers.
- **`sources/`** — non-API sources. `website.py` logs into the Players Manager
  (`GestionLogin.php` → `Gestion.php` → `ELO/database.php`) with `.env`
  credentials and downloads per-period player dumps (SQLite preferred, DBF
  fallback) into `data/player/` as `player<YYYYMM>.{sqlite,dbf}`. Synchronous.
- **`db/store.py`** — DuckDB store. Flow is **api/sources → db → analysis**.
  `connect()` ensures the schema; `ingest_player_dir()` loads the dumps;
  `load_clubs()` populates the club dim from the API. Bulk insert goes through a
  Polars/Arrow frame (row-by-row `executemany` against the PK is ~90× slower).
- **`analysis/rankings.py`** — club/player rankings over the store, each "as of"
  a period, returning Polars frames: `rank_clubs` (one parameterized count
  function: status/age/sex/foreign/region/rated/new), `rank_clubs_by_strength`
  (Elo aggregates incl. top-N boards), `rank_clubs_by_growth` (between two
  periods), `player_rating_evolution`, `rank_rating_changes`. Ages use
  **birth-year cohorts** (`year - birth_year`). Status presets in
  `STATUS_PRESETS` (member/registered/free_license/unaffiliated/all).

### Database schema (`db/store.py`)

Star schema. The **fact** `player_snapshots` has grain (period × player) and
faithfully mirrors each quarterly dump's full record — descriptive attributes
(name, sex, birthday, nationality, fide_id, died) live here too, because they
vary over time. `period` is the quarter-start `DATE`. Dimensions: `clubs` (from
the API) and `players` (a *derived view*: latest identity + first/last_seen +
deceased_since). Views `player_affiliations` (status ∈ member/free_license/
unaffiliated) and `player_rating_history` answer the time-dependent
membership and rating-evolution questions. `source_files` records provenance.

Two source eras are mapped to one canonical schema by `canonical_from_sqlite`
(2018-10+, English fields) and `canonical_from_dbf` (2007–2018, French fields).
Validated era mappings — change these only with care:
- `affiliated`: SQLite `Affiliated`; DBF `NOT SUPPRESS`.
- `free_license`: SQLite `G`; DBF always `False` (no such concept then).
- `Fed` `*` → foreigner (`foreign_`); strip it for `region` (V/F/D).
Parsing is tolerant (malformed numerics/dates → NULL); `idclub`/`fide_id` 0 → NULL.
- **`commands/`** — one Typer sub-app per area (`clubs`, `scrape`, `analyze`),
  mounted onto the root app in `cli.py` (entry point `frbe = frbe_tools.cli:app`).

The API tiers matter when extending: `anon` (public, no auth), `clb` (club-level,
bearer token), `mgmt` (federation admin). Only `anon` is wired up.

## Conventions

- **CSV schema is single-sourced.** `CLUB_CSV_FIELDS`, the `ClubRow` fields, and
  `ClubRow.as_dict()` (all in `api/models.py`) must stay in sync — change them
  together. Rows are always sorted by `idclub` before output (deterministic).
- **Failure semantics (preserve these):** a failed *index* fetch is fatal
  (`fetch_club_list` raises → CLI exits 1); a failed per-club *detail* fetch is
  non-fatal (`fetch_club_detail` logs a warning, returns `None`, row falls back
  to the index summary); malformed individual entries are logged and skipped.
- **Defensive parsing.** The API is loosely typed; parsers in `models.py` use
  `.get(...) or default` and ignore non-dict/empty values rather than raising.
- **DuckDB snapshots.** All ingested data is tagged with a `snapshot_date` so
  analyses can compare clubs/members over time — keep that when filling stubs.
- **Async tests without a plugin.** Tests drive async code via `asyncio.run()`
  and mock HTTP with `httpx.MockTransport` (no pytest-asyncio dependency).
- Stubs raise `NotImplementedError` with a message describing the intended
  behavior; CLI commands catch it and exit cleanly. Replace the body, keep the
  signature.
