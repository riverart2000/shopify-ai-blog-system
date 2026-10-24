"""End-to-end: catalogue → campaign → generate (real rendering) → approve →
publish (dry-run publisher) with the real worker, jobs and database."""
from __future__ import annotations

import io
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from PIL import Image

from db.social_engine import config_store, observability
from db.social_engine import posts as post_store
from db.social_engine import products as product_store
from services.social_engine import http as se_http
from services.social_engine.campaigns.engine import evaluate_campaign
from services.social_engine.jobs.worker import Worker
from services.social_engine.pipeline import actions
from services.social_engine.pipeline.channels import sync_channels
from services.social_engine.settings import save_settings


def _jpeg(color=(200, 190, 170)) -> bytes:
    image = Image.new("RGB", (900, 900), (247, 244, 236))
    for x in range(300, 600):
        for y in range(200, 700, 2):
            image.putpixel((x, y), color)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


@pytest.fixture
def cdn():
    payload = _jpeg()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"content-type": "image/jpeg"})

    se_http.TRANSPORT_OVERRIDES["cdn"] = httpx.MockTransport(handler)
    yield


async def _setup(store_id: str) -> None:
    await save_settings(store_id, {
        "enabled": True, "publisher": "dry_run",
        "captions": {"source": "template"},
        "ai": {"ai_review": False, "ai_decisions": False},
        "schedule": {"window_start": "00:05", "window_end": "23:55", "min_gap_minutes": 30,
                     "use_best_times": False, "plan_days_ahead": 1},
    })
    for pid, title, price, compare in (("1", "Magnesium Glycinate", 24.99, 29.99), ("2", "Ashwagandha KSM-66", 19.99, None),
                                       ("3", "Omega-3 Fish Oil", 21.5, None)):
        await product_store.upsert_product(store_id, {
            "product_id": pid, "title": title, "handle": f"p{pid}", "url": f"https://bioluxelab.com/products/p{pid}",
            "price_min": price, "price_max": price, "compare_at_max": compare, "on_sale": bool(compare),
            "discount_pct": 16.7 if compare else 0, "available": True, "status": "ACTIVE",
            "published_at": "2026-01-01T00:00:00Z", "shopify_created_at": "2026-01-01T00:00:00Z",
            "images": [{"url": f"https://cdn.test/{pid}.jpg"}, {"url": f"https://cdn.test/{pid}b.jpg"}],
            "featured_image_url": f"https://cdn.test/{pid}.jpg", "tags": ["wellness"],
        })
    result = await sync_channels(store_id)
    assert result["channels"] == 5
    assert result["pinterest_boards_set"], "dry-run Pinterest should get a default board"


@pytest.mark.asyncio
async def test_smartq_auto_pipeline_reaches_scheduled_in_dry_run(se_db, cdn):
    await _setup(se_db)
    campaign_id = await config_store.save_campaign(se_db, {
        "name": "Daily SmartQ", "rule_type": "smartq", "status": "active", "approval_mode": "auto",
        "config": {"order": "least_posted", "posts_per_day": {"single_product": 1}},
    })
    result = await evaluate_campaign(se_db, campaign_id, seed=3)
    assert result["planned"] >= 1, result
    await Worker(run_periodic=False).run_once(max_jobs=50)

    posts = await post_store.list_posts(se_db, campaign_id=campaign_id)
    assert posts and all(p["status"] == "scheduled" for p in posts), [(p["status"], p["error_message"]) for p in posts]
    post = await post_store.get_post(se_db, posts[0]["id"])
    assert len(post["targets"]) == 5
    assert all(t["external_id"].startswith("dry") or t["external_id"] for t in post["targets"])
    pins = [t for t in post["targets"] if t["provider"] == "pinterest"]
    assert pins and pins[0]["settings"].get("board") == "dry-board"
    assert {m["format"] for m in post["media"]} >= {"portrait", "pin"}
    assert all(m["external_id"] for m in post["media"] if m["role"] != "thumbnail" and m["id"] in
               {mid for t in post["targets"] for mid in t["media_ids"]})
    timeline = [row["stage"] for row in await observability.list_activity(entity_type="post", entity_id=post["id"])]
    assert "generate" in timeline and "publish" in timeline
    products = await product_store.get_products(se_db, post["product_ids"])
    assert products[0]["post_count"] == 1  # rotation bookkeeping


@pytest.mark.asyncio
async def test_manual_studio_post_waits_for_approval_then_publishes(se_db, cdn):
    await _setup(se_db)
    when = int(time.time()) + 6 * 3600
    created = await actions.create_manual_post(
        se_db, content_type="sale_promo", product_ids=["1"], channel_ids=["dry-instagram", "dry-x"],
        scheduled_at=when, texts={"coupon_code": "GLOW20", "promo_text": "Autumn wellness sale"},
    )
    await Worker(run_periodic=False).run_once()
    post = await post_store.get_post(se_db, created["post_id"])
    assert post["status"] == "pending_approval", post["error_message"]
    x = next(t for t in post["targets"] if t["provider"] == "x")
    assert len(x["caption"]) <= 280
    await actions.approve(se_db, post["id"], by="test")
    await Worker(run_periodic=False).run_once()
    post = await post_store.get_post(se_db, post["id"])
    assert post["status"] == "scheduled"
    assert all(t["scheduled_at"] == when for t in post["targets"])


@pytest.mark.asyncio
async def test_studio_post_with_theme_gets_occasion_and_sticker(se_db, cdn, monkeypatch):
    from services.social_engine.creatives.render import TemplateRenderer

    seen = []
    original = TemplateRenderer.render

    async def spy(self, request):
        seen.append(request)
        return await original(self, request)

    monkeypatch.setattr(TemplateRenderer, "render", spy)
    await _setup(se_db)
    created = await actions.create_manual_post(
        se_db, content_type="holiday", product_ids=["1"], channel_ids=["dry-instagram"],
        template_key="seasonal_scene", theme_key="black_friday", texts={"coupon_code": "BF30"},
    )
    await Worker(run_periodic=False).run_once()
    post = await post_store.get_post(se_db, created["post_id"])
    assert post["status"] == "pending_approval", post["error_message"]
    texts = seen[0].texts
    assert texts["occasion"] == "Black Friday"
    assert texts["sticker"] and texts["coupon_code"] == "BF30"
    assert seen[0].options.get("sticker") == texts["sticker"]
    assert seen[0].brand.colors.background.lower() == "#0b0b0b"  # theme palette


@pytest.mark.asyncio
async def test_studio_story_choice_sets_format_and_single_media(se_db, cdn):
    await _setup(se_db)
    created = await actions.create_manual_post(
        se_db, content_type="carousel", product_ids=["1", "2", "3"], channel_ids=["dry-instagram", "dry-x"],
        channel_settings={"dry-instagram": {"post_type": "story"}},
    )
    await Worker(run_periodic=False).run_once()
    post = await post_store.get_post(se_db, created["post_id"])
    assert post["status"] == "pending_approval", post["error_message"]
    insta = next(t for t in post["targets"] if t["channel_id"] == "dry-instagram")
    assert insta["format"] == "story" and len(insta["media_ids"]) == 1
    assert insta["settings"]["post_type"] == "story"
    await actions.approve(se_db, post["id"], by="test")
    await Worker(run_periodic=False).run_once()
    post = await post_store.get_post(se_db, post["id"])
    assert post["status"] == "scheduled", post["error_message"]


@pytest.mark.asyncio
async def test_promotion_takes_slot_from_smartq_and_uses_theme(se_db, cdn):
    await _setup(se_db)
    await save_settings(se_db, {"schedule": {"posts_per_channel_per_day": 1}})
    smartq = await config_store.save_campaign(se_db, {
        "name": "Daily", "rule_type": "smartq", "status": "active", "approval_mode": "manual",
        "config": {"order": "least_posted", "posts_per_day": {"single_product": 1}},
    })
    await evaluate_campaign(se_db, smartq, seed=1)
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    promo = await config_store.save_campaign(se_db, {
        "name": "Black Friday", "rule_type": "promotion", "status": "active", "approval_mode": "manual",
        "config": {"theme_key": "black_friday", "start_date": tomorrow.isoformat(), "duration_days": 3,
                   "posts_per_day": 1, "coupon_code": "BF25", "promo_texts": ["Black Friday: our best offer"],
                   "ai_background": True},
    })
    result = await evaluate_campaign(se_db, promo, seed=1)
    assert result["planned"] >= 1, result
    displaced = await post_store.list_posts(se_db, campaign_id=smartq, statuses=("cancelled",))
    assert displaced and displaced[0]["error_type"] == "Displaced"
    await Worker(run_periodic=False).run_once(max_jobs=50)
    promo_posts = await post_store.list_posts(se_db, campaign_id=promo)
    post = await post_store.get_post(se_db, promo_posts[0]["id"])
    assert post["status"] == "pending_approval", post["error_message"]
    warnings = post["generation"].get("warnings", [])
    assert any("live system" in w for w in warnings)  # AI background skipped outside live
    assert any(t["caption"] for t in post["targets"])


@pytest.mark.asyncio
async def test_promotion_preview_never_cancels_smartq_posts(se_db, cdn):
    """Preview (dry run) on a full day shows the promotion taking a slot but leaves SmartQ posts alone."""
    await _setup(se_db)
    await save_settings(se_db, {"schedule": {"posts_per_channel_per_day": 1}})
    smartq = await config_store.save_campaign(se_db, {
        "name": "Daily", "rule_type": "smartq", "status": "active", "approval_mode": "manual",
        "config": {"order": "least_posted", "posts_per_day": {"single_product": 1}},
    })
    await evaluate_campaign(se_db, smartq, seed=1)
    before = {p["id"]: p["status"] for p in await post_store.list_posts(se_db, campaign_id=smartq)}
    assert before
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    promo = await config_store.save_campaign(se_db, {
        "name": "Flash", "rule_type": "promotion", "status": "active", "approval_mode": "manual",
        "config": {"theme_key": "flash_sale", "start_date": tomorrow.isoformat(), "duration_days": 2,
                   "posts_per_day": 1, "promo_texts": ["Flash sale: today only"]},
    })
    preview = await evaluate_campaign(se_db, promo, seed=1, dry_run=True)
    assert preview["planned"] >= 1, preview
    after = {p["id"]: p["status"] for p in await post_store.list_posts(se_db, campaign_id=smartq)}
    assert after == before
    assert not await post_store.list_posts(se_db, campaign_id=promo)


# ---------------------------------------------------------------------------
# Daily auto-fill: every channel always gets its daily posts
# ---------------------------------------------------------------------------

def test_autofill_mix_adds_up_to_the_daily_target():
    from services.social_engine.campaigns.autofill import autofill_mix

    assert autofill_mix(2) == {"single_product": 1, "product_video": 1}
    assert autofill_mix(1) == {"single_product": 1}
    assert sum(autofill_mix(7).values()) == 7 and max(autofill_mix(20).values()) <= 3


@pytest.mark.asyncio
async def test_autofill_creates_campaign_and_fills_tomorrow(se_db, cdn):
    from services.social_engine.campaigns.autofill import AUTOFILL_NAME, ensure_autofill_campaign
    from services.social_engine.campaigns.engine import evaluate_due_campaigns
    from services.social_engine.scheduling.fill import daily_fill_status

    await _setup(se_db)
    created = await ensure_autofill_campaign(se_db)
    assert created["action"] == "created"
    campaign = await config_store.get_campaign(se_db, created["campaign_id"])
    assert campaign["name"] == AUTOFILL_NAME and campaign["status"] == "active"
    assert campaign["approval_mode"] == "ai_review" and campaign["channel_ids"] == []
    assert campaign["config"]["posts_per_day"] == {"single_product": 1, "product_video": 1}

    await evaluate_due_campaigns(se_db)
    tomorrow = (await daily_fill_status(se_db, days=2))[1]
    assert all(c["full"] for c in tomorrow["channels"]), tomorrow
    assert (await ensure_autofill_campaign(se_db))["action"] == "exists"   # no duplicates


@pytest.mark.asyncio
async def test_autofill_respects_pause_setting_and_comes_back_after_delete(se_db, cdn):
    from services.social_engine.campaigns.autofill import ensure_autofill_campaign

    await _setup(se_db)
    first = (await ensure_autofill_campaign(se_db))["campaign_id"]
    campaign = await config_store.get_campaign(se_db, first)
    await config_store.save_campaign(se_db, {**campaign, "status": "paused"})
    assert (await ensure_autofill_campaign(se_db))["action"] == "exists"   # a pause is respected

    await config_store.delete_campaign(se_db, first)
    again = await ensure_autofill_campaign(se_db)
    assert again["action"] == "created" and again["campaign_id"] != first

    await save_settings(se_db, {"schedule": {"posts_per_channel_per_day": 3}})
    assert (await ensure_autofill_campaign(se_db))["action"] == "updated"
    updated = await config_store.get_campaign(se_db, again["campaign_id"])
    assert sum(updated["config"]["posts_per_day"].values()) == 3

    await save_settings(se_db, {"schedule": {"always_fill_daily_slots": False}})
    assert (await ensure_autofill_campaign(se_db))["action"] == "off"


@pytest.mark.asyncio
async def test_autofill_leaves_merchant_smartq_alone(se_db, cdn):
    from services.social_engine.campaigns.autofill import ensure_autofill_campaign

    await _setup(se_db)
    mine = await config_store.save_campaign(se_db, {
        "name": "My SmartQ", "rule_type": "smartq", "status": "active", "approval_mode": "manual",
        "config": {"order": "least_posted", "posts_per_day": {"single_product": 1}},
    })
    result = await ensure_autofill_campaign(se_db)
    assert result == {"action": "exists", "campaign_id": mine}
    assert len(await config_store.list_campaigns(se_db)) == 1
