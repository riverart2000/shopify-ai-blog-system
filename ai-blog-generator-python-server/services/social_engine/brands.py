"""Brands: more than one business on the same Social Autopilot.

A brand is a store row with its own settings, channels, campaigns and posts. A brand that sells through Shopify
syncs its catalogue; one that doesn't (`catalog.source = "none"`: a consultancy, a performer) posts brand
content only. Everything about a brand is data: `apply_brand` reads a brand file (see `brands/` and
docs/social-engine/brands.md) and creates or updates the store row, its settings, its logo and its campaigns.
Applying the same file again changes nothing that is already right.
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

import db
from db.social_engine import config_store

from .errors import ValidationError
from .settings import load_settings, save_settings

logger = logging.getLogger("ai_blog_server.social_engine.brands")

BRANDS_DIR = Path(__file__).resolve().parents[2] / "brands"   # one <name>.json per brand


class BrandStore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    name: str = Field(min_length=2, max_length=80)
    domain: str = Field("", max_length=120)           # the brand's website host, e.g. revenuemindproai.com
    sort_order: int = Field(50, ge=1, le=1000)        # after the Shopify store, which stays the default


class BrandCampaign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    rule_type: str
    status: Literal["active", "paused"] = "active"
    approval_mode: Literal["manual", "ai_review", "auto"] = "ai_review"
    config: dict[str, Any] = Field(default_factory=dict)
    providers: list[str] = Field(default_factory=list)   # networks to post to; empty = every channel of the brand


class BrandFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    store: BrandStore
    logo_file: str = ""                                # PNG shown on every image; "~" allowed
    # The brand's channel group ("customer") in Postiz, by name. Required: without it the brand would see, and
    # could post to, every other brand's channels on the same Postiz account.
    postiz_group: str = Field(min_length=1, max_length=120)
    settings: dict[str, Any] = Field(default_factory=dict)
    campaigns: list[BrandCampaign] = Field(default_factory=list)


def read_brand_file(path: str | Path) -> BrandFile:
    file = Path(path).expanduser()
    if not file.is_file():
        raise ValidationError(f"Brand file {file} does not exist.")
    try:
        return BrandFile.model_validate(json.loads(file.read_text(encoding="utf-8")))
    except ValueError as exc:
        raise ValidationError(f"Brand file {file} is not valid: {exc}") from exc


async def _postiz_group_id(store_id: str, group: str) -> str:
    """The id of the Postiz channel group called `group` (matched without regard to case or spaces)."""
    from .context import load_store_context
    from .publishers import get_publisher

    def norm(name: str) -> str:
        return "".join(str(name or "").casefold().split())

    publisher = get_publisher(await load_store_context(store_id), correlation_id=f"brand:{store_id}")
    try:
        channels = await publisher.list_channels()
    finally:
        await publisher.aclose()
    groups = {norm(c.customer_name): c.customer_id for c in channels if c.customer_id}
    if norm(group) not in groups:
        names = sorted({c.customer_name for c in channels if c.customer_name})
        raise ValidationError(
            f"No channel in Postiz is in a group called '{group}' (groups with channels: {', '.join(names) or 'none'}). "
            "Connect the brand's channels in Postiz and move them into that group first."
        )
    return str(groups[norm(group)])


async def apply_brand(brand: BrandFile) -> dict[str, Any]:
    """Create or update the brand. Returns what was done, in plain words."""
    from .campaigns.engine import validate_campaign_config

    done: list[str] = []
    store_id = brand.store.id
    existing = await db.get_store(store_id)
    if existing and (existing.get("myshopify_domain") or existing.get("client_secret")):
        raise ValidationError(f"Store '{store_id}' is a Shopify store; a brand file can't take it over.")
    await db.upsert_store({
        "id": store_id, "name": brand.store.name, "myshopify_domain": "", "custom_domain": brand.store.domain,
        "client_id": "", "client_secret": "", "default_blog_handle": "news", "default_author": brand.store.name,
        "sort_order": brand.store.sort_order,
    })
    done.append(f"{'Updated' if existing else 'Created'} brand '{brand.store.name}' ({store_id})")

    if brand.logo_file:
        logo = Path(brand.logo_file).expanduser()
        if not logo.is_file():
            raise ValidationError(f"Logo file {logo} does not exist.")
        encoded = base64.b64encode(logo.read_bytes()).decode("ascii")
        if await db.get_store_setting(store_id, "logo_data", "") != encoded:
            await db.set_store_settings(store_id, {"logo_data": encoded})
            done.append(f"Logo set from {logo.name}")

    before = (await load_settings(store_id)).model_dump()
    # Saved switched off first: the brand must not run until its own Postiz group is pinned.
    await save_settings(store_id, {**brand.settings, "enabled": False})
    customer_id = await _postiz_group_id(store_id, brand.postiz_group)
    settings = await save_settings(store_id, {
        "postiz": {"customer_id": customer_id}, "enabled": bool(brand.settings.get("enabled", False)),
    })
    if settings.model_dump() != before:
        done.append(f"Settings saved (Postiz group '{brand.postiz_group}')")

    from .pipeline.channels import sync_channels

    synced = await sync_channels(store_id)
    if synced.get("new"):
        done.append("Channels found: " + ", ".join(synced["new"]))

    channels = await config_store.list_channels(store_id)
    by_name = {c["name"]: c for c in await config_store.list_campaigns(store_id)}
    for item in brand.campaigns:
        config = validate_campaign_config(item.rule_type, item.config)
        wanted = {p.lower() for p in item.providers}
        channel_ids = [str(c["channel_id"]) for c in channels if c["provider"].lower() in wanted] if wanted else []
        if wanted and not channel_ids:
            done.append(f"Campaign '{item.name}': none of {sorted(wanted)} is connected yet, so it posts to every channel")
        current = by_name.get(item.name)
        record = {
            "id": (current or {}).get("id", ""), "name": item.name, "rule_type": item.rule_type,
            "status": item.status, "config": config, "product_filter": {}, "channel_ids": channel_ids,
            "caption": (current or {}).get("caption") or {}, "content": (current or {}).get("content") or {},
            "approval_mode": item.approval_mode,
        }
        if current and all(current.get(key) == record[key] for key in
                           ("rule_type", "status", "config", "channel_ids", "approval_mode")):
            continue
        await config_store.save_campaign(store_id, record)
        done.append(f"{'Updated' if current else 'Created'} campaign '{item.name}'")
    logger.info("Brand %s applied: %s", store_id, "; ".join(done), extra={"store_id": store_id})
    return {"store_id": store_id, "done": done}
