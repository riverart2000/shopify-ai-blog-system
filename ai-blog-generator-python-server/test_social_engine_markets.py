"""Promotions for the UK, the US or both: posting hours, prices and caption language per market."""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from services.social_engine.campaigns.base import ScheduleConfig
from services.social_engine.campaigns.rules.promotion import PromotionConfig
from services.social_engine.context import StoreContext
from services.social_engine.markets import apply_market, market_for, schedule_for
from services.social_engine.scheduling.slots import SlotPlanner
from services.social_engine.settings import EngineSettings, ScheduleSettings

NY = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")


def sctx() -> StoreContext:
    return StoreContext(store_id="store_1", store={}, store_config=None, settings=EngineSettings())


PRODUCTS = [
    {"product_id": "111", "gid": "gid://shopify/Product/111", "title": "Foam Roller", "price_min": 26.19,
     "price_max": 26.19, "compare_at_max": None, "on_sale": False},
    {"product_id": "222", "title": "Loop Bands", "price_min": 13.47, "price_max": 13.47, "compare_at_max": None},
]


async def fake_shopify(store, query, variables):
    assert variables["ids"] == ["gid://shopify/Product/111", "gid://shopify/Product/222"]
    return {"nodes": [{"id": "gid://shopify/Product/111", "contextualPricing": {
        "minVariantPricing": {"price": {"amount": "36.0", "currencyCode": "USD"}, "compareAtPrice": {"amount": "0.0"}},
        "maxVariantPricing": {"price": {"amount": "36.0", "currencyCode": "USD"}, "compareAtPrice": {"amount": "0.0"}},
    }}]}  # no US price for the second product


def test_markets_from_countries():
    assert market_for(("US",)) == "US" and market_for(["GB"]) == "GB" and market_for(("GB", "US")) == "both"
    assert PromotionConfig(start_date=date(2026, 11, 1)).market == "GB"  # existing promotions behave as before


@pytest.mark.asyncio
async def test_us_posts_show_shopifys_dollar_prices_and_american_english():
    ctx, shown, texts, warnings = await apply_market(sctx(), PRODUCTS, {"promo_text": "Sale"}, "US",
                                                     graphql=fake_shopify)
    assert shown[0]["price_min"] == 36.0 and shown[0]["currency"] == "USD"
    assert shown[1]["price_min"] is None  # no US price: none shown rather than a £ one
    assert PRODUCTS[0]["price_min"] == 26.19  # the post's snapshot keeps the store's own prices
    assert ctx.settings.brand.currency_symbol == "$" and ctx.settings.captions.language == "en-US"
    assert "United States" in texts["audience"] and texts["promo_text"] == "Sale" and not warnings


@pytest.mark.asyncio
async def test_us_posts_without_any_us_price_show_no_prices():
    async def broken(store, query, variables):
        raise RuntimeError("Shopify is down")

    ctx, shown, _, warnings = await apply_market(sctx(), PRODUCTS, {}, "US", graphql=broken)
    assert not ctx.settings.brand.show_price and not ctx.settings.captions.include_price
    assert all(p["price_min"] is None for p in shown) and warnings


@pytest.mark.asyncio
async def test_both_countries_show_no_prices_and_uk_is_unchanged():
    ctx, shown, texts, _ = await apply_market(sctx(), PRODUCTS, {}, "both")
    assert not ctx.settings.brand.show_price and not ctx.settings.captions.include_price and shown == PRODUCTS
    assert "UK and the US" in texts["audience"]
    original = sctx()
    same, shown, texts, _ = await apply_market(original, PRODUCTS, {"a": "b"}, "GB")
    assert same is original and texts == {"a": "b"}


def test_us_promotions_post_in_new_york_daytime():
    schedule = schedule_for("US", ScheduleConfig())
    assert schedule.timezone == "America/New_York" and (schedule.window_start, schedule.window_end) == ("09:00", "21:00")
    assert schedule_for("GB", ScheduleConfig()) == ScheduleConfig()
    planner = SlotPlanner(ScheduleSettings(use_best_times=False, posts_per_channel_per_day=4),
                          [{"channel_id": "fb", "provider": "facebook"}], [],
                          now=datetime(2026, 11, 25, 6, 0, tzinfo=timezone.utc))
    for _ in range(3):
        when = planner.next_slot(["fb"], day=date(2026, 11, 26), window=schedule.window())
        assert when is not None
        local = when.astimezone(NY)
        assert local.date() == date(2026, 11, 26) and 9 <= local.hour < 21
        planner.reserve(["fb"], when)
    both = schedule_for("both", ScheduleConfig())
    planner = SlotPlanner(ScheduleSettings(use_best_times=False), [{"channel_id": "fb", "provider": "facebook"}], [],
                          now=datetime(2026, 11, 25, 6, 0, tzinfo=timezone.utc))
    when = planner.next_slot(["fb"], day=date(2026, 11, 27), window=both.window())
    assert 12 <= when.astimezone(LONDON).hour < 22


def test_a_promotion_can_set_its_own_hours_and_zone():
    with pytest.raises(ValueError):
        ScheduleConfig(timezone="Mars/Olympus")
    own = schedule_for("US", ScheduleConfig(window_start="07:00"))
    assert own.window_start == "07:00" and own.window_end == "21:00"
