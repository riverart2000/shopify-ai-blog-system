"""A second brand with no Shopify catalogue: brand file → store, settings, campaign → planned → generated
(real rendering) → scheduled through the dry-run publisher. Bio Luxe Lab's own behaviour must not change."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import db
from db.social_engine import config_store, observability
from db.social_engine import posts as post_store
from services.social_engine.ai import decisions, prompts
from services.social_engine.ai.guard import claim_issues
from services.social_engine.ai.llm import WRITER_TASKS, LLMRouter
from services.social_engine.brands import BrandFile, apply_brand, read_brand_file
from services.social_engine.campaigns.engine import evaluate_campaign
from services.social_engine.context import load_store_context
from services.social_engine.errors import ValidationError
from services.social_engine.jobs import periodic
from services.social_engine.jobs.worker import Worker
from services.social_engine.settings import EngineSettings, VoiceSettings, load_settings, save_settings

BRAND_FILE = Path(__file__).parent / "brands" / "revenuemind.json"


def _brand(tmp_path, **overrides) -> BrandFile:
    data = json.loads(BRAND_FILE.read_text())
    logo = tmp_path / "logo.png"
    from PIL import Image

    Image.new("RGBA", (300, 80), (48, 79, 255, 255)).save(logo)
    data["logo_file"] = str(logo)
    data["postiz_group"] = "Dry Run Customer"     # the dry-run publisher's channels belong to this group
    data["settings"]["publisher"] = "dry_run"
    data["settings"]["ai"] = {"ai_review": False, "ai_decisions": False}
    data["settings"]["captions"]["source"] = "template"     # no model is called in tests
    data["settings"]["schedule"].update({"window_start": "00:05", "window_end": "23:55", "use_best_times": False})
    data["campaigns"][0]["approval_mode"] = "auto"
    data["campaigns"][0]["config"]["ai_background"] = False
    data.update(overrides)
    return BrandFile.model_validate(data)


def test_the_revenuemind_brand_file_is_valid():
    brand = read_brand_file(BRAND_FILE)
    settings = EngineSettings.model_validate(brand.settings)
    kinds = prompts.content_kinds(settings.voice)
    campaign = brand.campaigns[0]
    assert settings.catalog.source == "none" and settings.guards.health_claim_checks is False
    assert set(campaign.config["kinds"]) <= set(kinds), "every kind the campaign posts is defined"
    assert campaign.config["link_url"].endswith("/book-call")
    from services.social_engine.creatives.library import get_builtin

    for key in campaign.config["kinds"]:
        assert get_builtin(kinds[key].template_key), f"{key}: template {kinds[key].template_key!r} exists"
    assert settings.brand.cta_text == "Book a call"


def test_voice_defaults_keep_the_built_in_wellness_prompts():
    settings = EngineSettings()
    system = prompts.caption_system_prompt(settings.brand, settings.captions, settings.voice)
    assert "premium UK wellness and food-supplement brand" in system and prompts.COMPLIANCE_RULES in system
    assert prompts.review_system(settings.voice) == prompts.REVIEW_SYSTEM
    assert prompts.decision_system(settings.voice) == prompts.DECISION_SYSTEM

    voice = VoiceSettings(sector="a UK consultancy", compliance_rules="1. Never invent client results.",
                          voice_rules="- Plain English.")
    system = prompts.caption_system_prompt(settings.brand, settings.captions, voice)
    assert "a UK consultancy" in system and "Never invent client results" in system and "- Plain English." in system
    assert "food-supplement" not in system and "nutrition" not in prompts.review_system(voice)
    assert "added automatically" in system, "links and hashtags stay out of captions for every brand"


def test_supplement_claim_checks_can_be_switched_off_for_other_brands():
    text = "Automation will fix the stress of chasing invoices and cure your Monday reporting pain."
    assert any(issue.severity == "block" for issue in claim_issues(text, "", "the caption"))
    assert claim_issues(text, "", "the caption", health=False) == []
    assert claim_issues("Only 3 left, selling fast", "", "the caption", health=False)[0].code == "urgency_wording"


def test_the_writer_only_gets_writing_tasks():
    router = LLMRouter(primary="gemma", fallback="backup", writer="postiz")   # type: ignore[arg-type]
    assert [p for p, _ in router._providers("captions")] == ["postiz", "gemma", "backup"]
    assert [fallback for _, fallback in router._providers("captions")] == [False, True, True]
    assert [p for p, _ in router._providers("review")] == ["gemma", "backup"]
    assert "review" not in WRITER_TASKS and "decide.brand_content" in WRITER_TASKS


@pytest.mark.asyncio
async def test_postiz_agent_reply_is_refused_when_it_used_a_tool(monkeypatch):
    from services.social_engine.errors import ExternalServiceError
    from services.social_engine.publishers.postiz_tools import PostizMCP

    mcp = PostizMCP("key", url="https://mcp.test/mcp")
    replies = [
        {"text": '{"headline": "Hi"}', "steps": [{"toolCalls": []}], "usage": {"totalTokens": 9}},
        {"text": "Scheduled it for you!", "steps": [{"toolCalls": [{"toolName": "integrationSchedulePostTool"}]}]},
    ]

    async def call(tool, arguments, **_):
        assert tool == "ask_postiz" and "message" in arguments
        return replies.pop(0)

    monkeypatch.setattr(mcp, "call", call)
    assert await mcp.ask_agent("write", timeout=5) == ('{"headline": "Hi"}', {"totalTokens": 9})
    with pytest.raises(ExternalServiceError, match="integrationSchedulePostTool"):
        await mcp.ask_agent("write", timeout=5)
    await mcp.aclose()


@pytest.mark.asyncio
async def test_brand_without_a_catalogue_posts_brand_content_end_to_end(se_db, tmp_path, monkeypatch):
    seen: list[dict] = []

    async def fake_brand_content(sctx, *, kind, topic_hint="", recent=None, topic="", offering_name=""):
        seen.append({"kind": kind, "topic": topic, "offering": offering_name})
        definition = prompts.content_kinds(sctx.settings.voice)[kind]
        points = [f"Point {n} for {kind}" for n in range(1, 4)] if definition.points else []
        return decisions.BrandContent(
            kind=kind, headline=f"Headline for {kind}", body="" if points else "The plain answer in one line.",
            points=points, caption="A useful caption about getting a process ready before automating it.",
        )

    monkeypatch.setattr(decisions, "generate_brand_content", fake_brand_content)

    # Bio Luxe Lab (the Shopify store of the fixture) is on, with a catalogue.
    await save_settings(se_db, {"enabled": True, "publisher": "dry_run"})
    with pytest.raises(ValidationError, match="No channel in Postiz is in a group"):
        await apply_brand(_brand(tmp_path, postiz_group="Nobody"))
    assert (await load_settings("revenuemind")).enabled is False, "a brand without its Postiz group never runs"

    result = await apply_brand(_brand(tmp_path))
    store_id = result["store_id"]
    assert store_id == "revenuemind" and any("Created campaign" in line for line in result["done"])
    assert (await apply_brand(_brand(tmp_path)))["done"] == ["Updated brand 'RevenueMindProAI' (revenuemind)"], \
        "applying the same file again changes nothing"

    settings = await load_settings(store_id)
    assert settings.enabled and settings.postiz.customer_id and settings.brand.website == "https://revenuemindproai.com"
    with pytest.raises(ValidationError, match="Shopify store"):
        await apply_brand(_brand(tmp_path, store={"id": se_db, "name": "Takeover"}))

    campaign = (await config_store.list_campaigns(store_id))[0]
    planned = await evaluate_campaign(store_id, campaign["id"], seed=1)
    assert planned["planned"] >= 1, planned
    await Worker(run_periodic=False).run_once(max_jobs=60)

    posts = await post_store.list_posts(store_id, campaign_id=campaign["id"])
    # Today's post is sent to the publisher; tomorrow's waits, approved, until its day.
    states = [(p["status"], p["error_message"]) for p in posts]
    assert posts and {status for status, _ in states} <= {"scheduled", "approved"} and ("scheduled", "") in states, states
    kinds = campaign["config"]["kinds"]
    assert [call["kind"] for call in seen][:len(posts)] == kinds[:len(posts)], "kinds go out in the configured order"
    first = next(p for p in posts if p["options"]["texts"]["brand_kind"] == kinds[0])
    post = await post_store.get_post(store_id, first["id"])
    assert post["content_type"] == "quote" and not post["product_ids"]
    assert post["template_key"] == "info_points_dark" and post["options"]["texts"]["topic"] == campaign["config"]["topics"][0]
    assert post["options"]["link_override"] == "https://revenuemindproai.com/book-call"
    assert post["targets"] and post["media"]
    captions = [t["caption"] for t in post["targets"] if t["provider"] in ("facebook", "x", "linkedin")]
    assert captions and all("revenuemindproai.com/book-call" in c for c in captions), captions
    assert all("utm_medium=autopilot" in c for c in captions)
    assert all(c.rstrip().endswith("#RevenueMindProAI") for c in captions), "the brand's closing hashtag marks every post"
    assert not await post_store.list_posts(se_db, limit=5), "nothing was planned for the other brand"

    sctx = await load_store_context(store_id)
    assert sctx.storefront_url == "https://revenuemindproai.com"
    assert (await db.get_stores())[0]["id"] == se_db, "the Shopify store stays the default store"

    # Recurring jobs: nothing that needs Shopify is queued for the brand.
    catalog_tasks = {task.name for task in periodic.PERIODIC_TASKS if task.needs_catalog}
    assert {"sync_catalog_incremental", "auto_occasion_promotions"} <= catalog_tasks
    await periodic.schedule_due_periodic_jobs()
    states = await observability.list_state("periodic:")
    assert f"periodic:sync_catalog_incremental:{se_db}" in states
    assert f"periodic:sync_catalog_incremental:{store_id}" not in states
    assert f"periodic:evaluate_campaigns:{store_id}" in states
