"""
security.py — Security primitives for the AI Blog Server.

Provides:
  - Password hashing/verification (bcrypt via passlib)
  - AuthMiddleware  — redirects unauthenticated requests to /login
  - SecurityHeadersMiddleware — adds OWASP-recommended HTTP security headers
  - limiter — slowapi rate limiter instance (attached to FastAPI app in main.py)
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

import bcrypt as _bcrypt
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

logger = logging.getLogger("ai_blog_server")

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode(), _bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Rate limiter (attach to app.state.limiter in main.py)
# ---------------------------------------------------------------------------

limiter = Limiter(key_func=get_remote_address)


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

# Paths that don't require authentication
_PUBLIC_PATHS = {"/login", "/health", "/api/generate"}
_PUBLIC_PREFIXES = ("/static/", "/api/")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path

        # Always allow public paths
        if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        # Check session
        if not request.session.get("authenticated") or not request.session.get("store_id"):
            # Preserve the intended destination so we can redirect after login
            next_url = request.url.path
            if request.url.query:
                next_url += f"?{request.url.query}"
            return RedirectResponse(f"/login?next={next_url}", status_code=303)

        return await call_next(request)


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)

        # Prevent MIME-type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Referrer privacy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Disable browser features not needed
        response.headers["Permissions-Policy"] = (
            "geolocation=(), camera=(), microphone=()"
        )

        # Content Security Policy
        # unsafe-inline required because templates use inline <style> and <script>
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )

        # HSTS — only sent over HTTPS; harmless if not yet on HTTPS
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )

        return response
