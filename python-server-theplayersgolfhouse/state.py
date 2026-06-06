"""
state.py — Shared mutable application state.

All route modules import config, templates, and reload_config() from here.
The lifespan in main.py is responsible for setting these values on startup.
"""
from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.templating import Jinja2Templates

if TYPE_CHECKING:
    from config import AppConfig

# Set by main.py lifespan before any request is served.
# Typed as Any here so ruff/mypy don't flag use-before-assignment;
# main.py assigns these before the first request.
config: "AppConfig" = None  # type: ignore[assignment]
_bootstrap: "AppConfig" = None  # type: ignore[assignment]

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["datetimeformat"] = lambda ts: (
    _dt.datetime.utcfromtimestamp(int(ts)).strftime("%d %b %Y %H:%M")
    if str(ts).isdigit()
    else str(ts)
)


def _app_root(request) -> str:
    scope = getattr(request, "scope", {}) or {}
    return str(scope.get("root_path", "") or "").rstrip("/")


templates.env.globals["app_root"] = _app_root

_logger = logging.getLogger("ai_blog_server")


async def reload_config() -> None:
    """No-op: all store/model/prompt config is now loaded per-request from DB."""
    pass
