"""``frbe web`` — serve the local HTMX dashboard with uvicorn."""

from __future__ import annotations

from typing import Annotated

import typer

from frbe_tools.config import load_settings


def serve(
    host: Annotated[
        str | None,
        typer.Option(
            "--host",
            help="Bind address (default: FRBE_WEB_HOST or 127.0.0.1). "
            "0.0.0.0 exposes the unauthenticated UI to the LAN.",
        ),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option("--port", "-P", help="Port (default: FRBE_WEB_PORT or 8080)."),
    ] = None,
) -> None:
    """Launch the local web UI for browsing club and player analyses.

    Reads the DuckDB store read-only, one short-lived connection per request, so
    it coexists with ``frbe db build`` (which needs the write lock). Precedence
    for host/port: CLI flag > ``FRBE_WEB_*`` env > default.
    """
    import uvicorn

    from frbe_tools.web.app import create_app

    settings = load_settings()
    bind_host = host or settings.web_host
    bind_port = port or settings.web_port
    typer.echo(f"Serving frbe-tools on http://{bind_host}:{bind_port} (Ctrl-C to stop)")
    uvicorn.run(create_app(settings), host=bind_host, port=bind_port)
