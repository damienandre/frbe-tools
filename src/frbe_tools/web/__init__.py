"""Local web UI for frbe-tools (FastAPI + Jinja2 + HTMX).

``create_app(settings)`` builds the application; ``frbe web`` serves it with
uvicorn. The UI is a thin presentation layer over :mod:`frbe_tools.analysis`,
opening the DuckDB store **read-only** so it never conflicts with ``frbe db build``.
"""

from frbe_tools.web.app import app, create_app

__all__ = ["app", "create_app"]
