"""services/title_service.py — Blog title pool: generate and manage pre-generated titles."""
from __future__ import annotations

import json
import logging
import re

import db
import providers
from utils import clean_title, log_debug_payload

logger = logging.getLogger("ai_blog_server")

_JSON_FORMAT_INSTRUCTION = (
    "\n\nReturn ONLY a valid JSON array. No markdown fences, no explanation. "
    "Each element must have exactly these fields:\n"
    '  "keyword": string — the long-tail keyword phrase\n'
    '  "search_intent": string — one-line description of search intent\n'
    '  "title": string — the blog post title\n'
    '  "meta_description": string — SEO meta description (max 160 chars)\n'
    "Raw JSON array only."
)


def _parse_title_array(raw: str) -> list[dict]:
    """Extract a JSON array from raw model output."""
    s = raw.strip()
    # Strip markdown fences
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    s = s.strip()
    # Find outermost [ ... ]
    start, end = s.find("["), s.rfind("]")
    if start == -1 or end <= start:
        raise ValueError(f"No JSON array found in model output: {s[:300]}")
    try:
        data = json.loads(s[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON parse failed: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array, got {type(data).__name__}")
    for item in data:
        if isinstance(item, dict) and item.get("title"):
            item["title"] = clean_title(item["title"])
    return data


async def fetch_titles(store_id: str) -> dict:
    """Generate a fresh batch of blog titles using the configured model.

    Returns:
        {"added": int, "pool_count": int, "error": str | None}
    """
    model_id = await db.get_store_setting(store_id, "title_gen_model_id", "")
    prompt_id = await db.get_store_setting(store_id, "title_gen_prompt_id", "")

    if not model_id:
        return {
            "added": 0,
            "pool_count": await db.count_title_pool(store_id),
            "error": "No title-generation model configured.",
        }

    model_row = await db.get_model(model_id)
    if not model_row or model_row.get("store_id") != store_id:
        return {
            "added": 0,
            "pool_count": await db.count_title_pool(store_id),
            "error": "Configured model not found.",
        }

    # Get the prompt text
    prompt_text = ""
    if prompt_id:
        prompts = await db.get_prompts(store_id)
        prompt_row = next((p for p in prompts if p["id"] == prompt_id), None)
        if prompt_row:
            prompt_text = prompt_row["text"]

    if not prompt_text:
        return {
            "added": 0,
            "pool_count": await db.count_title_pool(store_id),
            "error": "No title-generation prompt configured.",
        }

    full_prompt = f"{prompt_text}{_JSON_FORMAT_INSTRUCTION}"

    try:
        model = providers.ModelRecord.from_dict(model_row)
        provider = providers.get_text_provider(model)
        raw = await provider.generate_raw(full_prompt)
        log_debug_payload(logger, "Title generator raw response", {"raw": raw[:500]})
    except Exception as exc:
        logger.warning("Title generation failed for store %s: %s", store_id, exc)
        return {
            "added": 0,
            "pool_count": await db.count_title_pool(store_id),
            "error": f"Model call failed: {exc}",
        }

    try:
        titles = _parse_title_array(raw)
    except ValueError as exc:
        logger.warning("Title parse failed for store %s: %s", store_id, exc)
        return {
            "added": 0,
            "pool_count": await db.count_title_pool(store_id),
            "error": f"Could not parse response: {exc}",
        }

    added = await db.add_titles(store_id, titles)
    pool_count = await db.count_title_pool(store_id)
    logger.info("Titles for store %s: %d added, pool now %d", store_id, added, pool_count)
    return {"added": added, "pool_count": pool_count, "error": None}


async def pop_blog_title(store_id: str) -> dict | None:
    """Pop the oldest unused title from the pool.

    Auto-fetches a new batch if the pool is empty.
    Returns None if fetching also yields nothing (model not configured, etc.).
    """
    row = await db.pop_title(store_id)
    if row:
        return row

    # Pool empty — try to generate more
    result = await fetch_titles(store_id)
    if result["error"] or result["added"] == 0:
        logger.info(
            "Title pool empty and auto-fetch returned nothing for store %s: %s",
            store_id, result.get("error") or "0 titles generated",
        )
        return None

    return await db.pop_title(store_id)
