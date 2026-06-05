"""Helpers for using Shopify blog-handle context during generation."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import db
import shopify_client
from config import StoreConfig

logger = logging.getLogger("ai_blog_server")

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SCOPE_STOPWORDS = {
    "a", "an", "and", "at", "blog", "blogs", "by", "for", "from", "guide",
    "guides", "handle", "home", "in", "news", "of", "on", "or", "section",
    "shop", "shopify", "store", "the", "to", "wellness", "health",
}


@dataclass(frozen=True)
class BlogScope:
    handle: str
    section_name: str
    focus_terms: tuple[str, ...]


def _humanize_handle(handle: str) -> str:
    words = [part for part in str(handle or "").replace("_", "-").split("-") if part]
    if not words:
        return ""
    return " ".join(word.capitalize() for word in words)


def _extract_focus_terms(*parts: str) -> tuple[str, ...]:
    tokens: list[str] = []
    seen: set[str] = set()
    for part in parts:
        for token in _TOKEN_RE.findall(str(part or "").lower()):
            if len(token) < 4 or token in _SCOPE_STOPWORDS or token in seen:
                continue
            seen.add(token)
            tokens.append(token)
    return tuple(tokens)


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


async def resolve_blog_scope(
    store_id: str,
    store: StoreConfig,
    blog_handle: str,
) -> BlogScope | None:
    resolved_handle = (blog_handle or store.default_blog_handle or "").strip()
    if not resolved_handle:
        return None

    blog_options = await get_blog_options(store_id, store)
    blog_title = next((b["title"] for b in blog_options if b["handle"] == resolved_handle), "")
    section_name = blog_title or _humanize_handle(resolved_handle) or resolved_handle
    focus_terms = _extract_focus_terms(resolved_handle, blog_title)
    return BlogScope(
        handle=resolved_handle,
        section_name=section_name,
        focus_terms=focus_terms,
    )


def is_candidate_compatible(candidate_text: str, scope: BlogScope | None) -> bool:
    """Return True when a pooled title/keyword fits the scoped blog section.

    Broad sections may have no focus terms left after normalisation; in that case
    we do not filter candidate text.
    """
    if scope is None or not scope.focus_terms:
        return True
    candidate_terms = set(_extract_focus_terms(candidate_text))
    return bool(candidate_terms & set(scope.focus_terms))


async def pop_scoped_keyword(store_id: str, scope: BlogScope | None) -> dict | None:
    """Select and remove the first keyword that matches the blog section scope."""
    pool = await db.get_keyword_pool(store_id, limit=200)
    for row in pool:
        haystack = f"{row.get('keyword', '')} {row.get('content', '')}"
        if is_candidate_compatible(haystack, scope):
            await db.delete_keyword(int(row["id"]))
            return row

    if scope and scope.focus_terms:
        logger.info(
            "No keyword-pool entry matched blog scope | store=%s handle=%s focus_terms=%s",
            store_id,
            scope.handle,
            ", ".join(scope.focus_terms),
        )
    return None


async def apply_blog_scope(
    prompt_text: str,
    *,
    scope: BlogScope | None = None,
    store_id: str | None = None,
    store: StoreConfig | None = None,
    blog_handle: str = "",
) -> str:
    """Append handle-driven section guidance to the prompt.

    The Shopify blog handle is treated as the publishing section/category so the
    generated article stays aligned with that section's topic and search intent.
    """
    if scope is None:
        if store_id is None or store is None:
            return prompt_text
        scope = await resolve_blog_scope(store_id, store, blog_handle)

    if scope is None:
        return prompt_text

    focus_line = ""
    if scope.focus_terms:
        focus_line = (
            f"- The main topic must clearly relate to these section terms: {', '.join(scope.focus_terms)}.\n"
        )

    scope_block = (
        "\n\nSECTION SCOPE — HIGHEST PRIORITY:\n"
        f"- Shopify blog handle '{scope.handle}' is the publishing section for this article.\n"
        f"- Section/category name: {scope.section_name}.\n"
        f"{focus_line}"
        "- Keep the topic, title, examples, framing, and search intent tightly aligned to this section.\n"
        "- If any earlier prompt wording, pooled title, keyword, or example conflicts with this section, ignore the conflicting angle and keep the article inside this section.\n"
        "- Do not switch to a different wellness niche unless it directly supports this section's core topic."
    )
    return f"{prompt_text}{scope_block}"