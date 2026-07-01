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
uv run frbe analyze distribution rating --region F  # histogram of players by rating (or age)
uv run frbe analyze retention 351 --by age          # do a club's new members stay? (cohort retention)
uv run frbe web                        # local dashboard at http://127.0.0.1:8080
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
  periods), `club_history` (one club's metrics across all periods),
  `player_rating_evolution`, `rank_rating_changes`, `player_distribution`
  (histogram of players by rating, age or club tenure, scoped to a club /
  region-federation / global; rating keeps unrated as its own bucket, tenure is
  years since first joining the current club), `players_in_bucket` (the
  drill-down counterpart: the individual players inside one distribution bucket,
  identified by its lower bound — same scope/bin params), `club_retention`
  (join-**season** cohort retention triangle for one club: of members who first
  joined in a season — labelled `2024/25`, the Jul/Oct/Jan/Apr snapshot quarters
  grouped via `_season_year`, *not* calendar years — the % still members of *that*
  club +1y/+2y/… seasons later — point-in-time, same-club, left- *and*
  right-censored; optional `by` split on sex/rated/age at join time). Ages use
  **birth-year cohorts** (`year - birth_year`). Status
  presets in `STATUS_PRESETS` (member/registered/free_license/unaffiliated/all).
- **`web/`** — local dashboard (FastAPI + Jinja2 + HTMX), a thin presentation
  layer over `analysis/rankings.py`. `app.create_app(settings)` is the factory
  (plus a module-level `app` for `uvicorn …:app`); routes return full pages and
  HTMX table fragments (`/<area>` page + `/<area>/table` fragment). The `get_db`
  dependency opens a **read-only** `store.connect(..., read_only=True)` *per
  request* and closes it in a `finally` — important, because a DuckDB read lock
  held for the process lifetime would block `db build` (verified: it raises
  "Conflicting lock is held"). Per-request open/close means the lock is only held
  in-flight; a request landing mid-rebuild returns a 503. htmx + Chart.js are
  vendored under `web/static/`.

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
- **`commands/`** — one Typer sub-app per area (`clubs`, `scrape`, `db`,
  `analyze`), mounted onto the root app in `cli.py` (entry point
  `frbe = frbe_tools.cli:app`). `web` is the exception: a single root command
  (`app.command(name="web")(web.serve)`), not a sub-app.

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
  and mock HTTP with `httpx.MockTransport` (no pytest-asyncio dependency). Web
  routes are tested with `fastapi.testclient.TestClient` over a seeded temp
  DuckDB (`tests/test_web.py`).
- **Web dependency injection (gotcha).** The shared filter-param dependencies in
  `web/app.py` are injected via the default-value form (`p: dict = Depends(...)`),
  *not* `Annotated[dict, Depends(...)]`: FastAPI treats a `dict`-typed `Annotated`
  param as a query model and 422s. Hence the `B008` per-file ignore in
  `pyproject.toml` — keep both together.
- Stubs raise `NotImplementedError` with a message describing the intended
  behavior; CLI commands catch it and exit cleanly. Replace the body, keep the
  signature.
