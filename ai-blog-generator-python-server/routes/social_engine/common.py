"""Shared helpers for the Social Engine API routers."""
from __future__ import annotations

from contextvars import ContextVar

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from routes.api import _resolve_generation_store, _verify_backend_api_key
from services.social_engine.errors import (
    ConfigurationError, ExternalServiceError, NotFoundError, RateLimited, SocialEngineError, ValidationError,
)


# The Shopify app's Social Autopilot pages can show any brand (Bio Luxe Lab, RevenueMind Pro AI, ...). The app
# marks its requests with this header; a request that names no store is then answered for the brand chosen in
# the app's brand switch. Everything else (Task Agent, Telegram, scripts) keeps the default store.
APP_HEADER = "x-social-app"
ACTIVE_BRAND_KEY = "app:active_store"
_from_app: ContextVar[bool] = ContextVar("social_engine_from_app", default=False)


async def require_api_key(request: Request) -> None:
    # async on purpose: a sync dependency runs in a worker thread, where setting the context variable is lost.
    _verify_backend_api_key(request)
    _from_app.set(request.headers.get(APP_HEADER, "") == "1")


async def active_brand_id() -> str:
    """The brand chosen in the app's brand switch, or "" when none is chosen or it no longer exists."""
    from db.social_engine import observability

    import db

    active = str(await observability.get_state(ACTIVE_BRAND_KEY, "") or "")
    return active if active and await db.get_store(active) else ""


async def resolve_store_id(requested: str = "") -> str:
    if not requested and _from_app.get():
        requested = await active_brand_id()
    store = await _resolve_generation_store(requested or "")
    return str(store["id"])


def error_status(exc: SocialEngineError) -> int:
    if isinstance(exc, NotFoundError):
        return 404
    if isinstance(exc, (ValidationError, ConfigurationError)):
        return 400
    if isinstance(exc, RateLimited):
        return 429
    if isinstance(exc, ExternalServiceError):
        return 502
    return 500


async def social_engine_error_handler(request: Request, exc: SocialEngineError) -> JSONResponse:
    """Registered in main.py: turns engine errors into exact, readable API errors."""
    body = {"detail": exc.message, "error": exc.as_dict()}
    if isinstance(exc, RateLimited):
        body["retry_at"] = exc.retry_at
    return JSONResponse(status_code=error_status(exc), content=body)


def not_found(message: str) -> HTTPException:
    return HTTPException(status_code=404, detail=message)
