"""Template registry: lookup by key/content type, DB seeding and listing."""
from __future__ import annotations

import logging

from db.social_engine import config_store

from ..models import CONTENT_TYPE_LABELS
from .templates import TemplateDef, all_templates, template

logger = logging.getLogger("ai_blog_server.social_engine.creatives")


def get_builtin(key: str) -> TemplateDef | None:
    return template((key or "").strip())


def templates_for(content_type: str) -> list[str]:
    """Built-in template keys that serve `content_type` (best default first)."""
    wanted = (content_type or "").strip()
    matching = [t for t in all_templates() if wanted in t.content_types]
    # Dedicated templates (primary content type) first, then shared ones; videos last.
    matching.sort(key=lambda t: (t.kind == "video", t.primary_content_type != wanted))
    return [t.key for t in matching]


def default_template_for(content_type: str) -> TemplateDef | None:
    keys = templates_for(content_type)
    return get_builtin(keys[0]) if keys else None


async def seed_builtin_templates(store_id: str) -> int:
    """Insert or refresh every built-in template for a store.

    Idempotent. `upsert_template` never updates `is_active` on conflict, so a
    template the merchant switched off stays off. Returns the number written.
    """
    count = 0
    for item in all_templates():
        await config_store.upsert_template(store_id, item.to_record())
        count += 1
    logger.info("Seeded %d built-in social templates for store %s", count, store_id)
    return count


def _describe(item: TemplateDef, *, is_active: bool = True) -> dict:
    return {
        "key": item.key,
        "name": item.name,
        "description": item.description,
        "content_type": item.primary_content_type,
        "content_types": list(item.content_types),
        "content_type_labels": [CONTENT_TYPE_LABELS.get(ct, ct) for ct in item.content_types],
        "kind": item.kind,
        "formats": list(item.formats),
        "style": item.style,
        "min_products": item.min_products,
        "max_products": item.max_products,
        "needs_images": item.needs_images,
        "required_texts": list(item.required_texts),
        "default_texts": dict(item.defaults),
        "is_builtin": True,
        "is_active": is_active,
    }


async def list_available_templates(
    store_id: str, content_type: str = "", *, include_inactive: bool = False,
) -> list[dict]:
    """Templates a store can use (built-ins merged with the store's rows).

    Built-ins are listed even before seeding (active by default); the stored
    `is_active` flag wins once a row exists. Custom (non built-in) rows are
    included as stored.
    """
    rows = {row["key"]: row for row in await config_store.list_templates(store_id)}
    result: list[dict] = []
    ordered = [get_builtin(key) for key in templates_for(content_type)] if content_type else all_templates()
    builtin_keys = {t.key for t in all_templates()}
    for key in builtin_keys - {t.key for t in ordered if t}:
        rows.pop(key, None)
    for item in ordered:
        if item is None:
            continue
        row = rows.pop(item.key, None)
        active = bool(row["is_active"]) if row is not None else True
        if active or include_inactive:
            result.append(_describe(item, is_active=active))
    for row in rows.values():
        if row.get("is_builtin"):
            continue  # a retired built-in: no longer renderable
        spec = row.get("spec") or {}
        content_types = list(spec.get("content_types") or [row.get("content_type", "")])
        if content_type and content_type not in content_types:
            continue
        if not row.get("is_active") and not include_inactive:
            continue
        result.append({
            "key": row["key"], "name": row["name"], "description": spec.get("description", ""),
            "content_type": row.get("content_type", ""), "content_types": content_types,
            "content_type_labels": [CONTENT_TYPE_LABELS.get(ct, ct) for ct in content_types],
            "kind": row.get("kind", "image"), "formats": list(row.get("formats") or []),
            "style": spec.get("style", "custom"), "min_products": spec.get("min_products", 1),
            "max_products": spec.get("max_products", 1), "needs_images": spec.get("needs_images", True),
            "required_texts": list(spec.get("required_texts") or []), "default_texts": dict(spec.get("defaults") or {}),
            "is_builtin": False, "is_active": bool(row.get("is_active")),
        })
    return result
