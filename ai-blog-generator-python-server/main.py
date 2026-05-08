"""
main.py — Application entry point.
Responsibilities: .env loading, logging setup, lifespan, app assembly.
All route logic lives in routes/.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware

import db
import state
from config import AppConfig, load_config
from routes.api import router as api_router
from routes.auth import router as auth_router
from routes.generate import router as generate_router
from routes.scheduler_routes import router as scheduler_router
from routes.setup import router as setup_router
from security import AuthMiddleware, SecurityHeadersMiddleware, limiter

# ---------------------------------------------------------------------------
# .env loading — project dir then workspace root
# ---------------------------------------------------------------------------

_here = Path(__file__).parent
for _env_path in [_here / ".env", _here.parent / ".env"]:
    if _env_path.exists():
        load_dotenv(dotenv_path=_env_path, override=False)
        break

# ---------------------------------------------------------------------------
# Config path
# ---------------------------------------------------------------------------

CONFIG_PATH = os.environ.get("APP_CONFIG_PATH", "config.json")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(cfg: AppConfig) -> None:
    log_path = Path(cfg.logging.file_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    level = logging.DEBUG if cfg.server.is_debug else logging.INFO
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(log_path),
        maxBytes=cfg.logging.max_bytes,
        backupCount=cfg.logging.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler.setLevel(level)

    root = logging.getLogger("ai_blog_server")
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(stdout_handler)

    # Dedicated quality.log — one JSON line per quality check run
    quality_log_path = log_path.parent / "quality.log"
    quality_handler = logging.handlers.RotatingFileHandler(
        filename=str(quality_log_path),
        maxBytes=cfg.logging.max_bytes,
        backupCount=cfg.logging.backup_count,
        encoding="utf-8",
    )
    quality_handler.setFormatter(logging.Formatter("%(message)s"))
    quality_handler.setLevel(logging.INFO)
    quality_logger = logging.getLogger("ai_blog_server.quality")
    quality_logger.setLevel(logging.INFO)
    quality_logger.addHandler(quality_handler)
    quality_logger.propagate = False  # keep quality.log separate from main.log

    if not cfg.server.is_debug:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    state._bootstrap = load_config(CONFIG_PATH)
    setup_logging(state._bootstrap)

    db.set_db_path("data/ai_blog_server.db")
    await db.init_db()
    state.config = state._bootstrap  # server/logging config from file; all else from DB per-request

    logger = logging.getLogger("ai_blog_server")
    logger.info(
        "AI Blog Server starting | mode=%s port=%d",
        state.config.server.mode,
        state.config.server.port,
    )
    yield
    logger.info("AI Blog Server shutting down.")


# ---------------------------------------------------------------------------
# App assembly
# ---------------------------------------------------------------------------

app = FastAPI(title="AI Blog Generator", lifespan=lifespan)

# Rate limiter state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Middleware — order matters: outermost runs first on request, last on response.
# SessionMiddleware must wrap AuthMiddleware so sessions are available to it.
app.add_middleware(AuthMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "changeme-set-SESSION_SECRET-in-env"),
    session_cookie="aiblog_session",
    max_age=60 * 60 * 8,  # 8 hours
    # Set HTTPS_ONLY=true in .env when running behind Caddy (or any TLS proxy)
    https_only=os.environ.get("HTTPS_ONLY", "false").lower() == "true",
    same_site="lax",
)

app.include_router(auth_router)
app.include_router(generate_router)
app.include_router(api_router)
app.include_router(setup_router)
app.include_router(scheduler_router)

_STATIC_DIR = _here / "static"
_STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

_FAVICON = _here / "static" / "favicon.ico"

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    if _FAVICON.exists():
        return FileResponse(_FAVICON, media_type="image/x-icon")
    from fastapi import Response
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = load_config(CONFIG_PATH)
    setup_logging(cfg)
    uvicorn.run(
        "main:app",
        host=cfg.server.host,
        port=cfg.server.port,
        reload=cfg.server.is_debug,
        log_level="debug" if cfg.server.is_debug else "info",
    )
