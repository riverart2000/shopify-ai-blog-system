"""Social Engine AI layer: LLM router, captions, guards, review, decisions, UTM.

Unit tests never touch the real model: every HTTP call goes through a fake
transport in `TRANSPORT_OVERRIDES["llm"]`. Set SOCIAL_ENGINE_LIVE_LLM=1 to run
the live test against the local Gemma server.
"""
from __future__ import annotations

import json
import logging
import os
import re

import httpx
import pytest
import pytest_asyncio
from pydantic import BaseModel

import db
from db.social_engine import observability
from services.social_engine import http as se_http
from services.social_engine.ai import decisions
from services.social_engine.ai.base import (
    CaptionChannel, CaptionRequest, CaptionResult, ChannelCaption, LLMRequest,
)
from services.social_engine.ai.captions import generate_captions, template_captions
from services.social_engine.ai.guard import check_captions
from services.social_engine.ai.llm import OpenAICompatibleProvider, check_llm, get_router
from services.social_engine.ai.review import review_post
from services.social_engine.ai.text import (
    HASHTAG_RE, URL_RE, count_emojis, extract_json, measure_for, normalise_hashtag, strip_reasoning, x_length,
)
from services.social_engine.context import load_store_context
from services.social_engine.errors import ExternalServiceError, ValidationError
from services.social_engine.models import PROVIDER_PROFILES, profile_for
from services.social_engine.settings import AISettings, UtmSettings
from services.social_engine.utm import slugify, tracked_url

MAGNESIUM = {
    "product_id": "8001", "handle": "magnesium-glycinate", "title": "Magnesium Glycinate 400mg",
    "description": "<p>400mg of elemental magnesium per serving in the gentle bisglycinate form. Magnesium "
                   "contributes to a reduction of tiredness and fatigue. Vegan capsules, made in the UK.</p>",
    "product_type": "Supplement", "vendor": "BioluxeLab",
    "tags": ["magnesium", "evening ritual", "no-social-ads"],
    "collections": [{"id": "gid://shopify/Collection/11", "title": "Sleep & Recovery"}],
    "price_min": 24.99, "price_max": 24.99, "compare_at_max": None, "on_sale": False, "discount_pct": 0,
    "available": True, "url": "https://bioluxelab.com/products/magnesium-glycinate",
}
PRODUCT_URL = "https://bioluxelab.com/products/magnesium-glycinate?utm_source=social&utm_medium=social"


# ---------------------------------------------------------------------------
# Fake model server
# ---------------------------------------------------------------------------

def chat_response(content: str, *, model: str = "gemma-4-26b-a4b-it-8bit", finish: str = "stop") -> httpx.Response:
    return httpx.Response(200, json={
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    })


class FakeLLM:
    """Replies in order (the last reply repeats); records every request."""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content or b"{}") if request.method == "POST" else {}
        self.bodies.append(body)
        reply = self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]
        if callable(reply) and not isinstance(reply, httpx.Response):
            reply = reply(request, body)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return chat_response(reply if isinstance(reply, str) else json.dumps(reply))

    def install(self) -> "FakeLLM":
        se_http.TRANSPORT_OVERRIDES["llm"] = httpx.MockTransport(self)
        return self

    def prompt(self, index: int = -1) -> str:
        return self.bodies[index]["messages"][-1]["content"]


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    monkeypatch.setattr(se_http.ServiceClient, "_sleep_for", lambda self, attempt, response: 0.0)


@pytest_asyncio.fixture
async def sctx(se_db):
    return await load_store_context(se_db, correlation_id="post_test")


def caption_request(providers: list[str], **kwargs) -> CaptionRequest:
    channels = kwargs.pop("channels", None) or [
        CaptionChannel(channel_id=f"ch_{provider}", provider=provider, link_url=PRODUCT_URL) for provider in providers
    ]
    return CaptionRequest(
        store_id="store_1", content_type=kwargs.pop("content_type", "single_product"),
        products=kwargs.pop("products", [MAGNESIUM]), channels=channels, correlation_id="post_test", **kwargs,
    )


def good_caption_payload(**channels) -> dict:
    return {
        "headline": "A calm evening ritual", "subheadline": "Gentle magnesium glycinate", "cta": "Shop the range",
        "hook": "Your evening, simplified", "alt_text": "Magnesium Glycinate 400mg capsules",
        "base_caption": "Magnesium contributes to a reduction of tiredness and fatigue. 🌙",
        "hashtags": ["MagnesiumGlycinate", "EveningRitual"],
        "channels": {key: {"caption": value} for key, value in channels.items()},
    }


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provider_request_shape_json_mode_and_auth_only_with_key(se_db):
    fake = FakeLLM('{"ok": true}').install()
    provider = OpenAICompatibleProvider("http://gemma.test/v1", "gemma-x", timeout=30)
    response = await provider.complete(
        LLMRequest(system="sys", prompt="hello", temperature=0.2, max_tokens=99),
        se_http.CallContext(store_id=se_db, correlation_id="c-shape"), operation="llm.test",
    )
    request, body = fake.requests[0], fake.bodies[0]
    assert request.url.path == "/v1/chat/completions" and "authorization" not in request.headers
    assert body["model"] == "gemma-x" and body["response_format"] == {"type": "json_object"}
    assert body["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]
    assert body["temperature"] == 0.2 and body["max_tokens"] == 99
    assert response.text == '{"ok": true}' and response.provider == "gemma" and response.usage["completion_tokens"] == 20
    ledger = await observability.list_api_calls(correlation_id="c-shape")
    assert ledger[0]["service"] == "llm" and ledger[0]["operation"] == "llm.test"

    keyed = OpenAICompatibleProvider("http://gemma.test/v1", "gemma-x", api_key="secret-key")
    await keyed.complete(LLMRequest(prompt="hi", json_mode=False), se_http.CallContext())
    assert fake.requests[1].headers["authorization"] == "Bearer secret-key"
    assert "response_format" not in fake.bodies[1]


@pytest.mark.asyncio
async def test_provider_sends_vision_images_as_data_uri_parts(se_db, tmp_path):
    from PIL import Image

    image_path = tmp_path / "creative.png"
    Image.new("RGB", (2000, 1200), (40, 87, 70)).save(image_path)
    fake = FakeLLM('{"product_visible": true}').install()
    provider = OpenAICompatibleProvider("http://gemma.test/v1", "vision-model")
    await provider.complete(LLMRequest(prompt="look", images=[str(image_path)]), se_http.CallContext())
    content = fake.bodies[0]["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": "look"}
    assert content[1]["type"] == "image_url" and content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    ledger = await observability.list_api_calls(service="llm")
    assert "base64," not in ledger[0]["request_excerpt"]   # image data is redacted from the ledger


@pytest.mark.asyncio
async def test_think_tags_and_fences_are_stripped(se_db):
    FakeLLM('<think>Plan: be brief.</think>\n```json\n{"a": 1,}\n```').install()
    provider = OpenAICompatibleProvider("http://gemma.test/v1", "gemma-x")
    response = await provider.complete(LLMRequest(prompt="x"), se_http.CallContext())
    assert "<think>" not in response.text and extract_json(response.text) == {"a": 1}
    assert strip_reasoning("reasoning…</think>{\"b\": 2}") == '{"b": 2}'
    assert extract_json('Sure! Here [is] it: {"x": "a}b", "y": [1, 2]} hope that helps') == {"x": "a}b", "y": [1, 2]}
    with pytest.raises(ValueError):
        extract_json("no json here")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

async def _register_fallback(sctx, monkeypatch, reply):
    """A Models-page fallback whose provider is monkeypatched — no paid call is made."""
    import providers

    await db.upsert_model({
        "id": "m-opus", "store_id": "store_1", "name": "Claude Opus (Replicate)", "provider": "replicate",
        "model_type": "text", "model_name": "anthropic/claude-opus", "api_key": "", "endpoint": "",
        "extra_json": "{}", "priority": 0, "is_active": 1,
    })
    sctx.settings.ai.fallback_model_id = "m-opus"
    calls: list[str] = []

    class _FakeTextProvider:
        def __init__(self, model):
            self.model = model

        async def generate_raw(self, prompt: str, system_prompt: str = "") -> str:
            calls.append(prompt)
            if isinstance(reply, Exception):
                raise reply
            return reply

    monkeypatch.setattr(providers, "get_text_provider", lambda record: _FakeTextProvider(record))
    return calls


@pytest.mark.asyncio
async def test_router_falls_back_to_registry_model_when_gemma_is_unreachable(sctx, monkeypatch):
    fake = FakeLLM(httpx.ConnectError("refused")).install()
    calls = await _register_fallback(sctx, monkeypatch, '{"answer": "from fallback"}')
    router = get_router(sctx)
    response, used_fallback = await router.complete_with_meta(
        "captions", LLMRequest(prompt="hi"), ctx=sctx.call_context("c-fb"),
    )
    assert used_fallback and response.provider == "registry" and response.model == "anthropic/claude-opus"
    assert len(fake.requests) == 2 and len(calls) == 1   # Gemma retried once (local, free); fallback once
    assert "Return only one valid JSON object" in calls[0]
    ledger = await observability.list_api_calls(correlation_id="c-fb")
    assert [(row["url_path"].startswith("registry/"), row["ok"]) for row in ledger] == [(True, 1), (False, 0), (False, 0)]


@pytest.mark.asyncio
async def test_router_error_names_both_failures(sctx, monkeypatch):
    from providers import ProviderError

    FakeLLM(httpx.ConnectError("refused")).install()
    await _register_fallback(sctx, monkeypatch, ProviderError("Replicate API token is not configured", retryable=False))
    with pytest.raises(ExternalServiceError) as info:
        await get_router(sctx).complete("captions", LLMRequest(prompt="hi"), ctx=sctx.call_context())
    message = info.value.message
    assert "gemma (gemma-4-26b-a4b-it-8bit)" in message and "ConnectError" in message
    assert "registry (anthropic/claude-opus)" in message and "token is not configured" in message
    assert info.value.retryable is True   # Gemma being down is transient


class _Answer(BaseModel):
    answer: str
    score: int


@pytest.mark.asyncio
async def test_structured_repair_round_and_decision_log(sctx):
    fake = FakeLLM('{"answer": "yes"}', '{"answer": "yes", "score": 7}').install()
    instance, meta = await get_router(sctx).structured(
        "decide.test", LLMRequest(prompt="Decide."), _Answer, ctx=sctx.call_context("c-rep"),
        decision_type="test_decision", subject_id="subject-1",
    )
    assert instance.score == 7 and meta.repaired and not meta.used_fallback
    assert "could not be used" in fake.prompt(1) and "score" in fake.prompt(1)
    assert fake.bodies[1]["temperature"] <= 0.3
    ledger = await observability.list_api_calls(correlation_id="c-rep")
    assert [row["operation"] for row in ledger] == ["llm.decide.test.repair", "llm.decide.test"]
    rows = await observability.list_decisions(subject_id="subject-1")
    assert rows[0]["decision_type"] == "test_decision" and rows[0]["valid"] == 1
    assert rows[0]["output"]["output"] == {"answer": "yes", "score": 7}
    assert [a["outcome"] for a in rows[0]["output"]["attempts"]] == ["invalid", "valid"]


@pytest.mark.asyncio
async def test_structured_invalid_twice_raises_validation_error(sctx):
    FakeLLM("not json at all").install()
    with pytest.raises(ValidationError) as info:
        await get_router(sctx).structured(
            "decide.test", LLMRequest(prompt="x"), _Answer, ctx=sctx.call_context(), subject_id="s-bad",
        )
    assert info.value.retryable is False and "valid JSON" in info.value.message
    rows = await observability.list_decisions(subject_id="s-bad")
    assert rows[0]["valid"] == 0 and "invalid" in rows[0]["error"]


@pytest.mark.asyncio
async def test_structured_unreachable_is_retryable_external_error(sctx):
    FakeLLM(httpx.ConnectError("refused")).install()
    with pytest.raises(ExternalServiceError) as info:
        await get_router(sctx).structured("decide.test", LLMRequest(prompt="x"), _Answer, ctx=sctx.call_context())
    assert info.value.retryable is True and "no fallback model is configured" in info.value.message


@pytest.mark.asyncio
async def test_structured_uses_fallback_after_invalid_primary(sctx, monkeypatch):
    FakeLLM('{"answer": 1}').install()
    calls = await _register_fallback(sctx, monkeypatch, 'Here you go:\n```json\n{"answer": "ok", "score": 9}\n```')
    instance, meta = await get_router(sctx).structured(
        "decide.test", LLMRequest(prompt="x"), _Answer, ctx=sctx.call_context(), subject_id="s-fb",
    )
    assert instance.score == 9 and meta.used_fallback and len(calls) == 1
    row = (await observability.list_decisions(subject_id="s-fb"))[0]
    assert row["used_fallback"] == 1 and row["model"] == "anthropic/claude-opus"


@pytest.mark.asyncio
async def test_check_llm_reports_status(se_db):
    def models(request, body):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "gemma-4-26b-a4b-it-8bit"}]})
        return chat_response('{"status": "ok"}')

    FakeLLM(models).install()
    report = await check_llm(AISettings())
    assert report["ok"] and report["status"] == "ok" and report["model_listed"] and not report["vision_model_listed"]
    assert report["latency_ms"] is not None
    missing = await check_llm(AISettings(model="not-served"))
    assert missing["status"] == "model_missing" and "not-served" in missing["error"]
    FakeLLM(httpx.ConnectError("refused")).install()
    down = await check_llm(AISettings())
    assert down["status"] == "unreachable" and not down["ok"]


@pytest.mark.asyncio
async def test_doctor_gemma_check_live(sctx):
    """The live doctor check must not crash on check_llm's own status/message keys."""
    from services.social_engine.health import _gemma

    def models(request, body):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "gemma-4-26b-a4b-it-8bit"}]})
        return chat_response('{"status": "ok"}')

    FakeLLM(models).install()
    check = await _gemma(sctx, live=True)
    assert check["name"] == "gemma" and check["status"] == "ok", check
    assert "status" not in check["details"] and check["details"]["model_listed"]
    FakeLLM(httpx.ConnectError("refused")).install()
    down = await _gemma(sctx, live=True)
    assert down["status"] == "error", down


# ---------------------------------------------------------------------------
# Captions: deterministic limits under adversarial model output
# ---------------------------------------------------------------------------

SPAM = "Feel amazing ✨🌙💤🔥💯🙌🌿 "
LONG = ("Magnesium glycinate is gentle on the stomach. " * 120) + "Visit https://evil.example.com/x now! Link in bio! #Spam"
ADVERSARIAL = {
    "headline": "An extremely long headline that goes well past forty characters ✨",
    "subheadline": "x " * 120, "cta": "Click here to shop the whole range today", "hook": "h " * 80,
    "alt_text": "a" * 600, "base_caption": SPAM * 3 + LONG,
    "hashtags": [f"tag number {i}" for i in range(40)],
    "channels": {
        "instagram": {"caption": SPAM + LONG + "\n\n#a #b #c #d #e #f #g #h #i #j #k"},
        "x": {"caption": LONG},
        "pinterest": {"caption": "Pin it https://bioluxelab.com/products/x?y=1 " + LONG, "title": "T" * 300},
        "youtube": {"caption": LONG, "title": "Y " * 200},
    },
}


@pytest.mark.asyncio
async def test_ai_captions_respect_every_profile_limit_with_adversarial_output(sctx):
    FakeLLM(json.dumps(ADVERSARIAL)).install()
    providers = [*PROVIDER_PROFILES, "unknownnet"]
    channels = [CaptionChannel(channel_id=f"ch_{p}", provider=p, link_url=PRODUCT_URL) for p in providers]
    channels.append(CaptionChannel(channel_id="ch_x_short", provider="x", max_length=120, link_url=PRODUCT_URL))
    result = await generate_captions(caption_request([], channels=channels), sctx)

    assert result.source == "ai" and set(result.channel_captions) == {c.channel_id for c in channels}
    assert len(result.headline) <= 40 and len(result.subheadline) <= 70 and len(result.cta) <= 18
    assert len(result.alt_text) <= 250 and count_emojis(result.headline) == 0
    for channel in channels:
        item = result.channel_captions[channel.channel_id]
        profile = profile_for(channel.provider)
        limit = min(profile.max_length, channel.max_length or profile.max_length)
        assert 0 < measure_for(channel.provider)(item.caption) <= limit, channel.channel_id
        cap = min(5, profile.hashtags_max)
        caption_tags = HASHTAG_RE.findall(item.caption)
        assert len(caption_tags) <= cap and len(item.hashtags) <= cap, channel.channel_id
        assert len({tag.casefold() for tag in caption_tags}) == len(caption_tags)
        assert count_emojis(item.caption) <= 4, channel.channel_id
        assert "evil.example.com" not in item.caption + item.first_comment
        urls = URL_RE.findall(item.caption)
        if profile.link_policy == "inline" and channel.max_length is None:
            assert urls == [PRODUCT_URL], channel.channel_id
        elif profile.link_policy in ("bio", "settings_link"):
            assert urls == [], channel.channel_id
        assert len(re.findall(r"(?i)link in bio", item.caption)) <= 1
        if profile.title_max:
            assert 0 < len(item.title) <= profile.title_max
        else:
            assert item.title == ""
    assert result.channel_captions["ch_instagram"].first_comment.endswith(PRODUCT_URL)
    assert result.channel_captions["ch_tiktok"].first_comment == ""        # TikTok has no comments
    assert x_length(result.channel_captions["ch_x_short"].caption) <= 120
    assert result.channel_captions["ch_youtube"].hashtags[0] == "#Shorts"
    raw_x = result.channel_captions["ch_x"].caption
    assert len(raw_x) > x_length(raw_x)   # the tracked URL counts as 23 on X


@pytest.mark.asyncio
async def test_missing_channels_are_derived_from_base_caption(sctx):
    FakeLLM(json.dumps(good_caption_payload())).install()
    result = await generate_captions(caption_request(["facebook", "threads"]), sctx)
    facebook = result.channel_captions["ch_facebook"].caption
    assert facebook.startswith("Magnesium contributes to a reduction of tiredness and fatigue.")
    assert facebook.count(PRODUCT_URL) == 1 and "#MagnesiumGlycinate" in facebook
    assert HASHTAG_RE.findall(result.channel_captions["ch_threads"].caption) == ["MagnesiumGlycinate"]


@pytest.mark.asyncio
async def test_emoji_level_off_strips_every_emoji(sctx):
    payload = good_caption_payload(instagram=SPAM * 4 + "Calm 🌙 evenings.", x="Sleepy 💤 vibes ✨")
    FakeLLM(json.dumps(payload)).install()
    request = caption_request(["instagram", "x", "pinterest"], caption_overrides={"emoji_level": "off"})
    result = await generate_captions(request, sctx)
    for item in result.channel_captions.values():
        assert count_emojis(item.caption + item.title + item.first_comment) == 0
    assert "Calm evenings." in result.channel_captions["ch_instagram"].caption


@pytest.mark.asyncio
async def test_link_policy_variants(sctx):
    providers = ["instagram", "facebook", "pinterest", "tiktok", "x"]
    result = template_captions(caption_request(providers), sctx)
    caps = result.channel_captions
    assert "link in bio" in caps["ch_instagram"].caption.lower() and PRODUCT_URL not in caps["ch_instagram"].caption
    assert caps["ch_instagram"].first_comment == f"Shop Now: {PRODUCT_URL}"
    assert caps["ch_facebook"].caption.count(f"Shop Now: {PRODUCT_URL}") == 1
    assert URL_RE.search(caps["ch_pinterest"].caption) is None and "bio" not in caps["ch_pinterest"].caption.lower()
    assert "link in bio" in caps["ch_tiktok"].caption.lower() and caps["ch_tiktok"].first_comment == ""

    no_comment = template_captions(caption_request(providers, caption_overrides={"link_in_first_comment": False}), sctx)
    assert no_comment.channel_captions["ch_instagram"].first_comment == ""

    except_ig = template_captions(caption_request(providers, caption_overrides={"link_policy": "all_except_instagram"}), sctx)
    assert "bio" not in except_ig.channel_captions["ch_instagram"].caption.lower()
    assert except_ig.channel_captions["ch_instagram"].first_comment == ""
    assert PRODUCT_URL in except_ig.channel_captions["ch_facebook"].caption

    none = template_captions(caption_request(providers, caption_overrides={"link_policy": "none"}), sctx)
    for item in none.channel_captions.values():
        assert not URL_RE.search(item.caption + item.first_comment) and "bio" not in item.caption.lower()

    custom = template_captions(caption_request(providers, caption_overrides={
        "link_policy": "custom", "custom_url": "https://bioluxelab.com/pages/autumn?ref=1"}), sctx)
    facebook_url = URL_RE.findall(custom.channel_captions["ch_facebook"].caption)[0]
    assert facebook_url.startswith("https://bioluxelab.com/pages/autumn?ref=1&utm_source=facebook&utm_medium=social")

    bare = template_captions(caption_request(providers, caption_overrides={"include_cta": False}), sctx)
    assert f"\n\n{PRODUCT_URL}" in bare.channel_captions["ch_facebook"].caption
    assert "Shop Now" not in bare.channel_captions["ch_facebook"].caption


@pytest.mark.asyncio
async def test_template_placeholders_and_per_provider_templates(sctx):
    sctx.settings.captions.templates = {
        "default": "PRODUCT_TITLE — PRODUCT_PRICE\n\nPRODUCT_DESC\n\nShop at STORE_URL\nPRODUCT_TAGS TAG_CLOUD\nPRODUCT_URL",
        "x": "{{PRODUCT_TITLE}} PRODUCT_PRICE",
    }
    sctx.settings.captions.hashtag_clouds = [
        {"name": "Sleep", "tags": ["Sleep Well", "wind-down"], "collection_ids": ["11"]},
        {"name": "Default", "tags": ["UKWellness"], "is_default": True},
    ]
    result = template_captions(caption_request(["facebook", "x", "pinterest"]), sctx)
    facebook = result.channel_captions["ch_facebook"].caption
    assert result.source == "template" and result.model == ""
    assert facebook.startswith("Magnesium Glycinate 400mg — £24.99\n\n400mg of elemental magnesium")
    assert "Shop at bioluxelab.com" in facebook and not re.search(r"PRODUCT_|TAG_CLOUD|STORE_URL", facebook)
    assert facebook.count(PRODUCT_URL) == 1
    tags = result.channel_captions["ch_facebook"].hashtags
    assert len(tags) == 3 and "#NoSocialAds" not in tags      # internal flag tags are skipped
    assert result.channel_captions["ch_x"].caption.startswith("Magnesium Glycinate 400mg £24.99")
    pinterest_tags = result.channel_captions["ch_pinterest"].hashtags
    assert "#SleepWell" not in pinterest_tags and "#WindDown" not in pinterest_tags   # no clouds on Pinterest
    no_price = template_captions(caption_request(["facebook"], caption_overrides={"include_price": False}), sctx)
    assert "£" not in no_price.channel_captions["ch_facebook"].caption

    sctx.settings.captions.templates = {}
    sale = template_captions(caption_request(
        ["facebook"], content_type="sale_promo", texts={"promo_text": "Autumn offer", "discount_text": "20% off"},
    ), sctx)
    sale_caption = sale.channel_captions["ch_facebook"].caption
    assert sale_caption.startswith("Autumn offer\nMagnesium Glycinate 400mg\n20% off")
    assert "Use code" not in sale_caption          # the coupon line is dropped when no code is set


@pytest.mark.asyncio
async def test_template_source_never_calls_the_model(sctx):
    fake = FakeLLM(httpx.ConnectError("must not be called")).install()
    sctx.settings.captions.source = "template"
    result = await generate_captions(caption_request(["facebook"]), sctx)
    assert result.source == "template" and fake.requests == []


@pytest.mark.asyncio
async def test_unusable_ai_output_falls_back_to_template_captions(sctx):
    FakeLLM('{"headline": "only a headline"}').install()
    result = await generate_captions(caption_request(["facebook"]), sctx)
    assert result.source == "template" and "template captions were used" in result.warnings[0]


@pytest.mark.asyncio
async def test_banned_terms_are_removed_with_a_warning(sctx):
    sctx.settings.captions.banned_terms = ["cheap"]
    FakeLLM(json.dumps(good_caption_payload(facebook="A cheap and cheerful evening ritual."))).install()
    result = await generate_captions(caption_request(["facebook"]), sctx)
    assert "cheap" not in result.channel_captions["ch_facebook"].caption.lower()
    assert any("Removed banned term 'cheap'" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_caption_prompt_carries_compliance_language_and_network_rules(sctx):
    fake = FakeLLM(json.dumps(good_caption_payload())).install()
    request = caption_request(["instagram", "x", "pinterest"], angle="Evening wind-down ritual")
    await generate_captions(request, sctx)
    system = fake.bodies[0]["messages"][0]["content"]
    prompt = fake.prompt(0)
    assert "ASA/CAP" in system and "British English" in system and "never" in system.lower()
    assert '"instagram"' in prompt and '"x"' in prompt and "title of at most 100" in prompt
    assert "link in bio" not in prompt.lower().replace("'link in bio'", "")   # profile CTA advice is removed
    assert "Evening wind-down ritual" in prompt and "£24.99" in prompt


def test_hashtag_normalisation():
    assert normalise_hashtag("magnesium glycinate") == "#MagnesiumGlycinate"
    assert normalise_hashtag("#sleep-support") == "#SleepSupport"
    assert normalise_hashtag("women's health") == "#WomensHealth"
    assert normalise_hashtag("2026") == "" and normalise_hashtag("#") == ""


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------

def _result(caption: str, provider: str = "facebook", **extra) -> CaptionResult:
    return CaptionResult(
        base_caption=caption, hashtags=[], alt_text="",
        channel_captions={"ch1": ChannelCaption(channel_id="ch1", provider=provider, caption=caption, **extra)},
    )


@pytest.mark.asyncio
async def test_guard_blocks_disease_claims_and_holds(sctx):
    guard = check_captions(_result(f"This magnesium cures insomnia. {PRODUCT_URL}"), settings=sctx.settings)
    assert guard.hold
    claim = next(issue for issue in guard.issues if issue.code == "health_claim")
    assert claim.severity == "block" and claim.channel_id == "ch1" and "insomnia" in claim.message

    hedged = _result(f"It may help prevent headaches. {PRODUCT_URL}")
    assert check_captions(hedged, settings=sctx.settings).hold
    sctx.settings.guards.hold_health_claims = False
    relaxed = check_captions(hedged, settings=sctx.settings)
    assert not relaxed.hold and relaxed.issues[0].severity == "warning"

    for claim_text in ("Our magnesium cures insomnia.", "Fights fatigue naturally.", "Ashwagandha beats stress.",
                       "Your go-to for headache relief.", "Magnesium glycinate helps relieve anxiety."):
        found = check_captions(_result(claim_text), settings=sctx.settings).issues
        assert any(issue.code == "health_claim" and issue.severity == "block" for issue in found), claim_text
    for safe_text in ("Not intended to diagnose, treat, cure or prevent any disease.",
                      "Treat yourself after a stressful day.", "Beat the afternoon slump with a walk."):
        assert not any(i.code == "health_claim" for i in check_captions(_result(safe_text), settings=sctx.settings).issues)

    risky = check_captions(_result("Boosts your immunity this winter."), settings=sctx.settings)
    assert any(issue.code == "risky_wording" for issue in risky.issues)
    clean = check_captions(
        _result(f"Magnesium contributes to a reduction of tiredness and fatigue. {PRODUCT_URL}"), settings=sctx.settings,
    )
    assert not clean.hold and clean.issues == []


@pytest.mark.asyncio
async def test_guard_structural_checks(sctx):
    sctx.settings.captions.banned_terms = ["cheap"]
    too_long = check_captions(_result("word " * 70, provider="x"), settings=sctx.settings)
    assert any(issue.code == "too_long" and issue.severity == "block" for issue in too_long.issues)
    assert check_captions(_result("   "), settings=sctx.settings).issues[0].code == "empty_caption"
    tags = " ".join(f"#tag{i}" for i in range(31))
    instagram = check_captions(_result(f"Hello. Link in bio. {tags}", provider="instagram"), settings=sctx.settings)
    assert any(issue.code == "too_many_hashtags" and issue.severity == "block" for issue in instagram.issues)
    misc = check_captions(_result("A cheap PRODUCT_TITLE post #a #b #c #d", provider="facebook"), settings=sctx.settings)
    codes = {issue.code for issue in misc.issues}
    assert {"banned_term", "placeholder_left", "too_many_hashtags"} <= codes and misc.hold
    pin = check_captions(_result(f"Pin {PRODUCT_URL}", provider="pinterest"), settings=sctx.settings)
    assert any(issue.code == "url_in_caption" for issue in pin.issues)


@pytest.mark.asyncio
async def test_claims_trigger_one_rewrite_with_feedback(sctx):
    bad = good_caption_payload(facebook="Our magnesium cures insomnia for good.")
    fake = FakeLLM(json.dumps(bad), json.dumps(good_caption_payload())).install()
    result = await generate_captions(caption_request(["facebook"]), sctx)
    assert len(fake.requests) == 2 and "IMPORTANT — FIX FROM THE PREVIOUS DRAFT" in fake.prompt(1)
    assert "insomnia" in fake.prompt(1)
    assert not check_captions(result, settings=sctx.settings).hold
    assert any("rewritten 1 time" in warning for warning in result.warnings)


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------

def _review_handler(review: dict, vision: dict | None):
    def handler(request, body):
        if body.get("model") == "mlx-community--Qwen3-VL-8B-Instruct-8bit":
            if vision is None:
                return httpx.Response(404, json={"error": "model not found"})
            return chat_response(json.dumps(vision), model=body["model"])
        return chat_response(json.dumps(review))
    return handler


@pytest.mark.asyncio
async def test_review_approves_and_skips_unavailable_vision_gracefully(sctx, tmp_path):
    from PIL import Image

    image = tmp_path / "post.png"
    Image.new("RGB", (64, 64), (200, 200, 200)).save(image)
    FakeLLM(_review_handler({"score": "8/10", "critical_issues": [], "issues": ["Minor: vary the opener"],
                             "suggestions": ["Shorten the X caption"], "summary": "Solid."}, None)).install()
    captions = _result(f"Magnesium contributes to a reduction of tiredness and fatigue. {PRODUCT_URL}")
    review = await review_post(sctx, post={"id": "post_1", "content_type": "single_product"}, captions=captions,
                               products=[MAGNESIUM], media_paths=[str(image)])
    assert review.score == 8 and review.approve and review.model == "gemma-4-26b-a4b-it-8bit"
    assert any(issue.startswith("Vision check skipped") for issue in review.issues)
    assert (await observability.list_decisions(subject_id="post_1"))[0]["decision_type"] == "review"


@pytest.mark.asyncio
async def test_review_rejects_low_score_critical_issues_and_failed_vision(sctx, tmp_path):
    from PIL import Image

    image = tmp_path / "post.png"
    Image.new("RGB", (64, 64)).save(image)
    captions = _result(f"Magnesium contributes to a reduction of tiredness and fatigue. {PRODUCT_URL}")
    post = {"id": "post_2", "content_type": "single_product"}

    FakeLLM(_review_handler({"score": 9, "critical_issues": [], "issues": []},
                            {"product_visible": False, "text_legible": True, "issues": []})).install()
    vision_fail = await review_post(sctx, post=post, captions=captions, products=[MAGNESIUM], media_paths=[str(image)])
    assert not vision_fail.approve and any("not clearly visible" in issue for issue in vision_fail.issues)

    FakeLLM(_review_handler({"score": 9, "critical_issues": ["Invented 20% discount"]}, None)).install()
    critical = await review_post(sctx, post=post, captions=captions, products=[MAGNESIUM])
    assert not critical.approve and critical.issues[0] == "Critical: Invented 20% discount"

    FakeLLM(_review_handler({"score": 5, "critical_issues": []}, None)).install()
    low = await review_post(sctx, post=post, captions=captions, products=[MAGNESIUM])
    assert not low.approve

    FakeLLM(_review_handler({"score": 9}, None)).install()
    held = await review_post(sctx, post=post, captions=_result("This cures insomnia."), products=[MAGNESIUM])
    assert not held.approve and any(issue.startswith("Guard:") for issue in held.issues)


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

CANDIDATES = [
    {"product_id": "p1", "title": "Magnesium", "price": 24.99, "on_sale": False, "discount_pct": 0,
     "collections": ["Sleep"], "tags": ["magnesium"], "days_since_posted": 12, "post_count": 3, "is_new": False,
     "performance_score": 0.7},
    {"product_id": "p2", "title": "Vitamin D3", "price": 18.0, "on_sale": True, "discount_pct": 20,
     "collections": [], "tags": [], "days_since_posted": 30, "post_count": 1, "is_new": False, "performance_score": 0.4},
    {"product_id": "p3", "title": "Ashwagandha", "price": 22.0, "on_sale": False, "discount_pct": 0,
     "collections": [], "tags": [], "days_since_posted": None, "post_count": 0, "is_new": True, "performance_score": 0},
    {"product_id": "p4", "title": "Omega-3", "price": 27.5, "on_sale": False, "discount_pct": 0,
     "collections": [], "tags": [], "days_since_posted": 1, "post_count": 9, "is_new": False, "performance_score": 0.9},
]
SLOTS = [{"slot_id": f"s{i}", "local_time": f"2026-10-25 {8 + i}:00", "providers": ["instagram"]} for i in range(1, 8)]
PLAN_ARGS = dict(
    slots=SLOTS, candidates=CANDIDATES,
    allowed_content_types=["single_product", "sale_promo", "new_arrival", "collage", "quote"],
    templates_by_type={"single_product": ["product_card"], "sale_promo": ["sale_badge"], "collage": ["grid"]},
    recent_posts=[{"content_type": "single_product", "product_ids": ["p4"]}], performance={},
)


@pytest.mark.asyncio
async def test_content_plan_drops_invented_ids_types_and_templates(sctx):
    FakeLLM(json.dumps({"slots": [
        {"slot_id": "s1", "product_ids": ["p1"], "content_type": "single_product", "template_key": "product_card",
         "angle": "Evening ritual", "reason": "Strong performer"},
        {"slot_id": "s2", "product_ids": ["p999"], "content_type": "single_product"},
        {"slot_id": "s3", "product_ids": ["p2"], "content_type": "reel"},
        {"slot_id": "s4", "product_ids": ["p2"], "content_type": "Sale Promo", "template_key": "fancy"},
        {"slot_id": "s5", "product_ids": ["p1"], "content_type": "single_product"},
        {"slot_id": "s6", "product_ids": ["p4"], "content_type": "sale_promo"},
        {"slot_id": "s7", "product_ids": [], "content_type": "quote"},
        {"slot_id": "s99", "product_ids": ["p3"], "content_type": "new_arrival"},
        {"slot_id": "s1", "product_ids": ["p3"], "content_type": "new_arrival"},
    ]})).install()
    plan = await decisions.choose_content_plan(sctx, subject_id="plan-1", **PLAN_ARGS)
    by_slot = {slot.slot_id: slot for slot in plan.slots}
    assert set(by_slot) == {"s1", "s4", "s7"}
    assert by_slot["s1"].template_key == "product_card" and by_slot["s1"].angle == "Evening ritual"
    assert by_slot["s4"].content_type == "sale_promo" and by_slot["s4"].template_key == ""
    assert by_slot["s7"].product_ids == []
    assert plan.uncovered_slot_ids == ["s2", "s3", "s5", "s6"]
    reasons = {(item.slot_id, item.reason) for item in plan.rejected}
    assert ("s2", "not one of the candidate products") in reasons
    assert ("s3", "content type is not allowed") in reasons
    assert ("s5", "already used in another slot") in reasons
    assert ("s6", "sale_promo needs a product that is on sale") in reasons
    assert ("s99", "unknown slot id") in reasons and ("s1", "duplicate entry for this slot") in reasons
    assert any(item.field == "template_key" and item.value == "fancy" for item in plan.rejected)
    row = (await observability.list_decisions(subject_id="plan-1"))[0]
    assert row["decision_type"] == "content_plan" and row["valid"] == 1
    assert len(row["output"]["result"]["rejected"]) == len(plan.rejected)


@pytest.mark.asyncio
async def test_content_plan_unusable_output_repairs_then_leaves_slots_uncovered(sctx):
    fake = FakeLLM(json.dumps({"slots": [{"slot_id": "s1", "product_ids": ["ghost"], "content_type": "single_product"}]})).install()
    plan = await decisions.choose_content_plan(sctx, **PLAN_ARGS)
    assert len(fake.requests) == 2 and "ghost" in fake.prompt(1)
    assert plan.source == "none" and plan.slots == [] and plan.uncovered_slot_ids == [s["slot_id"] for s in SLOTS]


@pytest.mark.asyncio
async def test_content_plan_respects_ai_decisions_switch(sctx):
    fake = FakeLLM("{}").install()
    sctx.settings.ai.ai_decisions = False
    plan = await decisions.choose_content_plan(sctx, **PLAN_ARGS)
    assert fake.requests == [] and plan.source == "none" and len(plan.uncovered_slot_ids) == len(SLOTS)


def test_pre_rank_candidates_is_deterministic_and_compact():
    ranked = decisions.pre_rank_candidates(CANDIDATES, recent_posts=PLAN_ARGS["recent_posts"])
    assert [c["product_id"] for c in ranked] == ["p2", "p3", "p1", "p4"]
    many = [dict(CANDIDATES[0], product_id=f"x{i}", title=f"P{i:02d}") for i in range(60)]
    assert len(decisions.pre_rank_candidates(many)) == decisions.MAX_CANDIDATES
    assert decisions.pre_rank_candidates(many) == decisions.pre_rank_candidates(list(reversed(many)))


@pytest.mark.asyncio
async def test_promo_texts_are_capped_and_never_invent_numbers(sctx):
    FakeLLM(json.dumps({"texts": [
        "Autumn calm: 20% off", "This line is definitely longer than forty characters", "Now 30% off everything",
        "Cures stress fast", "Code AUTUMN20 for 20% off ✨", "autumn calm: 20% off", "#Save 20% today",
    ]})).install()
    texts = await decisions.generate_promo_texts(
        sctx, theme="Autumn", products=[CANDIDATES[1]], discount_text="20% off", coupon_code="AUTUMN20", count=5,
    )
    assert texts == ["Autumn calm: 20% off", "Code AUTUMN20 for 20% off", "Save 20% today"]
    assert all(len(text) <= 40 for text in texts)


@pytest.mark.asyncio
async def test_brand_content_validates_quiz_answers_and_repeats(sctx):
    first = {"headline": "Evening ritual quiz", "body": "Which habit helps you wind down?",
             "options": ["Dimming the lights", "Bright screens"], "answer": "Coffee", "caption": "Tell us below."}
    second = dict(first, answer="dimming the lights")
    fake = FakeLLM(json.dumps(first), json.dumps(second)).install()
    content = await decisions.generate_brand_content(sctx, kind="quiz", recent=["Morning light walks"])
    assert content.answer == "Dimming the lights" and content.options == ["Dimming the lights", "Bright screens"]
    assert "answer must be exactly one of the options" in fake.prompt(1)
    assert content.alt_text.startswith('BioluxeLab graphic: "Evening ritual quiz"')

    FakeLLM(json.dumps({"headline": "Rest is a ritual", "body": "", "caption": "Slow evenings."})).install()
    with pytest.raises(ValidationError):
        await decisions.generate_brand_content(sctx, kind="quote", recent=["Rest is a ritual."])


@pytest.mark.asyncio
async def test_performance_insights_only_use_numbers_types_and_hours_from_input(sctx):
    rollups = {
        "period_days": 28,
        "by_content_type": {"single_product": {"posts": 20, "engagement_rate": 0.031},
                            "collage": {"posts": 6, "engagement_rate": 0.045}},
        "by_hour": {"9": {"posts": 10}, "19": {"posts": 12}},
    }
    invented_summary = {"summary": "Engagement rose 73% this month.", "recommendations": [], "content_type_weights": {}}
    good = {
        "summary": "Collages lead with a 4.5% engagement rate across 6 posts.",
        "recommendations": ["Post more collages (4.5% engagement).", "Expect 3x growth by Christmas with 250 posts."],
        "content_type_weights": {"collage": 1.6, "single_product": 9, "carousel": 2.0},
        "best_hours": [19, "21:00"],
    }
    fake = FakeLLM(json.dumps(invented_summary), json.dumps(good)).install()
    insights = await decisions.performance_insights(sctx, rollups=rollups)
    assert "73" in fake.prompt(1)
    assert insights.content_type_weights == {"collage": 1.6, "single_product": 3.0}
    assert insights.best_hours == [19] and insights.recommendations == ["Post more collages (4.5% engagement)."]
    assert any("carousel" in item for item in insights.dropped) and any("21:00" in item for item in insights.dropped)
    empty = await decisions.performance_insights(sctx, rollups={})
    assert empty.summary == "Not enough performance data yet."


# ---------------------------------------------------------------------------
# UTM
# ---------------------------------------------------------------------------

def test_tracked_url_preserves_params_and_normalises_sources():
    settings = UtmSettings()
    url = tracked_url("https://bioluxelab.com/products/mag?variant=1&utm_source=old#reviews",
                      provider="instagram-standalone", campaign="Black Friday – 20% off!", content="post_1",
                      settings=settings)
    assert url == ("https://bioluxelab.com/products/mag?variant=1&utm_source=instagram&utm_medium=social"
                   "&utm_campaign=black-friday-20-off&utm_content=post_1#reviews")
    assert "utm_source=linkedin" in tracked_url("https://a.co/x", provider="linkedin-page", campaign="c", content="",
                                                settings=settings)
    override = UtmSettings(source_overrides={"x": "twitter"}, medium="organic-social")
    assert tracked_url("https://a.co/x", provider="x", campaign="c", content="", settings=override) == (
        "https://a.co/x?utm_source=twitter&utm_medium=organic-social&utm_campaign=c")
    assert tracked_url("https://a.co/x?a=1", provider="x", campaign="c", content="d",
                       settings=UtmSettings(enabled=False)) == "https://a.co/x?a=1"
    assert tracked_url("", provider="x", campaign="c", content="d", settings=settings) == ""
    assert slugify("  Crème Brûlée — Winter Sale!  ") == "creme-brulee-winter-sale"


# ---------------------------------------------------------------------------
# Live (optional): SOCIAL_ENGINE_LIVE_LLM=1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("SOCIAL_ENGINE_LIVE_LLM") != "1", reason="set SOCIAL_ENGINE_LIVE_LLM=1 to call local Gemma")
async def test_live_gemma_captions(sctx):
    health = await check_llm(sctx.settings.ai)
    assert health["ok"], health["error"]
    providers = ["instagram", "facebook", "x", "pinterest", "tiktok", "linkedin-page", "threads"]
    request = caption_request(providers, angle="The evening wind-down ritual")
    result = await generate_captions(request, sctx)
    assert result.source == "ai" and result.model
    for channel in request.channels:
        item = result.channel_captions[channel.channel_id]
        assert 0 < measure_for(channel.provider)(item.caption) <= profile_for(channel.provider).max_length
    guard = check_captions(result, settings=sctx.settings)
    assert not any(issue.severity == "block" for issue in guard.issues), guard.issues
    logging.getLogger("ai_blog_server.social_engine.ai").info(
        "Live captions: %s", json.dumps({cid: item.caption for cid, item in result.channel_captions.items()},
                                        indent=2, ensure_ascii=False),
    )
