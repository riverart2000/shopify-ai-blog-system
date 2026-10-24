"""Which products the auto-pilot may post (Outfy's SmartQ product rules).

Always: ACTIVE, published to the Online Store (has a storefront URL), not
deleted, not excluded by id or tag in the store settings. Optionally: in stock,
on sale, in given collections, carrying given tags.
"""
from __future__ import annotations

from typing import Iterable

from db.social_engine import products as product_store

from ..settings import EngineSettings, load_settings
from .normalize import legacy_id


def _tagset(tags: Iterable[str] | None) -> set[str]:
    return {str(tag).strip().lower() for tag in tags or [] if str(tag).strip()}


def _idset(ids: Iterable[str | int] | None) -> set[str]:
    return {legacy_id(value) for value in ids or []} - {""}


async def eligible_products(
    store_id: str,
    *,
    collection_ids: Iterable[str] | None = None,
    include_tags: Iterable[str] | None = None,
    exclude_tags: Iterable[str] | None = None,
    exclude_product_ids: Iterable[str] | None = None,
    in_stock_only: bool = True,
    on_sale_only: bool = False,
    settings: EngineSettings | None = None,
) -> list[dict]:
    """Products the auto-pilot may post, newest first. Empty filters mean "no filter".

    Ids may be numeric or Shopify gids; tags compare case-insensitively.
    """
    settings = settings or await load_settings(store_id)
    rows = await product_store.list_publishable_products(
        store_id, in_stock_only=in_stock_only, on_sale_only=on_sale_only,
    )
    excluded_ids = _idset([*settings.content.excluded_product_ids, *(exclude_product_ids or [])])
    excluded_tags = _tagset([*settings.content.excluded_tags, *(exclude_tags or [])])
    wanted_tags = _tagset(include_tags)
    wanted_collections = _idset(collection_ids)
    members = await product_store.collection_member_ids(store_id, wanted_collections) if wanted_collections else set()

    eligible: list[dict] = []
    for row in rows:
        product_id = str(row["product_id"])
        if product_id in excluded_ids:
            continue
        tags = _tagset(row.get("tags"))
        if tags & excluded_tags:
            continue
        if wanted_tags and not tags & wanted_tags:
            continue
        if wanted_collections:
            own = {legacy_id(item.get("id")) for item in row.get("collections") or [] if isinstance(item, dict)}
            if not own & wanted_collections and product_id not in members:
                continue
        eligible.append(row)
    return eligible
