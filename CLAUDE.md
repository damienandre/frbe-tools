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
uv run frbe clubs export -o clubs.csv  # run the implemented feature
uv run python -m frbe_tools --help     # module entry point
uv run ruff check                      # lint
uv run ruff format                     # format
uv run ty check                        # type check (Astral's ty)
uv run pytest                          # all tests
uv run pytest tests/test_clubs.py::TestBuildClubRow   # a single test class
```

CI (`.github/workflows/ci.yml`) runs ruff check, ruff format --check, ty, and
pytest on Python 3.14 — match it locally before pushing.

## Architecture

`src/` layout package `frbe_tools`. Layers, in dependency order:

- **`config.py`** — `Settings` dataclass built by `load_settings()` from
  `FRBE_*` env vars (and an optional `.env` via python-dotenv). The single place
  that reads the environment.
- **`api/`** — typed access to the public REST API. `client.create_client()` is
  the one `httpx.AsyncClient` factory (sizes the pool to `concurrency`, accepts a
  future bearer `token`). `clubs.py` holds endpoint calls; `models.py` holds the
  records (`ClubRow`) and defensive parsers.
- **`sources/`** — non-API sources. `website.py` (stub) logs into
  `GestionLogin.php` with `.env` credentials and downloads SQLite/DBF dumps.
- **`db/store.py`** (stub) — DuckDB store. The intended flow is **api/sources →
  db → analysis**: loaders ingest API rows and scraped dumps as dated snapshots.
- **`analysis/`** (stub) — Polars analyses computed over the DuckDB store.
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
