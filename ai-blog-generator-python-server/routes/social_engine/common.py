"""Shared helpers for the Social Engine API routers."""
from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from routes.api import _resolve_generation_store, _verify_backend_api_key
from services.social_engine.errors import (
    ConfigurationError, ExternalServiceError, NotFoundError, RateLimited, SocialEngineError, ValidationError,
)


def require_api_key(request: Request) -> None:
    _verify_backend_api_key(request)


async def resolve_store_id(requested: str = "") -> str:
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
