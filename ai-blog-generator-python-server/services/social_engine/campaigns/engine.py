"""Campaign engine: evaluate rules → planned posts → queued generation.

`evaluate_campaign(..., dry_run=True)` returns what a campaign *would* plan
without saving anything — use it from the UI or CLI to debug a rule.
"""
from __future__ import annotations

import copy
import logging
import random
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from db.social_engine import config_store, observability
from db.social_engine import posts as post_store

from ..activity import record
from ..context import StoreContext, load_store_context
from ..errors import ConfigurationError, NotFoundError, ValidationError
from ..jobs.queue import enqueue
from ..scheduling.slots import SlotPlanner
from .base import PlanItem, ProductFilter, RuleContext, ScheduleConfig, get_rule

logger = logging.getLogger("ai_blog_server.social_engine.campaigns")

_CAPACITY_STATUSES = ("pending", "publishing", "uncertain", "draft", "scheduled", "published")


def _state_key(campaign_id: str) -> str:
    return f"campaign_state:{campaign_id}"


def validate_campaign_config(rule_type: str, config: dict) -> dict:
    """Validate a campaign's rule config and return it with defaults filled in."""
    rule = get_rule(rule_type)
    if rule is None:
        raise ValidationError(f"Unknown campaign type '{rule_type}'.")
    try:
        return rule.config_model.model_validate(config or {}).model_dump(mode="json")
    except PydanticValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in err['loc']) or 'config'}: {err['msg']}" for err in exc.errors())
        raise ValidationError(f"Invalid {rule.label} settings — {problems}") from exc


async def campaign_channels(sctx: StoreContext, campaign: dict) -> list[dict]:
    usable = await config_store.list_channels(sctx.store_id, usable_only=True)
    wanted = [str(c) for c in campaign.get("channel_ids") or []]
    if wanted:
        usable = [c for c in usable if str(c["channel_id"]) in wanted]
    return usable


async def build_rule_context(
    sctx: StoreContext, campaign: dict, *, now: datetime | None = None, seed: int | None = None,
) -> RuleContext:
    rule = get_rule(campaign["rule_type"])
    if rule is None:
        raise ValidationError(f"Unknown campaign type '{campaign['rule_type']}'.")
    config = rule.config_model.model_validate(campaign.get("config") or {})
    channels = await campaign_channels(sctx, campaign)
    if not channels:
        raise ConfigurationError(
            "No usable channels for this campaign. Connect channels in Postiz, run 'Sync channels', "
            "and make sure the selected channels are enabled."
        )
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ts = int(now.timestamp())
    existing = await post_store.list_targets(
        sctx.store_id, statuses=_CAPACITY_STATUSES, scheduled_from=ts - 2 * 86400, scheduled_to=ts + 23 * 86400,
    )
    existing += await post_store.planned_channel_times(
        sctx.store_id, scheduled_from=ts - 2 * 86400, scheduled_to=ts + 23 * 86400,
    )
    planner = SlotPlanner(sctx.settings.schedule, channels, existing, now=now,
                          rng=random.Random(seed if seed is not None else ts))
    schedule = ScheduleConfig.model_validate((campaign.get("config") or {}).get("schedule") or {})
    state = await observability.get_state(_state_key(str(campaign["id"])), {}) or {}
    return RuleContext(
        sctx=sctx, campaign=campaign, config=config,
        product_filter=ProductFilter.model_validate(campaign.get("product_filter") or {}),
        schedule=schedule, channels=channels, planner=planner, now=now,
        rng=random.Random(seed if seed is not None else ts), state=state,
    )


async def _persist(rctx: RuleContext, items: list[PlanItem]) -> list[str]:
    created_ids: list[str] = []
    approval = rctx.campaign.get("approval_mode") or rctx.sctx.settings.default_approval_mode
    caption_overrides = rctx.campaign.get("caption") or {}
    for item in items:
        post_id, created = await post_store.create_post(rctx.store_id, {
            "campaign_id": rctx.campaign_id,
            "rule_type": rctx.campaign["rule_type"],
            "content_type": item.content_type,
            "template_key": item.template_key,
            "product_ids": item.product_ids,
            "title": item.title[:200],
            "angle": item.angle,
            "brief": item.brief,
            "status": "planned",
            "approval_mode": approval,
            "scheduled_at": item.scheduled_at,
            "dedupe_key": item.dedupe_key,
            "options": {
                **item.options,
                "channel_ids": item.channel_ids,
                "channel_times": item.channel_times,
                "texts": item.texts,
                "caption_overrides": caption_overrides,
            },
        })
        if not created:
            continue
        created_ids.append(post_id)
        await enqueue("post.generate", {"post_id": post_id}, store_id=rctx.store_id,
                      dedupe_key=f"generate:{post_id}", correlation_id=post_id)
        await record(rctx.store_id, "post", post_id,
                     f"Planned by campaign '{rctx.campaign.get('name')}' for "
                     f"{len(item.channel_ids)} channel(s)", stage="plan",
                     data={"dedupe_key": item.dedupe_key, "content_type": item.content_type,
                           "decided_by": item.options.get("decided_by", "")})
    return created_ids


async def evaluate_campaign(
    store_id: str,
    campaign: dict | str,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    seed: int | None = None,
) -> dict[str, Any]:
    """Run one campaign's rule. Returns a summary (and the items when dry_run)."""
    started = time.monotonic()
    if isinstance(campaign, str):
        found = await config_store.get_campaign(store_id, campaign)
        if not found:
            raise NotFoundError(f"Campaign '{campaign}' was not found.")
        campaign = found
    sctx = await load_store_context(store_id, correlation_id=str(campaign["id"]))
    rule = get_rule(campaign["rule_type"])
    if rule is None:
        raise ValidationError(f"Unknown campaign type '{campaign['rule_type']}'.")

    try:
        rctx = await build_rule_context(sctx, campaign, now=now, seed=seed)
    except (ConfigurationError, PydanticValidationError, ValidationError) as exc:
        message = str(exc)
        if not dry_run:
            await config_store.mark_campaign_evaluated(
                str(campaign["id"]), next_at=int(time.time()) + rule.default_interval_seconds, error=message,
            )
            await record(store_id, "campaign", str(campaign["id"]), message, level="warning", stage="evaluate")
        return {"campaign_id": campaign["id"], "planned": 0, "error": message}

    rctx.dry_run = dry_run
    original_state = copy.deepcopy(rctx.state)
    items = await rule.plan(rctx)

    if dry_run:
        return {
            "campaign_id": campaign["id"],
            "planned": len(items),
            "items": [asdict(item) | {"scheduled_at": item.scheduled_at} for item in items],
            "note": rctx.state.get("last_note", ""),
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    created = await _persist(rctx, items)
    note = rctx.state.pop("last_note", "")
    if rctx.state != original_state:
        await observability.set_state(_state_key(rctx.campaign_id), rctx.state)
    next_at = rule.next_evaluation(rctx)
    await config_store.mark_campaign_evaluated(rctx.campaign_id, next_at=next_at, error="")
    if created or note:
        await record(
            store_id, "campaign", rctx.campaign_id,
            f"Planned {len(created)} new post(s)" + (f" — {note}" if note else ""),
            stage="evaluate", data={"post_ids": created},
        )
    return {
        "campaign_id": rctx.campaign_id,
        "planned": len(created),
        "post_ids": created,
        "note": note,
        "next_evaluation_at": next_at,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


async def evaluate_due_campaigns(store_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    timestamp = int((now or datetime.now(timezone.utc)).timestamp())
    due = [c for c in await config_store.due_campaigns(timestamp) if c["store_id"] == store_id]
    # Promotions/occasions/events first; SmartQ fills whatever capacity is left.
    due.sort(key=lambda c: -getattr(get_rule(c["rule_type"]), "priority", 0))
    results = []
    for campaign in due:
        try:
            results.append(await evaluate_campaign(store_id, campaign, now=now))
        except Exception as exc:  # one broken campaign must not block the others
            message = f"{type(exc).__name__}: {exc}"
            logger.exception("Campaign %s evaluation failed", campaign["id"],
                             extra={"store_id": store_id, "correlation_id": campaign["id"]})
            await config_store.mark_campaign_evaluated(
                campaign["id"], next_at=timestamp + 900, error=message,
            )
            results.append({"campaign_id": campaign["id"], "planned": 0, "error": message})
    return {"evaluated": len(results), "planned": sum(r.get("planned", 0) for r in results), "results": results}
