from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("DEEPSEEK_API_KEY", "test")
os.environ.setdefault("GROK_API_KEY", "test")

import db
from services.intelligence_service import build_recommendations


def _summary(sessions: int = 1104) -> dict:
    return {
        "store_id": "test",
        "period_days": 90,
        "shopify": {
            "funnel": {
                "sessions": sessions,
                "cart_additions": 10,
                "checkouts": 9,
                "purchases": 1,
                "conversion_rate": 0.09,
                "add_to_cart_rate": 0.91,
                "checkout_completion_rate": 11.11,
            },
            "sources": [
                {"referrer_source": "social", "sessions": 456, "purchases": 0},
                {"referrer_source": "search", "sessions": 23, "purchases": 0},
            ],
            "landing_pages": [
                {"landing_page_path": "/", "sessions": 436, "purchases": 0},
            ],
            "catalog": {
                "products": 90, "missing_seo_descriptions": 89,
                "invalid_product_types": 53, "invalid_vendors": 27,
                "active_zero_stock": 1,
            },
            "content": {"articles": 749},
            "policies": {"shipping": False},
        },
        "ga4": {"connected": False, "status": "not configured"},
    }


def test_recommendations_are_grounded_and_commercially_prioritised():
    recommendations = build_recommendations(_summary())
    keys = {item["metric_key"] for item in recommendations}
    assert "store_conversion" in keys
    assert "social_conversion" in keys
    assert "content_search_return" in keys
    assert "shipping_policy" in keys
    assert all(item["evidence"] and item["action"] for item in recommendations)


def test_small_samples_do_not_trigger_conversion_claims():
    summary = _summary(sessions=40)
    summary["shopify"]["sources"][0]["sessions"] = 20
    summary["shopify"]["landing_pages"][0]["sessions"] = 20
    keys = {item["metric_key"] for item in build_recommendations(summary)}
    assert "store_conversion" not in keys
    assert "add_to_cart" not in keys
    assert "social_conversion" not in keys
    assert "homepage_conversion" not in keys


@pytest.mark.asyncio
async def test_intelligence_run_persistence():
    with tempfile.NamedTemporaryFile(suffix=".db") as file:
        db.set_db_path(file.name)
        await db.init_db()
        await db.upsert_store({
            "id": "test", "name": "Test", "myshopify_domain": "test.myshopify.com",
            "custom_domain": "", "client_id": "id", "client_secret": "secret",
            "default_blog_handle": "news", "default_author": "Team", "sort_order": 0,
        })
        run_id = await db.create_intelligence_run("test", 90)
        recommendation = build_recommendations(_summary())[0]
        await db.complete_intelligence_run(run_id, _summary(), [recommendation])
        latest = await db.get_latest_intelligence_run("test")
        saved = await db.get_run_recommendations(run_id)
        assert latest and latest["status"] == "complete"
        assert latest["summary"]["period_days"] == 90
        assert saved[0]["metric_key"] == recommendation["metric_key"]
