"""
routes/auth.py — Login and logout.

  GET  /login   — show login form with store selector
  POST /login   — verify/set password; rate-limited
  GET  /logout  — clear session and redirect to /login
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import db
import state
from security import hash_password, limiter, verify_password

router = APIRouter()
logger = logging.getLogger("ai_blog_server")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/", error: str = ""):
    stores = await db.get_stores()
    has_admin_password = bool(await db.get_admin_password_hash())
    return state.templates.TemplateResponse(
        request,
        "login.html",
        {
            "next": next,
            "error": error,
            "stores": stores,
            "has_admin_password": has_admin_password,
        },
    )


@router.post("/login", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def login_submit(
    request: Request,
    store_id: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/",
):
    async def _fail(error: str) -> HTMLResponse:
        stores = await db.get_stores()
        has_admin_password = bool(await db.get_admin_password_hash())
        return state.templates.TemplateResponse(
            request,
            "login.html",
            {
                "next": next,
                "error": error,
                "stores": stores,
                "has_admin_password": has_admin_password,
            },
        )

    client_ip = request.client.host if request.client else "unknown"

    if store_id == "__admin__":
        # ── Admin login ──────────────────────────────────────────────────────
        existing_hash = await db.get_admin_password_hash()
        if existing_hash is None:
            # First time — set admin password
            await db.set_admin_password_hash(hash_password(password))
            logger.info("Admin password set for the first time from %s", client_ip)
        elif not verify_password(password, existing_hash):
            logger.warning("Failed admin login from %s", client_ip)
            return await _fail("Incorrect password.")
        else:
            logger.info("Admin login from %s", client_ip)

        request.session["authenticated"] = True
        request.session["store_id"] = "__admin__"
        request.session["store_name"] = "Admin"
        safe_next = next if next.startswith("/") else "/"
        return RedirectResponse(safe_next, status_code=303)

    else:
        # ── Per-store login ──────────────────────────────────────────────────
        store = await db.get_store(store_id)
        if not store:
            return await _fail("Unknown store.")

        existing_hash = await db.get_store_password_hash(store_id)
        if not existing_hash:
            # First time for this store — set its password
            await db.set_store_password_hash(store_id, hash_password(password))
            logger.info("Password set for store '%s' from %s", store["name"], client_ip)
        elif not verify_password(password, existing_hash):
            logger.warning("Failed login for store '%s' from %s", store["name"], client_ip)
            return await _fail("Incorrect password.")
        else:
            logger.info("Login for store '%s' from %s", store["name"], client_ip)

        request.session["authenticated"] = True
        request.session["store_id"] = store_id
        request.session["store_name"] = store["name"]
        safe_next = next if next.startswith("/") else "/"
        return RedirectResponse(safe_next, status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
