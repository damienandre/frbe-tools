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
uv run frbe analyze player 1104                    # a player's Elo history
uv run frbe analyze movers 202401                  # biggest Elo gainers since 2024-01
```

Ages use birth-year cohorts (the chess youth-category convention: "under 20 in
2026" = born 2007+).

## Configuration

Copy `.env.example` to `.env` and fill in what you need. `.env` is gitignored.

| Variable | Purpose | Default |
| --- | --- | --- |
| `FRBE_API_BASE` | API base URL | `https://www.frbe-kbsb-ksb.be/api/v1` |
| `FRBE_DATA_DIR` | Local data directory | `data` |
| `FRBE_DB_PATH` | DuckDB database path | `data/frbe.duckdb` |
| `FRBE_USERNAME` | Manager-site login (for `scrape`) | — |
| `FRBE_PASSWORD` | Manager-site password (for `scrape`) | — |

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
