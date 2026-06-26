"""FastAPI application wiring the rankings analyses to an HTMX web UI.

Every ranking page is a GET form; HTMX swaps just the results table on each
filter change (``hx-get`` -> ``.../table``). The DuckDB store is opened
read-only with one short-lived connection per request (closed afterwards, so it
does not hold a lock that would block ``frbe db build``).
"""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any

import duckdb
import polars as pl
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from frbe_tools.analysis.rankings import (
    STATUS_PRESETS,
    club_history,
    latest_period,
    player_distribution,
    player_rating_evolution,
    rank_clubs,
    rank_clubs_by_growth,
    rank_clubs_by_strength,
    rank_rating_changes,
)
from frbe_tools.config import Settings, load_settings
from frbe_tools.db.store import connect, scalar

_HERE = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(_HERE / "templates"))

STRENGTH_METRICS = ("avg_elo", "median_elo", "max_elo", "top_n_sum")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def df_to_table(df: pl.DataFrame) -> tuple[list[str], list[dict[str, Any]]]:
    """Split a Polars frame into (column names, list-of-dict rows) for Jinja."""
    return df.columns, df.to_dicts()


def parse_period(con: duckdb.DuckDBPyConnection, raw: str | None) -> dt.date:
    """Resolve a ``YYYYMM`` / ``YYYY-MM-DD`` string to a period date.

    Empty or *unparseable* input falls back to the latest period. The forms type
    a period a character at a time (``hx-trigger`` on keyup), so partial strings
    (``2024``) and out-of-range values (``202413``) must degrade gracefully
    rather than raise — otherwise every intermediate keystroke would 500.
    """
    if raw:
        digits = raw.replace("-", "")
        try:
            if len(digits) == 6 and digits.isdigit():
                return dt.date(int(digits[:4]), int(digits[4:6]), 1)
            return dt.date.fromisoformat(raw)
        except ValueError:
            pass
    return latest_period(con)


def statuses_for(preset: str) -> tuple[str, ...]:
    """Look up a status preset, falling back to ``member`` for unknown input."""
    return STATUS_PRESETS.get(preset, STATUS_PRESETS["member"])


def _tri(value: str | None) -> bool | None:
    """Map a tri-state select ('', 'yes', 'no') to None / True / False."""
    if value in (None, "", "any"):
        return None
    return value in ("yes", "true", "1")


def _none(value: str | None) -> str | None:
    """Treat empty/``any`` selects as 'no filter'."""
    return None if value in ("", "any", None) else value


def _opt_int(raw: str | None, *, lo: int | None = None, hi: int | None = None) -> int | None:
    """Coerce an optional numeric query field to int; blank/non-numeric -> None.

    Plain-GET forms submit empty number inputs as ``""``, which 422 an ``int |
    None`` query param. Distribution parses these defensively (like
    ``parse_period`` does for the period field) so a blank field means "no
    filter" / "use the default" rather than an error. An in-range value is
    returned as-is; an out-of-range one is *clamped* to ``lo``/``hi`` (so e.g.
    ``bin=2000`` becomes the max bin, not a silent fall-back to the default).
    """
    if raw is None or raw.strip() == "":
        return None
    try:
        n = int(raw)
    except ValueError:
        return None
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def _density_curve(counts: list[int], *, bandwidth: float = 1.0) -> list[float]:
    """Gaussian-smoothed density over equally-spaced histogram bins.

    A binned kernel-density estimate evaluated at the bin centres: each bin is
    a weighted average of its neighbours under a Gaussian kernel (``bandwidth``
    in bin units). The result is rescaled to sum to the original total so the
    curve overlays directly on the same count axis as the bars.
    """
    n = len(counts)
    if n == 0:
        return []
    smoothed = [
        sum(c * math.exp(-0.5 * ((j - k) / bandwidth) ** 2) for k, c in enumerate(counts))
        for j in range(n)
    ]
    total = sum(counts)
    scale = sum(smoothed)
    if scale == 0:
        return [0.0] * n
    return [round(s * total / scale, 2) for s in smoothed]


def get_db(request: Request) -> Iterator[duckdb.DuckDBPyConnection]:
    """Yield a short-lived read-only connection, closed when the request ends.

    Opening per request (rather than caching for the process lifetime) keeps the
    DuckDB read lock held only while a request is in flight, so ``frbe db build``
    can open the file read-write whenever the UI is idle. A request that lands
    mid-rebuild gets a clean 503 instead of a stack trace.
    """
    db_path = request.app.state.db_path
    if not Path(db_path).exists():
        raise HTTPException(
            status_code=503, detail="Database not found. Run `frbe db build` first."
        )
    try:
        con = connect(db_path, read_only=True)
    except duckdb.Error as exc:  # e.g. a conflicting lock while `db build` writes
        raise HTTPException(
            status_code=503, detail="Database is busy (being rebuilt?). Try again shortly."
        ) from exc
    try:
        yield con
    finally:
        con.close()


DbDep = Annotated[duckdb.DuckDBPyConnection, Depends(get_db)]

# Shared query-parameter aliases keep the page and table routes in sync.
PeriodQ = Annotated[str | None, Query(description="YYYYMM or YYYY-MM-DD (default: latest).")]
StatusQ = Annotated[str, Query(description=f"One of: {', '.join(STATUS_PRESETS)}.")]
LimitQ = Annotated[int, Query(ge=1, le=500)]


# --------------------------------------------------------------------------- #
# Application factory
# --------------------------------------------------------------------------- #
def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app bound to ``settings`` (defaults to the environment)."""
    settings = settings or load_settings()
    application = FastAPI(title="frbe-tools", docs_url=None, redoc_url=None)
    application.state.db_path = settings.db_path
    application.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    @application.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request,
            "error.html",
            {
                "request": request,
                "nav": _NAV,
                "active": request.url.path,
                "status": exc.status_code,
                "detail": exc.detail,
            },
            status_code=exc.status_code,
        )

    _register_routes(application)
    return application


_NAV = [
    ("/", "Overview"),
    ("/clubs", "Clubs"),
    ("/strength", "Strength"),
    ("/growth", "Growth"),
    ("/movers", "Movers"),
    ("/distribution", "Distribution"),
]

DISTRIBUTION_DIMENSIONS = ("rating", "age", "tenure")


def _base(request: Request, **ctx: Any) -> dict[str, Any]:
    """Common template context (request + nav + active path)."""
    return {"request": request, "nav": _NAV, "active": request.url.path, **ctx}


def _register_routes(app: FastAPI) -> None:
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, db: DbDep) -> HTMLResponse:
        latest = latest_period(db)
        summary = {
            "latest": latest,
            "periods": scalar(db, "SELECT count(DISTINCT period) FROM player_snapshots"),
            "players": scalar(db, "SELECT count(DISTINCT idplayer) FROM player_snapshots"),
            "clubs": scalar(
                db,
                "SELECT count(DISTINCT idclub) FROM player_snapshots "
                "WHERE period = ? AND idclub IS NOT NULL",
                [latest],
            ),
            "members": scalar(
                db,
                "SELECT count(*) FROM player_snapshots WHERE period = ? AND affiliated",
                [latest],
            ),
        }
        return TEMPLATES.TemplateResponse(request, "index.html", _base(request, summary=summary))

    # ---- Clubs by player count ------------------------------------------- #
    def _clubs(db: DbDep, p: dict[str, Any]) -> dict[str, Any]:
        per = parse_period(db, p["period"])
        df = rank_clubs(
            db,
            per,
            statuses=statuses_for(p["status"]),
            sex=_none(p["sex"]),
            min_age=p["min_age"],
            max_age=p["max_age"],
            foreign=_tri(p["foreign"]),
            region=_none(p["region"]),
            rated_only=p["rated"],
            new_only=p["new"],
            limit=p["limit"],
        )
        cols, rows = df_to_table(df)
        return {"period": per, "columns": cols, "rows": rows, "links": {"idclub": "/clubs/{}"}}

    def _clubs_params(
        period: PeriodQ = None,
        status: StatusQ = "member",
        sex: Annotated[str, Query()] = "any",
        min_age: Annotated[int | None, Query(ge=0, le=120)] = None,
        max_age: Annotated[int | None, Query(ge=0, le=120)] = None,
        foreign: Annotated[str, Query()] = "any",
        region: Annotated[str, Query()] = "any",
        rated: Annotated[bool, Query()] = False,
        new: Annotated[bool, Query()] = False,
        limit: LimitQ = 25,
    ) -> dict[str, Any]:
        return {
            "period": period,
            "status": status,
            "sex": sex,
            "min_age": min_age,
            "max_age": max_age,
            "foreign": foreign,
            "region": region,
            "rated": rated,
            "new": new,
            "limit": limit,
        }

    @app.get("/clubs", response_class=HTMLResponse)
    def clubs_page(request: Request, db: DbDep, p: dict = Depends(_clubs_params)):
        ctx = _clubs(db, p)
        return TEMPLATES.TemplateResponse(
            request,
            "clubs.html",
            _base(request, form=p, presets=list(STATUS_PRESETS), **ctx),
        )

    @app.get("/clubs/table", response_class=HTMLResponse)
    def clubs_table(request: Request, db: DbDep, p: dict = Depends(_clubs_params)):
        return TEMPLATES.TemplateResponse(request, "_table.html", _base(request, **_clubs(db, p)))

    # ---- Strength -------------------------------------------------------- #
    def _strength_params(
        period: PeriodQ = None,
        metric: Annotated[str, Query()] = "avg_elo",
        top_n: Annotated[int, Query(ge=1, le=20)] = 4,
        status: StatusQ = "member",
        min_players: Annotated[int, Query(ge=1, le=100)] = 4,
        limit: LimitQ = 25,
    ) -> dict[str, Any]:
        return {
            "period": period,
            "metric": metric,
            "top_n": top_n,
            "status": status,
            "min_players": min_players,
            "limit": limit,
        }

    def _strength(db: DbDep, p: dict[str, Any]) -> dict[str, Any]:
        per = parse_period(db, p["period"])
        metric = p["metric"] if p["metric"] in STRENGTH_METRICS else "avg_elo"
        df = rank_clubs_by_strength(
            db,
            per,
            metric=metric,
            top_n=p["top_n"],
            statuses=statuses_for(p["status"]),
            min_players=p["min_players"],
            limit=p["limit"],
        )
        cols, rows = df_to_table(df)
        return {"period": per, "columns": cols, "rows": rows, "links": {"idclub": "/clubs/{}"}}

    @app.get("/strength", response_class=HTMLResponse)
    def strength_page(request: Request, db: DbDep, p: dict = Depends(_strength_params)):
        ctx = _strength(db, p)
        return TEMPLATES.TemplateResponse(
            request,
            "strength.html",
            _base(request, form=p, presets=list(STATUS_PRESETS), metrics=STRENGTH_METRICS, **ctx),
        )

    @app.get("/strength/table", response_class=HTMLResponse)
    def strength_table(request: Request, db: DbDep, p: dict = Depends(_strength_params)):
        return TEMPLATES.TemplateResponse(
            request, "_table.html", _base(request, **_strength(db, p))
        )

    # ---- Growth ---------------------------------------------------------- #
    def _growth_params(
        baseline: Annotated[str, Query()] = "201601",
        period: PeriodQ = None,
        status: StatusQ = "member",
        limit: LimitQ = 25,
    ) -> dict[str, Any]:
        return {"baseline": baseline, "period": period, "status": status, "limit": limit}

    def _growth(db: DbDep, p: dict[str, Any]) -> dict[str, Any]:
        per = parse_period(db, p["period"])
        base = parse_period(db, p["baseline"])
        df = rank_clubs_by_growth(
            db, per, base, statuses=statuses_for(p["status"]), limit=p["limit"]
        )
        cols, rows = df_to_table(df)
        return {
            "period": per,
            "baseline": base,
            "columns": cols,
            "rows": rows,
            "links": {"idclub": "/clubs/{}"},
        }

    @app.get("/growth", response_class=HTMLResponse)
    def growth_page(request: Request, db: DbDep, p: dict = Depends(_growth_params)):
        ctx = _growth(db, p)
        return TEMPLATES.TemplateResponse(
            request, "growth.html", _base(request, form=p, presets=list(STATUS_PRESETS), **ctx)
        )

    @app.get("/growth/table", response_class=HTMLResponse)
    def growth_table(request: Request, db: DbDep, p: dict = Depends(_growth_params)):
        return TEMPLATES.TemplateResponse(request, "_table.html", _base(request, **_growth(db, p)))

    # ---- Movers ---------------------------------------------------------- #
    def _movers_params(
        baseline: Annotated[str, Query()] = "202401",
        period: PeriodQ = None,
        club: Annotated[int | None, Query()] = None,
        losers: Annotated[bool, Query()] = False,
        status: StatusQ = "member",
        limit: LimitQ = 25,
    ) -> dict[str, Any]:
        return {
            "baseline": baseline,
            "period": period,
            "club": club,
            "losers": losers,
            "status": status,
            "limit": limit,
        }

    def _movers(db: DbDep, p: dict[str, Any]) -> dict[str, Any]:
        per = parse_period(db, p["period"])
        base = parse_period(db, p["baseline"])
        df = rank_rating_changes(
            db,
            per,
            base,
            statuses=statuses_for(p["status"]),
            idclub=p["club"],
            limit=p["limit"],
            ascending=p["losers"],
        )
        cols, rows = df_to_table(df)
        return {
            "period": per,
            "baseline": base,
            "columns": cols,
            "rows": rows,
            "links": {"idplayer": "/players/{}"},
        }

    @app.get("/movers", response_class=HTMLResponse)
    def movers_page(request: Request, db: DbDep, p: dict = Depends(_movers_params)):
        ctx = _movers(db, p)
        return TEMPLATES.TemplateResponse(
            request, "movers.html", _base(request, form=p, presets=list(STATUS_PRESETS), **ctx)
        )

    @app.get("/movers/table", response_class=HTMLResponse)
    def movers_table(request: Request, db: DbDep, p: dict = Depends(_movers_params)):
        return TEMPLATES.TemplateResponse(request, "_table.html", _base(request, **_movers(db, p)))

    # ---- Distribution (histogram by rating or age) ----------------------- #
    def _distribution_params(
        dimension: Annotated[str, Query()] = "rating",
        period: PeriodQ = None,
        club: Annotated[str | None, Query()] = None,
        region: Annotated[str, Query()] = "any",
        status: StatusQ = "member",
        bin_size: Annotated[str | None, Query(alias="bin")] = None,
        hide_unrated: Annotated[bool, Query()] = False,
    ) -> dict[str, Any]:
        # club/bin arrive as strings so a blank form field (``""``) degrades to
        # "no filter" instead of 422-ing the page (see _opt_int).
        return {
            "dimension": dimension,
            "period": period,
            "club": _opt_int(club),
            "region": region,
            "status": status,
            "bin_size": _opt_int(bin_size, lo=1, hi=1000),
            "hide_unrated": hide_unrated,
        }

    @app.get("/distribution", response_class=HTMLResponse)
    def distribution_page(request: Request, db: DbDep, p: dict = Depends(_distribution_params)):
        per = parse_period(db, p["period"])
        # Web layer degrades gracefully on bad input (like parse_period / the
        # _strength metric fallback): an unknown dimension renders the default
        # rather than 400-ing, whereas the CLI / player_distribution raise.
        dimension = p["dimension"] if p["dimension"] in DISTRIBUTION_DIMENSIONS else "rating"
        df = player_distribution(
            db,
            per,
            dimension=dimension,
            statuses=statuses_for(p["status"]),
            idclub=p["club"],
            region=_none(p["region"]),
            bin_size=p["bin_size"],
            include_unrated=not p["hide_unrated"],
        )
        cols, rows = df_to_table(df)
        labels = [r["bucket"] for r in rows]
        players = [r["players"] for r in rows]
        # Smooth a density curve over the numeric bands only; the categorical
        # "unrated" bucket has no position on the axis, so leave a gap (None) there.
        numeric = [
            (i, p) for i, (b, p) in enumerate(zip(labels, players, strict=True)) if b != "unrated"
        ]
        density: list[float | None] = [None] * len(labels)
        for (i, _), value in zip(numeric, _density_curve([p for _, p in numeric]), strict=True):
            density[i] = value
        chart = {"labels": labels, "players": players, "density": density}
        return TEMPLATES.TemplateResponse(
            request,
            "distribution.html",
            _base(
                request,
                form=p,
                period=per,
                dimension=dimension,
                presets=list(STATUS_PRESETS),
                dimensions=DISTRIBUTION_DIMENSIONS,
                columns=cols,
                rows=rows,
                chart=json.dumps(chart),
            ),
        )

    # ---- Club history (table + chart) ------------------------------------ #
    @app.get("/clubs/{idclub}", response_class=HTMLResponse)
    def club_detail(
        request: Request,
        db: DbDep,
        idclub: int,
        month: Annotated[int | None, Query(ge=1, le=12)] = None,
        youth_age: Annotated[int, Query(ge=0, le=120)] = 19,
    ):
        df = club_history(db, idclub, youth_max_age=youth_age, month=month)
        name = scalar(
            db, "SELECT coalesce(name_short, name_long) FROM clubs WHERE idclub = ?", [idclub]
        )
        cols, rows = df_to_table(df)
        chart = {
            "labels": [str(r["period"]) for r in rows],
            "members": [r["members"] for r in rows],
            "women": [r["women"] for r in rows],
            "youth": [r["youth"] for r in rows],
            "avg_elo": [r["avg_elo"] for r in rows],
        }
        return TEMPLATES.TemplateResponse(
            request,
            "club_history.html",
            _base(
                request,
                idclub=idclub,
                name=name,
                columns=cols,
                rows=rows,
                month=month,
                youth_age=youth_age,
                chart=json.dumps(chart),
            ),
        )

    # ---- Player rating evolution (table + chart) ------------------------- #
    @app.get("/players/{idplayer}", response_class=HTMLResponse)
    def player_detail(request: Request, db: DbDep, idplayer: int):
        df = player_rating_evolution(db, idplayer)
        name = scalar(
            db,
            "SELECT arg_max(name, period) FROM player_snapshots WHERE idplayer = ?",
            [idplayer],
        )
        cols, rows = df_to_table(df)
        chart = {
            "labels": [str(r["period"]) for r in rows],
            "elo": [r["elo"] for r in rows],
        }
        return TEMPLATES.TemplateResponse(
            request,
            "player.html",
            _base(
                request,
                idplayer=idplayer,
                name=name,
                columns=cols,
                rows=rows,
                chart=json.dumps(chart),
            ),
        )


# Module-level instance for ``uvicorn frbe_tools.web.app:app`` / future --reload.
app = create_app()
