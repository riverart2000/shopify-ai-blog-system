from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

from services.landing_pages.social_publisher.landing_page import LandingPagePublisher
from services.landing_pages.social_publisher.rss_feed import (
    read_product_section,
    write_product_section,
)


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def json(self) -> dict:
        return self.payload

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, payloads: list[dict]):
        self.payloads = list(payloads)
        self.calls: list[dict] = []

    def post(self, endpoint: str, **kwargs) -> FakeResponse:
        self.calls.append({"endpoint": endpoint, **kwargs})
        return FakeResponse(self.payloads.pop(0))


def publisher_with_payloads(payloads: list[dict]) -> LandingPagePublisher:
    publisher = LandingPagePublisher.__new__(LandingPagePublisher)
    publisher.session = FakeSession(payloads)
    publisher.endpoint = "https://store.test/graphql.json"
    publisher.token = "test-token"
    publisher.settings = SimpleNamespace(
        myshopify_domain="store.myshopify.com",
        storefront_domain="https://storefront.test/",
    )
    return publisher


def test_page_upsert_updates_legacy_title_match_and_normalises_seo() -> None:
    title = "Product - 20% OFF"
    publisher = publisher_with_payloads(
        [
            {"data": {"pages": {"nodes": []}}},
            {
                "data": {
                    "pages": {
                        "nodes": [
                            {
                                "id": "gid://shopify/Page/1",
                                "title": title,
                                "handle": "legacy-title-handle",
                            }
                        ]
                    }
                }
            },
            {
                "data": {
                    "pageUpdate": {
                        "page": {
                            "id": "gid://shopify/Page/1",
                            "title": title,
                            "handle": "legacy-title-handle",
                        },
                        "userErrors": [],
                    }
                }
            },
            {"data": {"metafieldsSet": {"metafields": [], "userErrors": []}}},
        ]
    )

    page_id, url, action, handle = publisher._upsert_page(
        title,
        "product-offer",
        "<p>Landing page</p>",
        True,
        "Description with\nmultiple\tlines",
    )

    assert page_id == "gid://shopify/Page/1"
    assert action == "updated"
    assert handle == "legacy-title-handle"
    assert url == "https://storefront.test/pages/legacy-title-handle"
    update_variables = publisher.session.calls[2]["json"]["variables"]
    assert update_variables["page"]["handle"] == "legacy-title-handle"
    seo_values = publisher.session.calls[3]["json"]["variables"]["metafields"]
    description = next(value for value in seo_values if value["key"] == "description_tag")
    assert description["value"] == "Description with multiple lines"


def test_page_upsert_creates_one_page_with_stable_handle() -> None:
    publisher = publisher_with_payloads(
        [
            {"data": {"pages": {"nodes": []}}},
            {"data": {"pages": {"nodes": []}}},
            {
                "data": {
                    "pageCreate": {
                        "page": {
                            "id": "gid://shopify/Page/2",
                            "title": "Product - Special Offer",
                            "handle": "product-offer",
                        },
                        "userErrors": [],
                    }
                }
            },
            {"data": {"metafieldsSet": {"metafields": [], "userErrors": []}}},
        ]
    )

    page_id, url, action, handle = publisher._upsert_page(
        "Product - Special Offer",
        "product-offer",
        "<p>Landing page</p>",
        True,
        "SEO description",
    )

    assert page_id == "gid://shopify/Page/2"
    assert action == "created"
    assert handle == "product-offer"
    assert url == "https://storefront.test/pages/product-offer"
    create_variables = publisher.session.calls[2]["json"]["variables"]
    assert create_variables["page"]["handle"] == "product-offer"


def _concept(slug: str, name: str, image_url: str) -> dict:
    return {
        "slug": slug,
        "cdn_url": image_url,
        "concept": {"concept": name, "social_text": f"Caption for {name}"},
    }


def test_rss_product_section_is_replaced_without_duplicates(tmp_path: Path) -> None:
    feed_path = tmp_path / "social" / "feed.xml"
    concepts = [
        _concept("lifestyle", "Lifestyle", "https://cdn.test/lifestyle.jpg"),
        _concept("benefits", "Benefits", "https://cdn.test/benefits.jpg"),
    ]

    first = write_product_section(
        feed_path,
        handle="product-one",
        product_title="Product One",
        landing_page_url="https://store.test/pages/product-one",
        concepts=concepts,
    )
    second = write_product_section(
        feed_path,
        handle="product-one",
        product_title="Product One",
        landing_page_url="https://store.test/pages/product-one",
        concepts=concepts,
    )

    assert first["action"] == "created"
    assert second["action"] == "updated"
    assert second["duplicate_prevented"] is True
    assert second["replaced_count"] == 2
    assert second["entry_count"] == 2

    tree = ET.parse(feed_path)
    guids = [item.findtext("guid") for item in tree.getroot().findall("channel/item")]
    assert guids == [
        "landing-page:product-one:lifestyle",
        "landing-page:product-one:benefits",
    ]

    section = read_product_section(feed_path, "product-one")
    assert section["entry_count"] == 2
    assert section["entries"][0]["image_url"] == "https://cdn.test/lifestyle.jpg"
