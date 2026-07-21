from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

from services.landing_pages.social_publisher.landing_page import LandingPagePublisher
from services.landing_pages.social_publisher.pipeline import SocialPublisher
from services.landing_pages.social_publisher.rss_feed import (
    read_product_section,
    write_product_section,
)
from routes.landing_pages import (
    GeneratePromptsRequest,
    _apply_store_grok_model,
    landing_page_product_summary,
)
from services.landing_pages.product_prompts.models import BlogContent, Campaign, Product
from services.landing_pages.product_prompts.prompting.grok import (
    GrokPromptGenerator,
    _product_evidence,
)
from services.landing_pages.product_prompts.prompting.template import (
    TemplatePromptGenerator,
    infer_target_sex,
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


def _product(title: str, description: str = "") -> Product:
    return Product(
        url="https://store.test/products/item",
        handle=title.lower().replace(" ", "-"),
        title=title,
        description_text=description,
    )


def test_persona_uses_explicit_mens_title_over_female_blog_copy() -> None:
    product = _product(
        "Mens Home Training Fitness Equipment",
        "A home fitness solution discussed by men and women.",
    )
    blog = BlogContent(text="Sarah explains why women enjoy convenient home workouts.")
    generator = TemplatePromptGenerator(SimpleNamespace())

    persona = generator.build_persona(product, blog)

    assert infer_target_sex(product, blog) == "man"
    assert persona.sex == "man"
    assert persona.name == "David"


def test_persona_uses_explicit_womens_title_over_male_description() -> None:
    product = _product("Women's Recovery Support", "Men also use recovery products.")
    persona = TemplatePromptGenerator(SimpleNamespace()).build_persona(product, BlogContent())
    assert persona.sex == "woman"


def test_grok_persona_conflict_is_rejected() -> None:
    product = _product("Mens Home Training Fitness Equipment")
    generator = GrokPromptGenerator(SimpleNamespace(grok_api_key="test"), object())
    generator._call_bundle = lambda *_args: {
        "persona": {"name": "Sarah", "age": 38, "sex": "woman"},
        "concepts": [],
        "landing_page_plan": {},
    }

    persona, outputs, _plan = generator.generate_bundle(
        product, BlogContent(), [], Campaign()
    )

    assert persona.sex == "man"
    assert persona.name == "David"
    assert outputs == []


def test_grok_persona_receives_all_product_and_blog_evidence() -> None:
    product = Product(
        url="https://store.test/products/mens-trainer",
        handle="mens-trainer",
        title="Mens Home Trainer",
        description_text="Compact resistance trainer for home workouts.",
        vendor="BioLuxe Lab",
        product_type="Fitness equipment",
        tags=["men", "strength", "home gym"],
        price="79.99",
        currency="GBP",
    )
    blog = BlogContent(
        title="Building a consistent strength routine",
        text="Designed for busy adults who need a quick workout before work.",
    )

    evidence = _product_evidence(product, blog)

    assert "Mens Home Trainer" in evidence
    assert "Fitness equipment" in evidence
    assert "men, strength, home gym" in evidence
    assert "79.99 GBP" in evidence
    assert "Compact resistance trainer" in evidence
    assert "Building a consistent strength routine" in evidence
    assert "busy adults who need a quick workout" in evidence


def test_landing_prompt_requests_use_grok_by_default() -> None:
    request = GeneratePromptsRequest(product_url="https://store.test/products/item")
    assert request.generator == "grok"


def test_store_grok_model_is_reused_by_landing_generator() -> None:
    settings = SimpleNamespace(
        grok_api_key=None,
        grok_base_url="",
        grok_model="",
        grok_timeout=300,
    )
    applied = _apply_store_grok_model(
        settings,
        {
            "id": "model-1",
            "store_id": "store-1",
            "name": "Grok",
            "provider": "openai",
            "model_type": "text",
            "model_name": "grok-latest",
            "api_key": "secret",
            "endpoint": "https://api.x.ai",
            "extra_json": '{"timeout": 90}',
            "priority": 0,
            "is_active": 1,
        },
    )
    assert applied is True
    assert settings.grok_api_key == "secret"
    assert settings.grok_base_url == "https://api.x.ai/v1"
    assert settings.grok_model == "grok-4.3"
    assert settings.grok_timeout == 90


def test_grok_chat_includes_product_images_for_persona_evidence() -> None:
    session = FakeSession(
        [{"choices": [{"message": {"content": '{"ok": true}'}}]}]
    )
    settings = SimpleNamespace(
        grok_api_key="secret",
        grok_base_url="https://api.x.ai/v1",
        grok_model="grok-4.3",
        grok_timeout=90,
    )
    generator = GrokPromptGenerator(settings, session)

    assert generator._chat("Analyse this product", ["https://cdn.test/product.jpg"]) == {
        "ok": True
    }
    content = session.calls[0]["json"]["messages"][1]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"] == "https://cdn.test/product.jpg"
    assert content[-1] == {"type": "text", "text": "Analyse this product"}


def test_strength_product_fallback_is_not_default_woman() -> None:
    product = _product(
        "Build Stronger Grip Daily with This Easy Hand Trainer",
        "Improve grip strength for weightlifting and forearm training.",
    )
    persona = TemplatePromptGenerator(SimpleNamespace()).build_persona(
        product, BlogContent()
    )
    assert persona.sex == "man"
    assert persona.name == "David"


def test_neutral_fallback_has_no_gender_default() -> None:
    product = _product("Portable Wellness Carry Case", "Keeps daily items organised.")
    persona = TemplatePromptGenerator(SimpleNamespace()).build_persona(
        product, BlogContent()
    )
    assert persona.sex == "person"
    assert persona.name == "Alex"


def test_product_summary_uses_full_shopify_handle_and_publication_url(tmp_path: Path) -> None:
    json_file = tmp_path / "a-very-long-truncated-storage-handle.json"
    full_handle = "a-very-long-shopify-product-handle-that-is-not-the-storage-filename"
    summary = landing_page_product_summary(
        json_file,
        {
            "product": {"handle": full_handle, "title": "Product"},
            "creative_concepts": [{"concept": "One"}, {"concept": "Two"}],
            "landing_page_publication": {
                "published_at": "2026-07-17T10:00:00+00:00",
                "page": {
                    "url": "https://store.test/pages/product-offer",
                    "handle": "product-offer",
                    "title": "Product - Special Offer",
                    "action": "created",
                },
            },
        },
    )

    assert summary["handle"] == full_handle
    assert summary["storage_handle"] == json_file.stem
    assert summary["concepts_generated"] == 2
    assert summary["landing_page"]["url"] == "https://store.test/pages/product-offer"
    assert summary["landing_page"]["published_at"] == "2026-07-17T10:00:00+00:00"


def test_social_concept_filter_accepts_image_slug_and_variation_suffix() -> None:
    publisher = SocialPublisher.__new__(SocialPublisher)
    publisher.concept_filter = {"premium-brand-image"}
    assert publisher._concept_matches_filter("Premium Brand Image") is True

    publisher.concept_filter = {"premium-brand-image_v1"}
    assert publisher._concept_matches_filter("Premium Brand Image") is True

    publisher.concept_filter = {"lifestyle-image"}
    assert publisher._concept_matches_filter("Premium Brand Image") is False


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


def test_landing_selection_does_not_discard_other_rss_concepts() -> None:
    all_concepts = [
        _concept("lifestyle", "Lifestyle", "https://cdn.test/lifestyle.jpg"),
        _concept("benefits", "Benefits", "https://cdn.test/benefits.jpg"),
        _concept("education", "Education", "https://cdn.test/education.jpg"),
        _concept("social-proof", "Social Proof", "https://cdn.test/social-proof.jpg"),
    ]

    landing_concepts = LandingPagePublisher._select_landing_concepts(
        all_concepts,
        ["Benefits", "Lifestyle"],
    )

    assert [item["slug"] for item in landing_concepts] == ["benefits", "lifestyle"]
    assert [item["slug"] for item in all_concepts] == [
        "lifestyle",
        "benefits",
        "education",
        "social-proof",
    ]


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
