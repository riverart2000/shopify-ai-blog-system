"""Self-correction: a rejected review is fixed by the engine before a person is asked."""
from __future__ import annotations

import json
import re
import time

import httpx
import pytest

from db.social_engine import observability
from db.social_engine import posts as post_store
from services.social_engine import http as se_http
from services.social_engine.ai.base import GuardIssue, GuardResult, ReviewResult
from services.social_engine.jobs.worker import Worker
from services.social_engine.pipeline import actions
from services.social_engine.pipeline.selfcorrect import ATTEMPT_KEY, FEEDBACK_KEY, plan_fix
from services.social_engine.settings import EngineSettings, save_settings
from test_social_engine_ai import FakeLLM, chat_response
from test_social_engine_e2e import _jpeg, _setup

TWO_PHOTOS = [{"product_id": "1", "images": [{"url": "a"}, {"url": "b"}]}]
ONE_PHOTO = [{"product_id": "1", "images": [{"url": "a"}]}]


def _review(approve: bool, *, score: int = 5, issues=(), vision_critical=False, suggestions=()) -> ReviewResult:
    return ReviewResult(score=score, approve=approve, issues=list(issues), suggestions=list(suggestions),
                        critical=[i for i in issues if i.startswith(("Critical", "Vision"))], vision_critical=vision_critical)


def _plan(**kwargs):
    defaults = dict(guard=GuardResult(hold=False, issues=[]), review=None, options={}, settings=EngineSettings(),
                    products=TWO_PHOTOS, captions_source="ai")
    return plan_fix(**{**defaults, **kwargs})


def test_plan_fix_rewrites_from_reviewer_notes_until_fixes_run_out():
    review = _review(False, issues=["Critical: 'six colours' is not in the product data", "Title says 'Review'"],
                     suggestions=["Drop the colour claim"])
    fix = _plan(review=review)
    assert fix and fix.attempt == 1 and not fix.next_image
    assert "six colours" in fix.feedback and "Drop the colour claim" in fix.feedback and "score 5/10" in fix.feedback
    assert "rewriting the captions" in fix.describe(2)
    assert _plan(review=review, options={ATTEMPT_KEY: 1}).attempt == 2
    assert _plan(review=review, options={ATTEMPT_KEY: 2}) is None          # max_review_rewrites = 2
    assert _plan(review=_review(True, score=9)) is None                     # nothing to fix
    assert _plan(review=review, captions_source="template") is None         # a template would come back identical


def test_plan_fix_handles_guard_holds_and_bad_photos():
    guard = GuardResult(hold=True, issues=[GuardIssue("health_claim", 'Medicinal claim ("cures insomnia")', "block")])
    fix = _plan(guard=guard)
    assert fix and "content guard" in fix.feedback and "cures insomnia" in fix.feedback
    # The product photo carries a body-shaping claim: try the next photo, keep the captions.
    vision = _review(False, score=9, issues=['Vision: the image shows a health or body claim ("Shaping Curves")'],
                     vision_critical=True)
    fix = _plan(review=vision)
    assert fix and fix.next_image and fix.feedback == ""
    assert "next product photo" in fix.describe(2)
    assert _plan(review=vision, options={"image_offset": 1}) is None       # both photos tried
    assert _plan(review=vision, products=ONE_PHOTO) is None                 # nothing else to show: a person decides


# ---------------------------------------------------------------------------
# Through the real pipeline (template rendering, worker, database)
# ---------------------------------------------------------------------------

@pytest.fixture
def cdn():
    payload = _jpeg()
    se_http.TRANSPORT_OVERRIDES["cdn"] = httpx.MockTransport(
        lambda request: httpx.Response(200, content=payload, headers={"content-type": "image/jpeg"}))
    yield


class Model:
    """Caption writer + reviewer: the writer invents a fact until told not to."""

    def __init__(self, reviews: list[dict]) -> None:
        self.reviews = reviews
        self.caption_prompts: list[str] = []
        self.review_prompts: list[str] = []

    def __call__(self, request, body):
        prompt = body["messages"][-1]["content"]
        if "CAPTIONS PER NETWORK" in prompt:
            self.review_prompts.append(prompt)
            return chat_response(json.dumps(self.reviews[min(len(self.review_prompts) - 1, len(self.reviews) - 1)]))
        self.caption_prompts.append(prompt)
        keys = re.search(r"exactly these keys: ([^.\n]+)", prompt).group(1).split(", ")
        fixed = "FIX FROM THE PREVIOUS DRAFT" in prompt
        text = "Gentle magnesium for a calm evening ritual." if fixed else "Magnesium in six colours for a calm evening."
        return chat_response(json.dumps({
            "headline": "A calm evening ritual", "subheadline": "Gentle magnesium glycinate", "cta": "Shop the range",
            "hook": "Your evening, simplified", "alt_text": "Magnesium Glycinate capsules", "base_caption": text,
            "hashtags": ["MagnesiumGlycinate", "EveningRitual"], "channels": {k: {"caption": text} for k in keys},
        }))


async def _ai_setup(store_id: str) -> None:
    await _setup(store_id)
    await save_settings(store_id, {"captions": {"source": "ai"}, "ai": {"ai_review": True, "vision_model": ""},
                                   "guards": {"max_review_rewrites": 2}})


@pytest.mark.asyncio
async def test_rejected_review_is_fixed_by_the_engine_and_auto_approved(se_db, cdn):
    await _ai_setup(se_db)
    model = Model([
        {"score": 6, "critical_issues": ["'six colours' is not in the product data"], "issues": [],
         "suggestions": ["Remove the colour claim"], "summary": "One invented fact."},
        {"score": 9, "critical_issues": [], "issues": [], "suggestions": [], "summary": "Good."},
    ])
    FakeLLM(model).install()
    created = await actions.create_manual_post(
        se_db, content_type="single_product", product_ids=["1"], channel_ids=["dry-instagram", "dry-x"],
        scheduled_at=int(time.time()) + 6 * 3600, approval_mode="ai_review",
    )
    await Worker(run_periodic=False).run_once()
    post = await post_store.get_post(se_db, created["post_id"])
    assert post["status"] in ("approved", "scheduled"), post["error_message"]
    assert len(model.caption_prompts) == 2 and len(model.review_prompts) == 2
    assert "six colours" in model.caption_prompts[1] and "Remove the colour claim" in model.caption_prompts[1]
    assert "six colours" not in post["targets"][0]["caption"]
    assert post["generation"]["review_fixes"] == 1 and post["review"]["ai_review"]["score"] == 9
    assert ATTEMPT_KEY not in post["options"] and FEEDBACK_KEY not in post["options"]
    assert any("1 fix" in w for w in post["generation"]["warnings"])
    messages = [a["message"] for a in await observability.list_activity(entity_type="post", entity_id=post["id"], limit=50)]
    assert any(m.startswith("Review found issues; rewriting the captions") for m in messages), messages
    assert any("after 1 fix" in m for m in messages), messages


@pytest.mark.asyncio
async def test_review_that_keeps_failing_waits_for_a_person_after_the_fixes(se_db, cdn):
    await _ai_setup(se_db)
    model = Model([{"score": 4, "critical_issues": ["Still wrong"], "issues": [], "suggestions": [], "summary": "No."}])
    FakeLLM(model).install()
    created = await actions.create_manual_post(
        se_db, content_type="single_product", product_ids=["2"], channel_ids=["dry-instagram"],
        scheduled_at=int(time.time()) + 6 * 3600, approval_mode="ai_review",
    )
    await Worker(run_periodic=False).run_once()
    post = await post_store.get_post(se_db, created["post_id"])
    assert post["status"] == "pending_approval"
    assert len(model.caption_prompts) == 3 and post["generation"]["review_fixes"] == 2
    assert ATTEMPT_KEY not in post["options"]   # "Regenerate" later starts clean


# ---------------------------------------------------------------------------
# Photo audit: supplier photos with printed claims are never used
# ---------------------------------------------------------------------------

def _vision_by_url(verdicts: dict[str, list[str]]):
    """Vision replies keyed by which image bytes were sent (the cdn fixture serves one JPEG, so key by call order)."""
    calls: list[dict] = []

    def handler(request, body):
        if body.get("model") == "mlx-community--Qwen3-VL-8B-Instruct-8bit":
            calls.append(body)
            claims = list(verdicts.values())[min(len(calls) - 1, len(verdicts) - 1)]
            return chat_response(json.dumps({"has_text": bool(claims), "claims": claims, "issues": []}),
                                 model=body["model"])
        return chat_response(json.dumps({"score": 9, "critical_issues": [], "issues": []}))
    handler.calls = calls
    return handler


@pytest.mark.asyncio
async def test_photos_with_printed_claims_are_skipped_and_a_bare_product_is_blocked(se_db, cdn):
    from db.social_engine import photos as photo_store
    from db.social_engine import products as product_store
    from services.social_engine.ai.photos import audit_pending, clean_products
    from services.social_engine.catalog import eligible_products
    from services.social_engine.context import load_store_context

    await _setup(se_db)
    await save_settings(se_db, {"ai": {"vision_model": "mlx-community--Qwen3-VL-8B-Instruct-8bit"}})
    sctx = await load_store_context(se_db, correlation_id="t")
    # Product 1: first photo says "Slim Legs", second is clean.
    handler = _vision_by_url({"a": ["Slim Legs"], "b": []})
    FakeLLM(handler).install()
    [product] = await product_store.get_products(se_db, ["1"])
    [cleaned] = await clean_products(sctx, [product])
    assert [img["url"] for img in cleaned["images"]] == ["https://cdn.test/1b.jpg"]
    assert cleaned["featured_image_url"] == "https://cdn.test/1b.jpg" and cleaned["photo_claims"] == 1
    assert not cleaned["photo_block"] and len(handler.calls) == 2
    # Verdicts are remembered: a second pass makes no vision call.
    await clean_products(sctx, [product])
    assert len(handler.calls) == 2
    assert (await photo_store.get_audits(se_db, ["https://cdn.test/1.jpg"]))["https://cdn.test/1.jpg"]["claims"] == ["Slim Legs"]

    # Product 2: every photo carries a claim → blocked, out of the campaigns, visible in the summary.
    FakeLLM(_vision_by_url({"a": ["Burn Fat"], "b": ["Thin Thigh"]})).install()
    [product2] = await product_store.get_products(se_db, ["2"])
    [blocked] = await clean_products(sctx, [product2])
    assert blocked["photo_block"] and blocked["images"] == [] and blocked["featured_image_url"] == ""
    assert await photo_store.blocked_product_ids(se_db) == {"2"}
    assert "2" not in {p["product_id"] for p in await eligible_products(se_db)}
    assert "1" in {p["product_id"] for p in await eligible_products(se_db)}

    # The periodic audit picks up the product nobody has checked yet (3), and only that one.
    FakeLLM(_vision_by_url({"a": [], "b": []})).install()
    summary = await audit_pending(se_db, max_products=5)
    assert summary["audited"] == 1 and summary["blocked"] == 0 and summary["remaining"] == 0
    assert set((await photo_store.product_summary(se_db))) == {"1", "2", "3"}


@pytest.mark.asyncio
async def test_generation_fails_clearly_for_a_product_with_no_clean_photo(se_db, cdn):
    from db.social_engine import photos as photo_store

    await _setup(se_db)
    for url in ("https://cdn.test/1.jpg", "https://cdn.test/1b.jpg"):
        await photo_store.save_audit(se_db, url=url, product_id="1", verdict="claim", claims=["Slim Legs"], model="m")
    created = await actions.create_manual_post(
        se_db, content_type="single_product", product_ids=["1"], channel_ids=["dry-instagram"],
        scheduled_at=int(time.time()) + 6 * 3600,
    )
    await Worker(run_periodic=False).run_once()
    post = await post_store.get_post(se_db, created["post_id"])
    assert post["status"] == "failed" and "Replace the product photos" in post["error_message"], post


# ---------------------------------------------------------------------------
# Backup image model + previews from the real catalogue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backup_image_model_takes_over_when_postiz_credits_are_gone(se_db, cdn, monkeypatch):
    import db as app_db
    import providers
    from services.social_engine.context import load_store_context
    from services.social_engine.errors import ConfigurationError, ExternalServiceError, RateLimited
    from services.social_engine.pipeline.media_sources import fallback_applies, fallback_image

    await _setup(se_db)
    await app_db.upsert_model({
        "id": "m-grok-img", "store_id": "store_1", "name": "Grok Image", "provider": "xai", "model_type": "image",
        "model_name": "grok-imagine-image-quality", "api_key": "k", "endpoint": "https://api.x.ai/v1/images/generations",
        "extra_json": "{}", "priority": 0, "is_active": 1,
    })
    await save_settings(se_db, {"postiz": {"image_fallback_model_id": "m-grok-img"}})
    sctx = await load_store_context(se_db, correlation_id="t")
    prompts: list[str] = []

    class _FakeImageProvider:
        def __init__(self, model):
            self.model = model

        async def generate_images(self, prompt, count=2, reference_image=None):
            prompts.append(prompt)
            return ["https://cdn.test/grok.jpg"]
    monkeypatch.setattr(providers, "get_image_provider", lambda record: _FakeImageProvider(record))

    # Credits gone or Postiz broken → fall back; switched off or not live → do not spend elsewhere.
    assert fallback_applies(RateLimited("Postiz AI image budget used (60 this month).", retry_at=0))
    assert fallback_applies(ExternalServiceError("postiz", "generateImageTool failed: no credits", retryable=False))
    assert not fallback_applies(ConfigurationError("Postiz AI images are disabled (monthly budget is 0)."))

    path, used = await fallback_image(sctx, prompt="Soft studio scene, no text", post_id="post_bg")
    assert path.endswith((".jpg", ".jpeg")) and used == "image_model:Grok Image" and prompts == ["Soft studio scene, no text"]
    ledger = await observability.list_api_calls(correlation_id="post_bg")
    assert ledger and ledger[0]["service"] == "image_model" and ledger[0]["ok"]

    await save_settings(se_db, {"postiz": {"image_fallback_model_id": "nope"}})
    sctx = await load_store_context(se_db, correlation_id="t")
    with pytest.raises(ConfigurationError):
        await fallback_image(sctx, prompt="Soft studio scene, no text", post_id="post_bg")


@pytest.mark.asyncio
async def test_template_previews_use_the_stores_own_products_without_claim_photos(se_db, cdn):
    from db.social_engine import photos as photo_store
    from services.social_engine.creatives.render import render_preview, store_preview_products

    assert await store_preview_products(se_db, 2) == []        # nothing synced yet → drawn samples
    await _setup(se_db)
    await photo_store.save_audit(se_db, url="https://cdn.test/3.jpg", product_id="3", verdict="claim",
                                 claims=["Burn Fat"], model="m")
    products = await store_preview_products(se_db, 2)
    assert [p.product_id for p in products] == ["3", "2"]       # newest first (ids inserted 1, 2, 3)
    assert products[0].image_urls == ["https://cdn.test/3b.jpg"] and products[0].price == "£21.50"
    media = await render_preview(se_db, "product_editorial", "square")
    assert media.path and media.width > 0


# ---------------------------------------------------------------------------
# Postiz AI images that hang
# ---------------------------------------------------------------------------

class _HangingMCP:
    """generateImageTool times out; the media library holds whatever `library` says."""

    def __init__(self, library):
        self.library = library
        self.calls = []

    async def call(self, tool, arguments, *, idempotent, timeout=None):
        from services.social_engine.errors import ExternalServiceError

        self.calls.append((tool, timeout))
        if tool == "generateImageTool":
            raise ExternalServiceError("postiz", "ReadTimeout: ", retryable=True)
        if tool == "mediaListTool":
            return {"pages": 1, "output": self.library}
        return {}

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_a_hanging_postiz_image_gives_up_after_90s_and_is_found_late_in_the_library(se_db, monkeypatch):
    from datetime import datetime, timezone

    from services.social_engine.context import load_store_context
    from services.social_engine.errors import ExternalServiceError
    from services.social_engine.publishers import postiz_tools
    from services.social_engine.publishers.guard import LIVE_PUBLISHING_ENV

    monkeypatch.setenv(LIVE_PUBLISHING_ENV, "1")
    monkeypatch.setattr(postiz_tools, "LATE_IMAGE_RECHECK_SECONDS", 0)
    sctx = await load_store_context(se_db, correlation_id="t")
    now = datetime.now(timezone.utc).isoformat()
    old = "2026-01-01T00:00:00Z"
    library = [
        {"id": "upload-1", "path": "https://uploads.postiz.com/6fba0ef5febb4400ae8778a5b854224d.png", "createdAt": now},
        {"id": "old-ai", "path": "https://uploads.postiz.com/AbCdEfGhIj.png", "createdAt": old},
        {"id": "late-ai", "path": "https://uploads.postiz.com/kc5p4XuNeS.png", "createdAt": now},
    ]
    mcp = _HangingMCP(library)
    image = await postiz_tools.PostizCreativeTools(sctx, mcp=mcp).generate_image("A calm sunrise studio scene, no text")
    assert image.external_id == "late-ai" and image.url.endswith("kc5p4XuNeS.png")
    assert ("generateImageTool", postiz_tools.IMAGE_TIMEOUT_SECONDS) in mcp.calls

    with pytest.raises(ExternalServiceError, match="didn't arrive within 90 s"):
        await postiz_tools.PostizCreativeTools(sctx, mcp=_HangingMCP(library[:2])).generate_image("Another calm scene, no text")


@pytest.mark.asyncio
async def test_timed_out_postiz_images_count_against_the_monthly_budget(se_db):
    from db.social_engine import observability
    from services.social_engine.publishers.postiz_tools import SERVICE, _used_this_month

    for status, ok, error in ((200, True, ""), (None, False, "ReadTimeout: ")):
        await observability.record_api_call(service=SERVICE, operation="mcp.generateImageTool", method="POST",
                                            url_path="", status_code=status, ok=ok, duration_ms=1, error=error)
    assert await _used_this_month("mcp.generateImageTool") == 2
