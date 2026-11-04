"""Automatic occasion promotions (Promotions tab).

Checked hourly; acts from `auto_promotions.run_hour` (store time). For each shopping occasion in the
calendar whose promotion window includes today (it opens the day before the occasion), it:

1. creates a real Shopify discount code (e.g. BLACKFRIDAY20: 20% off everything, once per customer,
   no combining, no minimum), valid from the day before the occasion to the day after it ends;
2. has Gemma write the promo lines (falls back to the theme's own lines);
3. saves and starts a promotion for that window, posting to every connected channel.

Each occasion gets its own promotion and code; overlapping promotions are fine (a UK and a US one can run
on the same day). Each promotion has the market of its occasion: UK-only occasions post on UK time in £,
US-only ones on New York time in $, shared ones at hours that suit both, without prices. Codes work for
every customer anywhere. An occasion is skipped if a promotion for it already covers the window (one set
up by hand, or this one, even if it was later removed). If the code can't be created, no promotion is
made, so a post never advertises a code that doesn't work. Wellness days get a greeting and a sale.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from db.social_engine import config_store, observability
from db.social_engine import products as product_store

from ..activity import record
from ..discounts import MARKER, DiscountError, GraphQL, code_for, ensure_code
from ..jobs.queue import enqueue
from ..markets import get_market, market_for
from ..settings import AutoPromotionSettings, EngineSettings, load_settings
from .holidays import OCCASIONS, occasion_date
from .themes import THEMES, PromotionTheme

logger = logging.getLogger("ai_blog_server.social_engine.auto_promotions")

AUTO_KEY = "auto_occasion"
OCCASION_DAYS: dict[str, int] = {"mental_health_week": 7, "hanukkah": 8}  # everything else is a single day


def theme_code(theme: PromotionTheme, percent: int) -> str:
    """The theme's short code if it has one (MOMSDAY20, SMALLBIZ20), else from its label (THANKSGIVING20)."""
    base = str(theme.extra.get("code") or "")
    return f"{base}{percent}" if base else code_for(theme.label, percent)


def windows(today: date, ap: AutoPromotionSettings, *, horizon_days: int = 0) -> list[dict[str, Any]]:
    """Occasion windows that are open today, plus (with horizon_days) the ones opening soon."""
    rows: list[dict[str, Any]] = []
    for theme in THEMES.values():
        if theme.category != "occasion" or not theme.occasion_key or theme.occasion_key not in OCCASIONS:
            continue
        if theme.key in ap.skip_themes:
            continue
        for year in (today.year - 1, today.year, today.year + 1):
            when = occasion_date(theme.occasion_key, year)
            if when is None:
                continue
            last_day = when + timedelta(days=OCCASION_DAYS.get(theme.key, 1) - 1)
            start, end = when - timedelta(days=ap.days_before), last_day + timedelta(days=ap.days_after)
            if end < today or start > today + timedelta(days=horizon_days):
                continue
            rows.append({
                "key": f"{theme.key}:{when.isoformat()}", "theme": theme, "occasion_date": when,
                "start": start, "end": end, "code": theme_code(theme, ap.percent),
                "market": market_for(OCCASIONS[theme.occasion_key].countries),
            })
    return sorted(rows, key=lambda r: (r["start"], r["theme"].label))


def _covered(window: dict[str, Any], campaigns: list[dict]) -> dict | None:
    """The promotion already covering this occasion window, if any (auto or hand-made, any status)."""
    for campaign in campaigns:
        if ((campaign.get("content") or {}).get(AUTO_KEY) or {}).get("key") == window["key"]:
            return campaign
        if campaign.get("rule_type") != "promotion":
            continue
        config = campaign.get("config") or {}
        if config.get("theme_key") != window["theme"].key:
            continue
        try:
            start = date.fromisoformat(str(config.get("start_date")))
            end = start + timedelta(days=int(config.get("duration_days", 1)) - 1)
        except (TypeError, ValueError):
            continue
        if start <= window["end"] and window["start"] <= end:
            return campaign
    return None


async def _promo_texts(store_id: str, theme: PromotionTheme, discount_text: str, code: str) -> tuple[list[str], str]:
    from ..ai.decisions import generate_promo_texts
    from ..context import load_store_context

    try:
        products = await product_store.list_products(store_id, available_only=True, order="newest", limit=8)
        texts = await generate_promo_texts(
            await load_store_context(store_id), theme=theme.label, products=products,
            discount_text=discount_text, coupon_code=code, count=6,
        )
        if texts:
            return texts, "gemma"
    except Exception as exc:  # the theme's own lines are a fine fallback
        logger.info("Promo lines fell back to the theme for %s: %s", theme.key, exc)
    return list(theme.promo_texts), "theme"


async def _create(
    store_id: str, settings: EngineSettings, window: dict[str, Any], today: date, tz: ZoneInfo,
    graphql: GraphQL | None,
) -> dict[str, Any]:
    from ..context import load_store_context
    from .base import model_defaults
    from .engine import validate_campaign_config
    from .rules.promotion import PromotionConfig

    ap = settings.auto_promotions
    theme: PromotionTheme = window["theme"]
    year = window["occasion_date"].year
    market = get_market(window["market"])
    sctx = await load_store_context(store_id)
    discount = await ensure_code(
        sctx.store_config,
        code=window["code"],
        title=f"{theme.label} {year}: {ap.percent}% off everything {MARKER}",
        starts_at=datetime.combine(window["start"], time.min, tz),
        ends_at=datetime.combine(window["end"], time(23, 59, 59), tz),
        percent=ap.percent,
        graphql=graphql,
    )
    discount_text = f"{ap.percent}% off everything"
    texts, texts_source = await _promo_texts(store_id, theme, discount_text, discount.code)
    first_day = max(window["start"], today)
    config = validate_campaign_config("promotion", {
        **model_defaults(PromotionConfig),
        "theme_key": theme.key, "scope": "store", "coupon_code": discount.code, "discount_text": discount_text,
        "promo_texts": texts, "start_date": first_day.isoformat(),
        "duration_days": (window["end"] - first_day).days + 1, "countdown_days": 1, "market": market.key,
    })
    name = f"{theme.label} {year}"
    if market.key in ("GB", "US") and f"({market.label})" not in theme.label:
        name += f" ({market.label})"
    campaign_id = await config_store.save_campaign(store_id, {
        "name": name, "rule_type": "promotion", "status": "active", "config": config,
        "channel_ids": [], "approval_mode": settings.default_approval_mode, "product_filter": {},
        "content": {AUTO_KEY: {
            "key": window["key"], "code": discount.code, "discount_id": discount.discount_id,
            "percent": ap.percent, "start": window["start"].isoformat(), "end": window["end"].isoformat(),
            "texts_source": texts_source, "market": market.key,
        }},
    })
    await enqueue("campaigns.evaluate", {"campaign_id": campaign_id}, store_id=store_id,
                  dedupe_key=f"campaign:{campaign_id}", correlation_id=campaign_id, priority=8)
    await record(
        store_id, "campaign", campaign_id,
        f"Auto promotion '{name}' started: code {discount.code} ({discount_text}, once per customer) valid "
        f"{window['start']:%d %b} – {window['end']:%d %b}" + (" (code reused from last time)"
                                                              if discount.action == "updated" else ""),
        stage="promotion",
    )
    return {"key": window["key"], "action": "created", "campaign_id": campaign_id, "code": discount.code,
            "discount": discount.action}


async def ensure_occasion_promotions(
    store_id: str, *, now: datetime | None = None, force: bool = False, graphql: GraphQL | None = None,
) -> dict[str, Any]:
    """Create today's due occasion promotions. Safe to call repeatedly (one per occasion window)."""
    settings = await load_settings(store_id)
    ap = settings.auto_promotions
    if not settings.enabled or not ap.enabled:
        return {"action": "off"}
    tz = ZoneInfo(settings.schedule.timezone)
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    if local.hour < ap.run_hour and not force:
        return {"action": "waiting", "until": f"{ap.run_hour:02d}:00"}
    today = local.date()
    campaigns = await config_store.list_campaigns(store_id)
    results: list[dict[str, Any]] = []
    for window in windows(today, ap):
        existing = _covered(window, campaigns)
        if existing:
            results.append({"key": window["key"], "action": "exists", "campaign_id": existing["id"]})
            continue
        try:
            results.append(await _create(store_id, settings, window, today, tz, graphql))
        except Exception as exc:
            message = str(exc) if isinstance(exc, DiscountError) else f"{type(exc).__name__}: {exc}"
            results.append({"key": window["key"], "action": "failed", "error": message[:300]})
            state_key = f"auto_promotion_error:{store_id}:{window['key']}"
            if await observability.get_state(state_key, "") != today.isoformat():  # once a day, not hourly
                await observability.set_state(state_key, today.isoformat())
                await record(store_id, "campaign", "", f"Auto promotion for {window['theme'].label} not created "
                             f"(no code, so nothing was posted): {message[:240]}", level="error", stage="promotion")
            logger.warning("Auto promotion %s failed: %s", window["key"], message)
    await observability.set_state(f"auto_promotions:last_check:{store_id}", int(local.timestamp()))
    return {"action": "checked", "today": today.isoformat(), "results": results}


async def overview(store_id: str, settings: EngineSettings, today: date) -> dict[str, Any]:
    """What the Promotions tab shows: the rules, and the next occasions with their status."""
    ap = settings.auto_promotions
    campaigns = await config_store.list_campaigns(store_id)
    upcoming = []
    for window in windows(today, ap, horizon_days=120)[:6]:
        existing = _covered(window, campaigns)
        auto = ((existing or {}).get("content") or {}).get(AUTO_KEY) or {}
        upcoming.append({
            "theme_key": window["theme"].key, "label": window["theme"].label, "emoji": window["theme"].emoji,
            "occasion_date": window["occasion_date"].isoformat(), "start": window["start"].isoformat(),
            "end": window["end"].isoformat(), "code": auto.get("code") or window["code"],
            "market": window["market"], "market_label": get_market(window["market"]).label,
            "status": ("created" if auto else "manual") if existing else "planned",
            "campaign_id": existing["id"] if existing else "",
        })
    skipped = [THEMES[key].label for key in ap.skip_themes if key in THEMES]
    last = await observability.get_state(f"auto_promotions:last_check:{store_id}", 0)
    return {**ap.model_dump(), "skipped_labels": skipped, "upcoming": upcoming, "last_check_at": last or None}
