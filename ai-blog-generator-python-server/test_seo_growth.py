from __future__ import annotations

import pytest

import db
from shopify_client import ShopifyArticle, _build_article_html
from services.seo_growth import orchestrator
from services.seo_growth import repair
from services.seo_growth.managed_content import (
    has_managed_keyword_blocks,
    remove_managed_keyword_blocks,
)
from services.seo_growth.opportunities import search_opportunities
from services.seo_growth.search_console import SearchConsoleError, normalise_site_url
from services.seo_growth.shopify_audit import audit_articles, audit_products


def _article(article_id: int, title: str, body: str, image: str = "hero.jpg") -> ShopifyArticle:
    return ShopifyArticle(
        id=article_id, blog_id=1, blog_handle="wellness", title=title,
        handle=f"article-{article_id}", body_html=body, summary_html="", tags="",
        article_url=f"https://bioluxelab.com/blogs/wellness/article-{article_id}",
        image_url=image, published_at="2026-01-01T00:00:00Z",
    )


def test_search_console_property_normalisation_is_explicit():
    assert normalise_site_url("sc-domain:BioLuxeLab.com") == "sc-domain:bioluxelab.com"
    assert normalise_site_url("https://bioluxelab.com") == "https://bioluxelab.com/"
    with pytest.raises(SearchConsoleError, match="blank"):
        normalise_site_url("")


def test_search_opportunities_keep_source_metrics_and_business_context():
    search = {
        "current": [{
            "query": "red light therapy at home", "page": "https://bioluxelab.com/blogs/a",
            "clicks": 2, "impressions": 500, "ctr": .004, "position": 8.2,
        }],
        "previous": [{
            "query": "red light therapy at home", "page": "https://bioluxelab.com/blogs/a",
            "clicks": 20, "impressions": 600, "ctr": .033, "position": 6.1,
        }],
    }
    intelligence = {
        "shopify": {"landing_pages": [{
            "landing_page_path": "/blogs/a", "sessions": 80,
            "sessions_with_cart_additions": 4, "sessions_that_completed_checkout": 1,
        }]},
    }
    items = search_opportunities(search, intelligence)
    categories = {item["category"] for item in items}
    assert {"click_through", "decline"}.issubset(categories)
    assert all(item["source"] == "google_search_console" for item in items)
    assert items[0]["metrics"]["shopify_sessions"] == 80


def test_article_audit_flags_public_keyword_dump_and_uncited_claims():
    article = _article(
        1, "At-home recovery guide",
        "<h2>Routine</h2><p>A study found this cures fatigue.</p>"
        "<p>Keywords: recovery, wellness</p><p>#Health #Recovery #Energy #Routine</p>",
    )
    summary, issues = audit_articles([article])
    keys = {item["key"].split(":", 1)[0] for item in issues}
    assert "article-unmanaged-keyword-blocks" in keys
    assert "article-health-citations" in keys
    assert summary["visible_keyword_blocks"] == 1


def test_managed_keyword_cleanup_is_exact_and_preserves_article_copy():
    body = (
        "<h2>Useful guide</h2><p>Keep this copy and #One natural tag.</p>"
        '<div style="margin-top:40px;padding-top:24px;border-top:1px solid #e5e7eb;">'
        '<div style="margin-bottom:10px;"><span style="display:inline-block;">sleep recovery</span></div>'
        '<div><span style="display:inline-block;">#Sleep</span><span>#Recovery</span></div></div>'
        '<div style="font-size:1px;color:transparent;line-height:1;overflow:hidden;height:1px;" '
        'aria-hidden="true"><span>sleep recovery</span> <span>#Sleep</span></div>'
    )
    assert has_managed_keyword_blocks(body)
    result = remove_managed_keyword_blocks(body)
    assert result.visible_blocks == 1
    assert result.hidden_blocks == 1
    assert result.html == "<h2>Useful guide</h2><p>Keep this copy and #One natural tag.</p>"
    assert not has_managed_keyword_blocks(result.html)


def test_new_article_html_does_not_publish_keyword_or_hashtag_blocks():
    html = _build_article_html(
        "<p>Helpful article copy.</p>", [], ["sleep recovery"], ["#Sleep"],
        ["how to recover sleep"], title="Sleep guide",
    )
    assert html == "<p>Helpful article copy.</p>"
    assert "#Sleep" not in html
    assert "transparent" not in html


def test_article_audit_groups_managed_blocks_into_one_repair_opportunity():
    managed = (
        "<p>Article copy.</p>"
        '<div style="font-size:1px;color:transparent;line-height:1;overflow:hidden;height:1px;" '
        'aria-hidden="true"><span>keyword</span></div>'
    )
    summary, issues = audit_articles([
        _article(1, "First", managed),
        _article(2, "Second", managed),
    ])
    repair_items = [item for item in issues if item.get("kind") == "safe_repair"]
    assert len(repair_items) == 1
    assert repair_items[0]["metrics"]["affected_count"] == 2
    assert summary["managed_keyword_blocks"] == 2


def test_product_audit_distinguishes_shopify_fallback_from_missing_content():
    products = [{
        "id": "gid://shopify/Product/1", "legacyResourceId": "1", "title": "Massage Tool",
        "handle": "massage-tool", "status": "ACTIVE", "vendor": "BioLuxeLab",
        "productType": "Massage", "description": "Full product description", "totalInventory": 3,
        "onlineStoreUrl": "https://bioluxelab.com/products/massage-tool",
        "seo": {"title": "", "description": ""},
        "featuredImage": {"url": "hero.jpg", "altText": "Massage tool"},
        "variants": {"nodes": [{"sku": "M-1", "barcode": "123456789", "price": "19.00"}]},
    }]
    summary, issues = audit_products(products, "https://bioluxelab.com")
    assert summary["automatic_seo_fallbacks"] == 1
    assert summary["missing_gtins"] == 0
    assert any(item["key"] == "product-seo-fallback:1" for item in issues)
    merchant_issue = next((item for item in issues if item["key"] == "merchant-fields:1"), None)
    assert merchant_issue is None


@pytest.mark.asyncio
async def test_seo_run_persistence_and_workflow_status_survive_new_audit(tmp_path):
    previous_path = db.get_db_path()
    db.set_db_path(str(tmp_path / "seo.db"))
    try:
        await db.init_db()
        await db.upsert_store({
            "id": "seo-store", "name": "BioLuxeLab",
            "myshopify_domain": "bio.myshopify.com", "custom_domain": "bioluxelab.com",
            "client_id": "id", "client_secret": "secret", "default_blog_handle": "news",
            "default_author": "Team", "sort_order": 0,
        })
        item = {
            "key": "stable-key", "category": "content", "severity": "high", "score": 90,
            "title": "Fix this", "evidence": "Measured", "action": "Review", "source": "shopify",
        }
        first = await db.create_seo_growth_run("seo-store", 90)
        await db.complete_seo_growth_run(first, "seo-store", {"store_id": "seo-store"}, [item])
        saved = await db.get_seo_growth_opportunities("seo-store", first, status="")
        assert await db.set_seo_opportunity_status("seo-store", saved[0]["id"], "planned")

        second = await db.create_seo_growth_run("seo-store", 90)
        await db.complete_seo_growth_run(second, "seo-store", {"store_id": "seo-store"}, [item])
        current = await db.get_seo_growth_opportunities("seo-store", second, status="")
        assert current[0]["status"] == "planned"
        assert (await db.get_latest_seo_growth_run("seo-store"))["status"] == "complete"
    finally:
        db.set_db_path(previous_path)


@pytest.mark.asyncio
async def test_orchestrator_reports_exact_final_error_once_without_retry(tmp_path, monkeypatch):
    previous_path = db.get_db_path()
    db.set_db_path(str(tmp_path / "seo-failure.db"))
    calls = 0

    async def fail_shopify(_store):
        nonlocal calls
        calls += 1
        raise RuntimeError("Shopify returned HTTP 429 during product audit")

    monkeypatch.setattr(orchestrator, "collect_shopify_audit", fail_shopify)
    try:
        await db.init_db()
        await db.upsert_store({
            "id": "seo-failure", "name": "BioLuxeLab",
            "myshopify_domain": "bio.myshopify.com", "custom_domain": "bioluxelab.com",
            "client_id": "id", "client_secret": "secret", "default_blog_handle": "news",
            "default_author": "Team", "sort_order": 0,
        })
        run_id = await db.create_seo_growth_run("seo-failure", 90)
        await orchestrator.run_audit(run_id, "seo-failure", 90)
        latest = await db.get_latest_seo_growth_run("seo-failure")
        assert calls == 1
        assert latest["status"] == "failed"
        assert latest["error_type"] == "RuntimeError"
        assert latest["error_message"] == "Shopify returned HTTP 429 during product audit"
    finally:
        db.set_db_path(previous_path)


@pytest.mark.asyncio
async def test_orchestrator_completes_shopify_only_audit_with_explicit_source_status(tmp_path, monkeypatch):
    previous_path = db.get_db_path()
    db.set_db_path(str(tmp_path / "seo-success.db"))

    async def shopify_audit(_store):
        return {
            "products": {"active_products": 3},
            "articles": {"articles": 5},
            "issues": [{
                "key": "shopify-only", "category": "merchant", "severity": "high",
                "score": 85, "title": "Complete product fields", "evidence": "Measured",
                "action": "Review", "source": "shopify",
            }],
        }

    monkeypatch.setattr(orchestrator, "collect_shopify_audit", shopify_audit)
    try:
        await db.init_db()
        await db.upsert_store({
            "id": "seo-success", "name": "BioLuxeLab",
            "myshopify_domain": "bio.myshopify.com", "custom_domain": "bioluxelab.com",
            "client_id": "id", "client_secret": "secret", "default_blog_handle": "news",
            "default_author": "Team", "sort_order": 0,
        })
        run_id = await db.create_seo_growth_run("seo-success", 90)
        await orchestrator.run_audit(run_id, "seo-success", 90)
        latest = await db.get_latest_seo_growth_run("seo-success")
        items = await db.get_seo_growth_opportunities("seo-success", run_id, status="")
        assert latest["status"] == "complete"
        assert latest["summary"]["search_console"]["status"] == "not_configured"
        assert "no Google search metrics were inferred" in latest["summary"]["search_console"]["message"]
        assert items[0]["opportunity_key"] == "shopify-only"
    finally:
        db.set_db_path(previous_path)


@pytest.mark.asyncio
async def test_safe_repair_preview_apply_and_restore_are_reversible(tmp_path, monkeypatch):
    previous_path = db.get_db_path()
    db.set_db_path(str(tmp_path / "seo-repair.db"))
    managed = (
        "<p>Keep this article.</p>"
        '<div style="font-size:1px;color:transparent;line-height:1;overflow:hidden;height:1px;" '
        'aria-hidden="true"><span>keyword</span></div>'
    )
    current = {"body": managed}

    async def articles(_store, limit_per_blog=0):
        assert limit_per_blog == 0
        return [_article(42, "Repair me", current["body"])]

    async def fetch_body(_store, blog_id, article_id):
        assert (blog_id, article_id) == (1, 42)
        return current["body"]

    async def update_body(_store, blog_id, article_id, body_html):
        assert (blog_id, article_id) == (1, 42)
        current["body"] = body_html
        return body_html

    monkeypatch.setattr(repair.shopify_client, "fetch_store_articles", articles)
    monkeypatch.setattr(repair.shopify_client, "fetch_article_body_html", fetch_body)
    monkeypatch.setattr(repair.shopify_client, "update_article_body_html", update_body)
    try:
        await db.init_db()
        await db.upsert_store({
            "id": "repair-store", "name": "BioLuxeLab",
            "myshopify_domain": "bio.myshopify.com", "custom_domain": "bioluxelab.com",
            "client_id": "id", "client_secret": "secret", "default_blog_handle": "news",
            "default_author": "Team", "sort_order": 0,
        })
        job_id = await db.create_seo_repair_job("repair-store", repair.RULE_KEY)
        await repair.scan_managed_keyword_blocks(job_id, "repair-store")
        preview = await db.get_seo_repair_job("repair-store", job_id)
        assert preview["status"] == "awaiting_approval"
        assert preview["total_items"] == 1

        assert await db.begin_seo_repair_phase(
            "repair-store", job_id, expected_status="awaiting_approval",
            status="applying", stage="starting_apply",
        )
        await repair.apply_managed_keyword_blocks(
            job_id, "repair-store", refresh_audit=False,
        )
        applied = await db.get_seo_repair_job("repair-store", job_id)
        assert applied["status"] == "complete"
        assert applied["changed_items"] == 1
        assert current["body"] == "<p>Keep this article.</p>"

        assert await db.begin_seo_repair_phase(
            "repair-store", job_id, expected_status="complete",
            status="restoring", stage="starting_restore",
        )
        await repair.restore_managed_keyword_blocks(job_id, "repair-store")
        restored = await db.get_seo_repair_job("repair-store", job_id)
        assert restored["status"] == "restored"
        assert current["body"] == managed
    finally:
        db.set_db_path(previous_path)
