"""Helpers for using Shopify blog-handle context during generation."""
from __future__ import annotations

import json
import logging

import db
import shopify_client
from config import StoreConfig

logger = logging.getLogger("ai_blog_server")


def _humanize_handle(handle: str) -> str:
    words = [part for part in str(handle or "").replace("_", "-").split("-") if part]
    if not words:
        return ""
    return " ".join(word.capitalize() for word in words)


async def get_blog_options(store_id: str, store: StoreConfig) -> list[dict[str, str]]:
    """Return known blog handles/titles, refreshing from Shopify when needed."""
    try:
        cached_raw = await db.get_store_setting(store_id, "cached_blogs", "[]")
        cached = json.loads(cached_raw)
        if isinstance(cached, list) and cached:
            return [
                {
                    "handle": str(item.get("handle", "")).strip(),
                    "title": str(item.get("title", "")).strip(),
                }
                for item in cached
                if str(item.get("handle", "")).strip()
            ]
    except Exception:
        pass

    try:
        blogs = await shopify_client.fetch_blogs(store)
        blog_options = [{"handle": b.handle, "title": b.title or b.handle} for b in blogs]
        await db.set_store_settings(store_id, {"cached_blogs": json.dumps(blog_options)})
        return blog_options
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch blog handles for store %s: %s", store_id, exc)
        return []


async def apply_blog_scope(
    prompt_text: str,
    *,
    store_id: str,
    store: StoreConfig,
    blog_handle: str,
) -> str:
    """Append handle-driven section guidance to the prompt.

    The Shopify blog handle is treated as the publishing section/category so the
    generated article stays aligned with that section's topic and search intent.
    """
    resolved_handle = (blog_handle or store.default_blog_handle or "").strip()
    if not resolved_handle:
        return prompt_text

    blog_options = await get_blog_options(store_id, store)
    blog_title = next((b["title"] for b in blog_options if b["handle"] == resolved_handle), "")
    section_name = blog_title or _humanize_handle(resolved_handle) or resolved_handle

    scope_block = (
        "\n\nPublishing section context:\n"
        f"- This article will be published under the Shopify blog handle '{resolved_handle}'.\n"
        f"- Treat that handle as the section/category scope for the article: {section_name}.\n"
        "- Keep the topic, examples, framing, and search intent tightly aligned to that section.\n"
        "- Avoid drifting into themes that belong in other blog sections unless they directly support this section's topic."
    )
    return f"{prompt_text}{scope_block}"