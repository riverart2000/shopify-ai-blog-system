"""Automatic occasion promotions: a real Shopify code + a running promotion per shopping occasion."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
import pytest_asyncio

from db.social_engine import config_store, observability
from services.social_engine.campaigns import auto_promotions
from services.social_engine.campaigns.auto_promotions import AUTO_KEY, ensure_occasion_promotions, windows
from services.social_engine.discounts import CREATE, FIND, MARKER, UPDATE, code_for
from services.social_engine.settings import AutoPromotionSettings, save_settings

# 10 Nov 2026 08:00 London (GMT): Singles' Day is tomorrow, so its promotion opens today.
SINGLES_EVE = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)


class FakeShopify:
    """Answers the three discount calls; `taken` maps code → existing discount title."""

    def __init__(self, taken: dict[str, str] | None = None, fail: str = "") -> None:
        self.taken = dict(taken or {})
        self.fail = fail
        self.created: list[dict] = []
        self.updated: list[dict] = []

    async def __call__(self, store, query: str, variables: dict) -> dict:
        if query == FIND:
            title = self.taken.get(variables["code"])
            return {"codeDiscountNodeByCode": None if title is None else
                    {"id": f"gid://shopify/DiscountCodeNode/{variables['code']}", "codeDiscount": {"title": title}}}
        if query == CREATE:
            if self.fail:
                return {"discountCodeBasicCreate": {"codeDiscountNode": None, "userErrors": [{"message": self.fail}]}}
            self.created.append(variables["discount"])
            return {"discountCodeBasicCreate": {"codeDiscountNode": {"id": "gid://shopify/DiscountCodeNode/1"},
                                                "userErrors": []}}
        if query == UPDATE:
            self.updated.append(variables)
            return {"discountCodeBasicUpdate": {"codeDiscountNode": {"id": variables["id"]}, "userErrors": []}}
        raise AssertionError("unexpected query")


@pytest_asyncio.fixture
async def store(se_db, monkeypatch):
    await save_settings(se_db, {"enabled": True, "publisher": "dry_run", "default_approval_mode": "ai_review"})

    async def lines(sctx, *, theme, products, discount_text, coupon_code, count):
        return [f"{theme}: {discount_text}", f"Use code {coupon_code}"]

    monkeypatch.setattr("services.social_engine.ai.decisions.generate_promo_texts", lines)
    return se_db


def test_code_names():
    assert code_for("Black Friday", 20) == "BLACKFRIDAY20"
    assert code_for("Mother's Day (UK)", 20) == "MOTHERSDAY20"
    assert code_for("Singles' Day", 20) == "SINGLESDAY20"


def test_windows_span_day_before_to_day_after_and_wellness_days_get_sales():
    ap = AutoPromotionSettings()
    singles = [w for w in windows(date(2026, 11, 10), ap) if w["theme"].key == "singles_day"]
    assert singles and (singles[0]["start"], singles[0]["end"]) == (date(2026, 11, 10), date(2026, 11, 12))
    blue = [w for w in windows(date(2027, 1, 18), ap) if w["theme"].key == "blue_monday"]
    assert blue and blue[0]["code"] == "BLUEMONDAY20"  # wellness days get a sale too now
    skipping = AutoPromotionSettings(skip_themes=["world_mental_health_day"])
    assert not [w for w in windows(date(2027, 10, 10), skipping) if w["theme"].key == "world_mental_health_day"]


def test_each_occasion_runs_in_its_own_country():
    ap = AutoPromotionSettings()
    late_nov = {w["theme"].key: w for w in windows(date(2026, 11, 26), ap)}
    assert late_nov["thanksgiving"]["market"] == "US" and late_nov["thanksgiving"]["code"] == "THANKSGIVING20"
    assert late_nov["black_friday"]["market"] == "both"  # shared days: one promotion for both countries
    boxing = {w["theme"].key: w for w in windows(date(2026, 12, 26), ap)}
    assert boxing["boxing_day"]["market"] == "GB"
    may = {w["theme"].key: w for w in windows(date(2027, 5, 31), ap)}
    assert may["memorial_day"]["market"] == "US" and may["bank_holiday_spring"]["market"] == "GB"  # same day
    assert may["bank_holiday_spring"]["code"] == "BANKHOLIDAY20"
    moms = {w["theme"].key: w for w in windows(date(2027, 5, 9), ap)}
    assert moms["mothers_day_us"]["code"] == "MOMSDAY20"  # not the UK MOTHERSDAY20
    diwali = {w["theme"].key: w for w in windows(date(2027, 10, 29), ap)}
    assert diwali["diwali"]["market"] == "both" and diwali["diwali"]["occasion_date"] == date(2027, 10, 29)


@pytest.mark.asyncio
async def test_creates_code_and_starts_promotion_once(store):
    shopify = FakeShopify()
    result = await ensure_occasion_promotions(store, now=SINGLES_EVE, graphql=shopify)
    created = [r for r in result["results"] if r["action"] == "created"]
    assert [r["code"] for r in created] == ["SINGLESDAY20"]

    discount = shopify.created[0]
    assert discount["code"] == "SINGLESDAY20" and discount["title"].endswith(MARKER)
    assert discount["customerGets"] == {"value": {"percentage": 0.2}, "items": {"all": True}}
    assert discount["appliesOncePerCustomer"] is True and discount["context"] == {"all": "ALL"}
    assert discount["combinesWith"] == {"orderDiscounts": False, "productDiscounts": False, "shippingDiscounts": False}
    assert "minimumRequirement" not in discount
    assert discount["startsAt"] == "2026-11-10T00:00:00+00:00" and discount["endsAt"] == "2026-11-12T23:59:59+00:00"

    campaign = await config_store.get_campaign(store, created[0]["campaign_id"])
    assert campaign["status"] == "active" and campaign["rule_type"] == "promotion"
    assert campaign["approval_mode"] == "ai_review" and campaign["channel_ids"] == []
    config = campaign["config"]
    assert config["coupon_code"] == "SINGLESDAY20" and config["discount_text"] == "20% off everything"
    assert (config["start_date"], config["duration_days"]) == ("2026-11-10", 3)
    assert config["promo_texts"] == ["Singles' Day: 20% off everything", "Use code SINGLESDAY20"]
    assert campaign["content"][AUTO_KEY]["key"] == "singles_day:2026-11-11"

    again = await ensure_occasion_promotions(store, now=SINGLES_EVE, graphql=shopify)
    assert [r["action"] for r in again["results"]] == ["exists"] and len(shopify.created) == 1


@pytest.mark.asyncio
async def test_a_us_occasion_makes_a_us_promotion_with_an_open_code(store):
    shopify = FakeShopify()
    eve = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)  # Thanksgiving is tomorrow
    result = await ensure_occasion_promotions(store, now=eve, graphql=shopify)
    created = {r["code"]: r for r in result["results"] if r["action"] == "created"}
    assert "THANKSGIVING20" in created
    campaign = await config_store.get_campaign(store, created["THANKSGIVING20"]["campaign_id"])
    assert campaign["name"] == "Thanksgiving 2026 (US)" and campaign["config"]["market"] == "US"
    assert campaign["content"][AUTO_KEY]["market"] == "US"
    discount = next(d for d in shopify.created if d["code"] == "THANKSGIVING20")
    assert discount["context"] == {"all": "ALL"}  # anyone, anywhere can use it


@pytest.mark.asyncio
async def test_waits_for_the_morning_check_unless_forced(store):
    early = datetime(2026, 11, 10, 5, 0, tzinfo=timezone.utc)
    assert (await ensure_occasion_promotions(store, now=early, graphql=FakeShopify()))["action"] == "waiting"
    forced = await ensure_occasion_promotions(store, now=early, force=True, graphql=FakeShopify())
    assert forced["action"] == "checked"


@pytest.mark.asyncio
async def test_a_hand_made_promotion_is_left_alone(store):
    await config_store.save_campaign(store, {
        "name": "My Singles' Day", "rule_type": "promotion", "status": "active",
        "config": {"theme_key": "singles_day", "start_date": "2026-11-09", "duration_days": 4},
    })
    shopify = FakeShopify()
    result = await ensure_occasion_promotions(store, now=SINGLES_EVE, graphql=shopify)
    assert [r["action"] for r in result["results"]] == ["exists"] and not shopify.created


@pytest.mark.asyncio
async def test_codes_made_by_hand_are_never_touched_but_our_own_are_reused(store):
    hand = FakeShopify(taken={"SINGLESDAY20": "Joe's own code"})
    result = await ensure_occasion_promotions(store, now=SINGLES_EVE, graphql=hand)
    assert result["results"][0]["code"] == "SINGLESDAY20-2026" and not hand.updated

    await config_store.delete_campaign(store, result["results"][0]["campaign_id"])
    ours = FakeShopify(taken={"SINGLESDAY20": f"Singles' Day 2025: 20% off everything {MARKER}"})
    reused = await ensure_occasion_promotions(store, now=SINGLES_EVE, graphql=ours)
    assert reused["results"][0]["code"] == "SINGLESDAY20" and len(ours.updated) == 1 and not ours.created


@pytest.mark.asyncio
async def test_no_promotion_without_a_working_code(store):
    shopify = FakeShopify(fail="Access denied for discountCodeBasicCreate")
    result = await ensure_occasion_promotions(store, now=SINGLES_EVE, graphql=shopify)
    assert result["results"][0]["action"] == "failed" and "Access denied" in result["results"][0]["error"]
    assert not [c for c in await config_store.list_campaigns(store) if c["rule_type"] == "promotion"]
    await ensure_occasion_promotions(store, now=SINGLES_EVE, graphql=shopify)  # an hour later
    state = await observability.get_state(f"auto_promotion_error:{store}:singles_day:2026-11-11", "")
    assert state == "2026-11-10"  # the error is logged once that day, not every hour


@pytest.mark.asyncio
async def test_a_removed_auto_promotion_is_not_recreated(store):
    first = await ensure_occasion_promotions(store, now=SINGLES_EVE, graphql=FakeShopify())
    campaign = await config_store.get_campaign(store, first["results"][0]["campaign_id"])
    await config_store.save_campaign(store, {**campaign, "status": "archived"})
    shopify = FakeShopify()
    again = await ensure_occasion_promotions(store, now=SINGLES_EVE, graphql=shopify)
    assert [r["action"] for r in again["results"]] == ["exists"] and not shopify.created


@pytest.mark.asyncio
async def test_switched_off(store):
    await save_settings(store, {"auto_promotions": {"enabled": False}})
    assert (await ensure_occasion_promotions(store, now=SINGLES_EVE, graphql=FakeShopify()))["action"] == "off"


@pytest.mark.asyncio
async def test_page_overview_lists_upcoming_occasions(store):
    from services.social_engine.settings import load_settings

    await ensure_occasion_promotions(store, now=SINGLES_EVE, graphql=FakeShopify())
    view = await auto_promotions.overview(store, await load_settings(store), date(2026, 11, 10))
    first = view["upcoming"][0]
    assert first["label"] == "Singles' Day" and first["status"] == "created" and first["code"] == "SINGLESDAY20"
    assert view["upcoming"][1]["label"] == "Thanksgiving" and view["upcoming"][1]["market"] == "US"
    black_friday = next(u for u in view["upcoming"] if u["label"] == "Black Friday")
    assert black_friday["status"] == "planned" and black_friday["market"] == "both"
    assert view["skipped_labels"] == [] and view["percent"] == 20  # wellness days get sales now
