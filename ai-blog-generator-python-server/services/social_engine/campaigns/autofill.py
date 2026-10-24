"""Daily auto-fill: every channel always gets its N posts a day.

When the engine is on and `schedule.always_fill_daily_slots` is set, a store
must have a SmartQ campaign. If it has none (active or paused), this creates
"Daily auto-fill": SmartQ on all channels, with a daily mix that adds up to
`schedule.posts_per_channel_per_day`.

SmartQ then does the rest: it plans the open slots for today (while the posting
window is open) and the days ahead, replaces posts that fail, are rejected or
expire, and gives way to promotions and holidays, which have higher priority.

The merchant stays in charge: pausing the campaign is respected (the health
check warns), deleting it while the setting is on brings it back, and turning
the setting off stops it. The auto-fill campaign keeps its mix in step with
`posts_per_channel_per_day`.
"""
from __future__ import annotations

import logging

from db.social_engine import config_store

from ..activity import record
from ..settings import EngineSettings, load_settings

logger = logging.getLogger("ai_blog_server.social_engine.campaigns")

AUTOFILL_NAME = "Daily auto-fill"
# Round-robin order for the daily mix; SmartQ allows at most 3 per type.
_MIX_ORDER = ("single_product", "product_video", "collage", "carousel", "product_of_the_day", "new_arrival")
_MAX_PER_TYPE = 3


def autofill_mix(posts_per_day: int) -> dict[str, int]:
    """Spread the daily posts over content types: 2 → one product post + one product video."""
    mix: dict[str, int] = {}
    remaining = max(1, min(posts_per_day, _MAX_PER_TYPE * len(_MIX_ORDER)))
    while remaining:
        for content_type in _MIX_ORDER:
            if remaining and mix.get(content_type, 0) < _MAX_PER_TYPE:
                mix[content_type] = mix.get(content_type, 0) + 1
                remaining -= 1
    return mix


def is_autofill(campaign: dict) -> bool:
    return bool((campaign.get("content") or {}).get("auto_fill"))


def _approval_mode(settings: EngineSettings) -> str:
    # Daily posts must go out without waiting on the merchant: Gemma reviews each
    # one and anything it flags waits for approval. A store already on fully
    # automatic posting keeps that.
    return "auto" if settings.default_approval_mode == "auto" else "ai_review"


async def ensure_autofill_campaign(store_id: str) -> dict:
    """Create or update the Daily auto-fill campaign when needed. Returns what it did."""
    settings = await load_settings(store_id)
    if not settings.enabled or not settings.schedule.always_fill_daily_slots:
        return {"action": "off"}
    per_day = settings.schedule.posts_per_channel_per_day
    smartq = [c for c in await config_store.list_campaigns(store_id, statuses=("active", "paused"))
              if c["rule_type"] == "smartq"]

    for campaign in smartq:
        if not is_autofill(campaign):
            continue
        config = dict(campaign.get("config") or {})
        current = config.get("posts_per_day") or {}
        if sum(int(v) for v in current.values()) != per_day:
            config["posts_per_day"] = autofill_mix(per_day)
            await config_store.save_campaign(store_id, {**campaign, "config": config})
            await record(store_id, "campaign", campaign["id"],
                         f"{AUTOFILL_NAME}: now {per_day} post(s) per channel per day", stage="plan")
            return {"action": "updated", "campaign_id": campaign["id"]}
    if smartq:
        return {"action": "exists", "campaign_id": smartq[0]["id"]}

    campaign_id = await config_store.save_campaign(store_id, {
        "name": AUTOFILL_NAME,
        "rule_type": "smartq",
        "status": "active",
        "approval_mode": _approval_mode(settings),
        "channel_ids": [],                       # every enabled channel
        "config": {"order": "ai", "posts_per_day": autofill_mix(per_day)},
        "content": {"auto_fill": True},
    })
    await record(store_id, "campaign", campaign_id,
                 f"Created {AUTOFILL_NAME} so every channel gets {per_day} post(s) a day "
                 "(turn off 'Always fill daily slots' in Settings to stop it)", stage="plan")
    logger.info("Created %s campaign %s", AUTOFILL_NAME, campaign_id, extra={"store_id": store_id})
    return {"action": "created", "campaign_id": campaign_id}
