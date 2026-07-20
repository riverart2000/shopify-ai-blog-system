from __future__ import annotations

import tempfile

import pytest

import db
from services.wellness_quiz_service import classify_product, public_config


def _product(title: str, price: str = "49.00", available: bool = True) -> dict:
    return {
        "id": "gid://shopify/Product/123",
        "legacyResourceId": "123",
        "title": title,
        "handle": "test-product",
        "status": "ACTIVE",
        "description": "A useful wellness tool",
        "tags": [],
        "productType": "Wellness",
        "onlineStoreUrl": "https://example.com/products/test-product",
        "featuredImage": {"url": "https://cdn.example.com/product.jpg"},
        "priceRangeV2": {"minVariantPrice": {"amount": price, "currencyCode": "GBP"}},
        "variants": {"nodes": [{"legacyResourceId": "456", "availableForSale": available}]},
        "collections": {"nodes": []},
        "guideTitle": {"value": "A related guide"},
        "guideUrl": {"value": "https://example.com/blogs/news/guide"},
    }


def test_quiz_classification_uses_commercial_product_facts():
    item = classify_product(_product("Deep Tissue Massage Gun"), "example.com", "https://example.com/pages/massage")
    assert item["available"] is True
    assert item["goal_scores"]["pain_mobility"] > item["goal_scores"]["beauty"]
    assert "device" in item["formats"]
    assert item["landing_page_url"].endswith("/pages/massage")
    assert item["variant_id"] == "456"


def test_quiz_excludes_unrelated_general_electronics():
    item = classify_product(_product("Protective Laptop Sleeve"), "example.com")
    assert item["available"] is False
    assert max(item["goal_scores"].values()) == 0


def test_public_quiz_avoids_sensitive_health_questions():
    config = public_config()
    assert [question["id"] for question in config["questions"]] == ["goal", "format", "time", "budget"]
    text = str(config).lower()
    assert "diagnos" not in text
    assert "medication" not in text


@pytest.mark.asyncio
async def test_quiz_events_produce_anonymous_funnel_summary():
    with tempfile.NamedTemporaryFile(suffix=".db") as file:
        db.set_db_path(file.name)
        await db.init_db()
        await db.record_wellness_quiz_event("store", {
            "session_id": "session_12345", "event_type": "started", "source_path": "/",
        })
        await db.record_wellness_quiz_event("store", {
            "session_id": "session_12345", "event_type": "completed", "goal": "relaxation",
            "answers": {"goal": "relaxation"}, "recommendations": ["diffuser"],
        })
        await db.record_wellness_quiz_event("store", {
            "session_id": "session_12345", "event_type": "recommendation_clicked",
            "goal": "relaxation", "product_handle": "diffuser",
        })
        summary = await db.get_wellness_quiz_summary("store", 90)
        assert summary["starts"] == 1
        assert summary["completions"] == 1
        assert summary["completion_rate"] == 100.0
        assert summary["click_through_rate"] == 100.0
        assert summary["goals"][0]["goal"] == "relaxation"
