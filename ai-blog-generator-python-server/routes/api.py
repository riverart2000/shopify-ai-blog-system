"""
routes/api.py — Utility and data endpoints.

  GET /health
  GET /api/blogs/{store_id}
  GET /history
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import time
from typing import Annotated
from urllib.parse import quote_plus

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

import db
import shopify_client
import state
import services.publer_service as publer_service
import services.social_post_service as social_post_service
from config import StoreConfig
from providers import AllModelsFailedError
from services import blog_scope, image_service, internal_links, llm_service, logo_service, title_service
from services.quality_service import html_to_review_text, review_draft
from utils import text_to_html

router = APIRouter()
logger = logging.getLogger("ai_blog_server")


class GenerateApiRequest(BaseModel):
    prompt: str
    store_id: str = ""
    model_id: str = ""
    blog_handle: str = ""


def _store_config_from_row(store_row: dict) -> StoreConfig:
    return StoreConfig(
        id=store_row["id"],
        name=store_row["name"],
        myshopify_domain=store_row["myshopify_domain"],
        custom_domain=store_row.get("custom_domain", ""),
        client_id=store_row["client_id"],
        client_secret=store_row["client_secret"],
        default_blog_handle=store_row.get("default_blog_handle", "news"),
        default_author=store_row.get("default_author", "Store Team"),
    )


def _verify_backend_api_key(request: Request) -> None:
    expected = os.environ.get("AI_BLOG_BACKEND_API_KEY") or os.environ.get("BLOG_GENERATOR_API_KEY") or ""
    if not expected:
        raise HTTPException(status_code=503, detail="AI blog backend API key is not configured.")

    header_name = os.environ.get("AI_BLOG_BACKEND_API_KEY_HEADER", "x-api-key")
    supplied = request.headers.get(header_name, "")
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid AI blog backend API key.")


async def _resolve_generation_store(requested_store_id: str) -> dict:
    store_id = requested_store_id.strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if store_id:
        store = await db.get_store(store_id)
        if not store:
            raise HTTPException(status_code=404, detail=f"Unknown store: {store_id}")
        return store

    stores = await db.get_stores()
    if not stores:
        raise HTTPException(status_code=404, detail="No stores are configured in the Python backend.")
    return stores[0]


async def _resolve_auto_blog_scope(
    store_id: str,
    store_cfg: StoreConfig,
    *candidate_parts: object,
) -> tuple[list[blog_scope.BlogScope], blog_scope.BlogScope | None, str]:
    candidate_text = "\n".join(
        str(part).strip()
        for part in candidate_parts
        if str(part).strip()
    )
    scopes = await blog_scope.get_blog_scopes(store_id, store_cfg)
    inferred_scope = blog_scope.best_matching_scope(candidate_text, scopes)
    if inferred_scope is None:
        inferred_scope = await blog_scope.resolve_blog_scope(
            store_id,
            store_cfg,
            store_cfg.default_blog_handle,
        )
    resolved_blog_handle = getattr(inferred_scope, "handle", "") or store_cfg.default_blog_handle
    return scopes, inferred_scope, resolved_blog_handle


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "mode": state.config.server.mode,
    }


@router.post("/api/generate")
async def api_generate(request: Request, payload: GenerateApiRequest):
    _verify_backend_api_key(request)

    prompt_text = payload.prompt.strip()
    if not prompt_text:
        raise HTTPException(status_code=400, detail="prompt is required")

    store = await _resolve_generation_store(payload.store_id)
    store_id = store["id"]
    store_cfg = _store_config_from_row(store)
    resolved_blog_handle = payload.blog_handle.strip() or store_cfg.default_blog_handle
    scope = await blog_scope.resolve_blog_scope(store_id, store_cfg, resolved_blog_handle)

    title_row = await title_service.pop_blog_title_for_scope(store_id, scope)
    if title_row:
        prompt_text += f"\n\nIMPORTANT — You MUST use exactly this title for the blog post: {title_row['title']}"
        if title_row.get("keyword"):
            prompt_text += f"\nFocus keyword: {title_row['keyword']}"
        if title_row.get("meta_description"):
            prompt_text += (
                "\nUse this exact text as the article summary/meta description: "
                f"{title_row['meta_description']}"
            )
        logger.info("API generation using pooled blog title %r for store %s", title_row["title"], store_id)
    else:
        keyword_row = await blog_scope.pop_scoped_keyword(store_id, scope)
        if keyword_row:
            prompt_text += f"\n\nFocus keyword for this article: {keyword_row['keyword']}"
            keyword_context = keyword_row.get("content", "").strip()
            if keyword_context:
                prompt_text += (
                    "\n\nWhat people are currently discussing about this topic "
                    f"(use as context, do not quote directly):\n{keyword_context[:600]}"
                )
            logger.info("API generation using pooled keyword %r for store %s", keyword_row["keyword"], store_id)

    prompt_text = await blog_scope.apply_blog_scope(
        prompt_text,
        scope=scope,
    )

    try:
        blog_data = await llm_service.generate_text(store_id, prompt_text, model_id=payload.model_id or None)
        image_urls = await image_service.generate_images(
            store_id,
            blog_data["title"],
            blog_data["summary"],
            prompt_text,
        )
    except AllModelsFailedError as exc:
        logger.error("API text generation failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected API generation error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return JSONResponse(
        {
            "title": blog_data["title"],
            "summary": blog_data["summary"],
            "content": blog_data["content"],
            "keywords": blog_data.get("keywords", []),
            "hashtags": blog_data.get("hashtags", []),
            "long_tail_keywords": blog_data.get("long_tail_keywords", []),
            "pin_description": blog_data.get("pin_description", ""),
            "images": image_urls,
            "store_id": store_id,
            "model": blog_data.get("_model_name", ""),
            "provider": blog_data.get("_model_provider", ""),
        }
    )


@router.get("/api/blogs/{store_id}")
async def api_blogs(store_id: str):
    store_row = await db.get_store(store_id)
    if not store_row:
        return {"error": f"Unknown store: {store_id}", "blogs": []}
    store = _store_config_from_row(store_row)
    try:
        blogs = await shopify_client.fetch_blogs(store)
        blog_list = [{"id": b.id, "handle": b.handle, "title": b.title} for b in blogs]
        # Cache handles in store_settings so other pages can show a dropdown
        import json as _json
        await db.set_store_settings(store_id, {"cached_blogs": _json.dumps(blog_list)})
        return {"blogs": blog_list}
    except shopify_client.ShopifyError as exc:
        logger.warning("Failed to fetch blogs for store %s: %s", store_id, exc)
        return {"error": str(exc), "blogs": []}


@router.get("/api/products/{store_id}")
async def api_products(store_id: str):
    import json as _json
    store_row = await db.get_store(store_id)
    if not store_row:
        return {"error": f"Unknown store: {store_id}", "products": []}
    store = _store_config_from_row(store_row)
    try:
        products = await shopify_client.fetch_products(store)
        product_list = [{"id": p.id, "title": p.title, "handle": p.handle, "url": p.url} for p in products]
        await db.set_store_settings(store_id, {"cached_products": _json.dumps(product_list)})
        return {"products": product_list}
    except shopify_client.ShopifyError as exc:
        logger.warning("Failed to fetch products for store %s: %s", store_id, exc)
        # Fall back to cached list if available
        cached = await db.get_store_setting(store_id, "cached_products", "[]")
        try:
            product_list = _json.loads(cached)
        except Exception:
            product_list = []
        return {"products": product_list, "cached": True, "error": str(exc)}


# ---------------------------------------------------------------------------
# JSON endpoints consumed by the Shopify Remix app (no session cookie needed)
# ---------------------------------------------------------------------------

@router.get("/api/history")
async def api_history(request: Request, limit: int = 50):
    """Return recent blog generations as JSON. Auth: x-api-key header."""
    _verify_backend_api_key(request)
    rows = await db.get_recent_generations(limit=min(limit, 200))
    return {"generations": rows}


@router.get("/api/schedule")
async def api_schedule(request: Request):
    """Return all active scheduled jobs as JSON. Auth: x-api-key header."""
    _verify_backend_api_key(request)
    rows = await db.get_all_active_jobs()
    return {"jobs": rows}


@router.get("/api/errors")
async def api_errors(request: Request, store_id: str = "", limit: int = 30):
    """Return recent generation errors as JSON. Auth: x-api-key header."""
    _verify_backend_api_key(request)
    if store_id:
        rows = await db.get_recent_errors(store_id=store_id, limit=min(limit, 100))
    else:
        # No store filter — read directly so we don't need store_id
        import aiosqlite as _aiosqlite
        from db.base import get_db_path as _get_db_path
        async with _aiosqlite.connect(_get_db_path()) as _db:
            _db.row_factory = _aiosqlite.Row
            async with _db.execute(
                "SELECT * FROM generation_errors ORDER BY created_at DESC LIMIT ?",
                (min(limit, 100),),
            ) as _cur:
                rows = [dict(r) for r in await _cur.fetchall()]
    return {"errors": rows}


# --- Social Posts + Publer ---

def _safe_json_loads(raw: str, fallback):
    try:
        return json.loads(raw)
    except Exception:
        return fallback


class SocialGenerateRequest(BaseModel):
    store_id: str = ""
    product_title: str = ""
    product_handle: str = ""
    product_url: str = ""
    brief_text: str = ""
    model_id: str = ""


class SocialDefaultsSaveRequest(BaseModel):
    store_id: str = ""
    default_workspace_id: str = ""
    default_account_ids: list[str] = []
    default_providers: list[str] = []
    default_mode: str = "draft"


class SocialPublishRequest(BaseModel):
    store_id: str = ""
    workspace_id: str = ""
    campaign_name: str = ""
    product_handle: str = ""
    product_title: str = ""
    product_url: str = ""
    brief_text: str = ""
    base_text: str = ""
    provider_texts: dict[str, str] = {}
    account_ids: list[str] = []
    mode: str = "draft"
    scheduled_at: str = ""


@router.get("/api/social/workspaces")
async def api_social_workspaces(request: Request):
    _verify_backend_api_key(request)
    if not publer_service.is_configured():
        return {
            "configured": False,
            "docs_url": publer_service.docs_url(),
            "workspaces": [],
        }

    try:
        workspaces = await publer_service.list_workspaces()
    except publer_service.PublerError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "configured": True,
        "docs_url": publer_service.docs_url(),
        "workspaces": workspaces,
    }


@router.get("/api/social/accounts")
async def api_social_accounts(request: Request, workspace_id: str = ""):
    _verify_backend_api_key(request)
    if not workspace_id.strip():
        raise HTTPException(status_code=400, detail="workspace_id is required")
    if not publer_service.is_configured():
        raise HTTPException(status_code=503, detail="PUBLER_API_KEY is not configured on the backend")

    try:
        accounts = await publer_service.list_accounts(workspace_id.strip())
    except publer_service.PublerError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {"workspace_id": workspace_id.strip(), "accounts": accounts}


@router.get("/api/social/defaults")
async def api_social_defaults(request: Request, store_id: str = ""):
    _verify_backend_api_key(request)
    store = await _resolve_generation_store(store_id)
    sid = store["id"]

    workspace_id = await db.get_store_setting(sid, "social_default_workspace_id", "")
    account_ids = _safe_json_loads(
        await db.get_store_setting(sid, "social_default_account_ids", "[]"),
        [],
    )
    providers = _safe_json_loads(
        await db.get_store_setting(sid, "social_default_providers", '["instagram","facebook","x"]'),
        ["instagram", "facebook", "x"],
    )
    mode = (await db.get_store_setting(sid, "social_default_mode", "draft")).strip() or "draft"

    return {
        "store_id": sid,
        "defaults": {
            "workspace_id": workspace_id,
            "account_ids": [str(a).strip() for a in account_ids if str(a).strip()],
            "providers": [str(p).strip().lower() for p in providers if str(p).strip()],
            "mode": mode if mode in {"draft", "scheduled", "publish_now"} else "draft",
        },
    }


@router.post("/api/social/defaults/save")
async def api_social_defaults_save(request: Request, payload: SocialDefaultsSaveRequest):
    _verify_backend_api_key(request)
    store = await _resolve_generation_store(payload.store_id)
    sid = store["id"]

    normalized_mode = payload.default_mode.strip().lower() or "draft"
    if normalized_mode not in {"draft", "scheduled", "publish_now"}:
        raise HTTPException(status_code=400, detail="default_mode must be draft, scheduled, or publish_now")

    account_ids = [str(a).strip() for a in payload.default_account_ids if str(a).strip()]
    providers = [str(p).strip().lower() for p in payload.default_providers if str(p).strip()]

    await db.set_store_settings(
        sid,
        {
            "social_default_workspace_id": payload.default_workspace_id.strip(),
            "social_default_account_ids": json.dumps(account_ids),
            "social_default_providers": json.dumps(providers),
            "social_default_mode": normalized_mode,
        },
    )
    return {"ok": True}


@router.get("/api/social/history")
async def api_social_history(request: Request, store_id: str = "", limit: int = 30):
    _verify_backend_api_key(request)
    store = await _resolve_generation_store(store_id)
    sid = store["id"]
    rows = await db.get_recent_social_posts(sid, limit=min(max(limit, 1), 200))
    return {"store_id": sid, "rows": rows}


@router.post("/api/social/generate")
async def api_social_generate(request: Request, payload: SocialGenerateRequest):
    _verify_backend_api_key(request)
    store_row = await _resolve_generation_store(payload.store_id)
    sid = store_row["id"]

    product_title = payload.product_title.strip()
    if not product_title:
        raise HTTPException(status_code=400, detail="product_title is required")

    try:
        generated = await social_post_service.generate_social_post_variants(
            store_id=sid,
            store_name=store_row["name"],
            product_title=product_title,
            product_url=payload.product_url.strip(),
            brief_text=payload.brief_text.strip(),
            model_id=payload.model_id.strip() or None,
        )
    except AllModelsFailedError as exc:
        raise HTTPException(status_code=502, detail=f"Social post generation failed: {exc}") from exc
    except Exception as exc:
        logger.exception("Unexpected social generation error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": True,
        "store_id": sid,
        "product_title": payload.product_title.strip(),
        "product_handle": payload.product_handle.strip(),
        "product_url": payload.product_url.strip(),
        "brief_text": payload.brief_text.strip(),
        "campaign_name": generated.get("campaign_name", ""),
        "summary": generated.get("summary", ""),
        "keywords": generated.get("keywords", []),
        "hashtags": generated.get("hashtags", []),
        "provider_texts": generated.get("provider_texts", {}),
        "generated_by": generated.get("generated_by", ""),
        "generated_provider": generated.get("generated_provider", ""),
    }


@router.post("/api/social/publish")
async def api_social_publish(request: Request, payload: SocialPublishRequest):
    _verify_backend_api_key(request)
    store_row = await _resolve_generation_store(payload.store_id)
    sid = store_row["id"]

    if not publer_service.is_configured():
        raise HTTPException(status_code=503, detail="PUBLER_API_KEY is not configured on the backend")

    workspace_id = payload.workspace_id.strip()
    if not workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id is required")

    mode = payload.mode.strip().lower() or "draft"
    if mode not in {"draft", "scheduled", "publish_now"}:
        raise HTTPException(status_code=400, detail="mode must be draft, scheduled, or publish_now")

    account_ids = [str(a).strip() for a in payload.account_ids if str(a).strip()]
    if not account_ids:
        raise HTTPException(status_code=400, detail="account_ids must include at least one account")

    provider_texts = {
        str(provider).strip().lower(): str(text).strip()
        for provider, text in (payload.provider_texts or {}).items()
        if str(provider).strip() and str(text).strip()
    }
    if not provider_texts:
        raise HTTPException(status_code=400, detail="provider_texts is required")

    scheduled_at = payload.scheduled_at.strip()
    if mode == "scheduled" and not scheduled_at:
        raise HTTPException(status_code=400, detail="scheduled_at is required for scheduled mode")

    try:
        created = await publer_service.create_text_post(
            workspace_id=workspace_id,
            account_ids=account_ids,
            provider_texts=provider_texts,
            mode=mode,
            scheduled_at=scheduled_at,
        )
        job_id = str(created.get("job_id") or "").strip()

        await db.log_social_post(
            store_id=sid,
            store_name=store_row["name"],
            workspace_id=workspace_id,
            campaign_name=payload.campaign_name.strip(),
            product_handle=payload.product_handle.strip(),
            product_title=payload.product_title.strip(),
            brief_text=payload.brief_text.strip(),
            base_text=payload.base_text.strip(),
            provider_texts=provider_texts,
            account_ids=account_ids,
            mode=mode,
            scheduled_at=scheduled_at or None,
            publer_job_id=job_id,
            publer_status="queued",
            publer_failures=[],
        )
    except publer_service.PublerError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "ok": True,
        "store_id": sid,
        "workspace_id": workspace_id,
        "job_id": job_id,
        "mode": mode,
        "status": "queued",
    }


@router.get("/api/social/job-status")
async def api_social_job_status(request: Request, workspace_id: str = "", job_id: str = ""):
    _verify_backend_api_key(request)
    if not publer_service.is_configured():
        raise HTTPException(status_code=503, detail="PUBLER_API_KEY is not configured on the backend")

    if not workspace_id.strip() or not job_id.strip():
        raise HTTPException(status_code=400, detail="workspace_id and job_id are required")

    try:
        status = await publer_service.get_job_status(
            workspace_id=workspace_id.strip(),
            job_id=job_id.strip(),
        )
    except publer_service.PublerError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    await db.update_social_post_job_status(
        publer_job_id=job_id.strip(),
        status=str(status.get("status") or "unknown"),
        failures=status.get("failures", []),
    )

    return {
        "ok": True,
        "workspace_id": workspace_id.strip(),
        "job_id": job_id.strip(),
        "status": status.get("status", "unknown"),
        "payload": status.get("payload", {}),
        "failures": status.get("failures", []),
    }


# --- Schedule CRUD ---

@router.get("/api/schedule/jobs")
async def api_schedule_jobs(request: Request, store_id: str = ""):
    """Return all jobs for a store. Auth: x-api-key header."""
    _verify_backend_api_key(request)
    sid = store_id.strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    rows = await db.get_scheduled_jobs(sid) if sid else []
    return {"jobs": rows, "store_id": sid}


class JobUpsertRequest(BaseModel):
    store_id: str = ""
    job_id: str = ""
    name: str
    prompt_id: str
    blog_handle: str = ""
    author: str = ""
    cron_expr: str
    timezone: str = "UTC"
    is_active: bool = True
    is_product_blog: bool = False
    use_keyword_pool: bool = False


@router.post("/api/schedule/save")
async def api_schedule_save(request: Request, payload: JobUpsertRequest):
    """Create or update a scheduled job. Auth: x-api-key header."""
    _verify_backend_api_key(request)

    sid = payload.store_id.strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    if not sid:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="store_id is required")

    if not payload.name.strip() or not payload.cron_expr.strip() or not payload.prompt_id.strip():
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="name, prompt_id and cron_expr are required")

    try:
        from croniter import croniter as _croniter  # type: ignore
        if not _croniter.is_valid(payload.cron_expr.strip()):
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Invalid cron expression (use 5-field format)")
        import datetime
        _next = int(_croniter(payload.cron_expr.strip(), datetime.datetime.utcnow()).get_next(float))
    except ImportError:
        _next = None

    job_id = await db.upsert_job({
        "id": payload.job_id.strip() or None,
        "store_id": sid,
        "name": payload.name.strip(),
        "prompt_id": payload.prompt_id.strip(),
        "blog_handle": blog_scope.normalize_scheduled_blog_handle(payload.blog_handle),
        "author": payload.author.strip(),
        "cron_expr": payload.cron_expr.strip(),
        "timezone": payload.timezone.strip() or "UTC",
        "is_active": 1 if payload.is_active else 0,
        "next_run_at": _next,
        "is_product_blog": 1 if payload.is_product_blog else 0,
        "use_keyword_pool": 1 if payload.use_keyword_pool else 0,
    })
    return {"ok": True, "job_id": job_id}


@router.post("/api/schedule/delete")
async def api_schedule_delete(request: Request):
    """Delete a scheduled job by id. Auth: x-api-key header."""
    _verify_backend_api_key(request)
    body = await request.json()
    job_id = (body.get("job_id") or "").strip()
    if not job_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="job_id is required")
    await db.delete_job(job_id)
    return {"ok": True}


@router.post("/api/schedule/toggle")
async def api_schedule_toggle(request: Request):
    """Toggle a job's is_active flag. Auth: x-api-key header."""
    _verify_backend_api_key(request)
    body = await request.json()
    job_id = (body.get("job_id") or "").strip()
    is_active = bool(body.get("is_active", True))
    if not job_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="job_id is required")
    # Fetch all jobs across all stores to find this one
    import aiosqlite as _aiosqlite
    from db.base import get_db_path as _get_db_path
    async with _aiosqlite.connect(_get_db_path()) as _db:
        _db.row_factory = _aiosqlite.Row
        async with _db.execute("SELECT * FROM scheduled_jobs WHERE id=?", (job_id,)) as _cur:
            row = await _cur.fetchone()
    if row:
        job = dict(row)
        try:
            from croniter import croniter as _croniter  # type: ignore
            import datetime
            _next = int(_croniter(job["cron_expr"], datetime.datetime.utcnow()).get_next(float)) if is_active else job.get("next_run_at")
        except Exception:
            _next = job.get("next_run_at")
        await db.upsert_job({**job, "is_active": 1 if is_active else 0, "next_run_at": _next})
    return {"ok": True}


@router.get("/api/schedule/recent-runs")
async def api_schedule_recent_runs(request: Request, job_id: str = "", limit: int = 10):
    """Return the most recent published generations for a scheduled job. Auth: x-api-key header."""
    _verify_backend_api_key(request)
    if not job_id.strip():
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="job_id is required")
    limit = max(1, min(limit, 50))
    runs = await db.get_recent_runs_for_job(job_id.strip(), limit)
    return {"runs": runs}


# --- Prompts CRUD ---

@router.get("/api/prompts")
async def api_prompts(request: Request, store_id: str = ""):
    """Return prompts for a store. Auth: x-api-key header."""
    _verify_backend_api_key(request)
    sid = store_id.strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    rows = await db.get_prompts(sid) if sid else []
    return {"prompts": rows, "store_id": sid}


class PromptUpsertRequest(BaseModel):
    store_id: str = ""
    prompt_id: str = ""
    name: str
    text: str
    sort_order: int = 0


@router.post("/api/prompts/save")
async def api_prompts_save(request: Request, payload: PromptUpsertRequest):
    """Create or update a prompt. Auth: x-api-key header."""
    _verify_backend_api_key(request)
    import uuid as _uuid
    sid = payload.store_id.strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    if not sid:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="store_id is required")
    if not payload.name.strip() or not payload.text.strip():
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="name and text are required")
    prompt_id = payload.prompt_id.strip() or str(_uuid.uuid4())
    await db.upsert_prompt({
        "id": prompt_id,
        "store_id": sid,
        "name": payload.name.strip(),
        "text": payload.text.strip(),
        "sort_order": payload.sort_order,
    })
    return {"ok": True, "prompt_id": prompt_id}


@router.post("/api/prompts/delete")
async def api_prompts_delete(request: Request):
    """Delete a prompt by id. Auth: x-api-key header."""
    _verify_backend_api_key(request)
    body = await request.json()
    prompt_id = (body.get("prompt_id") or "").strip()
    if not prompt_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="prompt_id is required")
    await db.delete_prompt(prompt_id)
    return {"ok": True}


# --- Stores list (read-only, for dropdowns) ---

@router.get("/api/stores")
async def api_stores(request: Request):
    """Return all configured backend stores. Auth: x-api-key header."""
    _verify_backend_api_key(request)
    stores = await db.get_stores()
    safe = [{"id": s["id"], "name": s["name"], "myshopify_domain": s["myshopify_domain"],
             "default_blog_handle": s.get("default_blog_handle", "news"),
             "default_author": s.get("default_author", "")} for s in stores]
    return {"stores": safe}


@router.get("/history", response_class=HTMLResponse)
async def history(request: Request):
    store_id = request.session.get("store_id", "")
    scan_requested = request.query_params.get("scan_store") == "1"
    deleted = request.query_params.get("deleted") == "1"
    scan_error = request.query_params.get("scan_error", "")
    store_posts: list[dict] = []
    store_name = ""

    if store_id == "__admin__":
        rows = await db.get_recent_generations(limit=100)
    else:
        rows = await db.get_recent_generations(store_id=store_id, limit=50)
        store_row = await db.get_store(store_id)
        if not store_row:
            return RedirectResponse("/logout", status_code=303)
        store_name = store_row["name"]
        if scan_requested and not scan_error:
            try:
                store = _store_config_from_row(store_row)
                articles = await shopify_client.fetch_store_articles(store)
                verdict_order = {"blocked": 0, "review": 1, "ready": 2}
                for article in articles:
                    summary_text = html_to_review_text(article.summary_html)
                    content_text = html_to_review_text(article.body_html)
                    if not summary_text:
                        summary_text = " ".join(content_text.split())[:170]
                    report = await review_draft(
                        store_id=store_id,
                        title=article.title,
                        summary=summary_text,
                        content=content_text,
                        image_count=1 if article.image_url else 0,
                        exclude_article_url=article.article_url,
                        exclude_article_id=str(article.id),
                    )
                    top_issues = [c.message for c in report.checks if c.status != "pass"][:2]
                    store_posts.append(
                        {
                            "id": article.id,
                            "blog_id": article.blog_id,
                            "blog_handle": article.blog_handle,
                            "title": article.title,
                            "article_url": article.article_url,
                            "published_at": article.published_at,
                            "quality": report.as_dict(),
                            "top_issue": " ".join(top_issues) if top_issues else "Looks healthy.",
                        }
                    )
                store_posts.sort(
                    key=lambda post: (
                        verdict_order.get(post["quality"]["verdict"], 3),
                        post["quality"]["score"],
                        post["title"].lower(),
                    )
                )
            except shopify_client.ShopifyError as exc:
                logger.warning("Failed to scan store posts for %s: %s", store_id, exc)
                scan_error = str(exc)

    return state.templates.TemplateResponse(
        request,
        "history.html",
        {
            "rows": rows,
            "store_posts": store_posts,
            "scan_requested": scan_requested,
            "scan_error": scan_error,
            "deleted": deleted,
            "is_admin": store_id == "__admin__",
            "store_name": store_name,
        },
    )


@router.post("/history/store-posts/delete")
async def delete_store_post(
    request: Request,
    article_id: Annotated[int, Form()],
    blog_id: Annotated[int, Form()],
):
    store_id = request.session.get("store_id", "")
    if not store_id or store_id == "__admin__":
        return RedirectResponse("/setup", status_code=303)

    store_row = await db.get_store(store_id)
    if not store_row:
        return RedirectResponse("/logout", status_code=303)

    try:
        await shopify_client.delete_article(_store_config_from_row(store_row), blog_id, article_id)
        return RedirectResponse("/history?scan_store=1&deleted=1", status_code=303)
    except shopify_client.ShopifyError as exc:
        logger.warning("Failed to delete Shopify article %s/%s for store %s: %s", blog_id, article_id, store_id, exc)
        return RedirectResponse(
            f"/history?scan_store=1&scan_error={quote_plus(str(exc)[:180])}",
            status_code=303,
        )


# ===========================================================================
# Full generate / publish pipeline — consumed by the Shopify Remix admin app
# ===========================================================================

@router.get("/api/init")
async def api_init(request: Request, store_id: str = ""):
    """Return data needed to render the generate form (prompts, models, blogs)."""
    _verify_backend_api_key(request)
    import json as _json
    sid = store_id.strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    if not sid:
        raise HTTPException(status_code=404, detail="No stores configured.")
    store = await db.get_store(sid)
    if not store:
        raise HTTPException(status_code=404, detail=f"Unknown store: {sid}")
    prompts = await db.get_prompts(sid)
    models = [m for m in await db.get_models(sid) if m.get("model_type") == "text" and m.get("is_active")]
    default_prompt_id = await db.get_store_setting(sid, "default_prompt_id", "")
    try:
        blogs = _json.loads(await db.get_store_setting(sid, "cached_blogs", "[]"))
    except Exception:
        blogs = []
    return {
        "store_id": sid,
        "store": {"id": store["id"], "name": store["name"], "default_blog_handle": store.get("default_blog_handle", "news"), "default_author": store.get("default_author", "")},
        "prompts": prompts,
        "models": models,
        "blogs": blogs,
        "default_prompt_id": default_prompt_id,
    }


class GenerateDraftRequest(BaseModel):
    store_id: str = ""
    prompt_id: str = ""
    custom_prompt: str = ""
    blog_handle: str = ""
    author: str = ""
    model_id: str = ""
    product_url: str = ""


@router.post("/api/generate/draft")
async def api_generate_draft(request: Request, payload: GenerateDraftRequest):
    """Full blog generation (LLM + images + quality review). Returns draft data."""
    _verify_backend_api_key(request)
    import re as _re
    store = await _resolve_generation_store(payload.store_id)
    store_id = store["id"]
    store_cfg = _store_config_from_row(store)
    requested_blog_handle = payload.blog_handle.strip()
    uses_auto_blog_handle = blog_scope.is_auto_blog_handle(requested_blog_handle)

    # Resolve prompt text
    if payload.prompt_id and payload.prompt_id != "custom":
        prompts = await db.get_prompts(store_id)
        prompt_cfg = next((p for p in prompts if p["id"] == payload.prompt_id), None)
        if not prompt_cfg:
            raise HTTPException(status_code=404, detail=f"Unknown prompt: {payload.prompt_id}")
        extra = payload.custom_prompt.strip()
        prompt_text = f"{prompt_cfg['text']}\n\n{extra}" if extra else prompt_cfg["text"]
    else:
        prompt_text = payload.custom_prompt.strip()
        if not prompt_text:
            raise HTTPException(status_code=400, detail="prompt text is required")

    resolved_author = payload.author.strip() or store["default_author"]
    resolved_product_url = payload.product_url.strip()
    product_title = ""

    # Product URL enrichment
    if resolved_product_url:
        product_handle_pre = resolved_product_url.rstrip("/").split("/")[-1]
        product_details = await shopify_client.fetch_product_details(store_cfg, product_handle_pre)
        if product_details:
            import re as _re_html
            desc = _re_html.sub(r"<[^>]+>", " ", product_details["description"]).strip()
            desc = " ".join(desc.split())[:600]
            product_title = product_details["title"]
            product_info = f"Product name: {product_title}\nProduct URL: {resolved_product_url}"
            if desc:
                product_info += f"\nProduct description: {desc}"
            if product_details["tags"]:
                product_info += f"\nProduct tags/categories: {product_details['tags']}"
            prompt_text = (
                f"{prompt_text}\n\nWrite this blog post specifically about the following "
                f"product from {store['name']}:\n{product_info}"
            )
        else:
            prompt_text = f"{prompt_text}\n\nWrite this blog post about the product: {resolved_product_url}"

    if uses_auto_blog_handle:
        scopes, scope, resolved_blog_handle = await _resolve_auto_blog_scope(
            store_id,
            store_cfg,
            prompt_text,
            product_title,
            resolved_product_url,
        )
    else:
        resolved_blog_handle = requested_blog_handle or store_cfg.default_blog_handle
        scope = await blog_scope.resolve_blog_scope(store_id, store_cfg, resolved_blog_handle)

    # Title pool / keyword pool injection
    title_row = None
    title_pool_id = 0
    if not resolved_product_url:
        if uses_auto_blog_handle:
            title_row, matched_scope = await title_service.pop_blog_title_for_auto_scope(
                store_id,
                scopes,
                scope,
            )
            if matched_scope is not None:
                scope = matched_scope
                resolved_blog_handle = getattr(scope, "handle", "") or store_cfg.default_blog_handle
        else:
            title_row = await title_service.pop_blog_title_for_scope(store_id, scope)
        if title_row:
            title_pool_id = title_row["id"]
            title_inject = f"\n\nIMPORTANT — You MUST use exactly this title: {title_row['title']}"
            if title_row.get("keyword"):
                title_inject += f"\nFocus keyword: {title_row['keyword']}"
            if title_row.get("meta_description"):
                title_inject += f"\nUse this as the summary/meta description: {title_row['meta_description']}"
            prompt_text = f"{prompt_text}{title_inject}"
        else:
            kw_row = await blog_scope.pop_scoped_keyword(store_id, scope)
            if kw_row:
                kw_block = f"\n\nFocus keyword for this article: {kw_row['keyword']}"
                kw_content = kw_row.get("content", "").strip()
                if kw_content:
                    kw_block += f"\n\nContext (do not quote directly):\n{kw_content[:600]}"
                prompt_text = f"{prompt_text}{kw_block}"

    prompt_text = await blog_scope.apply_blog_scope(
        prompt_text,
        scope=scope,
    )

    # LLM generation
    try:
        blog_data = await llm_service.generate_text(store_id, prompt_text, model_id=payload.model_id or None)
    except AllModelsFailedError as exc:
        raise HTTPException(status_code=502, detail=f"Blog generation failed: {exc}") from exc
    except Exception as exc:
        logger.exception("Unexpected error during draft generation")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    title = blog_data["title"]
    summary = blog_data["summary"]
    content = blog_data["content"]
    content = _re.sub(
        r"^\s*(?:<h[12][^>]*>.*?</h[12]>|#{1,2}\s+[^\n]+|\*\*[^\n]+\*\*)\s*\n?",
        "", content, count=1, flags=_re.IGNORECASE | _re.DOTALL,
    ).lstrip()
    keywords = blog_data.get("keywords", [])
    hashtags = blog_data.get("hashtags", [])
    long_tail_keywords = blog_data.get("long_tail_keywords", [])
    pin_description = blog_data.get("pin_description", "")
    generated_by = blog_data.get("_model_name", "")

    # Images
    image_url_list, image_types, image_labels = await image_service.generate_typed_images(
        store_id,
        title,
        summary,
        prompt_text,
    )
    if resolved_product_url:
        product_handle = resolved_product_url.rstrip("/").split("/")[-1]
        product_image_cdn = await shopify_client.fetch_product_image_url(store_cfg, product_handle)
        image_url_list, image_types, image_labels = image_service.use_product_featured_image(
            product_image_cdn,
            image_url_list,
            image_types,
            image_labels,
        )

    # Quality review
    quality_report = (await review_draft(
        store_id=store_id,
        title=title,
        summary=summary,
        content=content,
        keywords=keywords,
        prompt_text=prompt_text,
        product_url=resolved_product_url,
        product_title=product_title,
        image_count=len(image_url_list),
    )).as_dict()

    return {
        "ok": True,
        "store_id": store_id,
        "prompt_id": payload.prompt_id,
        "prompt_text": prompt_text,
        "blog_handle": resolved_blog_handle,
        "author": resolved_author,
        "title": title,
        "summary": summary,
        "content": content,
        "keywords": keywords,
        "hashtags": hashtags,
        "long_tail_keywords": long_tail_keywords,
        "pin_description": pin_description,
        "image_urls": image_url_list,
        "image_types": image_types,
        "generated_by": generated_by,
        "product_url": resolved_product_url,
        "product_title": product_title,
        "quality_report": quality_report,
        "title_pool_id": title_pool_id,
    }


class PublishArticleRequest(BaseModel):
    store_id: str = ""
    prompt_id: str = ""
    prompt_text: str = ""
    blog_handle: str = ""
    author: str = ""
    title: str
    summary: str = ""
    content: str
    keywords: list[str] = []
    hashtags: list[str] = []
    long_tail_keywords: list[str] = []
    pin_description: str = ""
    image_urls: list[str] = []
    image_types: list[str] = []
    selected_image_index: int = 0
    product_url: str = ""
    product_title: str = ""
    title_pool_id: int = 0


@router.post("/api/publish/article")
async def api_publish_article(request: Request, payload: PublishArticleRequest):
    """Publish an (optionally edited) draft to Shopify."""
    _verify_backend_api_key(request)
    store_row = await _resolve_generation_store(payload.store_id)
    store_id = store_row["id"]
    store = _store_config_from_row(store_row)
    requested_blog_handle = payload.blog_handle.strip()
    if blog_scope.is_auto_blog_handle(requested_blog_handle):
        _, _, resolved_blog_handle = await _resolve_auto_blog_scope(
            store_id,
            store,
            payload.prompt_text,
            payload.title,
            payload.summary,
            " ".join(payload.keywords),
            " ".join(payload.long_tail_keywords),
            payload.product_title,
            payload.product_url,
        )
    else:
        resolved_blog_handle = requested_blog_handle or store.default_blog_handle

    # Quality check
    quality_report = await review_draft(
        store_id=store_id,
        title=payload.title,
        summary=payload.summary,
        content=payload.content,
        keywords=payload.keywords,
        prompt_text=payload.prompt_text,
        product_url=payload.product_url,
        product_title=payload.product_title,
        image_count=len(payload.image_urls),
    )
    if quality_report.publish_blocked:
        raise HTTPException(
            status_code=422,
            detail=f"Quality checks blocked publishing (score {quality_report.score}). Fix failing items and try again.",
        )

    # Re-stamp images with logo
    logo_b64 = await db.get_store_setting(store_id, "logo_data", "")
    ordered_image_urls = list(payload.image_urls)
    composited: list[str] = []
    for i, url in enumerate(payload.image_urls):
        img_type = payload.image_types[i] if i < len(payload.image_types) else "photo"
        if img_type == "product":
            composited.append(await logo_service.stamp_infographic(url, logo_b64))
        elif img_type in ("photo", "hero_photo"):
            composited.append(await logo_service.stamp_photo(url, payload.title, logo_b64))
        else:
            composited.append(await logo_service.stamp_infographic(url, logo_b64))

    featured_image_url = ""
    if composited and 0 < payload.selected_image_index < len(composited):
        composited = (
            [composited[payload.selected_image_index]]
            + composited[: payload.selected_image_index]
            + composited[payload.selected_image_index + 1:]
        )
        ordered_image_urls = (
            [ordered_image_urls[payload.selected_image_index]]
            + ordered_image_urls[: payload.selected_image_index]
            + ordered_image_urls[payload.selected_image_index + 1:]
        )
    if composited:
        featured_image_url = composited[0]

    content_html = text_to_html(payload.content)
    if payload.product_url.strip():
        cta_label = f"Shop {payload.product_title.strip()}" if payload.product_title.strip() else "Shop this product"
        content_html += f'\n<p><a href="{payload.product_url}" target="_blank" rel="noopener">{cta_label}</a></p>'

    try:
        related_links = await internal_links.build_internal_links(
            store,
            store_id,
            title=payload.title,
            keywords=payload.keywords,
            long_tail_keywords=payload.long_tail_keywords,
            current_url="",
            max_links=4,
        )
        related_block = internal_links.render_related_block(related_links)
        if related_block:
            content_html += "\n" + related_block
    except Exception as exc:  # noqa: BLE001 — best-effort, never block publish
        logger.warning("Internal link build failed for API publish store %s: %s", store_id, exc)

    pin_image_url = ""
    if composited:
        try:
            pin_image_url = await logo_service.stamp_pin(featured_image_url, payload.title, logo_b64)
        except Exception as exc:  # noqa: BLE001 — pin is optional
            logger.warning("Pin image build failed for API publish store %s: %s", store_id, exc)

    try:
        result = await shopify_client.publish_article(
            store=store,
            blog_handle=resolved_blog_handle,
            title=payload.title,
            content_html=content_html,
            summary=payload.summary,
            keywords=payload.keywords,
            hashtags=payload.hashtags,
            author=payload.author,
            image_url_list=ordered_image_urls,
            featured_image_url=featured_image_url,
            product_url=payload.product_url.strip(),
            product_title=payload.product_title.strip(),
            long_tail_keywords=payload.long_tail_keywords,
            pin_description=payload.pin_description,
            pin_image_url=pin_image_url,
        )
    except shopify_client.ShopifyError as exc:
        logger.error("Shopify publish failed via API: %s", exc)
        raise HTTPException(status_code=502, detail=f"Could not publish to Shopify: {exc}") from exc

    await db.log_generation(
        store_id=store_id,
        store_name=store_row["name"],
        blog_handle=resolved_blog_handle,
        prompt_id=payload.prompt_id,
        prompt_text=payload.prompt_text,
        title=payload.title,
        summary=payload.summary,
        keywords=payload.keywords,
        hashtags=payload.hashtags,
        image_count=len(composited),
        article_id=str(result.article_id),
        article_url=result.article_url,
        status="published",
    )
    if payload.title_pool_id and not payload.product_url.strip():
        await db.mark_title_published(payload.title_pool_id)

    return {"ok": True, "article_url": result.article_url, "article_id": str(result.article_id), "message": "Published successfully."}


# ===========================================================================
# AI models CRUD
# ===========================================================================

@router.get("/api/models")
async def api_models_list(request: Request, store_id: str = ""):
    """List all AI models for a store."""
    _verify_backend_api_key(request)
    sid = store_id.strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    rows = await db.get_models(sid) if sid else []
    return {"models": rows, "store_id": sid}


class ModelUpsertRequest(BaseModel):
    store_id: str = ""
    model_id: str = ""
    name: str
    provider: str
    model_type: str = "text"
    model_name: str = ""
    api_key: str = ""
    endpoint: str = ""
    extra_json: str = "{}"
    priority: int = 0
    is_active: bool = True


@router.post("/api/models/save")
async def api_models_save(request: Request, payload: ModelUpsertRequest):
    """Create or update an AI model."""
    _verify_backend_api_key(request)
    import json as _json
    import uuid as _uuid
    sid = payload.store_id.strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    if not sid:
        raise HTTPException(status_code=400, detail="store_id is required")
    if not payload.name.strip() or not payload.provider.strip():
        raise HTTPException(status_code=400, detail="name and provider are required")
    try:
        _json.loads(payload.extra_json.strip() or "{}")
    except _json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"extra_json is not valid JSON: {exc}") from exc
    mid = payload.model_id.strip() or str(_uuid.uuid4())
    await db.upsert_model({
        "id": mid, "store_id": sid,
        "name": payload.name.strip(), "provider": payload.provider.strip(),
        "model_type": payload.model_type.strip() or "text",
        "model_name": payload.model_name.strip(), "api_key": payload.api_key.strip(),
        "endpoint": payload.endpoint.strip(), "extra_json": payload.extra_json.strip() or "{}",
        "priority": payload.priority, "is_active": 1 if payload.is_active else 0,
    })
    return {"ok": True, "model_id": mid}


@router.post("/api/models/delete")
async def api_models_delete(request: Request):
    """Delete an AI model by id."""
    _verify_backend_api_key(request)
    body = await request.json()
    model_id = (body.get("model_id") or "").strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id is required")
    await db.delete_model(model_id)
    return {"ok": True}


@router.post("/api/models/toggle")
async def api_models_toggle(request: Request):
    """Toggle a model's is_active flag."""
    _verify_backend_api_key(request)
    body = await request.json()
    model_id = (body.get("model_id") or "").strip()
    is_active = bool(body.get("is_active", True))
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id is required")
    await db.set_model_active(model_id, is_active)
    return {"ok": True}


# ===========================================================================
# Store settings
# ===========================================================================

@router.get("/api/settings")
async def api_settings_get(request: Request, store_id: str = ""):
    """Get store settings (no sensitive values returned in full)."""
    _verify_backend_api_key(request)
    import json as _json
    sid = store_id.strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    if not sid:
        raise HTTPException(status_code=404, detail="No stores configured.")
    store = await db.get_store(sid)
    if not store:
        raise HTTPException(status_code=404, detail=f"Unknown store: {sid}")
    logo_data = await db.get_store_setting(sid, "logo_data", "")
    prompt_ending = await db.get_store_setting(sid, "prompt_ending", "")
    tavily_api_key = await db.get_store_setting(sid, "tavily_api_key", "")
    exa_api_key = await db.get_store_setting(sid, "exa_api_key", "")
    keyword_niche = await db.get_store_setting(sid, "keyword_niche", "")
    keyword_max_pool = int(await db.get_store_setting(sid, "keyword_max_pool", "100"))
    title_gen_model_id = await db.get_store_setting(sid, "title_gen_model_id", "")
    title_gen_prompt_id = await db.get_store_setting(sid, "title_gen_prompt_id", "")
    default_prompt_id = await db.get_store_setting(sid, "default_prompt_id", "")
    try:
        social_share_buttons = _json.loads(
            await db.get_store_setting(sid, "social_share_buttons", '["x","facebook","linkedin"]')
        )
    except Exception:
        social_share_buttons = ["x", "facebook", "linkedin"]
    social_x_handle = await db.get_store_setting(sid, "social_x_handle", "")
    return {
        "store_id": sid,
        "default_blog_handle": store.get("default_blog_handle", "news"),
        "default_author": store.get("default_author", ""),
        "myshopify_domain": store.get("myshopify_domain", ""),
        "has_logo": bool(logo_data),
        "prompt_ending": prompt_ending,
        "tavily_api_key_set": bool(tavily_api_key),
        "exa_api_key_set": bool(exa_api_key),
        "keyword_niche": keyword_niche,
        "keyword_max_pool": keyword_max_pool,
        "title_gen_model_id": title_gen_model_id,
        "title_gen_prompt_id": title_gen_prompt_id,
        "default_prompt_id": default_prompt_id,
        "social_share_buttons": social_share_buttons,
        "social_x_handle": social_x_handle,
    }


class SettingsSaveRequest(BaseModel):
    store_id: str = ""
    default_blog_handle: str = ""
    default_author: str = ""
    prompt_ending: str = ""
    tavily_api_key: str = ""
    exa_api_key: str = ""
    keyword_niche: str = ""
    keyword_max_pool: int = 100
    title_gen_model_id: str = ""
    title_gen_prompt_id: str = ""
    default_prompt_id: str = ""
    social_x_handle: str = ""


@router.post("/api/settings/save")
async def api_settings_save(request: Request, payload: SettingsSaveRequest):
    """Save store settings."""
    _verify_backend_api_key(request)
    sid = payload.store_id.strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    if not sid:
        raise HTTPException(status_code=400, detail="store_id is required")
    store = await db.get_store(sid)
    if not store:
        raise HTTPException(status_code=404, detail=f"Unknown store: {sid}")
    if payload.default_blog_handle.strip() or payload.default_author.strip():
        await db.upsert_store({
            **store,
            "default_blog_handle": payload.default_blog_handle.strip() or store["default_blog_handle"],
            "default_author": payload.default_author.strip() or store["default_author"],
        })
    settings_to_save: dict = {
        "prompt_ending": payload.prompt_ending,
        "keyword_niche": payload.keyword_niche.strip(),
        "keyword_max_pool": str(max(10, min(500, payload.keyword_max_pool))),
        "title_gen_model_id": payload.title_gen_model_id.strip(),
        "title_gen_prompt_id": payload.title_gen_prompt_id.strip(),
        "default_prompt_id": payload.default_prompt_id.strip(),
        "social_x_handle": payload.social_x_handle.strip().lstrip("@"),
    }
    if payload.tavily_api_key.strip():
        settings_to_save["tavily_api_key"] = payload.tavily_api_key.strip()
    if payload.exa_api_key.strip():
        settings_to_save["exa_api_key"] = payload.exa_api_key.strip()
    await db.set_store_settings(sid, settings_to_save)
    return {"ok": True, "message": "Settings saved."}


# ===========================================================================
# Keyword pool
# ===========================================================================

@router.get("/api/keywords")
async def api_keywords_list(request: Request, store_id: str = "", limit: int = 200):
    """Return the keyword pool for a store."""
    _verify_backend_api_key(request)
    sid = store_id.strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    rows = await db.get_keyword_pool(sid, limit=min(limit, 500)) if sid else []
    count = await db.count_keyword_pool(sid) if sid else 0
    niche = await db.get_store_setting(sid, "keyword_niche", "") if sid else ""
    return {"keywords": rows, "count": count, "keyword_niche": niche, "store_id": sid}


@router.post("/api/keywords/fetch")
async def api_keywords_fetch(request: Request):
    """Trigger an immediate keyword fetch for a store."""
    _verify_backend_api_key(request)
    from services.keyword_service import fetch_keywords
    body = await request.json()
    sid = (body.get("store_id") or "").strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    if not sid:
        raise HTTPException(status_code=400, detail="store_id is required")
    niche = await db.get_store_setting(sid, "keyword_niche", "")
    max_pool = int(await db.get_store_setting(sid, "keyword_max_pool", "100"))
    result = await fetch_keywords(sid, niche, max_pool=max_pool)
    return {"ok": result.get("error") is None, **result}


@router.post("/api/keywords/delete")
async def api_keywords_delete(request: Request):
    """Delete a single keyword by id."""
    _verify_backend_api_key(request)
    body = await request.json()
    keyword_id = body.get("keyword_id")
    if keyword_id is None:
        raise HTTPException(status_code=400, detail="keyword_id is required")
    await db.delete_keyword(int(keyword_id))
    return {"ok": True}


@router.post("/api/keywords/clear")
async def api_keywords_clear(request: Request):
    """Clear the entire keyword pool for a store."""
    _verify_backend_api_key(request)
    body = await request.json()
    sid = (body.get("store_id") or "").strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    if not sid:
        raise HTTPException(status_code=400, detail="store_id is required")
    count = await db.clear_keyword_pool(sid)
    return {"ok": True, "count": count}


# ===========================================================================
# Title pool
# ===========================================================================

@router.get("/api/titles")
async def api_titles_list(request: Request, store_id: str = "", limit: int = 200):
    """Return the title pool for a store."""
    _verify_backend_api_key(request)
    sid = store_id.strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    rows = await db.get_title_pool(sid, limit=min(limit, 500)) if sid else []
    count = await db.count_title_pool(sid) if sid else 0
    return {"titles": rows, "count": count, "store_id": sid}


@router.post("/api/titles/generate")
async def api_titles_generate(request: Request):
    """Trigger an immediate title batch generation for a store."""
    _verify_backend_api_key(request)
    from services.title_service import fetch_titles as _fetch_titles
    body = await request.json()
    sid = (body.get("store_id") or "").strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    if not sid:
        raise HTTPException(status_code=400, detail="store_id is required")
    result = await _fetch_titles(sid)
    return {"ok": result.get("error") is None, **result}


@router.post("/api/titles/delete")
async def api_titles_delete(request: Request):
    """Delete a single title by id."""
    _verify_backend_api_key(request)
    body = await request.json()
    title_id = body.get("title_id")
    if title_id is None:
        raise HTTPException(status_code=400, detail="title_id is required")
    await db.delete_title(int(title_id))
    return {"ok": True}


@router.post("/api/titles/clear")
async def api_titles_clear(request: Request):
    """Clear the entire title pool for a store."""
    _verify_backend_api_key(request)
    body = await request.json()
    sid = (body.get("store_id") or "").strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    if not sid:
        raise HTTPException(status_code=400, detail="store_id is required")
    count = await db.clear_title_pool(sid)
    return {"ok": True, "count": count}


# ── Quality check endpoints ────────────────────────────────────────────────

_LLM_REVIEW_SYSTEM = (
    "You are a senior ecommerce SEO editor. Review drafts for usefulness, search intent, "
    "commerce fit, readability, claim safety, and brand trust. Be concise and actionable."
)
_LLM_REVIEW_PROMPT_ENDING = """Return ONLY a single valid JSON object with exactly these fields:
    "title": string - concise editorial verdict with a score, for example "Editorial Review - 82/100"
    "summary": string - 1-2 sentence overall assessment
    "keywords": array of strings - 3-6 short issue categories
    "hashtags": array of strings - empty array
    "content": string - actionable review notes in plain text. Use ## headings and - bullets. Include Strengths, Issues, and Recommended Edits. Do not rewrite the full article.

No markdown fences. No explanation. Raw JSON only."""


class QualityReviewRequest(BaseModel):
    store_id: str = ""
    title: str = ""
    summary: str = ""
    content: str = ""
    keywords: list[str] = []
    prompt_text: str = ""
    product_url: str = ""
    product_title: str = ""
    image_count: int = 0


class LlmReviewRequest(BaseModel):
    store_id: str = ""
    title: str = ""
    summary: str = ""
    content: str = ""
    keywords: list[str] = []
    prompt_text: str = ""
    product_url: str = ""
    product_title: str = ""
    model_id: str = ""


@router.post("/api/quality/review")
async def api_quality_review(request: Request, payload: QualityReviewRequest):
    """Run fast heuristic quality checks on a draft."""
    _verify_backend_api_key(request)
    sid = payload.store_id.strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    report = await review_draft(
        store_id=sid,
        title=payload.title,
        summary=payload.summary,
        content=payload.content,
        keywords=payload.keywords,
        prompt_text=payload.prompt_text,
        product_url=payload.product_url,
        product_title=payload.product_title,
        image_count=payload.image_count,
    )
    return {"ok": True, "quality_report": report.as_dict()}


@router.post("/api/quality/llm-review")
async def api_quality_llm_review(request: Request, payload: LlmReviewRequest):
    """Run a full LLM editorial review on a draft."""
    _verify_backend_api_key(request)
    sid = payload.store_id.strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    keywords = payload.keywords or []
    review_prompt = f"""
Review this generated ecommerce blog draft before publishing.

Original prompt:
{payload.prompt_text.strip() or "(not provided)"}

Target SEO keywords:
{', '.join(keywords) if keywords else "(not provided)"}

Related product title:
{payload.product_title.strip() or "(not provided)"}

Related product URL:
{payload.product_url.strip() or "(not provided)"}

Draft title:
{payload.title.strip()}

Draft summary:
{payload.summary.strip()}

Draft content:
{payload.content.strip()}

Review for prompt relevance, search intent, SEO completeness, product fit, readability, trust and claim safety, AI artifacts, and concrete edit recommendations.
""".strip()
    try:
        review = await llm_service.generate_text(
            sid,
            review_prompt,
            system_prompt=_LLM_REVIEW_SYSTEM,
            model_id=payload.model_id.strip() or None,
            prompt_ending_override=_LLM_REVIEW_PROMPT_ENDING,
        )
    except AllModelsFailedError as exc:
        raise HTTPException(status_code=502, detail=f"LLM review failed: {exc}")
    except Exception as exc:
        logger.exception("Unexpected error during LLM quality review")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")
    return {
        "ok": True,
        "review": {
            "title": review.get("title", "Editorial Review"),
            "summary": review.get("summary", ""),
            "content": review.get("content", ""),
            "keywords": review.get("keywords", []),
            "model": review.get("_model_name", ""),
            "provider": review.get("_model_provider", ""),
        },
    }


class ProductBlogGenerateRequest(BaseModel):
    store_id: str = ""
    product_title: str = ""
    product_handle: str = ""
    product_url: str = ""
    blog_handle: str = "inside-the-products"
    prompt_id: str = ""


async def run_product_blog_generation_task(
    store_id: str,
    prompt_text: str,
    blog_handle: str,
    prompt_id_val: str,
    product_url: str,
    product_title: str,
    product_handle: str,
    task_key: str
):
    from services import publish_service
    try:
        state.product_blog_tasks[task_key]["status"] = "processing"
        result = await publish_service.run(
            store_id=store_id,
            prompt_text=prompt_text,
            blog_handle=blog_handle,
            author="Store Team",
            prompt_id=prompt_id_val,
            product_url=product_url,
            product_title=product_title,
        )
        state.product_blog_tasks[task_key].update({
            "status": "success",
            "article_id": str(result.article_id),
            "article_url": result.article_url,
            "title": result.title,
            "updated_at": time.time()
        })
    except Exception as exc:
        logger.exception("Failed to generate blog for product %s in background", product_title)
        state.product_blog_tasks[task_key].update({
            "status": "failed",
            "error": f"Generation failed: {str(exc)}",
            "updated_at": time.time()
        })


@router.post("/api/products/generate-blog")
async def api_product_blog_generate(
    request: Request,
    payload: ProductBlogGenerateRequest,
    background_tasks: BackgroundTasks
):
    _verify_backend_api_key(request)
    sid = payload.store_id.strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    if not sid:
        raise HTTPException(status_code=400, detail="store_id is required")

    phandle = payload.product_handle.strip()
    if not phandle:
        raise HTTPException(status_code=400, detail="product_handle is required")

    task_key = f"{sid}:{phandle}"

    # Check if there is already a running task
    existing_task = state.product_blog_tasks.get(task_key)
    if existing_task and existing_task.get("status") in ("pending", "processing"):
        return {
            "ok": True,
            "status": existing_task["status"],
            "message": f"Generation for {payload.product_title} is already in progress."
        }

    prompt_id = payload.prompt_id.strip()
    if not prompt_id:
        prompt_id = await db.get_store_setting(sid, "default_prompt_id", "")

    prompts = await db.get_prompts(sid)
    prompt_cfg = None
    if prompt_id:
        prompt_cfg = next((p for p in prompts if p["id"] == prompt_id), None)
    if not prompt_cfg and prompts:
        prompt_cfg = prompts[0]

    if not prompt_cfg:
        prompt_text = "Write a highly engaging, helpful product guide and blog post."
        prompt_id_val = "default"
    else:
        prompt_text = prompt_cfg["text"]
        prompt_id_val = prompt_cfg["id"]

    # Initialise state
    state.product_blog_tasks[task_key] = {
        "status": "pending",
        "article_id": None,
        "article_url": None,
        "title": None,
        "error": None,
        "updated_at": time.time()
    }

    background_tasks.add_task(
        run_product_blog_generation_task,
        store_id=sid,
        prompt_text=prompt_text,
        blog_handle=payload.blog_handle.strip() or "inside-the-products",
        prompt_id_val=prompt_id_val,
        product_url=payload.product_url.strip(),
        product_title=payload.product_title.strip(),
        product_handle=phandle,
        task_key=task_key
    )

    return {
        "ok": True,
        "status": "pending",
        "message": f"Successfully queued generation for {payload.product_title}"
    }


@router.get("/api/products/generate-blog/status")
async def api_product_blog_generate_status(request: Request, store_id: str, product_handle: str):
    _verify_backend_api_key(request)
    if not store_id or not product_handle:
        raise HTTPException(status_code=400, detail="store_id and product_handle are required")

    key = f"{store_id}:{product_handle}"
    task = state.product_blog_tasks.get(key)
    if not task:
        return {
            "status": "idle"
        }

    return {
        "status": task.get("status", "idle"),
        "article_id": task.get("article_id"),
        "article_url": task.get("article_url"),
        "title": task.get("title"),
        "error": task.get("error")
    }


class ProductBlogEnsureDescriptionRequest(BaseModel):
    store_id: str = ""
    product_title: str = ""
    product_handle: str = ""
    product_url: str = ""
    guide_title: str = ""
    guide_url: str = ""


@router.post("/api/products/ensure-description")
async def api_product_ensure_description(request: Request, payload: ProductBlogEnsureDescriptionRequest):
    _verify_backend_api_key(request)
    sid = payload.store_id.strip() or os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if not sid:
        stores = await db.get_stores()
        sid = stores[0]["id"] if stores else ""
    if not sid:
        raise HTTPException(status_code=400, detail="store_id is required")

    logger.info("Ensuring description link for product %s (%s)", payload.product_title, payload.guide_url)

    # 1. Look up any cached keywords/hashtags in generations database for this guide_url
    import json
    import aiosqlite
    from db.base import get_db_path

    keywords = []
    hashtags = []
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT keywords, hashtags FROM generations WHERE store_id=? AND article_url=? LIMIT 1",
            (sid, payload.guide_url)
        ) as cur:
            row = await cur.fetchone()

    if row:
        try:
            keywords = json.loads(row["keywords"] or "[]")
        except Exception:
            keywords = []
        try:
            hashtags = json.loads(row["hashtags"] or "[]")
        except Exception:
            hashtags = []

    # If empty, let's generate some fallback keywords and hashtags from the product's title or guide title
    text_source = payload.guide_title or payload.product_title or ""
    if not keywords:
        words = [w.strip() for w in text_source.split() if len(w.strip()) > 2]
        keywords = words + [text_source] if len(words) > 1 else words
    if not hashtags:
        words = [w.strip() for w in text_source.split() if len(w.strip()) > 2]
        hashtags = [f"#{w}" for w in words]

    # Resolve store config
    store_row = await db.get_store(sid)
    if not store_row:
        raise HTTPException(status_code=404, detail="Store not found in database")
    store_cfg = _store_config_from_row(store_row)

    try:
        await shopify_client._update_product_description_with_guide_link(
            store=store_cfg,
            product_handle=payload.product_handle.strip(),
            guide_title=payload.guide_title.strip(),
            guide_url=payload.guide_url.strip(),
            keywords=keywords,
            hashtags=hashtags,
        )
        return {
            "ok": True,
            "message": f"Successfully ensured/updated description for {payload.product_title}"
        }
    except Exception as exc:
        logger.exception("Failed to update product description with guide link")
        raise HTTPException(status_code=500, detail=str(exc))
