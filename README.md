# frbe-tools

Tools to access and analyze data of the Belgian chess federation — **FRBE**
(French), **KBSB** (Dutch), **KSB** (German), **RBCF** (English translation).

The goal is a small, composable toolkit that pulls federation data from multiple
sources, consolidates it into a local analytical database, and produces useful
analyses (club rankings, club evolution over time, and more).

## Data sources

- **Public REST API** — `https://www.frbe-kbsb-ksb.be/api/v1/…`
  ([OpenAPI spec](https://kbsb-api-dot-website-kbsb-prod.ew.r.appspot.com/openapi.json)).
  Endpoints are split into `anon` (public), `clb` (club-level, token), and
  `mgmt` (federation admin) tiers. Clubs export uses the public tier.
- **Manager website dumps** — SQLite/DBF database exports behind a login at
  `GestionLogin.php`, accessed with credentials supplied via `.env`.

## Status

| Feature | Command | State |
| --- | --- | --- |
| Export club admin data to CSV | `frbe clubs export` | ✅ implemented |
| Scrape player DB dumps | `frbe scrape players` | ✅ implemented |
| Consolidate snapshots in DuckDB | `frbe db build` / `frbe db info` | ✅ implemented |
| Club & player rankings | `frbe analyze …` | ✅ implemented |
| Local web dashboard | `frbe web` | ✅ implemented |

## Install

Requires [uv](https://docs.astral.sh/uv/) and Python 3.14.

```bash
uv sync
```

## Usage

```bash
uv run frbe --help
uv run frbe clubs export                    # export all clubs to data/clubs.csv

# Build the analytical database (requires credentials in .env, see below)
uv run frbe scrape players                  # download player dumps -> data/player/
uv run frbe db build                        # ingest dumps + clubs into DuckDB
uv run frbe db info                         # summarize the database
```

Outputs (club emails, player databases, the DuckDB file) are written into the
gitignored `data/` directory — keep them out of version control.

Also runnable as a module: `uv run python -m frbe_tools …`.

## Database

`frbe db build` consolidates the quarterly player dumps into a DuckDB star
schema (`data/frbe.duckdb`):

- **`player_snapshots`** — fact table, one row per player per quarter (the full
  record: identity, club, affiliation, national Elo).
- **`clubs`** / **`players`** — dimensions (clubs from the API; `players` is a
  view giving each player's latest identity + lifecycle).
- **`player_affiliations`** — time-dependent club membership
  (`member` / `free_license` / `unaffiliated`).
- **`player_rating_history`** — Elo evolution over time.

Example queries (DuckDB):

```sql
-- clubs ranked by current members
SELECT idclub, count(*) FROM player_affiliations
WHERE period = '2026-07-01' AND status = 'member' GROUP BY idclub ORDER BY 2 DESC;

-- a player's rating evolution
SELECT period, elo FROM player_rating_history WHERE idplayer = 1104 ORDER BY period;
```

## Analysis

`frbe analyze` ranks clubs and players over the store (defaults to the latest
period; pass `--period YYYYMM` for a historical one):

```bash
uv run frbe analyze clubs                          # clubs by members
uv run frbe analyze clubs --status registered      # members + free licences
uv run frbe analyze clubs --max-age 19             # youth (under-20 cohort)
uv run frbe analyze clubs --sex F                  # women members
uv run frbe analyze strength --metric top_n_sum    # top-4-board strength
uv run frbe analyze growth 201601                  # member growth since 2016-01
uv run frbe analyze club-history 230               # one club's metrics over time
uv run frbe analyze club-history 230 --month 7     # July snapshots only (no seasonal noise)
uv run frbe analyze player 1104                    # a player's Elo history
uv run frbe analyze movers 202401                  # biggest Elo gainers since 2024-01
uv run frbe analyze movers 202401 --club 901       # best movers within one club
```

Ages use birth-year cohorts (the chess youth-category convention: "under 20 in
2026" = born 2007+).

## Web UI

`frbe web` serves a local dashboard (FastAPI + Jinja2 + HTMX) over the same
analyses — interactive filtering, plus Elo/membership charts for individual
clubs and players. Each request opens the DuckDB store **read-only** and closes
it when the response is sent, so the UI coexists with `frbe db build`: a rebuild
started while the UI is idle proceeds normally, and a page loaded during the
brief moment a rebuild holds the write lock returns a "try again shortly" message
rather than failing hard. (DuckDB allows either one writer or many readers, so a
request *exactly* overlapping a rebuild is the only contention point.)

```bash
uv run frbe web                 # http://127.0.0.1:8080
uv run frbe web --port 9000     # override the port (flag > FRBE_WEB_PORT > 8080)
```

Pages: overview, club rankings, strength, growth, movers, and per-club /
per-player detail (with charts). Filters update the results table in place via
HTMX — no page reloads. htmx and Chart.js are vendored under `web/static/`, so
the UI works offline.

## Configuration

Copy `.env.example` to `.env` and fill in what you need. `.env` is gitignored.

| Variable | Purpose | Default |
| --- | --- | --- |
| `FRBE_API_BASE` | API base URL | `https://www.frbe-kbsb-ksb.be/api/v1` |
| `FRBE_DATA_DIR` | Local data directory | `data` |
| `FRBE_DB_PATH` | DuckDB database path | `data/frbe.duckdb` |
| `FRBE_USERNAME` | Manager-site login (for `scrape`) | — |
| `FRBE_PASSWORD` | Manager-site password (for `scrape`) | — |
| `FRBE_WEB_HOST` | `frbe web` bind address | `127.0.0.1` |
| `FRBE_WEB_PORT` | `frbe web` port | `8080` |

> ⚠️ The web UI is **unauthenticated** and exposes the full local player
> database. The default `127.0.0.1` keeps it on your machine only; setting
> `FRBE_WEB_HOST=0.0.0.0` (or `--host 0.0.0.0`) publishes it to your whole LAN —
> only do that on a network you trust.

## Development

```bash
uv sync                       # install runtime + dev dependencies
uv run ruff check             # lint
uv run ruff format            # format
uv run ty check               # type check
uv run pytest                 # tests
uv run pre-commit install     # enable git hooks (ruff lint + format)
```

## License

[MIT](LICENSE)
