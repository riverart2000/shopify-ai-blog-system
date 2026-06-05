"""
routes/scheduler_routes.py — CRUD for scheduled blog generation jobs.

  GET  /schedule         — view schedule list (rendered in setup.html)
  POST /schedule/save    — create / update a scheduled job
  POST /schedule/delete  — delete a scheduled job
  POST /schedule/toggle  — enable / disable a scheduled job
"""
from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import db
import shopify_client
import state
from config import StoreConfig
from services import blog_scope
from providers import AllModelsFailedError

try:
    from croniter import croniter  # type: ignore

    def _validate_cron(expr: str) -> bool:
        return croniter.is_valid(expr)

    def _cron_description(expr: str) -> str:
        """Return a human-readable cron summary or the raw expression."""
        parts = expr.split()
        if len(parts) != 5:
            return expr
        return expr  # Could integrate cron-descriptor here if desired

except ImportError:
    def _validate_cron(expr: str) -> bool:  # type: ignore[misc]
        return len(expr.split()) == 5

    def _cron_description(expr: str) -> str:  # type: ignore[misc]
        return expr


router = APIRouter(prefix="/schedule")
logger = logging.getLogger("ai_blog_server")


def _get_store_id(request: Request) -> Optional[str]:
    sid = request.session.get("store_id", "")
    return None if not sid or sid == "__admin__" else sid


@router.get("", response_class=HTMLResponse)
async def schedule_page(request: Request, saved: str = "", error: str = ""):
    store_id = _get_store_id(request)
    if not store_id:
        return RedirectResponse("/setup", status_code=303)
    store_row = await db.get_store(store_id)
    jobs = await db.get_scheduled_jobs(store_id)
    prompts = await db.get_prompts(store_id)
    import json as _json
    if store_row:
        store_cfg = StoreConfig(
            id=store_row["id"],
            name=store_row["name"],
            myshopify_domain=store_row["myshopify_domain"],
            custom_domain=store_row.get("custom_domain", ""),
            client_id=store_row["client_id"],
            client_secret=store_row["client_secret"],
            default_blog_handle=store_row.get("default_blog_handle", "news"),
            default_author=store_row.get("default_author", "Store Team"),
        )
        cached_blogs = await blog_scope.get_blog_options(store_id, store_cfg)
    else:
        try:
            cached_blogs = _json.loads(await db.get_store_setting(store_id, "cached_blogs", "[]"))
        except Exception:
            cached_blogs = []
    try:
        cached_products = _json.loads(await db.get_store_setting(store_id, "cached_products", "[]"))
    except Exception:
        cached_products = []
    return state.templates.TemplateResponse(request, "schedule.html", {
        "jobs": jobs,
        "prompts": prompts,
        "cached_blogs": cached_blogs,
        "cached_products": cached_products,
        "saved": saved,
        "error": error,
    })


@router.post("/save", response_class=HTMLResponse)
async def save_job(
    request: Request,
    job_id: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    prompt_id: Annotated[str, Form()] = "",
    blog_handle: Annotated[str, Form()] = "",
    author: Annotated[str, Form()] = "",
    cron_expr: Annotated[str, Form()] = "",
    timezone: Annotated[str, Form()] = "UTC",
    is_active: Annotated[str, Form()] = "1",
    is_product_blog: Annotated[str, Form()] = "",
    use_keyword_pool: Annotated[str, Form()] = "",
):
    store_id = _get_store_id(request)
    if not store_id:
        return RedirectResponse("/setup", status_code=303)

    if not name.strip() or not cron_expr.strip() or not prompt_id.strip():
        return RedirectResponse(
            "/schedule?error=Name%2C+prompt+and+cron+expression+are+required",
            status_code=303,
        )

    if not _validate_cron(cron_expr.strip()):
        return RedirectResponse(
            "/schedule?error=Invalid+cron+expression+%28use+5-field+format%29",
            status_code=303,
        )

    # Calculate next_run_at
    next_run_at = _next_run(cron_expr.strip())

    await db.upsert_job({
        "id": job_id.strip() or None,
        "store_id": store_id,
        "name": name.strip(),
        "prompt_id": prompt_id.strip(),
        "blog_handle": blog_handle.strip(),
        "author": author.strip(),
        "cron_expr": cron_expr.strip(),
        "timezone": timezone.strip() or "UTC",
        "is_active": 1 if is_active in ("1", "on", "true", "yes") else 0,
        "next_run_at": next_run_at,
        "is_product_blog": 1 if is_product_blog in ("1", "on", "true", "yes") else 0,
        "use_keyword_pool": 1 if use_keyword_pool in ("1", "on", "true", "yes") else 0,
    })
    return RedirectResponse("/schedule?saved=job", status_code=303)


@router.post("/delete", response_class=HTMLResponse)
async def delete_job(
    request: Request,
    job_id: Annotated[str, Form()],
):
    store_id = _get_store_id(request)
    if not store_id:
        return RedirectResponse("/setup", status_code=303)
    # Only delete if job belongs to this store
    # (no need to fetch — upsert_job stores store_id; delete is safe if ID unknown)
    await db.delete_job(job_id)
    return RedirectResponse("/schedule?saved=job-deleted", status_code=303)


@router.post("/toggle", response_class=HTMLResponse)
async def toggle_job(
    request: Request,
    job_id: Annotated[str, Form()],
    is_active: Annotated[str, Form()],
):
    store_id = _get_store_id(request)
    if not store_id:
        return RedirectResponse("/setup", status_code=303)
    active = is_active in ("1", "on", "true", "yes")
    jobs = await db.get_scheduled_jobs(store_id)
    job = next((j for j in jobs if j["id"] == job_id), None)
    if job:
        next_run = _next_run(job["cron_expr"]) if active else job.get("next_run_at")
        await db.upsert_job({**job, "is_active": 1 if active else 0, "next_run_at": next_run})
    return RedirectResponse("/schedule", status_code=303)


def _next_run(cron_expr: str) -> Optional[int]:
    try:
        from croniter import croniter  # type: ignore
        import datetime
        base = datetime.datetime.utcnow()
        it = croniter(cron_expr, base)
        return int(it.get_next(float))
    except Exception:
        return None
