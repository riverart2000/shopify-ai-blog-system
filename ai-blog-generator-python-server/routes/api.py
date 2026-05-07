"""
routes/api.py — Utility and data endpoints.

  GET /health
  GET /api/blogs/{store_id}
  GET /history
"""
from __future__ import annotations

import hmac
import logging
import os
from typing import Annotated
from urllib.parse import quote_plus

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

import db
import shopify_client
import state
from config import StoreConfig
from providers import AllModelsFailedError
from services import image_service, llm_service, title_service
from services.quality_service import html_to_review_text, review_draft

router = APIRouter()
logger = logging.getLogger("ai_blog_server")


class GenerateApiRequest(BaseModel):
    prompt: str
    store_id: str = ""
    model_id: str = ""


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

    title_row = await title_service.pop_blog_title(store_id)
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
        keyword_row = await db.pop_keyword(store_id)
        if keyword_row:
            prompt_text += f"\n\nFocus keyword for this article: {keyword_row['keyword']}"
            keyword_context = keyword_row.get("content", "").strip()
            if keyword_context:
                prompt_text += (
                    "\n\nWhat people are currently discussing about this topic "
                    f"(use as context, do not quote directly):\n{keyword_context[:600]}"
                )
            logger.info("API generation using pooled keyword %r for store %s", keyword_row["keyword"], store_id)

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
