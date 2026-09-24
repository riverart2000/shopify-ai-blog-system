from __future__ import annotations

import pytest

from config import StoreConfig
from services import product_blog_sync
from shopify_client import ShopifyArticle


def _store() -> StoreConfig:
    return StoreConfig(
        id="store-1",
        name="BioLuxeLab",
        myshopify_domain="bioluxelab.myshopify.com",
        custom_domain="bioluxelab.com",
        client_id="client",
        client_secret="secret",
        default_blog_handle="inside-the-products",
        default_author="Store Team",
    )


def _article(article_id: int, handle: str, title: str, body: str, summary: str) -> ShopifyArticle:
    return ShopifyArticle(
        id=article_id,
        blog_id=77,
        blog_handle="inside-the-products",
        title=title,
        handle=handle,
        body_html=body,
        summary_html=f"<p>{summary}</p>",
        tags="",
        article_url=f"https://bioluxelab.com/blogs/inside-the-products/{handle}",
        image_url="",
        published_at="2026-08-18T00:00:00Z",
    )


def test_cta_matching_ignores_ordinary_related_product_links() -> None:
    markup = """
      <p><a href="/products/main-product">Shop Main Product</a></p>
      <div class="related"><a href="/products/another-product">Another Product</a></div>
    """
    assert product_blog_sync._article_cta_handles(markup) == {"main-product"}


@pytest.mark.asyncio
async def test_reconcile_repairs_only_authoritative_matches(monkeypatch) -> None:
    article_a = _article(
        1,
        "guide-a",
        "Guide A",
        '<p>Guide body.</p><a href="/products/product-b">Related Product B</a>',
        "A summary",
    )
    article_b = _article(
        2,
        "guide-b",
        "Guide B",
        '<p><a href="https://bioluxelab.com/products/product-b">Shop Product B</a></p>',
        "B summary",
    )
    products = [
        {
            "id": "gid://shopify/Product/101",
            "legacyResourceId": "101",
            "title": "Product A",
            "handle": "product-a",
            "descriptionHtml": "<p>Original copy</p>",
            "guideTitle": None,
            "guideUrl": None,
            "guideExcerpt": None,
        },
        {
            "id": "gid://shopify/Product/102",
            "legacyResourceId": "102",
            "title": "Product B",
            "handle": "product-b",
            "descriptionHtml": '<a href="/blogs/inside-the-products/guide-b">Guide B</a>',
            "guideTitle": {"value": "Guide B"},
            "guideUrl": {"value": article_b.article_url},
            "guideExcerpt": {"value": "B summary"},
        },
    ]
    provenance = {
        "/blogs/inside-the-products/guide-a": {"product-a"},
    }

    async def fake_load_sources(_store, _blog_handle):
        return products, [article_a, article_b], provenance

    calls: list[tuple[str, str]] = []

    async def fake_metafields(_store, product_id, _title, _url, _excerpt):
        calls.append(("metafields", product_id))

    async def fake_description(_store, product_id, _handle, _body, _title, _url):
        calls.append(("description", product_id))
        return "added missing Related Guide link"

    async def fake_article_link(_store, article, _title, _url):
        calls.append(("article", str(article.id)))

    monkeypatch.setattr(product_blog_sync, "_load_sources", fake_load_sources)
    monkeypatch.setattr(
        product_blog_sync.shopify_client,
        "set_related_product_guide_metafields_by_id",
        fake_metafields,
    )
    monkeypatch.setattr(
        product_blog_sync.shopify_client,
        "sync_product_description_guide_link",
        fake_description,
    )
    monkeypatch.setattr(
        product_blog_sync.shopify_client,
        "append_product_link_to_article",
        fake_article_link,
    )

    result = await product_blog_sync.reconcile_product_blogs(_store())

    by_handle = {item["product_handle"]: item for item in result["products"]}
    assert by_handle["product-a"]["status"] == "repaired"
    assert by_handle["product-b"]["status"] == "matched"
    assert calls == [
        ("metafields", "gid://shopify/Product/101"),
        ("description", "101"),
        ("article", "1"),
    ]
    assert result["counts"]["repaired"] == 1
    assert result["counts"]["matched"] == 1
    assert result["counts"]["orphans"] == 0


@pytest.mark.asyncio
async def test_reconcile_never_guesses_from_matching_title(monkeypatch) -> None:
    article = _article(1, "same-title", "Same Product", "<p>No product CTA.</p>", "Summary")
    products = [
        {
            "id": "gid://shopify/Product/101",
            "legacyResourceId": "101",
            "title": "Same Product",
            "handle": "same-product",
            "descriptionHtml": "",
            "guideTitle": None,
            "guideUrl": None,
            "guideExcerpt": None,
        }
    ]

    async def fake_load_sources(_store, _blog_handle):
        return products, [article], {}

    monkeypatch.setattr(product_blog_sync, "_load_sources", fake_load_sources)
    result = await product_blog_sync.reconcile_product_blogs(_store(), repair=False)

    assert result["products"][0]["status"] == "missing"
    assert result["products"][0]["article_url"] == ""
    assert result["counts"]["orphans"] == 1


@pytest.mark.asyncio
async def test_reconcile_reports_duplicate_article_claims_without_repair(monkeypatch) -> None:
    body = '<p><a href="/products/product-a">Shop Product A</a></p>'
    products = [
        {
            "id": "gid://shopify/Product/101",
            "legacyResourceId": "101",
            "title": "Product A",
            "handle": "product-a",
            "descriptionHtml": "",
            "guideTitle": None,
            "guideUrl": None,
            "guideExcerpt": None,
        }
    ]

    async def fake_load_sources(_store, _blog_handle):
        return products, [
            _article(1, "guide-a-one", "Guide A One", body, "One"),
            _article(2, "guide-a-two", "Guide A Two", body, "Two"),
        ], {}

    monkeypatch.setattr(product_blog_sync, "_load_sources", fake_load_sources)
    result = await product_blog_sync.reconcile_product_blogs(_store())

    assert result["products"][0]["status"] == "duplicate"
    assert "2 articles claim this product" in result["products"][0]["issues"][0]
    assert result["counts"]["duplicate"] == 1

