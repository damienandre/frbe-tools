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
| Scrape website DB dumps | `frbe scrape dumps` | 🚧 stub |
| Consolidate snapshots in DuckDB | (library) | 🚧 stub |
| Club rankings / evolution | `frbe analyze …` | 🚧 stub |

## Install

Requires [uv](https://docs.astral.sh/uv/) and Python 3.14.

```bash
uv sync
```

## Usage

```bash
uv run frbe --help
uv run frbe clubs export                    # export all clubs to data/clubs.csv
uv run frbe clubs export -o out.csv         # custom output path
uv run frbe clubs export -c 20              # 20 parallel requests (default 10)
uv run frbe -v clubs export                 # debug logging
```

The export contains personal email addresses of club board members and is
written into the gitignored `data/` directory by default — keep it out of
version control.

Also runnable as a module: `uv run python -m frbe_tools …`.

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
