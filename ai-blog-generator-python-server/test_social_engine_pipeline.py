"""Pipeline behaviour that doesn't need rendering or an LLM: approval actions,
daily slot filling, missed approvals, freshness guard, Pinterest board choice."""
from __future__ import annotations

import time

import pytest

from db.social_engine import config_store, observability
from db.social_engine import posts as post_store
from db.social_engine import products as product_store
from services.social_engine.errors import ValidationError
from services.social_engine.pipeline import actions
from services.social_engine.pipeline.channels import pick_board
from services.social_engine.pipeline.freshness import check_post, compare
from services.social_engine.pipeline.generate import product_snapshot
from services.social_engine.scheduling.fill import daily_fill_status, reslot_missed_approvals


async def _channels(store_id: str) -> None:
    for cid, provider in (("ig", "instagram"), ("fb", "facebook")):
        await config_store.upsert_channel_from_publisher(store_id, {
            "channel_id": cid, "provider": provider, "name": provider.title(),
        })


async def _product(store_id: str, pid: str = "1", price: float = 20.0, available: bool = True) -> dict:
    await product_store.upsert_product(store_id, {
        "product_id": pid, "title": f"Product {pid}", "handle": f"p-{pid}", "url": f"https://x/p-{pid}",
        "price_min": price, "price_max": price, "available": available, "status": "ACTIVE",
    })
    return await product_store.get_product(store_id, pid)


async def _generated_post(store_id: str, *, status: str = "pending_approval", when: int | None = None) -> str:
    product = await _product(store_id)
    when = when or int(time.time()) + 3 * 3600
    post_id, _ = await post_store.create_post(store_id, {
        "content_type": "single_product", "product_ids": ["1"], "status": status, "scheduled_at": when,
        "options": {"channel_ids": ["ig", "fb"], "channel_times": {"ig": when, "fb": when}},
        "generation": {"product_snapshot": product_snapshot([product])},
    })
    await post_store.replace_targets(store_id, post_id, [
        {"channel_id": "ig", "provider": "instagram", "caption": "Hello", "scheduled_at": when},
        {"channel_id": "fb", "provider": "facebook", "caption": "Hello", "scheduled_at": when},
    ])
    return post_id


@pytest.mark.asyncio
async def test_approve_queues_publish_and_reject_is_guarded(se_db):
    await _channels(se_db)
    post_id = await _generated_post(se_db)
    result = await actions.approve(se_db, post_id, by="tester")
    assert result["status"] == "approved" and result["job_id"]
    post = await post_store.get_post(se_db, post_id)
    assert post["status"] == "approved" and post["approved_by"] == "tester"
    with pytest.raises(ValidationError):
        await actions.approve(se_db, post_id)  # already approved
    other = await _generated_post(se_db)
    await actions.reject(se_db, other, reason="off brand")
    assert (await post_store.get_post(se_db, other))["rejected_reason"] == "off brand"


@pytest.mark.asyncio
async def test_edit_changes_caption_and_time_but_rejects_empty_caption(se_db):
    await _channels(se_db)
    post_id = await _generated_post(se_db)
    later = int(time.time()) + 5 * 3600
    await actions.edit(se_db, post_id, target_captions={"ig": "New caption"}, scheduled_at=later)
    post = await post_store.get_post(se_db, post_id)
    ig = next(t for t in post["targets"] if t["channel_id"] == "ig")
    assert ig["caption"] == "New caption" and ig["scheduled_at"] == later and post["scheduled_at"] == later
    with pytest.raises(ValidationError):
        await actions.edit(se_db, post_id, target_captions={"ig": "   "})


@pytest.mark.asyncio
async def test_daily_fill_counts_slots_per_channel(se_db):
    await _channels(se_db)
    await _generated_post(se_db, when=int(time.time()) + 86400)
    days = await daily_fill_status(se_db, days=3)
    assert len(days) == 3
    planned = sum(c["planned"] for d in days for c in d["channels"])
    assert planned == 2  # one post → instagram + facebook
    assert all(c["target"] == 2 for d in days for c in d["channels"])


@pytest.mark.asyncio
async def test_missed_approval_moves_once_then_expires(se_db):
    await _channels(se_db)
    past = int(time.time()) - 3600
    post_id = await _generated_post(se_db, when=past)
    first = await reslot_missed_approvals(se_db)
    assert first == {"moved": 1, "expired": 0}
    post = await post_store.get_post(se_db, post_id)
    assert post["scheduled_at"] > time.time() and post["status"] == "pending_approval"
    # Simulate missing the new time too.
    await post_store.update_post(se_db, post_id, scheduled_at=past)
    second = await reslot_missed_approvals(se_db)
    assert second["expired"] == 1
    post = await post_store.get_post(se_db, post_id)
    assert post["status"] == "cancelled" and post["error_type"] == "Expired"
    assert all(t["status"] == "cancelled" for t in post["targets"])


@pytest.mark.asyncio
async def test_freshness_detects_price_change_and_sell_out(se_db):
    await _channels(se_db)
    post_id = await _generated_post(se_db)
    post = await post_store.get_post(se_db, post_id)
    assert (await check_post(se_db, post))[0] == "ok"
    await _product(se_db, price=15.0)
    verdict, reason = await check_post(se_db, post)
    assert verdict == "changed" and "price changed" in reason
    await _product(se_db, price=15.0, available=False)
    assert (await check_post(se_db, post))[0] == "sold_out"


def test_compare_sale_status_change():
    before = {"1": {"price_min": 10.0, "compare_at_max": None, "on_sale": False, "available": True, "title": "A"}}
    after = {"1": {**before["1"], "on_sale": True, "compare_at_max": 12.0}}
    assert compare(before, after)[0] == "changed"
    assert compare(before, {})[0] == "sold_out"


def test_pick_board_prefers_brand_name():
    boards = [{"name": "Products", "id": "1"}, {"name": "Bioluxelab", "id": "2"}, {"name": "Other", "id": "3"}]
    assert pick_board(boards, ["BioluxeLab"])["id"] == "2"
    assert pick_board(boards, ["Unknown brand"])["id"] == "1"
    assert pick_board([], ["x"]) is None


@pytest.mark.asyncio
async def test_activity_timeline_records_actions(se_db):
    await _channels(se_db)
    post_id = await _generated_post(se_db)
    await actions.approve(se_db, post_id)
    timeline = await observability.list_activity(entity_type="post", entity_id=post_id)
    assert any("Approved" in row["message"] for row in timeline)
