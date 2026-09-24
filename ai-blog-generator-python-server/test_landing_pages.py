from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

import db
from services.landing_pages.social_publisher.landing_page import LandingPagePublisher
from services.landing_pages.social_publisher.pipeline import SocialPublisher
from services.landing_pages.social_publisher.rss_feed import (
    read_product_section,
    write_product_section,
)
from services.landing_pages.video_service import LandingPageVideoService
from routes.landing_pages import (
    GeneratePromptsRequest,
    _apply_store_grok_model,
    _configure_store_shopify,
    landing_page_product_summary,
)
from services.landing_pages.product_prompts.blog import BlogScraper
from services.landing_pages.product_prompts.models import (
    BlogContent,
    Campaign,
    ClientPersona,
    Product,
)
from services.landing_pages.product_prompts.prompting.grok import (
    _BRAND_CASTING_RULE,
    GrokGenerationError,
    GrokPromptGenerator,
    _product_evidence,
)
from services.landing_pages.social_publisher.image_backends.grok import (
    _FIDELITY_CLAUSE,
    GrokImageBackend,
)
from services.landing_pages.product_prompts.utils import build_session
from services.xai_billing_service import check_xai_credit
from services.landing_page_jobs import (
    create_job,
    fail_interrupted_jobs,
    get_job,
    update_progress,
)
from services.landing_pages.product_prompts.prompting.template import (
    TemplatePromptGenerator,
    infer_target_sex,
)


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload
        self.status_code = 200
        self.text = ""

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


class FakeDownloadResponse(FakeResponse):
    def __init__(self, payload: dict, content: bytes = b""):
        super().__init__(payload)
        self.content = content


class FakeVideoSession(FakeSession):
    def __init__(self, post_payloads: list[dict], get_responses: list[FakeDownloadResponse]):
        super().__init__(post_payloads)
        self.get_responses = list(get_responses)
        self.get_calls: list[dict] = []

    def get(self, endpoint: str, **kwargs) -> FakeDownloadResponse:
        self.get_calls.append({"endpoint": endpoint, **kwargs})
        return self.get_responses.pop(0)


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

    with pytest.raises(GrokGenerationError) as caught:
        generator.generate_bundle(product, BlogContent(), [], Campaign())

    assert caught.value.error_type == "persona_validation_error"
    assert "requires 'man'" in str(caught.value)
    assert "no template fallback or retry" in str(caught.value)


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


def test_brand_casting_accepts_white_or_mixed_black_white_personas() -> None:
    GrokPromptGenerator._validate_brand_casting(
        ClientPersona(race="White", ethnicity="White")
    )
    GrokPromptGenerator._validate_brand_casting(
        ClientPersona(
            race="Mixed Black and White",
            ethnicity="Mixed Black and White",
        )
    )


def test_brand_casting_rejects_other_or_ambiguous_persona_output() -> None:
    with pytest.raises(GrokGenerationError) as caught:
        GrokPromptGenerator._validate_brand_casting(
            ClientPersona(race="East Asian", ethnicity="East Asian")
        )
    assert caught.value.error_type == "persona_casting_validation_error"
    assert "White' or 'Mixed Black and White" in str(caught.value)
    assert "no fallback or retry" in str(caught.value)


def test_brand_casting_rejects_contradictory_ethnicity_field() -> None:
    with pytest.raises(GrokGenerationError) as caught:
        GrokPromptGenerator._validate_brand_casting(
            ClientPersona(race="White", ethnicity="another category")
        )
    assert caught.value.error_type == "persona_casting_validation_error"
    assert "both fields to match exactly" in str(caught.value)


def test_reference_people_are_always_replaced_not_classified() -> None:
    assert "never identify or classify" in _BRAND_CASTING_RULE
    assert "never preserve or imitate" in _FIDELITY_CLAUSE
    assert "fictional persona specified in the scene prompt" in _FIDELITY_CLAUSE


def test_landing_prompt_requests_use_grok_by_default() -> None:
    request = GeneratePromptsRequest(product_url="https://store.test/products/item")
    assert request.generator == "grok"
    assert request.fetcher == "shopify"


def test_store_shopify_credentials_are_reused_by_landing_fetcher() -> None:
    settings = SimpleNamespace(
        myshopify_domain=None,
        shopify_client_id=None,
        shopify_client_secret=None,
    )
    _configure_store_shopify(settings, {
        "myshopify_domain": "store.myshopify.com",
        "client_id": "client-id",
        "client_secret": "client-secret",
    })
    assert settings.myshopify_domain == "store.myshopify.com"
    assert settings.shopify_client_id == "client-id"
    assert settings.shopify_client_secret == "client-secret"


def test_linked_blog_uses_authenticated_shopify_admin_api() -> None:
    session = FakeSession([])
    session.get_calls = []
    responses = [
        FakeResponse({"blogs": [{"id": 123, "handle": "inside-the-products"}]}),
        FakeResponse({
            "articles": [{
                "title": "MacBook Sleeve Guide",
                "body_html": '<p>Protect your laptop.</p><img src="https://cdn.test/blog.jpg">',
                "image": {"src": "https://cdn.test/hero.jpg"},
            }]
        }),
    ]

    def fake_get(endpoint: str, **kwargs):
        session.get_calls.append({"endpoint": endpoint, **kwargs})
        return responses.pop(0)

    session.get = fake_get
    settings = SimpleNamespace(
        myshopify_domain="store.myshopify.com",
        shopify_access_token="admin-token",
        shopify_api_version="2026-01",
        request_timeout=30,
    )
    article = BlogScraper(settings, session).scrape(
        "https://store.test/blogs/inside-the-products/macbook-sleeve-guide"
    )
    assert article.title == "MacBook Sleeve Guide"
    assert article.text == "Protect your laptop."
    assert article.image_urls == [
        "https://cdn.test/hero.jpg",
        "https://cdn.test/blog.jpg",
    ]
    assert session.get_calls[0]["headers"]["X-Shopify-Access-Token"] == "admin-token"
    assert "/admin/api/2026-01/blogs.json" in session.get_calls[0]["endpoint"]


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


def test_strength_audience_constraint_is_binding_for_grok() -> None:
    from services.landing_pages.product_prompts.prompting.template import audience_constraint

    product = _product(
        "Build Stronger Grip Daily with This Easy Hand Trainer",
        "Improve grip strength and forearm performance for weightlifting.",
    )
    constraint = audience_constraint(product, BlogContent())
    assert "BINDING PRIMARY COMMERCIAL AUDIENCE" in constraint
    assert "MUST be a 'man'" in constraint


def test_missing_grok_key_is_visible_in_diagnostics() -> None:
    settings = SimpleNamespace(grok_api_key=None, grok_model="grok-4.3")
    generator = GrokPromptGenerator(settings, object())
    with pytest.raises(GrokGenerationError) as caught:
        generator.generate_bundle(_product("Neutral Item"), BlogContent(), [], Campaign())
    assert caught.value.error_type == "configuration_error"
    assert "No request was sent" in str(caught.value)


def test_paid_post_requests_are_never_retried() -> None:
    session = build_session("test-agent", max_retries=3)
    retry = session.get_adapter("https://").max_retries
    assert "POST" not in retry.allowed_methods
    assert "GET" in retry.allowed_methods


def test_credit_check_accepts_existing_grok_env_names(monkeypatch) -> None:
    monkeypatch.delenv("XAI_MANAGEMENT_API_KEY", raising=False)
    monkeypatch.delenv("XAI_TEAM_ID", raising=False)
    monkeypatch.setenv("GROK_BILLING_API_KEY", "billing-secret")
    monkeypatch.setenv("GROK_TEAM_ID", "team-1")
    monkeypatch.setenv("XAI_MIN_CREDIT_CENTS", "100")

    class BillingResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"total": {"val": -1250}}

    def fake_get(url, *, headers, timeout):
        assert url.endswith("/v1/billing/teams/team-1/prepaid/balance")
        assert headers["Authorization"] == "Bearer billing-secret"
        assert timeout == 15
        return BillingResponse()

    monkeypatch.setattr("services.xai_billing_service.requests.get", fake_get)
    result = check_xai_credit()
    assert result["can_start"] is True
    assert result["available_cents"] == 1250
    assert result["available_display"] == "$12.50"
    assert result["retry_attempted"] is False


def test_low_credit_blocks_generation_without_retry(monkeypatch) -> None:
    monkeypatch.setenv("GROK_BILLING_API_KEY", "billing-secret")
    monkeypatch.setenv("GROK_TEAM_ID", "team-1")
    monkeypatch.setenv("XAI_MIN_CREDIT_CENTS", "100")

    class BillingResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"total": {"val": -25}}

    monkeypatch.setattr(
        "services.xai_billing_service.requests.get",
        lambda *args, **kwargs: BillingResponse(),
    )
    result = check_xai_credit()
    assert result["status"] == "insufficient_credit"
    assert result["can_start"] is False
    assert "$0.25" in result["message"]
    assert result["retry_attempted"] is False


@pytest.mark.asyncio
async def test_generation_jobs_persist_progress_dedupe_and_interruptions(tmp_path: Path) -> None:
    previous_path = db.get_db_path()
    try:
        db.set_db_path(str(tmp_path / "jobs.db"))
        await db.init_db()
        credit = {"can_start": True, "available_display": "$12.50"}
        first, created = create_job(
            shop="store.myshopify.com",
            store_id="store-1",
            product_url="https://store.test/products/item",
            credit=credit,
        )
        duplicate, duplicate_created = create_job(
            shop="store.myshopify.com",
            store_id="store-1",
            product_url="https://store.test/products/item",
            credit=credit,
        )
        assert created is True
        assert duplicate_created is False
        assert duplicate["id"] == first["id"]

        update_progress(first["id"], "waiting_for_grok", 55, "One request sent.")
        running = get_job(first["id"])
        assert running is not None
        assert running["status"] == "running"
        assert running["progress"] == 55

        assert fail_interrupted_jobs() == 1
        interrupted = get_job(first["id"])
        assert interrupted is not None
        assert interrupted["status"] == "failed"
        assert interrupted["error_type"] == "job_interrupted"
        assert "not retried" in interrupted["error_message"]
    finally:
        db.set_db_path(previous_path)


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


def test_grok_multi_image_edit_uses_xai_images_object_array() -> None:
    session = FakeSession(
        [{"data": [{"b64_json": base64.b64encode(b"generated").decode("ascii")}]}]
    )
    settings = SimpleNamespace(
        grok_api_key="test-key",
        grok_base_url="https://api.x.ai/v1",
        grok_timeout=30,
        grok_image_model="grok-imagine-image",
        grok_image_quality_model="grok-imagine-image-quality",
    )
    backend = GrokImageBackend(settings, session)

    images = backend.generate(
        "Create a product advert",
        reference_images=[b"first-reference", b"second-reference"],
    )

    payload = session.calls[0]["json"]
    assert "image" not in payload
    assert [entry["type"] for entry in payload["images"]] == [
        "image_url",
        "image_url",
    ]
    assert all(entry["url"].startswith("data:image/jpeg;base64,") for entry in payload["images"])
    assert images[0].data == b"generated"


def test_social_publisher_records_exact_concept_failure() -> None:
    publisher = SocialPublisher.__new__(SocialPublisher)
    publisher.concept_filter = None
    publisher.failures = []
    publisher._load_references = lambda *_args: [b"reference"]
    publisher._process_concept = lambda *_args: (_ for _ in ()).throw(
        RuntimeError("xAI returned HTTP 400: invalid images field")
    )

    produced = publisher._process_product(
        Path("example-product.json"),
        {
            "creative_concepts": [{"concept": "Lifestyle Proof"}],
            "product": {},
            "persona": {},
            "campaign": {},
        },
        Path("social"),
    )

    assert produced == []
    assert publisher.failures == [
        "Lifestyle Proof: xAI returned HTTP 400: invalid images field"
    ]


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


def test_video_script_uses_grok_43_and_accepts_model_chosen_duration() -> None:
    script = {
        "duration_seconds": 11,
        "hook": "Stop the scroll",
        "campaign_goal": "Demonstrate the product",
        "scenes": [{"start_second": 0, "end_second": 11, "visual_action": "Orbit"}],
        "audio_direction": "Modern",
        "final_cta": "Shop now",
        "spoken_script": "I use this every day because it makes my routine feel much easier.",
        "video_prompt": "An exact 11-second handheld UGC product demonstration.",
        "posting_text": "See it in action. #wellness",
    }
    session = FakeSession(
        [{"choices": [{"message": {"content": __import__("json").dumps(script)}}]}]
    )
    settings = SimpleNamespace(
        grok_api_key="secret",
        grok_base_url="https://api.x.ai/v1",
        grok_model="some-other-store-model",
        grok_timeout=90,
    )
    service = LandingPageVideoService(settings, session)

    result = service.create_script(
        {"product": {"title": "Product"}, "persona": {}, "campaign": {}},
        {"concept": "Lifestyle"},
        "Original posting text",
    )

    assert result["duration_seconds"] == 11
    assert result["model"] == "grok-4.3"
    assert result["spoken_script"] in result["video_prompt"]
    assert "MANDATORY UGC SPEECH AND LIP-SYNC" in result["video_prompt"]
    assert session.calls[0]["json"]["model"] == "grok-4.3"
    assert "6-to-12-second" in session.calls[0]["json"]["messages"][1]["content"]


def test_video_script_rejects_missing_ugc_speech_but_preserves_long_speech() -> None:
    with pytest.raises(RuntimeError, match="no recoverable scene speech"):
        LandingPageVideoService._validate_spoken_script("", 8)

    long_script = " ".join(["word"] * 30)
    assert LandingPageVideoService._validate_spoken_script(long_script, 8) == long_script
    assert "paid script was preserved" in (
        LandingPageVideoService._speech_fit_warning(long_script, 8).lower()
    )


def test_video_script_recovers_dialogue_from_scene_speech() -> None:
    script = {
        "scenes": [
            {"speech": "I tried this in my daily routine."},
            {"speech": "It is simple, practical, and easy to carry."},
        ]
    }
    assert LandingPageVideoService._spoken_script_from_response(script, 10) == (
        "I tried this in my daily routine. "
        "It is simple, practical, and easy to carry."
    )


def test_rss_includes_video_as_separate_duplicate_safe_entry(tmp_path: Path) -> None:
    feed_path = tmp_path / "social" / "feed.xml"
    concepts = [_concept("lifestyle", "Lifestyle", "https://cdn.test/lifestyle.jpg")]
    videos = [{
        "slug": "lifestyle",
        "concept": "Lifestyle",
        "cdn_url": "https://cdn.test/lifestyle.mp4",
        "posting_text": "Watch the product in action.",
    }]

    first = write_product_section(
        feed_path,
        handle="product-video",
        product_title="Product Video",
        landing_page_url="https://store.test/pages/product-video",
        concepts=concepts,
        videos=videos,
    )
    second = write_product_section(
        feed_path,
        handle="product-video",
        product_title="Product Video",
        landing_page_url="https://store.test/pages/product-video",
        concepts=concepts,
        videos=videos,
    )

    assert first["entry_count"] == 2
    assert second["entry_count"] == 2
    assert second["replaced_count"] == 2
    video_entry = next(item for item in second["entries"] if item["media_type"] == "video/mp4")
    assert video_entry["guid"] == "landing-page:product-video:video:lifestyle"
    assert video_entry["video_url"] == "https://cdn.test/lifestyle.mp4"
    section = read_product_section(feed_path, "product-video")
    assert section["entry_count"] == 2
    assert section["entries"][1]["video_url"] == "https://cdn.test/lifestyle.mp4"


def test_video_render_uses_creative_image_grok_video_and_480p(tmp_path: Path) -> None:
    image_path = tmp_path / "product__lifestyle.jpg"
    image_path.write_bytes(b"creative-image-bytes")
    output_path = tmp_path / "product__lifestyle.mp4"
    session = FakeVideoSession(
        [{"request_id": "video-request-1"}],
        [
            FakeDownloadResponse({
                "status": "done",
                "model": "grok-imagine-video",
                "video": {"url": "https://video.test/result.mp4", "duration": 9},
            }),
            FakeDownloadResponse({}, content=b"generated-mp4"),
        ],
    )
    settings = SimpleNamespace(
        grok_api_key="secret",
        grok_base_url="https://api.x.ai/v1",
        grok_video_model="grok-imagine-video",
        grok_timeout=90,
    )

    result = LandingPageVideoService(settings, session).generate_video(
        {
            "duration_seconds": 9,
            "spoken_script": "This fits naturally into my routine and feels so easy to use.",
            "video_prompt": "Exact nine-second handheld UGC product sequence",
        },
        image_path,
        output_path,
    )

    request = session.calls[0]["json"]
    assert request["model"] == "grok-imagine-video"
    assert request["duration"] == 9
    assert request["resolution"] == "480p"
    assert request["aspect_ratio"] == "9:16"
    assert request["image"]["url"].startswith("data:image/jpeg;base64,")
    assert "Script (speak exactly, with no added or omitted words):" in request["prompt"]
    assert "This fits naturally into my routine" in request["prompt"]
    assert "Synchronize every mouth movement precisely" in request["prompt"]
    assert output_path.read_bytes() == b"generated-mp4"
    assert result["video_file"] == "product__lifestyle.mp4"


def test_shopify_video_upload_uses_video_staging_and_returns_mp4(tmp_path: Path) -> None:
    video_path = tmp_path / "creative.mp4"
    video_path.write_bytes(b"mp4-data")
    publisher = publisher_with_payloads(
        [
            {"data": {"stagedUploadsCreate": {"stagedTargets": [{
                "url": "https://upload.test",
                "resourceUrl": "https://staged.test/video",
                "parameters": [],
            }], "userErrors": []}}},
            {},
            {"data": {"fileCreate": {"files": [{"id": "gid://shopify/Video/1"}], "userErrors": []}}},
            {"data": {"node": {"fileStatus": "READY", "sources": [
                {"url": "https://cdn.test/creative.m3u8", "mimeType": "application/x-mpegURL", "format": "m3u8"},
                {"url": "https://cdn.test/creative.mp4", "mimeType": "video/mp4", "format": "mp4"},
            ]}}},
        ]
    )

    file_id, video_url = publisher._upload_video(video_path)

    assert file_id == "gid://shopify/Video/1"
    assert video_url == "https://cdn.test/creative.mp4"
    staged_input = publisher.session.calls[0]["json"]["variables"]["input"][0]
    assert staged_input["resource"] == "VIDEO"
    assert staged_input["fileSize"] == str(video_path.stat().st_size)
    create_input = publisher.session.calls[2]["json"]["variables"]["files"][0]
    assert create_input == {
        "alt": "creative",
        "contentType": "VIDEO",
        "originalSource": "https://staged.test/video",
    }
