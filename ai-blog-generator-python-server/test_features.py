"""
test_features.py — Comprehensive feature tests for the AI Blog Generator server.

Covers:
  1. DB layer   — stores, models, prompts, generations, scheduled jobs, settings
  2. utils.py   — text_to_html
  3. providers  — ModelRecord, ProviderError, AllModelsFailedError, provider registry
  4. security   — hash_password / verify_password
  5. services   — llm_service failover, image_service soft-fail (mocked providers)
  6. HTTP routes — auth, generate, api, setup, schedule (httpx + FastAPI TestClient)

Run with:
    pytest test_features.py -v
or for quick summary:
    pytest test_features.py -v --tb=short
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# ── ensure project root is on sys.path ──────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

# ── set dummy env vars before any app imports ────────────────────────────────
os.environ.setdefault("SESSION_SECRET", "test-secret-key-for-pytest")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
os.environ.setdefault("GROK_API_KEY", "test-key")
os.environ.setdefault("REPLICATE_API_TOKEN", "test-key")

import db
import state
from config import (
    AppConfig,
    DeepSeekConfig,
    GrokConfig,
    LoggingConfig,
    PromptConfig,
    ServerConfig,
    StoreConfig,
)
from providers import AllModelsFailedError, ModelRecord, ProviderError
from security import hash_password, verify_password
from utils import text_to_html

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_app_config() -> AppConfig:
    """Minimal AppConfig for tests — no real API keys needed."""
    return AppConfig(
        server=ServerConfig(host="127.0.0.1", port=8000, mode="debug", secret_key="test"),
        logging=LoggingConfig(file_path="/tmp/test_blog.log", max_bytes=1024, backup_count=1),
        deepseek=DeepSeekConfig(
            api_key="test", endpoint="https://api.deepseek.com/chat/completions",
            model="deepseek-chat", temperature=0.7, timeout_seconds=90, max_retries=2,
            system_prompt="",
        ),
        grok=GrokConfig(
            api_key="test", endpoint="https://api.x.ai/v1/images/generations",
            model="grok-2-image", image_count=2, timeout_seconds=60,
        ),
        stores=[],
        prompts=[],
        default_prompt_id="",
    )


def _make_store(store_id: str = "store-test", name: str = "Test Store") -> dict:
    return {
        "id": store_id,
        "name": name,
        "myshopify_domain": f"{store_id}.myshopify.com",
        "client_id": "cid",
        "client_secret": "csec",
        "default_blog_handle": "news",
        "default_author": "Test Author",
        "sort_order": 0,
    }


def _make_model(store_id: str, model_type: str = "text", provider: str = "deepseek",
                is_active: bool = True, priority: int = 0) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "store_id": store_id,
        "name": f"{provider}-{model_type}",
        "provider": provider,
        "model_type": model_type,
        "model_name": "test-model",
        "api_key": "key-test",
        "endpoint": "",
        "extra_json": '{"temperature": 0.5}',
        "priority": priority,
        "is_active": 1 if is_active else 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# pytest-asyncio config
# ─────────────────────────────────────────────────────────────────────────────

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest_asyncio.fixture(scope="session")
async def tmp_db() -> AsyncGenerator[str, None]:
    """Create a fresh temp SQLite DB for the entire test session."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db.set_db_path(db_path)
    await db.init_db()
    state.config = _make_app_config()

    yield db_path

    os.unlink(db_path)


# ─────────────────────────────────────────────────────────────────────────────
# ① DB — admin password
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminPassword:
    async def test_no_password_initially(self, tmp_db):
        result = await db.get_admin_password_hash()
        assert result is None

    async def test_set_and_retrieve_password(self, tmp_db):
        await db.set_admin_password_hash("hashed_admin_pw")
        result = await db.get_admin_password_hash()
        assert result == "hashed_admin_pw"

    async def test_overwrite_password(self, tmp_db):
        await db.set_admin_password_hash("new_hash")
        result = await db.get_admin_password_hash()
        assert result == "new_hash"
        # Reset for other tests
        await db.set_admin_password_hash("")


# ─────────────────────────────────────────────────────────────────────────────
# ② DB — stores CRUD
# ─────────────────────────────────────────────────────────────────────────────

class TestStoresCRUD:
    async def test_stores_empty_initially(self, tmp_db):
        stores = await db.get_stores()
        assert isinstance(stores, list)

    async def test_upsert_and_get_store(self, tmp_db):
        store = _make_store("s1", "Store One")
        await db.upsert_store(store)

        result = await db.get_store("s1")
        assert result is not None
        assert result["name"] == "Store One"
        assert result["myshopify_domain"] == "s1.myshopify.com"

    async def test_update_existing_store(self, tmp_db):
        store = _make_store("s1", "Store One Updated")
        await db.upsert_store(store)

        result = await db.get_store("s1")
        assert result["name"] == "Store One Updated"

    async def test_get_nonexistent_store_returns_none(self, tmp_db):
        result = await db.get_store("does-not-exist")
        assert result is None

    async def test_stores_list(self, tmp_db):
        await db.upsert_store(_make_store("s2", "Store Two"))
        stores = await db.get_stores()
        ids = [s["id"] for s in stores]
        assert "s1" in ids
        assert "s2" in ids

    async def test_delete_store(self, tmp_db):
        await db.upsert_store(_make_store("s-del", "To Delete"))
        await db.delete_store("s-del")
        result = await db.get_store("s-del")
        assert result is None

    async def test_delete_store_cascades_models(self, tmp_db):
        await db.upsert_store(_make_store("s-cascade"))
        mid = await db.upsert_model(_make_model("s-cascade"))
        await db.delete_store("s-cascade")
        model = await db.get_model(mid)
        assert model is None


# ─────────────────────────────────────────────────────────────────────────────
# ③ DB — store passwords
# ─────────────────────────────────────────────────────────────────────────────

class TestStorePasswords:
    async def test_no_password_initially(self, tmp_db):
        result = await db.get_store_password_hash("s1")
        # May be None or "" depending on how row was inserted
        assert not result  # falsy either way

    async def test_set_and_verify(self, tmp_db):
        hashed = hash_password("mypassword")
        await db.set_store_password_hash("s1", hashed)
        stored = await db.get_store_password_hash("s1")
        assert stored is not None
        assert verify_password("mypassword", stored)
        assert not verify_password("wrongpass", stored)


# ─────────────────────────────────────────────────────────────────────────────
# ④ DB — store settings
# ─────────────────────────────────────────────────────────────────────────────

class TestStoreSettings:
    async def test_default_value(self, tmp_db):
        val = await db.get_store_setting("s1", "nonexistent_key", "default_val")
        assert val == "default_val"

    async def test_set_and_get(self, tmp_db):
        await db.set_store_settings("s1", {"theme": "dark", "lang": "en"})
        assert await db.get_store_setting("s1", "theme") == "dark"
        assert await db.get_store_setting("s1", "lang") == "en"

    async def test_get_all_settings(self, tmp_db):
        all_settings = await db.get_all_store_settings("s1")
        assert isinstance(all_settings, dict)
        assert all_settings["theme"] == "dark"

    async def test_overwrite_setting(self, tmp_db):
        await db.set_store_settings("s1", {"theme": "light"})
        val = await db.get_store_setting("s1", "theme")
        assert val == "light"


# ─────────────────────────────────────────────────────────────────────────────
# ⑤ DB — models CRUD
# ─────────────────────────────────────────────────────────────────────────────

class TestModelsCRUD:
    async def test_no_models_initially(self, tmp_db):
        models = await db.get_models("s1")
        assert isinstance(models, list)

    async def test_upsert_and_get_model(self, tmp_db):
        model = _make_model("s1", "text", "deepseek")
        mid = await db.upsert_model(model)
        result = await db.get_model(mid)
        assert result is not None
        assert result["provider"] == "deepseek"
        assert result["model_type"] == "text"

    async def test_auto_generates_id(self, tmp_db):
        model = _make_model("s1")
        del model["id"]
        mid = await db.upsert_model(model)
        assert mid  # non-empty UUID

    async def test_get_active_text_models(self, tmp_db):
        # Add active text + inactive text + active image
        active_text = _make_model("s2", "text", "openai", is_active=True)
        inactive_text = _make_model("s2", "text", "deepseek", is_active=False)
        active_image = _make_model("s2", "image", "grok", is_active=True)
        for m in [active_text, inactive_text, active_image]:
            await db.upsert_model(m)

        result = await db.get_active_text_models("s2")
        types = [r["model_type"] for r in result]
        actives = [r["is_active"] for r in result]
        assert all(t == "text" for t in types)
        assert all(a for a in actives)

    async def test_get_active_image_models(self, tmp_db):
        result = await db.get_active_image_models("s2")
        assert all(r["model_type"] == "image" for r in result)
        assert all(r["is_active"] for r in result)

    async def test_toggle_model_active(self, tmp_db):
        model = _make_model("s1", is_active=True)
        mid = await db.upsert_model(model)
        await db.set_model_active(mid, False)
        result = await db.get_model(mid)
        assert result["is_active"] == 0

        await db.set_model_active(mid, True)
        result = await db.get_model(mid)
        assert result["is_active"] == 1

    async def test_delete_model(self, tmp_db):
        model = _make_model("s1")
        mid = await db.upsert_model(model)
        await db.delete_model(mid)
        assert await db.get_model(mid) is None

    async def test_models_ordered_by_priority(self, tmp_db):
        sid = "s-prio"
        await db.upsert_store(_make_store(sid))
        await db.upsert_model(_make_model(sid, priority=5))
        await db.upsert_model(_make_model(sid, priority=1))
        await db.upsert_model(_make_model(sid, priority=3))
        models = await db.get_models(sid)
        priorities = [m["priority"] for m in models]
        assert priorities == sorted(priorities)


# ─────────────────────────────────────────────────────────────────────────────
# ⑥ DB — prompts CRUD
# ─────────────────────────────────────────────────────────────────────────────

class TestPromptsCRUD:
    async def test_no_prompts_initially(self, tmp_db):
        prompts = await db.get_prompts("s1")
        assert isinstance(prompts, list)

    async def test_upsert_and_get_prompt(self, tmp_db):
        pid = str(uuid.uuid4())
        await db.upsert_prompt({"id": pid, "store_id": "s1", "name": "My Prompt",
                                 "text": "Write about X", "sort_order": 0})
        prompts = await db.get_prompts("s1")
        names = [p["name"] for p in prompts]
        assert "My Prompt" in names

    async def test_update_prompt(self, tmp_db):
        pid = str(uuid.uuid4())
        await db.upsert_prompt({"id": pid, "store_id": "s1", "name": "Old",
                                 "text": "Old text", "sort_order": 0})
        await db.upsert_prompt({"id": pid, "store_id": "s1", "name": "Updated",
                                 "text": "New text", "sort_order": 0})
        prompts = await db.get_prompts("s1")
        match = next(p for p in prompts if p["id"] == pid)
        assert match["name"] == "Updated"
        assert match["text"] == "New text"

    async def test_delete_prompt(self, tmp_db):
        pid = str(uuid.uuid4())
        await db.upsert_prompt({"id": pid, "store_id": "s1", "name": "Del",
                                 "text": "x", "sort_order": 0})
        await db.delete_prompt(pid)
        prompts = await db.get_prompts("s1")
        assert not any(p["id"] == pid for p in prompts)

    async def test_prompts_scoped_to_store(self, tmp_db):
        p1 = str(uuid.uuid4())
        p2 = str(uuid.uuid4())
        await db.upsert_prompt({"id": p1, "store_id": "s1", "name": "P1", "text": "t", "sort_order": 0})
        await db.upsert_prompt({"id": p2, "store_id": "s2", "name": "P2", "text": "t", "sort_order": 0})
        s1_prompts = await db.get_prompts("s1")
        s2_prompts = await db.get_prompts("s2")
        s1_ids = [p["id"] for p in s1_prompts]
        s2_ids = [p["id"] for p in s2_prompts]
        assert p1 in s1_ids
        assert p1 not in s2_ids
        assert p2 in s2_ids


# ─────────────────────────────────────────────────────────────────────────────
# ⑦ DB — generations + errors
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerations:
    async def test_log_and_retrieve_generation(self, tmp_db):
        await db.log_generation(
            store_id="s1", store_name="Store One", blog_handle="news",
            prompt_id="pid1", prompt_text="Write about coffee",
            title="Coffee Blog", summary="About coffee",
            content_text="## Coffee\n\nFresh coffee brewing tips for daily routines.",
            keywords=["coffee", "brew"], hashtags=["#coffee"],
            image_count=2, article_id="art-1", article_url="https://shop.com/blog/1",
            status="published",
        )
        rows = await db.get_recent_generations(store_id="s1", limit=100)
        assert len(rows) >= 1
        latest = rows[0]
        assert latest["title"] == "Coffee Blog"
        assert "Fresh coffee brewing tips" in latest["content_text"]
        assert isinstance(latest["keywords"], list)
        assert "coffee" in latest["keywords"]
        assert isinstance(latest["hashtags"], list)

    async def test_generation_scoped_to_store(self, tmp_db):
        await db.log_generation(
            store_id="s2", store_name="Store Two", blog_handle="blog",
            prompt_id="", prompt_text="Write",
            title="S2 Post", summary="Summary", keywords=[], hashtags=[],
            image_count=0,
        )
        s1_rows = await db.get_recent_generations(store_id="s1")
        s2_rows = await db.get_recent_generations(store_id="s2")
        s1_titles = [r["title"] for r in s1_rows]
        s2_titles = [r["title"] for r in s2_rows]
        assert "S2 Post" not in s1_titles
        assert "S2 Post" in s2_titles

    async def test_get_all_generations_when_no_store_id(self, tmp_db):
        all_rows = await db.get_recent_generations(limit=100)
        assert len(all_rows) >= 2  # both s1 and s2 entries

    async def test_log_and_retrieve_model_error(self, tmp_db):
        await db.log_model_error("s1", "mid1", "deepseek", "provider_error", "Rate limited")
        errors = await db.get_recent_errors("s1", limit=5)
        assert len(errors) >= 1
        assert errors[0]["error_type"] == "provider_error"
        assert "Rate limited" in errors[0]["message"]


class TestSocialPosts:
    async def test_log_and_retrieve_social_post(self, tmp_db):
        await db.log_social_post(
            store_id="s1",
            store_name="Store One",
            workspace_id="workspace-1",
            campaign_name="Launch Wave",
            product_handle="pro-serum",
            product_title="Pro Serum",
            brief_text="Highlight glow and hydration",
            base_text="Glow routine launch",
            provider_texts={
                "instagram": "Glow starts here.",
                "facebook": "Hydration + glow combo.",
            },
            account_ids=["acc-1", "acc-2"],
            mode="draft",
            scheduled_at=None,
            publer_job_id="job-100",
            publer_status="queued",
            publer_failures=[],
        )

        rows = await db.get_recent_social_posts("s1", limit=10)
        assert rows
        latest = rows[0]
        assert latest["campaign_name"] == "Launch Wave"
        assert latest["provider_texts"]["instagram"] == "Glow starts here."
        assert latest["account_ids"] == ["acc-1", "acc-2"]

    async def test_update_social_post_job_status(self, tmp_db):
        await db.log_social_post(
            store_id="s1",
            store_name="Store One",
            workspace_id="workspace-2",
            campaign_name="Retarget Burst",
            product_handle="night-cream",
            product_title="Night Cream",
            brief_text="",
            base_text="Night routine",
            provider_texts={"x": "Night routine refresh"},
            account_ids=["acc-3"],
            mode="scheduled",
            scheduled_at="2026-01-02T10:00:00Z",
            publer_job_id="job-200",
            publer_status="queued",
            publer_failures=[],
        )

        await db.update_social_post_job_status(
            publer_job_id="job-200",
            status="done",
            failures=[{"account_id": "acc-3", "reason": "none"}],
        )

        rows = await db.get_recent_social_posts("s1", limit=20)
        match = next((row for row in rows if row["publer_job_id"] == "job-200"), None)
        assert match is not None
        assert match["publer_status"] == "done"
        assert isinstance(match["publer_failures_list"], list)


class TestSocialPostService:
    async def test_generate_variants_requires_product_title(self, tmp_db):
        from services import social_post_service

        with pytest.raises(ValueError, match="product_title is required"):
            await social_post_service.generate_social_post_variants(
                store_id="s1",
                store_name="Store One",
                product_title="",
                product_url="https://example.com/products/item",
                brief_text="",
            )

    async def test_generate_variants_fills_missing_provider_lines(self, tmp_db):
        from services import social_post_service

        await db.upsert_store(_make_store("social-s1", "Store One"))
        await db.upsert_model(_make_model("social-s1", "text", "deepseek", is_active=True))

        llm_payload = {
            "title": "Glow campaign",
            "summary": "Short objective",
            "keywords": ["glow", "hydration"],
            "hashtags": ["#Glow", "Hydration"],
            "content": "instagram: Insta caption only",
        }

        with patch(
            "services.social_post_service.llm_service.generate_text",
            new_callable=AsyncMock,
            return_value=llm_payload,
        ):
            result = await social_post_service.generate_social_post_variants(
                store_id="social-s1",
                store_name="Store One",
                product_title="Pro Serum",
                product_url="https://example.com/products/pro-serum",
                brief_text="Drive curiosity",
            )

        provider_texts = result["provider_texts"]
        assert provider_texts["instagram"].startswith("Insta caption only")
        assert all(provider in provider_texts for provider in ["facebook", "x", "linkedin", "pinterest"])
        assert "tiktok" not in provider_texts
        discount_url = result["discount_url"]
        assert discount_url.startswith("https://bioluxelab.com/discount/LAUNCH20?redirect=/products/pro-serum")
        assert all(discount_url in provider_texts[provider] for provider in ["instagram", "facebook", "x", "linkedin", "pinterest"])
        assert result["hashtags"][0].startswith("#")
        assert "Offer style to apply: Direct Offers" in result["text_generation_prompt"]
        assert isinstance(result["image_generation_prompts"], list)
        assert "text_generation_prompt_combined" in result


# ─────────────────────────────────────────────────────────────────────────────
# ⑧ DB — scheduled jobs
# ─────────────────────────────────────────────────────────────────────────────

class TestScheduledJobs:
    async def test_no_jobs_initially(self, tmp_db):
        jobs = await db.get_scheduled_jobs("s1")
        assert isinstance(jobs, list)

    async def test_upsert_and_retrieve_job(self, tmp_db):
        job_id = await db.upsert_job({
            "id": "", "store_id": "s1", "name": "Daily Post",
            "prompt_id": "pid1", "blog_handle": "news",
            "author": "Bot", "cron_expr": "0 9 * * 1-5",
            "timezone": "UTC", "is_active": 1, "next_run_at": None,
        })
        assert job_id
        jobs = await db.get_scheduled_jobs("s1")
        names = [j["name"] for j in jobs]
        assert "Daily Post" in names

    async def test_update_job(self, tmp_db):
        jid = str(uuid.uuid4())
        await db.upsert_job({
            "id": jid, "store_id": "s1", "name": "Old",
            "prompt_id": "", "blog_handle": "", "author": "",
            "cron_expr": "0 9 * * *", "timezone": "UTC",
            "is_active": 1, "next_run_at": None,
        })
        await db.upsert_job({
            "id": jid, "store_id": "s1", "name": "Updated",
            "prompt_id": "", "blog_handle": "", "author": "",
            "cron_expr": "0 10 * * *", "timezone": "UTC",
            "is_active": 1, "next_run_at": None,
        })
        jobs = await db.get_scheduled_jobs("s1")
        match = next((j for j in jobs if j["id"] == jid), None)
        assert match is not None
        assert match["name"] == "Updated"
        assert match["cron_expr"] == "0 10 * * *"

    async def test_delete_job(self, tmp_db):
        jid = await db.upsert_job({
            "id": "", "store_id": "s1", "name": "To Delete",
            "prompt_id": "", "blog_handle": "", "author": "",
            "cron_expr": "0 9 * * *", "timezone": "UTC",
            "is_active": 1, "next_run_at": None,
        })
        await db.delete_job(jid)
        jobs = await db.get_scheduled_jobs("s1")
        assert not any(j["id"] == jid for j in jobs)

    async def test_due_jobs(self, tmp_db):
        import time
        past = int(time.time()) - 100
        future = int(time.time()) + 3600

        jid_due = await db.upsert_job({
            "id": "", "store_id": "s1", "name": "Due",
            "prompt_id": "", "blog_handle": "", "author": "",
            "cron_expr": "* * * * *", "timezone": "UTC",
            "is_active": 1, "next_run_at": past,
        })
        jid_not_due = await db.upsert_job({
            "id": "", "store_id": "s1", "name": "NotDue",
            "prompt_id": "", "blog_handle": "", "author": "",
            "cron_expr": "* * * * *", "timezone": "UTC",
            "is_active": 1, "next_run_at": future,
        })

        due = await db.get_due_jobs()
        due_ids = [j["id"] for j in due]
        assert jid_due in due_ids
        assert jid_not_due not in due_ids

    async def test_update_job_run_times(self, tmp_db):
        import time
        jid = await db.upsert_job({
            "id": "", "store_id": "s1", "name": "RunTest",
            "prompt_id": "", "blog_handle": "", "author": "",
            "cron_expr": "0 9 * * *", "timezone": "UTC",
            "is_active": 1, "next_run_at": None,
        })
        now = int(time.time())
        await db.update_job_run_times(jid, now, now + 86400)
        jobs = await db.get_scheduled_jobs("s1")
        match = next((j for j in jobs if j["id"] == jid), None)
        assert match["last_run_at"] == now
        assert match["next_run_at"] == now + 86400

    async def test_get_all_active_jobs(self, tmp_db):
        active_jobs = await db.get_all_active_jobs()
        assert isinstance(active_jobs, list)
        # All must be active
        for j in active_jobs:
            assert j["is_active"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# ⑨ DB — token cache
# ─────────────────────────────────────────────────────────────────────────────

class TestTokenCache:
    async def test_no_token_initially(self, tmp_db):
        token = await db.get_cached_token("s1")
        assert token is None

    async def test_save_and_retrieve_token(self, tmp_db):
        import time
        expires = int(time.time()) + 3600
        await db.save_token("s1", "tok-abc123", expires)
        token = await db.get_cached_token("s1")
        assert token == "tok-abc123"

    async def test_expired_token_not_returned(self, tmp_db):
        import time
        past = int(time.time()) - 10
        await db.save_token("s1", "expired-token", past)
        token = await db.get_cached_token("s1")
        assert token is None


# ─────────────────────────────────────────────────────────────────────────────
# ⑩ utils.py — text_to_html
# ─────────────────────────────────────────────────────────────────────────────

class TestTextToHtml:
    def test_plain_paragraph(self):
        result = text_to_html("Hello world")
        assert result == "<p>Hello world</p>"

    def test_h2_heading_double_hash(self):
        result = text_to_html("## Section Title")
        assert result == "<h2>Section Title</h2>"

    def test_h1_heading_single_hash_becomes_h2(self):
        result = text_to_html("# Main Title")
        assert result == "<h2>Main Title</h2>"

    def test_bullet_list(self):
        result = text_to_html("- Item A\n- Item B\n- Item C")
        assert "<ul>" in result
        assert "<li>Item A</li>" in result
        assert "<li>Item B</li>" in result
        assert "<li>Item C</li>" in result
        assert "</ul>" in result

    def test_asterisk_bullet(self):
        result = text_to_html("* Star item")
        assert "<li>Star item</li>" in result

    def test_list_closes_on_blank_line(self):
        result = text_to_html("- A\n- B\n\nParagraph after")
        assert "</ul>" in result
        assert "<p>Paragraph after</p>" in result
        # Ensure </ul> comes before <p>
        assert result.index("</ul>") < result.index("<p>Paragraph after</p>")

    def test_heading_closes_list(self):
        result = text_to_html("- Item\n## New Section")
        assert "</ul>" in result
        assert "<h2>New Section</h2>" in result

    def test_empty_string(self):
        result = text_to_html("")
        assert result == ""

    def test_multiline_mixed_content(self):
        text = "## Intro\n\nThis is a paragraph.\n\n- Point 1\n- Point 2\n\n## Conclusion\n\nFinal thoughts."
        result = text_to_html(text)
        assert "<h2>Intro</h2>" in result
        assert "<p>This is a paragraph.</p>" in result
        assert "<li>Point 1</li>" in result
        assert "<h2>Conclusion</h2>" in result
        assert "<p>Final thoughts.</p>" in result

    def test_trailing_list_gets_closed(self):
        result = text_to_html("- Only item")
        assert result.endswith("</ul>")


# ─────────────────────────────────────────────────────────────────────────────
# ⑪ providers — ModelRecord, ProviderError, AllModelsFailedError
# ─────────────────────────────────────────────────────────────────────────────

class TestProviderTypes:
    def test_model_record_from_dict(self):
        d = {
            "id": "mid1", "store_id": "s1", "name": "Test Model",
            "provider": "deepseek", "model_type": "text",
            "model_name": "deepseek-chat", "api_key": "key",
            "endpoint": "", "extra_json": '{"temperature": 0.9}',
            "priority": 1, "is_active": True,
        }
        m = ModelRecord.from_dict(d)
        assert m.id == "mid1"
        assert m.provider == "deepseek"
        assert m.extra["temperature"] == 0.9
        assert m.is_active is True

    def test_model_record_extra_defaults_on_bad_json(self):
        d = {
            "id": "x", "store_id": "s", "name": "n", "provider": "p",
            "model_type": "text", "model_name": "", "api_key": "",
            "endpoint": "", "extra_json": "INVALID JSON",
            "priority": 0, "is_active": 1,
        }
        m = ModelRecord.from_dict(d)
        assert m.extra == {}

    def test_model_record_extra_empty_json(self):
        d = {
            "id": "x", "store_id": "s", "name": "n", "provider": "p",
            "model_type": "text", "model_name": "", "api_key": "",
            "endpoint": "", "extra_json": "",
            "priority": 0, "is_active": 0,
        }
        m = ModelRecord.from_dict(d)
        assert m.extra == {}
        assert m.is_active is False

    def test_model_record_resolves_provider_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_TOKEN", "replicate-test-key")
        m = ModelRecord.from_dict({
            "id": "x", "store_id": "s", "name": "n", "provider": "replicate",
            "model_type": "image", "model_name": "", "api_key": "",
            "endpoint": "", "extra_json": "{}", "priority": 0, "is_active": 1,
        })
        assert m.resolved_api_key == "replicate-test-key"

    def test_model_record_resolves_explicit_env_reference(self, monkeypatch):
        monkeypatch.setenv("CUSTOM_PROVIDER_KEY", "custom-test-key")
        m = ModelRecord.from_dict({
            "id": "x", "store_id": "s", "name": "n", "provider": "replicate",
            "model_type": "image", "model_name": "", "api_key": "env:CUSTOM_PROVIDER_KEY",
            "endpoint": "", "extra_json": "{}", "priority": 0, "is_active": 1,
        })
        assert m.resolved_api_key == "custom-test-key"

    def test_provider_error_retryable_default(self):
        err = ProviderError("Something went wrong")
        assert err.retryable is True
        assert str(err) == "Something went wrong"

    def test_provider_error_non_retryable(self):
        err = ProviderError("Unauthorized", retryable=False)
        assert err.retryable is False

    def test_all_models_failed_error(self):
        failures = [("model-a", "timeout"), ("model-b", "401")]
        err = AllModelsFailedError(failures)
        assert err.failures == failures
        assert "model-a" in str(err)
        assert "model-b" in str(err)

    def test_provider_registry_text(self):
        from providers import get_text_provider, DeepSeekProvider, OpenAITextProvider
        m = ModelRecord.from_dict({
            "id": "x", "store_id": "s", "name": "n", "provider": "deepseek",
            "model_type": "text", "model_name": "", "api_key": "k",
            "endpoint": "", "extra_json": "{}", "priority": 0, "is_active": 1,
        })
        p = get_text_provider(m)
        assert isinstance(p, DeepSeekProvider)

    def test_provider_registry_image(self):
        from providers import get_image_provider, GrokProvider
        m = ModelRecord.from_dict({
            "id": "x", "store_id": "s", "name": "n", "provider": "grok",
            "model_type": "image", "model_name": "", "api_key": "k",
            "endpoint": "", "extra_json": "{}", "priority": 0, "is_active": 1,
        })
        p = get_image_provider(m)
        assert isinstance(p, GrokProvider)

    def test_unknown_provider_raises(self):
        from providers import get_text_provider
        m = ModelRecord.from_dict({
            "id": "x", "store_id": "s", "name": "n", "provider": "unknown",
            "model_type": "text", "model_name": "", "api_key": "",
            "endpoint": "", "extra_json": "{}", "priority": 0, "is_active": 1,
        })
        with pytest.raises(ValueError, match="Unknown text provider"):
            get_text_provider(m)


# ─────────────────────────────────────────────────────────────────────────────
# ⑫ security — hash_password / verify_password
# ─────────────────────────────────────────────────────────────────────────────

class TestSecurity:
    def test_hash_is_different_from_plain(self):
        hashed = hash_password("mysecret")
        assert hashed != "mysecret"

    def test_verify_correct_password(self):
        hashed = hash_password("correct")
        assert verify_password("correct", hashed) is True

    def test_reject_wrong_password(self):
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False

    def test_two_hashes_of_same_password_differ(self):
        # bcrypt generates unique salts
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2
        assert verify_password("same", h1)
        assert verify_password("same", h2)

    def test_verify_malformed_hash_returns_false(self):
        assert verify_password("anything", "not-a-valid-hash") is False

    def test_verify_empty_password(self):
        hashed = hash_password("")
        assert verify_password("", hashed) is True
        assert verify_password("notempty", hashed) is False


# ─────────────────────────────────────────────────────────────────────────────
# ⑬ services — llm_service failover (mocked providers)
# ─────────────────────────────────────────────────────────────────────────────

class TestLLMService:
    async def _setup_text_model(self, tmp_db, store_id: str, provider: str = "deepseek"):
        await db.upsert_store(_make_store(store_id))
        model = _make_model(store_id, "text", provider, is_active=True)
        await db.upsert_model(model)
        return model

    async def test_generate_text_success(self, tmp_db):
        from services import llm_service
        sid = "llm-s1"
        await self._setup_text_model(tmp_db, sid)

        mock_result = {
            "title": "Test Title", "summary": "Test summary",
            "content": "Body content", "keywords": ["a"], "hashtags": ["#a"],
        }
        mock_provider = AsyncMock()
        mock_provider.generate_text = AsyncMock(return_value=mock_result)

        with patch("providers.get_text_provider", return_value=mock_provider):
            result = await llm_service.generate_text(sid, "Write about X")

        assert result["title"] == "Test Title"
        assert result["keywords"] == ["a"]

    async def test_generate_text_no_models_raises(self, tmp_db):
        from services import llm_service
        sid = "llm-empty"
        await db.upsert_store(_make_store(sid))
        # No models added

        with pytest.raises(AllModelsFailedError, match="No active text models"):
            await llm_service.generate_text(sid, "prompt")

    async def test_generate_text_failover_to_second_model(self, tmp_db):
        from services import llm_service
        sid = "llm-failover"
        await db.upsert_store(_make_store(sid))
        # Two active text models: priority 0 fails, priority 1 succeeds
        m1 = _make_model(sid, "text", "deepseek", priority=0)
        m2 = _make_model(sid, "text", "openai", priority=1)
        await db.upsert_model(m1)
        await db.upsert_model(m2)

        good_result = {
            "title": "Fallback Title", "summary": "s", "content": "c",
            "keywords": [], "hashtags": [],
        }
        call_count = {"n": 0}

        async def side_effect(prompt, system_prompt=""):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ProviderError("timeout", retryable=True)
            return good_result

        mock_provider = AsyncMock()
        mock_provider.generate_text = side_effect

        with patch("providers.get_text_provider", return_value=mock_provider):
            result = await llm_service.generate_text(sid, "prompt")

        assert result["title"] == "Fallback Title"
        assert call_count["n"] == 2

    async def test_generate_text_non_retryable_skips_provider(self, tmp_db):
        from services import llm_service
        sid = "llm-nonretry"
        await db.upsert_store(_make_store(sid))
        # Two deepseek models — non-retryable auth error should skip both
        m1 = _make_model(sid, "text", "deepseek", priority=0)
        m2 = _make_model(sid, "text", "deepseek", priority=1)
        await db.upsert_model(m1)
        await db.upsert_model(m2)

        mock_provider = AsyncMock()
        mock_provider.generate_text = AsyncMock(
            side_effect=ProviderError("401 Unauthorized", retryable=False)
        )

        with patch("providers.get_text_provider", return_value=mock_provider):
            with pytest.raises(AllModelsFailedError):
                await llm_service.generate_text(sid, "prompt")

        # Should have only been called once (non-retryable skips rest of same provider)
        assert mock_provider.generate_text.call_count == 1

    async def test_generate_text_all_fail_raises(self, tmp_db):
        from services import llm_service
        sid = "llm-allfail"
        await db.upsert_store(_make_store(sid))
        await db.upsert_model(_make_model(sid, "text", "deepseek"))

        mock_provider = AsyncMock()
        mock_provider.generate_text = AsyncMock(
            side_effect=ProviderError("All broken", retryable=True)
        )

        with patch("providers.get_text_provider", return_value=mock_provider):
            with pytest.raises(AllModelsFailedError) as exc_info:
                await llm_service.generate_text(sid, "prompt")

        assert len(exc_info.value.failures) == 1


# ─────────────────────────────────────────────────────────────────────────────
# ⑭ services — image_service soft failure (mocked providers)
# ─────────────────────────────────────────────────────────────────────────────

class TestImageService:
    async def test_returns_urls_on_success(self, tmp_db):
        from services import image_service
        sid = "img-s1"
        await db.upsert_store(_make_store(sid))
        await db.upsert_model(_make_model(sid, "image", "grok"))

        mock_provider = AsyncMock()
        mock_provider.generate_images = AsyncMock(
            return_value=["https://img.example.com/1.png", "https://img.example.com/2.png"]
        )

        with patch("providers.get_image_provider", return_value=mock_provider):
            urls = await image_service.generate_images(sid, "Title", "Summary", "Prompt")

        assert len(urls) == 4
        assert all(u.startswith("https://") for u in urls)

    async def test_returns_empty_list_when_no_models(self, tmp_db):
        from services import image_service
        sid = "img-nomodels"
        await db.upsert_store(_make_store(sid))
        # No image models

        urls = await image_service.generate_images(sid, "Title", "Summary", "Prompt")
        assert urls == []

    async def test_soft_fail_returns_empty_on_all_errors(self, tmp_db):
        from services import image_service
        sid = "img-fail"
        await db.upsert_store(_make_store(sid))
        await db.upsert_model(_make_model(sid, "image", "grok"))

        mock_provider = AsyncMock()
        mock_provider.generate_images = AsyncMock(
            side_effect=ProviderError("Image service down", retryable=True)
        )

        with patch("providers.get_image_provider", return_value=mock_provider):
            urls = await image_service.generate_images(sid, "Title", "Summary", "Prompt")

        # Soft failure — returns empty list, does NOT raise
        assert urls == []

    async def test_image_prompt_includes_title(self, tmp_db):
        from services import image_service
        sid = "img-prompt"
        await db.upsert_store(_make_store(sid))
        await db.upsert_model(_make_model(sid, "image", "grok"))

        captured = {}
        mock_provider = AsyncMock()

        async def capture_prompt(prompt, count):
            captured["prompt"] = prompt
            return ["https://img.example.com/1.jpg"]

        mock_provider.generate_images = capture_prompt

        with patch("providers.get_image_provider", return_value=mock_provider):
            await image_service.generate_images(sid, "My Blog Title", "Summary", "Prompt")

        assert "My Blog Title" in captured["prompt"]

    async def test_generate_typed_images_returns_step_and_checklist_types(self, tmp_db):
        from services import image_service

        sid = "img-typed"
        await db.upsert_store(_make_store(sid))

        async def fake_generate_one(store_id, image_prompt, label):
            slug = label.replace(" ", "-")
            return f"https://img.example.com/{slug}.png"

        with patch("services.image_service._generate_one", side_effect=fake_generate_one):
            urls, image_types, image_labels = await image_service.generate_typed_images(
                sid,
                "How To Build Better Sleep Habits",
                "A practical summary.",
                "Prompt context.",
            )

        assert image_types == ["hero_photo", "infographic", "step_card", "checklist_card"]
        assert image_labels == [
            "Hero Photo",
            "Infographic",
            "Step-by-Step Visual Card",
            "Checklist/Tips Card",
        ]
        assert len(urls) == 4

    async def test_use_product_featured_image_falls_back_when_all_types_are_hero(self, tmp_db):
        from services import image_service

        merged_urls, merged_types, merged_labels = image_service.use_product_featured_image(
            "https://cdn.shopify.com/product-main.png",
            [
                "https://img.example.com/hero-1.png",
                "https://img.example.com/hero-2.png",
                "https://img.example.com/hero-3.png",
                "https://img.example.com/hero-4.png",
            ],
            ["hero_photo", "hero_photo", "hero_photo", "hero_photo"],
            ["Hero 1", "Hero 2", "Hero 3", "Hero 4"],
        )

        assert merged_urls[0] == "https://cdn.shopify.com/product-main.png"
        assert merged_types[0] == "product"
        assert merged_labels[0] == "Product Image"
        assert merged_urls[1:] == [
            "https://img.example.com/hero-1.png",
            "https://img.example.com/hero-2.png",
            "https://img.example.com/hero-3.png",
            "https://img.example.com/hero-4.png",
        ]
        assert len(merged_urls) == 5

    async def test_use_product_featured_image_handles_missing_types_and_labels(self, tmp_db):
        from services import image_service

        merged_urls, merged_types, merged_labels = image_service.use_product_featured_image(
            "https://cdn.shopify.com/product-main.png",
            [
                "https://img.example.com/a.png",
                "https://img.example.com/b.png",
                "https://img.example.com/c.png",
                "https://img.example.com/d.png",
            ],
            [],
            [],
        )

        assert merged_urls == [
            "https://cdn.shopify.com/product-main.png",
            "https://img.example.com/a.png",
            "https://img.example.com/b.png",
            "https://img.example.com/c.png",
            "https://img.example.com/d.png",
        ]
        assert merged_types == ["product", "generated", "generated", "generated", "generated"]
        assert merged_labels == ["Product Image", "Generated", "Generated", "Generated", "Generated"]

    async def test_generate_feature_image_falls_back_to_simpler_prompt(self, tmp_db):
        from services import image_service

        sid = "img-feature-fallback"
        await db.upsert_store(_make_store(sid))

        captured_prompts = []

        async def fake_generate_one(store_id, image_prompt, label):
            captured_prompts.append(image_prompt)
            if len(captured_prompts) == 1:
                return None
            return "https://img.example.com/featured.jpg"

        with patch("services.image_service._generate_one", side_effect=fake_generate_one):
            url = await image_service.generate_feature_image(sid, "My Blog Title", "Summary", "Prompt")

        assert url == "https://img.example.com/featured.jpg"
        assert len(captured_prompts) == 2
        assert captured_prompts[0].startswith("Professional high-quality photograph for blog article: My Blog Title.")
        assert captured_prompts[1].startswith("Editorial wellness lifestyle photograph illustrating: My Blog Title.")


class TestBlogScope:
    async def test_normalize_scheduled_blog_handle_accepts_legacy_auto_alias(self, tmp_db):
        from services import blog_scope

        assert blog_scope.is_auto_blog_handle("auto") is True
        assert blog_scope.normalize_scheduled_blog_handle("auto") == blog_scope.AUTO_BLOG_HANDLE

    async def test_scope_compatible_title_selection_skips_mismatch(self, tmp_db):
        from services import blog_scope, title_service

        sid = "scope-title"
        await db.upsert_store(_make_store(sid, "Store One"))
        await db.add_titles(sid, [
            {
                "title": "Best Sleep Routine For Deep Rest",
                "keyword": "sleep routine",
                "search_intent": "Improve sleep quality",
                "meta_description": "Sleep guide",
            },
            {
                "title": "Best Mobility Routine For Tight Hips At Home",
                "keyword": "mobility routine",
                "search_intent": "Improve mobility at home",
                "meta_description": "Mobility guide",
            },
        ])

        scope = blog_scope.BlogScope(
            handle="home-fitness-mobility",
            section_name="Home Fitness Mobility",
            focus_terms=("fitness", "mobility"),
        )

        row = await title_service.pop_blog_title_for_scope(sid, scope)

        assert row is not None
        assert row["title"] == "Best Mobility Routine For Tight Hips At Home"
        remaining = await db.get_title_pool(sid)
        assert any(t["title"] == "Best Sleep Routine For Deep Rest" for t in remaining)

    async def test_scope_compatible_keyword_selection_skips_mismatch(self, tmp_db):
        from services import blog_scope

        sid = "scope-keyword"
        await db.upsert_store(_make_store(sid, "Store One"))
        await db.add_keywords(sid, [
            {"keyword": "sleep routine for beginners", "content": "sleep content"},
            {"keyword": "mobility exercises at home", "content": "mobility content"},
        ])

        scope = blog_scope.BlogScope(
            handle="home-fitness-mobility",
            section_name="Home Fitness Mobility",
            focus_terms=("fitness", "mobility"),
        )

        row = await blog_scope.pop_scoped_keyword(sid, scope)

        assert row is not None
        assert row["keyword"] == "mobility exercises at home"
        remaining = await db.get_keyword_pool(sid)
        assert any(k["keyword"] == "sleep routine for beginners" for k in remaining)

    async def test_best_matching_scope_prefers_matching_blog_handle(self, tmp_db):
        from services import blog_scope

        scopes = [
            blog_scope.BlogScope(
                handle="sleep-advice",
                section_name="Sleep Advice",
                focus_terms=("sleep", "routine"),
            ),
            blog_scope.BlogScope(
                handle="home-fitness-mobility",
                section_name="Home Fitness Mobility",
                focus_terms=("fitness", "mobility"),
            ),
        ]

        match = blog_scope.best_matching_scope(
            "Best home mobility routine for beginners",
            scopes,
        )

        assert match is not None
        assert match.handle == "home-fitness-mobility"


class TestSchedulerRouting:
    async def test_get_next_run_at_respects_timezone(self):
        from services.schedule_time import get_next_run_at

        next_run = get_next_run_at(
            "0 16 * * *",
            "Europe/London",
            now_utc=dt.datetime(2026, 6, 5, 14, 30, tzinfo=dt.timezone.utc),
        )

        assert next_run == int(dt.datetime(2026, 6, 5, 15, 0, tzinfo=dt.timezone.utc).timestamp())

    async def test_get_next_run_at_returns_none_for_invalid_timezone(self):
        from services.schedule_time import get_next_run_at

        next_run = get_next_run_at(
            "0 16 * * *",
            "Not/AZone",
            now_utc=dt.datetime(2026, 6, 5, 14, 30, tzinfo=dt.timezone.utc),
        )

        assert next_run is None

    async def test_process_job_blank_blog_handle_auto_routes_title_pool(self, tmp_db):
        import scheduler

        sid = "sched-blank-auto-store"

        await db.upsert_store(_make_store(sid, "Store One Updated"))
        await db.upsert_prompt({
            "id": "sched-blank-auto",
            "store_id": sid,
            "name": "Scheduler Prompt",
            "text": "Write a useful store blog post.",
            "sort_order": 0,
        })
        await db.add_titles(
            sid,
            [
                {
                    "title": "Best Home Mobility Routine for Beginners",
                    "keyword": "home mobility routine",
                    "search_intent": "Mobility improvement",
                    "meta_description": "Mobility article.",
                }
            ],
        )

        job = {
            "id": "job-blank-auto-1",
            "store_id": sid,
            "name": "Blank Handle Auto Route Job",
            "prompt_id": "sched-blank-auto",
            "blog_handle": "",
            "author": "",
            "cron_expr": "0 9 * * *",
            "use_keyword_pool": 0,
            "is_product_blog": 0,
        }

        with patch(
            "scheduler.blog_scope.get_blog_options",
            new_callable=AsyncMock,
            return_value=[
                {"handle": "sleep-advice", "title": "Sleep Advice"},
                {"handle": "home-fitness-mobility", "title": "Home Fitness Mobility"},
            ],
        ), \
             patch(
                 "scheduler.publish_service.run",
                 new_callable=AsyncMock,
                 return_value=SimpleNamespace(title="Done", article_id="1"),
             ) as publish_mock, \
             patch("scheduler._get_next_run", return_value=1234567890):
            await scheduler._process_job(job)

        assert publish_mock.await_count == 1
        assert publish_mock.await_args.kwargs["blog_handle"] == "home-fitness-mobility"
        assert publish_mock.await_args.kwargs["preselected_title_row"]["title"] == "Best Home Mobility Routine for Beginners"

    async def test_process_job_auto_routes_title_pool_to_matching_blog_handle(self, tmp_db):
        import scheduler

        sid = "sched-auto-store"

        await db.upsert_store(_make_store(sid, "Store One Updated"))
        await db.upsert_prompt({
            "id": "sched-auto",
            "store_id": sid,
            "name": "Scheduler Prompt",
            "text": "Write a useful store blog post.",
            "sort_order": 0,
        })
        await db.add_titles(
            sid,
            [
                {
                    "title": "Best Home Mobility Routine for Beginners",
                    "keyword": "home mobility routine",
                    "search_intent": "Mobility improvement",
                    "meta_description": "Mobility article.",
                }
            ],
        )

        job = {
            "id": "job-auto-1",
            "store_id": sid,
            "name": "Auto Route Job",
            "prompt_id": "sched-auto",
            "blog_handle": "__auto__",
            "author": "",
            "cron_expr": "0 9 * * *",
            "use_keyword_pool": 0,
            "is_product_blog": 0,
        }

        with patch(
            "scheduler.blog_scope.get_blog_options",
            new_callable=AsyncMock,
            return_value=[
                {"handle": "sleep-advice", "title": "Sleep Advice"},
                {"handle": "home-fitness-mobility", "title": "Home Fitness Mobility"},
            ],
        ), \
             patch(
                 "scheduler.publish_service.run",
                 new_callable=AsyncMock,
                 return_value=SimpleNamespace(title="Done", article_id="1"),
             ) as publish_mock, \
             patch("scheduler._get_next_run", return_value=1234567890):
            await scheduler._process_job(job)

        assert publish_mock.await_count == 1
        assert publish_mock.await_args.kwargs["blog_handle"] == "home-fitness-mobility"
        assert publish_mock.await_args.kwargs["preselected_title_row"]["title"] == "Best Home Mobility Routine for Beginners"

    async def test_process_product_job_auto_blog_handle_falls_back_to_default(self, tmp_db):
        import scheduler

        sid = "sched-product-auto-store"

        await db.upsert_store(_make_store(sid, "Store One Updated"))
        await db.upsert_prompt({
            "id": "sched-product-auto",
            "store_id": sid,
            "name": "Scheduler Product Prompt",
            "text": "Write a useful product blog post.",
            "sort_order": 0,
        })

        job = {
            "id": "job-product-auto-1",
            "store_id": sid,
            "name": "Product Auto Route Job",
            "prompt_id": "sched-product-auto",
            "blog_handle": "__auto__",
            "author": "",
            "cron_expr": "0 9 * * *",
            "use_keyword_pool": 0,
            "is_product_blog": 1,
        }

        with patch(
            "scheduler.shopify_client.fetch_products",
            new_callable=AsyncMock,
            return_value=[],
        ), \
             patch(
                 "scheduler.publish_service.run",
                 new_callable=AsyncMock,
                 return_value=SimpleNamespace(title="Done", article_id="1"),
             ) as publish_mock, \
             patch("scheduler._get_next_run", return_value=1234567890):
            await scheduler._process_job(job)

        assert publish_mock.await_count == 1
        assert publish_mock.await_args.kwargs["blog_handle"] == "news"


# ─────────────────────────────────────────────────────────────────────────────
# ⑮ HTTP Routes (via httpx + FastAPI TestClient)
# ─────────────────────────────────────────────────────────────────────────────

def _make_session_cookie(session_data: dict, secret: str = "test-secret-key-for-pytest") -> str:
    """Build a valid Starlette signed session cookie without going through /login."""
    import base64
    import json
    import itsdangerous
    signer = itsdangerous.TimestampSigner(secret)
    payload = base64.b64encode(json.dumps(session_data).encode()).decode()
    return signer.sign(payload).decode()

@pytest_asyncio.fixture(scope="session")
async def http_client(tmp_db):
    """Shared httpx AsyncClient using the real app with test DB."""
    import httpx
    from main import app

    # Patch the lifespan so it uses our already-initialised test DB
    @asynccontextmanager
    async def test_lifespan(app):
        db.set_db_path(tmp_db)
        state.config = _make_app_config()
        yield

    app.router.lifespan_context = test_lifespan

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        yield client


class TestHealthEndpoint:
    async def test_health_returns_ok(self, http_client):
        response = await http_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    async def test_health_no_auth_required(self, http_client):
        # /health is a public path — should not redirect to /login
        response = await http_client.get("/health")
        assert response.status_code == 200


class TestAuthRoutes:
    async def test_login_page_renders(self, http_client):
        response = await http_client.get("/login")
        assert response.status_code == 200
        assert b"login" in response.content.lower() or b"store" in response.content.lower()

    async def test_unauthenticated_redirect_to_login(self, http_client):
        # Fresh client with no session — should redirect / to /login
        import httpx
        from main import app

        @asynccontextmanager
        async def test_lifespan(a):
            db.set_db_path(http_client._transport._app.__dict__.get("_db_path", ""))
            state.config = _make_app_config()
            yield

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as fresh:
            resp = await fresh.get("/")
        assert resp.status_code in (302, 303, 307)
        assert "/login" in resp.headers.get("location", "")

    async def test_admin_login_sets_session(self, http_client, tmp_db):
        # Reset admin password first
        await db.set_admin_password_hash("")
        response = await http_client.post(
            "/login",
            data={"store_id": "__admin__", "password": "adminpass", "next": "/"},
        )
        # Should redirect on success
        assert response.status_code in (302, 303)
        assert "aiblog_session" in response.cookies or response.headers.get("location")

    async def test_admin_wrong_password_rejected(self, http_client):
        response = await http_client.post(
            "/login",
            data={"store_id": "__admin__", "password": "wrongpassword", "next": "/"},
        )
        # Re-renders login with error, or 200 with error message
        assert response.status_code in (200, 303)
        if response.status_code == 200:
            assert b"incorrect" in response.content.lower() or b"password" in response.content.lower()

    async def test_store_login_first_time_sets_password(self, http_client, tmp_db):
        # Ensure store s1 exists with no password
        await db.set_store_password_hash("s1", "")
        response = await http_client.post(
            "/login",
            data={"store_id": "s1", "password": "storepass123", "next": "/"},
        )
        assert response.status_code in (302, 303)
        # Verify password was persisted
        stored = await db.get_store_password_hash("s1")
        assert stored
        assert verify_password("storepass123", stored)

    async def test_logout_clears_session(self, http_client):
        response = await http_client.get("/logout")
        assert response.status_code in (302, 303)
        assert "/login" in response.headers.get("location", "")

    async def test_unknown_store_login_fails(self, http_client):
        response = await http_client.post(
            "/login",
            data={"store_id": "nonexistent-store-xyz", "password": "pass", "next": "/"},
        )
        assert response.status_code in (200, 303)
        if response.status_code == 200:
            assert b"unknown" in response.content.lower() or b"store" in response.content.lower()


class TestAuthedRoutes:
    """Tests that require an authenticated session (admin or store)."""

    @pytest_asyncio.fixture
    async def admin_client(self, tmp_db):
        """Client with admin session injected directly (no /login, avoids rate limit)."""
        import httpx
        from main import app

        @asynccontextmanager
        async def test_lifespan(a):
            db.set_db_path(tmp_db)
            state.config = _make_app_config()
            yield

        app.router.lifespan_context = test_lifespan

        cookie = _make_session_cookie(
            {"authenticated": True, "store_id": "__admin__", "store_name": "Admin"}
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=True,
            cookies={"aiblog_session": cookie},
        ) as c:
            yield c

    @pytest_asyncio.fixture
    async def store_client(self, tmp_db):
        """Client with store s1 session injected directly."""
        import httpx
        from main import app

        @asynccontextmanager
        async def test_lifespan(a):
            db.set_db_path(tmp_db)
            state.config = _make_app_config()
            yield

        app.router.lifespan_context = test_lifespan

        cookie = _make_session_cookie(
            {"authenticated": True, "store_id": "s1", "store_name": "Store One Updated"}
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=True,
            cookies={"aiblog_session": cookie},
        ) as c:
            yield c

    async def test_admin_setup_page(self, admin_client):
        resp = await admin_client.get("/setup")
        assert resp.status_code == 200
        assert b"store" in resp.content.lower()

    async def test_store_setup_page_shows_react_app_install_button(self, store_client, monkeypatch):
        await db.upsert_store(_make_store("s1", "Store One Updated"))
        monkeypatch.setenv("SHOPIFY_REACT_APP_URL", "https://react.example.com")

        resp = await store_client.get("/setup?tab=shopify")

        assert resp.status_code == 200
        assert b"install / open shopify react app" in resp.content.lower()
        assert b"react.example.com/auth/login?shop=s1.myshopify.com" in resp.content.lower()

    async def test_store_index_page(self, store_client):
        resp = await store_client.get("/")
        assert resp.status_code == 200

    async def test_history_page(self, store_client):
        resp = await store_client.get("/history")
        assert resp.status_code == 200

    async def test_history_scan_shows_current_store_posts(self, store_client):
        from shopify_client import ShopifyArticle

        await db.upsert_store(_make_store("s1", "Store One Updated"))
        article = ShopifyArticle(
            id=123,
            blog_id=77,
            blog_handle="news",
            title="Low Quality Store Post",
            handle="low-quality-store-post",
            body_html="<p>Tiny post.</p>",
            summary_html="<p>Too short.</p>",
            tags="",
            article_url="https://s1.com/blogs/news/low-quality-store-post",
            image_url="",
            published_at="2026-05-04T10:00:00Z",
        )

        with patch("routes.api.shopify_client.fetch_store_articles",
                   new_callable=AsyncMock, return_value=[article]):
            resp = await store_client.get("/history?scan_store=1")

        assert resp.status_code == 200
        assert b"current store posts" in resp.content.lower()
        assert b"low quality store post" in resp.content.lower()
        assert b"delete" in resp.content.lower()

    async def test_delete_current_store_post(self, store_client):
        await db.upsert_store(_make_store("s1", "Store One Updated"))

        with patch("routes.api.shopify_client.delete_article",
                   new_callable=AsyncMock) as delete_mock, \
             patch("routes.api.shopify_client.fetch_store_articles",
                   new_callable=AsyncMock, return_value=[]):
            resp = await store_client.post(
                "/history/store-posts/delete",
                data={"article_id": "123", "blog_id": "77"},
            )

        assert resp.status_code == 200
        assert b"shopify post deleted" in resp.content.lower()
        assert delete_mock.await_args.args[1] == 77
        assert delete_mock.await_args.args[2] == 123

    async def test_schedule_page_store(self, store_client):
        resp = await store_client.get("/schedule")
        assert resp.status_code == 200
        assert b"schedule" in resp.content.lower() or b"job" in resp.content.lower()

    async def test_schedule_page_admin_redirects(self, admin_client):
        # Admin should be redirected away from /schedule
        resp = await admin_client.get("/schedule")
        # Either 200 redirect to /setup or the redirect itself
        assert resp.status_code in (200, 302, 303)

    async def test_api_blogs_unknown_store(self, admin_client):
        resp = await admin_client.get("/api/blogs/nonexistent-store")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    async def test_admin_add_store(self, admin_client, tmp_db):
        resp = await admin_client.post(
            "/setup/stores/save",
            data={
                "store_id": "",
                "name": "New Test Store",
                "myshopify_domain": "newtest.myshopify.com",
                "client_id": "", "client_secret": "",
                "default_blog_handle": "blog", "default_author": "Author",
            },
        )
        assert resp.status_code == 200
        stores = await db.get_stores()
        names = [s["name"] for s in stores]
        assert "New Test Store" in names

    async def test_admin_delete_store(self, admin_client, tmp_db):
        # Add a store then delete it
        await db.upsert_store(_make_store("del-via-http", "HTTP Delete Store"))
        resp = await admin_client.post(
            "/setup/stores/delete",
            data={"store_id": "del-via-http"},
        )
        assert resp.status_code == 200
        assert await db.get_store("del-via-http") is None

    async def test_store_add_model(self, store_client, tmp_db):
        resp = await store_client.post(
            "/setup/models/save",
            data={
                "model_id": "",
                "name": "HTTP Test Model",
                "provider": "deepseek",
                "model_type": "text",
                "model_name": "deepseek-chat",
                "api_key": "key123",
                "endpoint": "",
                "priority": "0",
                "is_active": "1",
                "extra_json": "{}",
            },
        )
        assert resp.status_code == 200
        models = await db.get_models("s1")
        names = [m["name"] for m in models]
        assert "HTTP Test Model" in names

    async def test_store_add_prompt(self, store_client, tmp_db):
        resp = await store_client.post(
            "/setup/prompts/save",
            data={"prompt_id": "", "name": "HTTP Prompt", "text": "Write about tea"},
        )
        assert resp.status_code == 200
        prompts = await db.get_prompts("s1")
        names = [p["name"] for p in prompts]
        assert "HTTP Prompt" in names

    async def test_store_add_schedule_job(self, store_client, tmp_db):
        # Ensure a prompt exists for s1 with a known ID (route requires non-empty prompt_id)
        await db.upsert_prompt({
            "id": "sched-test-prompt",
            "store_id": "s1",
            "name": "Sched Test Prompt",
            "text": "Write about {topic}",
            "sort_order": 0,
        })
        resp = await store_client.post(
            "/schedule/save",
            data={
                "job_id": "",
                "name": "HTTP Job",
                "cron_expr": "0 9 * * 1-5",
                "prompt_id": "sched-test-prompt",
                "blog_handle": "news",
                "author": "",
                "timezone": "Europe/London",
            },
        )
        assert resp.status_code == 200
        jobs = await db.get_scheduled_jobs("s1")
        names = [j["name"] for j in jobs]
        assert "HTTP Job" in names

        saved_job = next(j for j in jobs if j["name"] == "HTTP Job")
        assert saved_job["timezone"] == "Europe/London"

    async def test_store_add_schedule_job_normalizes_auto_blog_handle(self, store_client, tmp_db):
        await db.upsert_prompt({
            "id": "sched-test-auto-prompt",
            "store_id": "s1",
            "name": "Sched Auto Prompt",
            "text": "Write about {topic}",
            "sort_order": 0,
        })

        resp = await store_client.post(
            "/schedule/save",
            data={
                "job_id": "",
                "name": "HTTP Auto Job",
                "cron_expr": "0 9 * * 1-5",
                "prompt_id": "sched-test-auto-prompt",
                "blog_handle": "auto",
                "author": "",
                "timezone": "Europe/London",
            },
        )

        assert resp.status_code == 200
        jobs = await db.get_scheduled_jobs("s1")
        saved_job = next(j for j in jobs if j["name"] == "HTTP Auto Job")
        assert saved_job["blog_handle"] == "__auto__"

    async def test_schedule_page_renders_blog_handle_dropdown(self, store_client):
        await db.upsert_store(_make_store("s1", "Store One Updated"))

        with patch(
            "routes.scheduler_routes.blog_scope.get_blog_options",
            new_callable=AsyncMock,
            return_value=[
                {"handle": "wellness", "title": "Wellness Tips"},
                {"handle": "buying-guides", "title": "Buying Guides"},
            ],
        ):
            resp = await store_client.get("/schedule")

        assert resp.status_code == 200
        assert b'<select name="blog_handle">' in resp.content
        assert b"Auto (best matching blog handle)" in resp.content
        assert b"Store default (news)" in resp.content
        assert b"Wellness Tips (wellness)" in resp.content
        assert b"Buying Guides (buying-guides)" in resp.content

    async def test_schedule_page_shows_last_routed_blog_handle(self, store_client):
        await db.upsert_store(_make_store("s1", "Store One Updated"))
        job_id = await db.upsert_job({
            "id": "",
            "store_id": "s1",
            "name": "Auto Route Job",
            "prompt_id": "pid1",
            "blog_handle": "",
            "author": "",
            "cron_expr": "0 9 * * *",
            "timezone": "UTC",
            "is_active": 1,
            "next_run_at": None,
            "is_product_blog": 0,
            "use_keyword_pool": 0,
        })
        await db.log_generation(
            store_id="s1",
            store_name="Store One Updated",
            blog_handle="home-fitness-mobility",
            prompt_id="pid1",
            prompt_text="Write a useful store blog post.",
            title="Best Home Mobility Routine for Beginners",
            summary="A practical guide to mobility.",
            content_text="Mobility guidance.",
            keywords=["mobility"],
            hashtags=["#mobility"],
            image_count=0,
            status="published",
            scheduled_job_id=job_id,
        )

        resp = await store_client.get("/schedule")

        assert resp.status_code == 200
        assert b"Last published to: home-fitness-mobility" in resp.content
        assert b"Blog: (auto from title pool)" in resp.content

    async def test_generate_preview_includes_blog_handle_scope(self, store_client):
        await db.upsert_store(_make_store("s1", "Store One Updated"))
        strong_blog = {
            "title": "How To Build Better Sleep Habits",
            "summary": "A practical guide to better rest.",
            "content": ("Useful sleep guidance " * 160),
            "keywords": ["sleep habits"],
            "hashtags": ["#sleep"],
        }

        with patch(
            "routes.generate.blog_scope.get_blog_options",
            new_callable=AsyncMock,
            return_value=[{"handle": "news", "title": "Sleep Advice"}],
        ), \
             patch("routes.generate.llm_service.generate_text",
                   new_callable=AsyncMock, return_value=strong_blog) as generate_mock, \
             patch("routes.generate.image_service.generate_typed_images",
                   new_callable=AsyncMock, return_value=([], [], [])):
            resp = await store_client.post(
                "/generate",
                data={
                    "prompt_id": "custom",
                    "custom_prompt": "Write a useful store blog post.",
                    "blog_handle": "news",
                    "author_name": "Store Team",
                    "model_id": "",
                    "product_url": "",
                },
            )

        assert resp.status_code == 200
        prompt_arg = generate_mock.await_args.args[1]
        assert "SECTION SCOPE — HIGHEST PRIORITY:" in prompt_arg
        assert "Shopify blog handle 'news'" in prompt_arg
        assert "Sleep Advice" in prompt_arg

    async def test_generate_preview_uses_title_pool_entry_matching_blog_scope(self, store_client):
        await db.upsert_store(_make_store("s1", "Store One Updated"))
        await db.add_titles(
            "s1",
            [
                {
                    "title": "Best Biohacking Sleep Routine at Home for Beginners",
                    "keyword": "sleep routine at home",
                    "search_intent": "Sleep improvement",
                    "meta_description": "Sleep article.",
                },
                {
                    "title": "Best Home Mobility Routine for Beginners",
                    "keyword": "home mobility routine",
                    "search_intent": "Mobility improvement",
                    "meta_description": "Mobility article.",
                },
            ],
        )

        strong_blog = {
            "title": "Best Home Mobility Routine for Beginners",
            "summary": "A practical guide to mobility.",
            "content": ("Useful mobility guidance " * 160),
            "keywords": ["mobility"],
            "hashtags": ["#mobility"],
        }

        with patch(
            "routes.generate.blog_scope.get_blog_options",
            new_callable=AsyncMock,
            return_value=[{"handle": "home-fitness-mobility", "title": "Home Fitness Mobility"}],
        ), \
             patch("routes.generate.llm_service.generate_text",
                   new_callable=AsyncMock, return_value=strong_blog) as generate_mock, \
             patch("routes.generate.image_service.generate_typed_images",
                   new_callable=AsyncMock, return_value=([], [], [])):
            resp = await store_client.post(
                "/generate",
                data={
                    "prompt_id": "custom",
                    "custom_prompt": "Write a useful store blog post.",
                    "blog_handle": "home-fitness-mobility",
                    "author_name": "Store Team",
                    "model_id": "",
                    "product_url": "",
                },
            )

        assert resp.status_code == 200
        prompt_arg = generate_mock.await_args.args[1]
        assert "Best Home Mobility Routine for Beginners" in prompt_arg
        assert "Best Biohacking Sleep Routine at Home for Beginners" not in prompt_arg

    async def test_generate_preview_uses_keyword_pool_entry_matching_blog_scope(self, store_client):
        await db.upsert_store(_make_store("s1", "Store One Updated"))
        await db.add_keywords(
            "s1",
            [
                {"keyword": "best sleep routine at home", "content": "Sleep optimisation tips."},
                {"keyword": "home mobility exercises for beginners", "content": "Mobility and flexibility ideas."},
            ],
        )

        strong_blog = {
            "title": "How To Improve Home Mobility",
            "summary": "A practical guide to mobility.",
            "content": ("Useful mobility guidance " * 160),
            "keywords": ["mobility"],
            "hashtags": ["#mobility"],
        }

        with patch(
            "routes.generate.blog_scope.get_blog_options",
            new_callable=AsyncMock,
            return_value=[{"handle": "home-fitness-mobility", "title": "Home Fitness Mobility"}],
        ), \
             patch("routes.generate.llm_service.generate_text",
                   new_callable=AsyncMock, return_value=strong_blog) as generate_mock, \
             patch("routes.generate.image_service.generate_typed_images",
                   new_callable=AsyncMock, return_value=([], [], [])):
            resp = await store_client.post(
                "/generate",
                data={
                    "prompt_id": "custom",
                    "custom_prompt": "Write a useful store blog post.",
                    "blog_handle": "home-fitness-mobility",
                    "author_name": "Store Team",
                    "model_id": "",
                    "product_url": "",
                },
            )

        assert resp.status_code == 200
        prompt_arg = generate_mock.await_args.args[1]
        assert "Focus keyword for this article: home mobility exercises for beginners" in prompt_arg
        assert "Focus keyword for this article: best sleep routine at home" not in prompt_arg

    async def test_generate_preview_shows_quality_checks(self, store_client):
        await db.upsert_store(_make_store("s1", "Store One Updated"))
        strong_blog = {
            "title": "How To Choose The Best Everyday Product For Your Needs",
            "summary": "A practical guide that helps shoppers compare options, understand tradeoffs, and decide which product is the best fit before they visit the store.",
            "content": (
                "## What Makes This Product Useful\n\n"
                + ("useful buying advice " * 120)
                + "\n\n## How To Choose The Right Option\n\n"
                + ("practical shopper guidance " * 120)
                + "\n\n## Why It Fits Real Customer Needs\n\n"
                + ("specific store-focused examples " * 120)
                + "\n\n## Ready To Browse The Full Range\n\n"
                + ("shop now for the best fit " * 80)
            ),
            "keywords": ["best product"],
            "hashtags": ["#shop"],
        }

        with patch("routes.generate.llm_service.generate_text",
                   new_callable=AsyncMock, return_value=strong_blog), \
             patch("routes.generate.image_service.generate_typed_images",
                 new_callable=AsyncMock, return_value=([], [], [])):
            resp = await store_client.post(
                "/generate",
                data={
                    "prompt_id": "custom",
                    "custom_prompt": "Write a useful store blog post.",
                    "blog_handle": "news",
                    "author_name": "Store Team",
                    "model_id": "",
                    "product_url": "",
                },
            )

        assert resp.status_code == 200
        assert b"quality checks" in resp.content.lower()
        assert b"local draft score" in resp.content.lower()

    async def test_generate_preview_product_blog_uses_typed_images(self, store_client):
        await db.upsert_store(_make_store("s1", "Store One Updated"))
        strong_blog = {
            "title": "Stone Mug Gift Guide",
            "summary": "A practical guide to the product.",
            "content": ("Useful product guidance " * 120),
            "keywords": ["stone mug"],
            "hashtags": ["#mug"],
        }

        with patch("routes.generate.shopify_client.fetch_product_details",
                   new_callable=AsyncMock,
                   return_value={
                       "title": "Stone Mug",
                       "description": "A handmade stone mug.",
                       "tags": "gift, kitchen",
                   }), \
             patch("routes.generate.shopify_client.fetch_product_image_url",
                   new_callable=AsyncMock,
                   return_value="https://cdn.shopify.com/product-main.png") as product_image_mock, \
             patch("routes.generate.llm_service.generate_text",
                   new_callable=AsyncMock, return_value=strong_blog), \
             patch("routes.generate.image_service.generate_typed_images",
                   new_callable=AsyncMock,
                   return_value=(
                       [
                           "https://img.example.com/hero.png",
                           "https://img.example.com/info.png",
                           "https://img.example.com/steps.png",
                           "https://img.example.com/checklist.png",
                       ],
                       ["hero_photo", "infographic", "step_card", "checklist_card"],
                       [
                           "Hero Photo",
                           "Infographic",
                           "Step-by-Step Visual Card",
                           "Checklist/Tips Card",
                       ],
                   )) as typed_images_mock:
            resp = await store_client.post(
                "/generate",
                data={
                    "prompt_id": "custom",
                    "custom_prompt": "Write a useful product blog post.",
                    "blog_handle": "news",
                    "author_name": "Store Team",
                    "model_id": "",
                    "product_url": "https://s1.myshopify.com/products/stone-mug",
                },
            )

        assert resp.status_code == 200
        assert b"product image" in resp.content.lower()
        assert b"step-by-step visual card" in resp.content.lower()
        assert b"checklist/tips card" in resp.content.lower()
        typed_images_mock.assert_awaited_once()
        product_image_mock.assert_awaited_once()

    async def test_api_generate_draft_returns_typed_images_and_pin_metadata(self, http_client, monkeypatch):
        monkeypatch.setenv("AI_BLOG_BACKEND_API_KEY", "test-api-key")
        await db.upsert_store(_make_store("s1", "Store One Updated"))

        quality_report = MagicMock()
        quality_report.as_dict.return_value = {
            "score": 92,
            "publish_blocked": False,
            "checks": [],
        }

        blog_data = {
            "title": "Guide Title",
            "summary": "Guide summary",
            "content": "## Heading\n\nUseful content. " * 40,
            "keywords": ["guide"],
            "hashtags": ["#guide"],
            "long_tail_keywords": ["best guide for beginners"],
            "pin_description": "A pin description",
            "_model_name": "test-model",
        }

        with patch("routes.api.llm_service.generate_text",
                   new_callable=AsyncMock, return_value=blog_data), \
             patch("routes.api.image_service.generate_typed_images",
                   new_callable=AsyncMock,
                   return_value=(
                       [
                           "https://img.example.com/hero.png",
                           "https://img.example.com/info.png",
                           "https://img.example.com/steps.png",
                           "https://img.example.com/checklist.png",
                       ],
                       ["hero_photo", "infographic", "step_card", "checklist_card"],
                       [
                           "Hero Photo",
                           "Infographic",
                           "Step-by-Step Visual Card",
                           "Checklist/Tips Card",
                       ],
                   )), \
             patch("routes.api.review_draft",
                   new_callable=AsyncMock, return_value=quality_report):
            resp = await http_client.post(
                "/api/generate/draft",
                headers={"x-api-key": "test-api-key"},
                json={
                    "store_id": "s1",
                    "prompt_id": "custom",
                    "custom_prompt": "Write a useful store blog post.",
                    "blog_handle": "news",
                    "author": "Store Team",
                    "model_id": "",
                    "product_url": "",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["image_types"] == ["hero_photo", "infographic", "step_card", "checklist_card"]
        assert data["long_tail_keywords"] == ["best guide for beginners"]
        assert data["pin_description"] == "A pin description"

    async def test_api_generate_draft_auto_blog_handle_uses_matching_scope(self, http_client, monkeypatch):
        monkeypatch.setenv("AI_BLOG_BACKEND_API_KEY", "test-api-key")
        await db.upsert_store(_make_store("s1", "Store One Updated"))

        quality_report = MagicMock()
        quality_report.as_dict.return_value = {
            "score": 92,
            "publish_blocked": False,
            "checks": [],
        }

        blog_data = {
            "title": "Guide Title",
            "summary": "Guide summary",
            "content": "## Heading\n\nUseful content. " * 40,
            "keywords": ["mobility"],
            "hashtags": ["#mobility"],
            "long_tail_keywords": ["best home mobility routine"],
            "pin_description": "A pin description",
            "_model_name": "test-model",
        }

        with patch(
            "routes.api.blog_scope.get_blog_options",
            new_callable=AsyncMock,
            return_value=[
                {"handle": "sleep-advice", "title": "Sleep Advice"},
                {"handle": "home-fitness-mobility", "title": "Home Fitness Mobility"},
            ],
        ), \
             patch("routes.api.llm_service.generate_text",
                   new_callable=AsyncMock, return_value=blog_data) as generate_mock, \
             patch("routes.api.image_service.generate_typed_images",
                   new_callable=AsyncMock,
                   return_value=(
                       ["https://img.example.com/hero.png"],
                       ["hero_photo"],
                       ["Hero Photo"],
                   )), \
             patch("routes.api.review_draft",
                   new_callable=AsyncMock, return_value=quality_report):
            resp = await http_client.post(
                "/api/generate/draft",
                headers={"x-api-key": "test-api-key"},
                json={
                    "store_id": "s1",
                    "prompt_id": "custom",
                    "custom_prompt": "Write a useful guide about home mobility routines for beginners.",
                    "blog_handle": "auto",
                    "author": "Store Team",
                    "model_id": "",
                    "product_url": "",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["blog_handle"] == "home-fitness-mobility"
        prompt_arg = generate_mock.await_args.args[1]
        assert "Shopify blog handle 'home-fitness-mobility'" in prompt_arg

    async def test_api_generate_draft_product_blog_uses_typed_images(self, http_client, monkeypatch):
        monkeypatch.setenv("AI_BLOG_BACKEND_API_KEY", "test-api-key")
        await db.upsert_store(_make_store("s1", "Store One Updated"))

        quality_report = MagicMock()
        quality_report.as_dict.return_value = {
            "score": 92,
            "publish_blocked": False,
            "checks": [],
        }

        blog_data = {
            "title": "Stone Mug Gift Guide",
            "summary": "A practical guide to the product.",
            "content": "## Heading\n\nUseful content. " * 40,
            "keywords": ["stone mug"],
            "hashtags": ["#mug"],
            "long_tail_keywords": ["best stone mug for gifting"],
            "pin_description": "A product pin description",
            "_model_name": "test-model",
        }

        with patch("routes.api.shopify_client.fetch_product_details",
                   new_callable=AsyncMock,
                   return_value={
                       "title": "Stone Mug",
                       "description": "A handmade stone mug.",
                       "tags": "gift, kitchen",
                   }), \
             patch("routes.api.shopify_client.fetch_product_image_url",
                 new_callable=AsyncMock,
                 return_value="https://cdn.shopify.com/product-main.png") as product_image_mock, \
             patch("routes.api.llm_service.generate_text",
                   new_callable=AsyncMock, return_value=blog_data), \
             patch("routes.api.image_service.generate_typed_images",
                   new_callable=AsyncMock,
                   return_value=(
                       [
                           "https://img.example.com/hero.png",
                           "https://img.example.com/info.png",
                           "https://img.example.com/steps.png",
                           "https://img.example.com/checklist.png",
                       ],
                       ["hero_photo", "infographic", "step_card", "checklist_card"],
                       [
                           "Hero Photo",
                           "Infographic",
                           "Step-by-Step Visual Card",
                           "Checklist/Tips Card",
                       ],
                   )) as typed_images_mock, \
             patch("routes.api.review_draft",
                   new_callable=AsyncMock, return_value=quality_report):
            resp = await http_client.post(
                "/api/generate/draft",
                headers={"x-api-key": "test-api-key"},
                json={
                    "store_id": "s1",
                    "prompt_id": "custom",
                    "custom_prompt": "Write a useful product blog post.",
                    "blog_handle": "news",
                    "author": "Store Team",
                    "model_id": "",
                    "product_url": "https://s1.myshopify.com/products/stone-mug",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["image_urls"][0] == "https://cdn.shopify.com/product-main.png"
        assert data["image_types"] == [
            "product", "infographic", "step_card", "checklist_card", "hero_photo"
        ]
        assert len(data["image_urls"]) == 5
        assert "https://img.example.com/hero.png" in data["image_urls"]
        typed_images_mock.assert_awaited_once()
        product_image_mock.assert_awaited_once()

    async def test_api_publish_article_appends_related_block(self, http_client, monkeypatch):
        monkeypatch.setenv("AI_BLOG_BACKEND_API_KEY", "test-api-key")
        await db.upsert_store(_make_store("s1", "Store One Updated"))

        quality_report = SimpleNamespace(publish_blocked=False)

        publish_result = SimpleNamespace(
            article_url="https://s1.com/blogs/news/guide-title",
            article_id="987",
        )

        with patch("routes.api.review_draft",
                   new_callable=AsyncMock, return_value=quality_report), \
             patch("routes.api.internal_links.build_internal_links",
                   new_callable=AsyncMock, return_value=[MagicMock()]), \
             patch("routes.api.internal_links.render_related_block",
                   return_value="<div>Related reading &amp; products</div>"), \
             patch("routes.api.logo_service.stamp_photo",
                 new_callable=AsyncMock, return_value="data:image/jpeg;base64,ZmVhdHVyZWQ="), \
             patch("routes.api.logo_service.stamp_infographic",
                 new_callable=AsyncMock, return_value="data:image/jpeg;base64,ZmVhdHVyZWQ="), \
             patch("routes.api.logo_service.stamp_pin",
                   new_callable=AsyncMock, return_value="https://img.example.com/pin.png"), \
             patch("routes.api.shopify_client.publish_article",
                   new_callable=AsyncMock, return_value=publish_result) as publish_mock:
            resp = await http_client.post(
                "/api/publish/article",
                headers={"x-api-key": "test-api-key"},
                json={
                    "store_id": "s1",
                    "prompt_id": "custom",
                    "prompt_text": "Write a useful store blog post.",
                    "blog_handle": "news",
                    "author": "Store Team",
                    "title": "Guide Title",
                    "summary": "Guide summary",
                    "content": "Useful content. " * 120,
                    "keywords": ["guide"],
                    "hashtags": ["#guide"],
                    "long_tail_keywords": ["best guide for beginners"],
                    "pin_description": "A pin description",
                    "image_urls": ["https://img.example.com/hero.png"],
                    "image_types": ["hero_photo"],
                    "selected_image_index": 0,
                    "product_url": "",
                    "product_title": "",
                    "title_pool_id": 0,
                },
            )

        assert resp.status_code == 200
        publish_kwargs = publish_mock.await_args.kwargs
        assert "Related reading &amp; products" in publish_kwargs["content_html"]
        assert publish_kwargs["long_tail_keywords"] == ["best guide for beginners"]
        assert publish_kwargs["pin_description"] == "A pin description"
        assert publish_kwargs["pin_image_url"] == "https://img.example.com/pin.png"
        assert publish_kwargs["image_url_list"] == ["https://img.example.com/hero.png"]
        assert publish_kwargs["featured_image_url"].startswith("data:image/jpeg;base64,")

    async def test_api_publish_article_auto_blog_handle_uses_matching_scope(self, http_client, monkeypatch):
        monkeypatch.setenv("AI_BLOG_BACKEND_API_KEY", "test-api-key")
        await db.upsert_store(_make_store("s1", "Store One Updated"))

        quality_report = SimpleNamespace(publish_blocked=False)
        publish_result = SimpleNamespace(
            article_url="https://s1.com/blogs/home-fitness-mobility/guide-title",
            article_id="987",
        )

        with patch(
            "routes.api.blog_scope.get_blog_options",
            new_callable=AsyncMock,
            return_value=[
                {"handle": "sleep-advice", "title": "Sleep Advice"},
                {"handle": "home-fitness-mobility", "title": "Home Fitness Mobility"},
            ],
        ), \
             patch("routes.api.review_draft",
                   new_callable=AsyncMock, return_value=quality_report), \
             patch("routes.api.internal_links.build_internal_links",
                   new_callable=AsyncMock, return_value=[]), \
             patch("routes.api.internal_links.render_related_block",
                   return_value=""), \
             patch("routes.api.logo_service.stamp_photo",
                   new_callable=AsyncMock, return_value="data:image/jpeg;base64,ZmVhdHVyZWQ="), \
             patch("routes.api.logo_service.stamp_pin",
                   new_callable=AsyncMock, return_value="https://img.example.com/pin.png"), \
             patch("routes.api.shopify_client.publish_article",
                   new_callable=AsyncMock, return_value=publish_result) as publish_mock:
            resp = await http_client.post(
                "/api/publish/article",
                headers={"x-api-key": "test-api-key"},
                json={
                    "store_id": "s1",
                    "prompt_id": "custom",
                    "prompt_text": "Write a useful guide about home mobility routines for beginners.",
                    "blog_handle": "auto",
                    "author": "Store Team",
                    "title": "Best Home Mobility Routine for Beginners",
                    "summary": "Guide summary",
                    "content": "Useful mobility content. " * 120,
                    "keywords": ["mobility"],
                    "hashtags": ["#mobility"],
                    "long_tail_keywords": ["best home mobility routine"],
                    "pin_description": "A pin description",
                    "image_urls": ["https://img.example.com/hero.png"],
                    "image_types": ["hero_photo"],
                    "selected_image_index": 0,
                    "product_url": "",
                    "product_title": "",
                    "title_pool_id": 0,
                },
            )

        assert resp.status_code == 200
        assert publish_mock.await_args.kwargs["blog_handle"] == "home-fitness-mobility"

    async def test_api_schedule_save_normalizes_auto_blog_handle(self, http_client, monkeypatch):
        monkeypatch.setenv("AI_BLOG_BACKEND_API_KEY", "test-api-key")
        await db.upsert_store(_make_store("s1", "Store One Updated"))
        await db.upsert_prompt({
            "id": "api-sched-auto-prompt",
            "store_id": "s1",
            "name": "API Schedule Auto Prompt",
            "text": "Write about {topic}",
            "sort_order": 0,
        })

        resp = await http_client.post(
            "/api/schedule/save",
            headers={"x-api-key": "test-api-key"},
            json={
                "store_id": "s1",
                "job_id": "",
                "name": "API Auto Job",
                "prompt_id": "api-sched-auto-prompt",
                "blog_handle": "auto",
                "author": "",
                "cron_expr": "0 9 * * 1-5",
                "timezone": "Europe/London",
                "is_active": True,
                "is_product_blog": False,
                "use_keyword_pool": False,
            },
        )

        assert resp.status_code == 200
        jobs = await db.get_scheduled_jobs("s1")
        saved_job = next(j for j in jobs if j["name"] == "API Auto Job")
        assert saved_job["blog_handle"] == "__auto__"

    async def test_publish_article_keeps_all_body_images_when_featured_is_data_uri(self, tmp_db):
        import shopify_client

        store = StoreConfig(
            id="s1",
            name="Store One",
            myshopify_domain="s1.myshopify.com",
            client_id="cid",
            client_secret="csec",
            default_blog_handle="news",
            default_author="Test Author",
        )

        body_images = [
            "https://img.example.com/hero.png",
            "https://img.example.com/info.png",
            "https://img.example.com/steps.png",
            "https://img.example.com/checklist.png",
        ]

        with patch("shopify_client.resolve_blog_id", new_callable=AsyncMock, return_value=321), \
             patch("shopify_client._get_token", new_callable=AsyncMock, return_value="token"), \
             patch("shopify_client.upload_image_to_shopify", new_callable=AsyncMock, side_effect=lambda store, image_url, filename: image_url), \
             patch("shopify_client._post", new_callable=AsyncMock, return_value={
                 "article": {"id": 987, "handle": "guide-post"}
             }) as post_mock:
            await shopify_client.publish_article(
                store=store,
                blog_handle="news",
                title="Guide Title",
                content_html="<p>Intro</p><p>Body</p>",
                summary="Guide summary",
                keywords=["guide"],
                hashtags=["#guide"],
                author="Store Team",
                image_url_list=body_images,
                featured_image_url="data:image/jpeg;base64,ZmVhdHVyZWQ=",
            )

        payload = post_mock.await_args.args[3]
        body_html = payload["article"]["body_html"]
        for url in body_images:
            assert url in body_html
        assert payload["article"]["image"] == {
            "attachment": "ZmVhdHVyZWQ=",
            "filename": "featured_image.jpg",
        }

    async def test_blog_image_upload_uses_staged_graphql_and_returns_shopify_cdn(self, tmp_db):
        import shopify_client

        store = StoreConfig(
            id="s1",
            name="Store One",
            myshopify_domain="s1.myshopify.com",
            client_id="cid",
            client_secret="csec",
            default_blog_handle="news",
            default_author="Test Author",
        )

        class FakeResponse:
            status_code = 201
            content = b"source-image"
            text = ""

            def raise_for_status(self):
                return None

        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.posts = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, *args, **kwargs):
                return FakeResponse()

            async def post(self, url, **kwargs):
                self.posts.append((url, kwargs))
                return FakeResponse()

        graphql_results = [
            {"stagedUploadsCreate": {"stagedTargets": [{
                "url": "https://upload.test",
                "resourceUrl": "https://staged.test/blog-image",
                "parameters": [{"name": "key", "value": "value"}],
            }], "userErrors": []}},
            {"fileCreate": {"files": [{
                "id": "gid://shopify/MediaImage/1",
                "fileStatus": "READY",
                "image": {"url": "https://cdn.shopify.com/blog-image.webp"},
            }], "userErrors": []}},
        ]

        with patch("shopify_client._get_token", new_callable=AsyncMock, return_value="token"), \
             patch("shopify_client._graphql", new_callable=AsyncMock, side_effect=graphql_results) as graphql_mock, \
             patch("shopify_client.httpx.AsyncClient", side_effect=FakeClient), \
             patch("services.image_optimizer.optimize_image", return_value=b"optimised-webp"):
            url = await shopify_client.upload_image_to_shopify(
                store,
                "https://images.example.com/generated.png",
                "product_guide_image_2.png",
            )

        assert url == "https://cdn.shopify.com/blog-image.webp"
        assert graphql_mock.await_count == 2
        staged_input = graphql_mock.await_args_list[0].args[4]["input"][0]
        assert staged_input == {
            "filename": "product_guide_image_2.webp",
            "mimeType": "image/webp",
            "httpMethod": "POST",
            "resource": "IMAGE",
        }
        create_input = graphql_mock.await_args_list[1].args[4]["files"][0]
        assert create_input["originalSource"] == "https://staged.test/blog-image"

    async def test_product_blog_publish_stops_if_any_generated_image_is_lost(self, tmp_db):
        import shopify_client

        store = StoreConfig(
            id="s1",
            name="Store One",
            myshopify_domain="s1.myshopify.com",
            client_id="cid",
            client_secret="csec",
            default_blog_handle="news",
            default_author="Test Author",
        )
        images = [
            "https://img.example.com/product.png",
            "https://img.example.com/info.png",
            "https://img.example.com/steps.png",
            "https://img.example.com/checklist.png",
        ]

        with patch(
            "shopify_client.upload_image_to_shopify",
            new_callable=AsyncMock,
            side_effect=[
                "https://cdn.shopify.com/product.webp",
                "https://cdn.shopify.com/info.webp",
                None,
                "https://cdn.shopify.com/checklist.webp",
            ],
        ), pytest.raises(shopify_client.ShopifyError, match="image integrity check failed"):
            await shopify_client.publish_article(
                store=store,
                blog_handle="inside-the-products",
                title="Complete Product Guide",
                content_html="<p>Introduction</p><p>Details</p>",
                summary="Summary",
                keywords=[],
                hashtags=[],
                author="Store Team",
                image_url_list=images,
                product_url="https://s1.myshopify.com/products/item",
                product_title="Item",
            )

    async def test_background_product_blog_keeps_product_image_and_every_generation(self, tmp_db):
        from services import publish_service

        store_row = _make_store("s1", "Store One")
        generated_images = [
            "https://img.example.com/hero.png",
            "https://img.example.com/info.png",
            "https://img.example.com/steps.png",
            "https://img.example.com/checklist.png",
        ]
        blog_data = {
            "title": "Complete Product Guide",
            "summary": "A practical and complete product guide.",
            "content": "## Introduction\n\n" + ("Useful product guidance. " * 160),
            "keywords": ["product guide"],
            "hashtags": ["#productguide"],
            "long_tail_keywords": ["complete product guide for beginners"],
        }
        publish_result = SimpleNamespace(
            article_id="987",
            article_url="https://s1.com/blogs/inside-the-products/complete-product-guide",
            blog_handle="inside-the-products",
        )

        with patch("services.publish_service.db.get_store", new_callable=AsyncMock, return_value=store_row), \
             patch("services.publish_service.blog_scope.resolve_blog_scope", new_callable=AsyncMock, return_value=SimpleNamespace(handle="inside-the-products")), \
             patch("services.publish_service.blog_scope.apply_blog_scope", new_callable=AsyncMock, side_effect=lambda prompt, **kwargs: prompt), \
             patch("services.publish_service.llm_service.generate_text", new_callable=AsyncMock, return_value=blog_data), \
             patch("services.publish_service.shopify_client.fetch_product_details", new_callable=AsyncMock, return_value={
                 "title": "Item", "description": "A useful product.", "tags": "wellness"
             }), \
             patch("services.publish_service.db.get_store_setting", new_callable=AsyncMock, return_value=""), \
             patch("services.publish_service.shopify_client.fetch_product_image_data_uri", new_callable=AsyncMock, return_value="data:image/png;base64,cHJvZHVjdA=="), \
             patch("services.publish_service.logo_service.stamp_infographic", new_callable=AsyncMock, return_value="data:image/webp;base64,c3RhbXBlZA=="), \
             patch("services.publish_service.image_service.generate_typed_images", new_callable=AsyncMock, return_value=(
                 generated_images,
                 ["hero_photo", "infographic", "step_card", "checklist_card"],
                 ["Hero Photo", "Infographic", "Step Card", "Checklist Card"],
             )), \
             patch("services.publish_service.review_draft", new_callable=AsyncMock, return_value=SimpleNamespace(publish_blocked=False)), \
             patch("services.publish_service.internal_links.build_internal_links", new_callable=AsyncMock, return_value=[]), \
             patch("services.publish_service.logo_service.stamp_pin", new_callable=AsyncMock, return_value=""), \
             patch("services.publish_service.shopify_client.publish_article", new_callable=AsyncMock, return_value=publish_result) as publish_mock, \
             patch("services.publish_service.db.log_generation", new_callable=AsyncMock):
            result = await publish_service.run(
                store_id="s1",
                prompt_text="Write a complete product guide.",
                blog_handle="inside-the-products",
                author="Store Team",
                product_url="https://s1.myshopify.com/products/item",
                product_title="Item",
            )

        body_images = publish_mock.await_args.kwargs["image_url_list"]
        assert body_images == [
            "data:image/webp;base64,c3RhbXBlZA==",
            "https://img.example.com/info.png",
            "https://img.example.com/steps.png",
            "https://img.example.com/checklist.png",
            "https://img.example.com/hero.png",
        ]
        assert set(generated_images).issubset(body_images)
        assert result.image_count == 5

    async def test_publish_blocks_low_quality_draft(self, store_client):
        await db.upsert_store(_make_store("s1", "Store One Updated"))
        with patch("routes.generate.shopify_client.publish_article",
                   new_callable=AsyncMock) as publish_mock:
            resp = await store_client.post(
                "/publish",
                data={
                    "prompt_id": "custom",
                    "prompt_text": "Write a useful store blog post.",
                    "blog_handle": "news",
                    "author": "Store Team",
                    "title": "Bad",
                    "summary": "Too short.",
                    "content": "Tiny post.",
                    "keywords_json": "[]",
                    "hashtags_json": "[]",
                    "image_urls_json": "[]",
                    "image_types_json": "[]",
                    "selected_image_index": "0",
                    "product_url": "",
                    "product_title": "",
                },
            )

        assert resp.status_code == 200
        assert b"quality checks blocked publishing" in resp.content.lower()
        publish_mock.assert_not_awaited()

    async def test_schedule_invalid_cron_returns_error(self, store_client):
        resp = await store_client.post(
            "/schedule/save",
            data={
                "job_id": "",
                "name": "Bad Job",
                "cron_expr": "not a cron",  # invalid
                "prompt_id": "sched-test-prompt",
                "blog_handle": "",
                "author": "",
                "timezone": "UTC",
            },
        )
        assert resp.status_code == 200
        assert b"invalid" in resp.content.lower() or b"cron" in resp.content.lower()

    async def test_change_store_password(self, store_client, tmp_db):
        resp = await store_client.post(
            "/setup/password",
            data={"new_password": "newpass456", "confirm_password": "newpass456"},
        )
        assert resp.status_code == 200
        stored = await db.get_store_password_hash("s1")
        assert stored is not None
        assert verify_password("newpass456", stored)

    async def test_change_password_mismatch(self, store_client):
        resp = await store_client.post(
            "/setup/password",
            data={"new_password": "abc", "confirm_password": "xyz"},
        )
        assert resp.status_code == 200
        # Follow-redirects lands on /setup?error=... — error text is in the page
        assert b"do not match" in resp.content.lower() or b"passwords" in resp.content.lower()


# ─────────────────────────────────────────────────────────────────────────────
# ⑯ publish_service — mocked end-to-end pipeline
# ─────────────────────────────────────────────────────────────────────────────

class TestPublishService:
    async def test_run_pipeline_success(self, tmp_db):
        from services.publish_service import run

        sid = "pub-s1"
        await db.upsert_store(_make_store(sid))

        fake_blog_data = {
            "title": "How To Choose The Best Everyday Product For Your Needs",
            "summary": "A practical guide that helps shoppers compare options, understand tradeoffs, and decide which product is the best fit before they visit the store.",
            "content": (
                "## What Makes This Product Useful\n\n"
                + ("useful buying advice " * 120)
                + "\n\n## How To Choose The Right Option\n\n"
                + ("practical shopper guidance " * 120)
                + "\n\n## Why It Fits Real Customer Needs\n\n"
                + ("specific store-focused examples " * 120)
                + "\n\n## Ready To Browse The Full Range\n\n"
                + ("shop now for the best fit " * 80)
                + "\n\nBrowse the full collection and shop now."
            ),
            "keywords": ["k1"], "hashtags": ["#h1"],
        }
        fake_publish_result = MagicMock()
        fake_publish_result.article_id = "art-123"
        fake_publish_result.article_url = "https://store.com/blog/1"
        fake_publish_result.blog_handle = "news"

        with patch("services.publish_service.llm_service.generate_text",
                   new_callable=AsyncMock, return_value=fake_blog_data), \
             patch("services.publish_service.image_service.generate_images",
                   new_callable=AsyncMock, return_value=[]), \
             patch("services.publish_service.shopify_client.publish_article",
                   new_callable=AsyncMock, return_value=fake_publish_result):

            result = await run(
                store_id=sid,
                prompt_text="Write about tea",
                blog_handle="news",
                author="Bot",
                prompt_id="pid-1",
            )

        # Verify the log was written to DB
        rows = await db.get_recent_generations(store_id=sid, limit=5)
        assert len(rows) == 1
        assert rows[0]["title"] == "How To Choose The Best Everyday Product For Your Needs"
        assert rows[0]["status"] == "published"

    async def test_run_pipeline_unknown_store_raises(self, tmp_db):
        from services.publish_service import run

        with pytest.raises(ValueError, match="Store not found"):
            await run("nonexistent-store-xyz", "prompt", "news", "author")

    async def test_run_pipeline_text_failure_propagates(self, tmp_db):
        from services.publish_service import run

        sid = "pub-fail"
        await db.upsert_store(_make_store(sid))

        with patch("services.publish_service.llm_service.generate_text",
                   new_callable=AsyncMock,
                   side_effect=AllModelsFailedError([("m", "err")])):
            with pytest.raises(AllModelsFailedError):
                await run(sid, "prompt", "news", "author")

    async def test_run_pipeline_blocked_by_quality_gate(self, tmp_db):
        from services.publish_service import run
        from services.quality_service import QualityGateError

        sid = "pub-quality"
        await db.upsert_store(_make_store(sid))

        fake_blog_data = {
            "title": "Bad",
            "summary": "Too short.",
            "content": "Tiny post.",
            "keywords": [],
            "hashtags": [],
        }

        with patch("services.publish_service.llm_service.generate_text",
                   new_callable=AsyncMock, return_value=fake_blog_data), \
             patch("services.publish_service.image_service.generate_images",
                   new_callable=AsyncMock, return_value=[]), \
             patch("services.publish_service.shopify_client.publish_article",
                   new_callable=AsyncMock) as publish_mock:
            with pytest.raises(QualityGateError):
                await run(
                    store_id=sid,
                    prompt_text="Write about tea",
                    blog_handle="news",
                    author="Bot",
                    prompt_id="pid-1",
                )

        rows = await db.get_recent_generations(store_id=sid, limit=5)
        assert len(rows) == 1
        assert rows[0]["status"] == "blocked_quality"
        assert rows[0]["content_text"] == "Tiny post."
        publish_mock.assert_not_awaited()


class TestShopifyClient:
    async def test_publish_article_sets_shared_related_guide_metafields(self, tmp_db):
        import shopify_client

        store = StoreConfig(
            id="s1",
            name="Store One",
            myshopify_domain="s1.myshopify.com",
            client_id="cid",
            client_secret="csec",
            default_blog_handle="news",
            default_author="Test Author",
        )

        with patch("shopify_client.resolve_blog_id", new_callable=AsyncMock, return_value=321), \
             patch("shopify_client._get_token", new_callable=AsyncMock, return_value="token"), \
             patch("shopify_client._post", new_callable=AsyncMock, return_value={
                 "article": {"id": 987, "handle": "guide-post"}
             }), \
             patch("shopify_client._set_related_product_guide_metafields", new_callable=AsyncMock) as guide_mock, \
             patch("shopify_client._update_product_description_with_guide_link", new_callable=AsyncMock) as desc_mock:
            result = await shopify_client.publish_article(
                store=store,
                blog_handle="news",
                title="Guide Title",
                content_html="<p>Body</p>",
                summary="Guide summary",
                keywords=["guide"],
                hashtags=["#guide"],
                author="Store Team",
                image_url_list=[],
                product_url="https://s1.myshopify.com/products/stone-mug",
                product_title="Stone Mug",
            )

        assert result.article_id == 987
        assert result.product_page_linked is True
        guide_mock.assert_awaited_once_with(
            store=store,
            product_handle="stone-mug",
            guide_title="Guide Title",
            guide_url="https://s1.com/blogs/news/guide-post",
            guide_excerpt="Guide summary",
        )
        desc_mock.assert_awaited_once_with(
            store=store,
            product_handle="stone-mug",
            guide_title="Guide Title",
            guide_url="https://s1.com/blogs/news/guide-post",
            keywords=["guide"],
            hashtags=["#guide"],
            long_tail_keywords=[],
        )

    async def test_update_article_image_uploads_and_puts_article_image(self, tmp_db):
        import shopify_client

        store = StoreConfig(
            id="s1",
            name="Store One",
            myshopify_domain="s1.myshopify.com",
            client_id="cid",
            client_secret="csec",
            default_blog_handle="news",
            default_author="Test Author",
        )

        with patch("shopify_client.upload_image_to_shopify", new_callable=AsyncMock, return_value="https://cdn.shopify.com/image.png") as upload_mock, \
             patch("shopify_client._get_token", new_callable=AsyncMock, return_value="token"), \
             patch("shopify_client._put", new_callable=AsyncMock, return_value={
                 "article": {"id": 222, "image": {"src": "https://cdn.shopify.com/image.png"}}
             }) as put_mock:
            image_src = await shopify_client.update_article_image(
                store=store,
                blog_id=111,
                article_id=222,
                title="Guide Title",
                image_url="https://example.com/generated.png",
            )

        assert image_src == "https://cdn.shopify.com/image.png"
        upload_mock.assert_awaited_once_with(
            store,
            "https://example.com/generated.png",
            "guide_title_featured_image.png",
        )
        put_payload = put_mock.await_args.args[3]
        assert put_payload == {
            "article": {
                "id": 222,
                "image": {"src": "https://cdn.shopify.com/image.png"},
            }
        }

    async def test_delete_article_retries_with_resolved_blog_id(self, tmp_db):
        import shopify_client

        store = StoreConfig(
            id="s1",
            name="Store One",
            myshopify_domain="s1.myshopify.com",
            client_id="cid",
            client_secret="csec",
            default_blog_handle="news",
            default_author="Test Author",
        )
        delete_once = AsyncMock(side_effect=[
            shopify_client.ShopifyError(
                'Shopify DELETE https://s1.myshopify.com/admin/api/2025-01/blogs/111/articles/222.json returned 404: {"errors":"Not Found"}'
            ),
            None,
        ])

        with patch("shopify_client._delete_article_once", delete_once), \
             patch("shopify_client._find_article_blog_id", new_callable=AsyncMock, return_value=999), \
             patch("shopify_client.get_access_scopes", new_callable=AsyncMock) as scopes_mock:
            await shopify_client.delete_article(store, 111, 222)

        assert delete_once.await_args_list[0].args[1:] == (111, 222)
        assert delete_once.await_args_list[1].args[1:] == (999, 222)
        scopes_mock.assert_not_awaited()

    async def test_delete_article_reports_missing_write_content_scope(self, tmp_db):
        import shopify_client

        store = StoreConfig(
            id="s1",
            name="Store One",
            myshopify_domain="s1.myshopify.com",
            client_id="cid",
            client_secret="csec",
            default_blog_handle="news",
            default_author="Test Author",
        )
        delete_once = AsyncMock(side_effect=shopify_client.ShopifyError(
            'Shopify DELETE https://s1.myshopify.com/admin/api/2025-01/blogs/111/articles/222.json returned 404: {"errors":"Not Found"}'
        ))

        with patch("shopify_client._delete_article_once", delete_once), \
             patch("shopify_client._find_article_blog_id", new_callable=AsyncMock, return_value=111), \
             patch("shopify_client.get_access_scopes", new_callable=AsyncMock, return_value={"read_content"}):
            with pytest.raises(shopify_client.ShopifyError, match="write_content"):
                await shopify_client.delete_article(store, 111, 222)


class TestSystemEvents:
    async def test_persists_redacts_summarises_and_resolves(self, tmp_path):
        from services import system_events

        event_db = tmp_path / "system-events.db"
        with patch("services.system_events.get_db_path", return_value=str(event_db)):
            system_events.record_event(
                level="ERROR",
                component="ai_blog_server.shopify",
                operation="product_blog_generation",
                store_id="store-one",
                correlation_id="store-one:test-product",
                message="Upload failed api_key=super-secret-value",
                details="Authorization: Bearer another-secret\ndata:image/png;base64," + "A" * 120,
            )

            events = system_events.list_events(unresolved_only=True)
            assert len(events) == 1
            assert "super-secret-value" not in events[0]["message"]
            assert "another-secret" not in events[0]["details"]
            assert "[REDACTED]" in events[0]["message"]
            assert "[IMAGE DATA REDACTED]" in events[0]["details"]

            health = system_events.summary()
            assert health["errors_24h"] == 1
            assert health["unresolved"] == 1
            assert system_events.set_resolved(events[0]["id"], True) is True
            assert system_events.summary()["unresolved"] == 0


class TestQualityService:
    async def test_evaluate_draft_ready(self):
        from services.quality_service import evaluate_draft

        content = (
            "## What Makes This Product Useful\n\n"
            + ("useful buying advice " * 120)
            + "\n\n## How To Choose The Right Option\n\n"
            + ("practical shopper guidance " * 120)
            + "\n\n## Why It Fits Real Customer Needs\n\n"
            + ("specific store-focused examples " * 120)
            + "\n\n## Ready To Browse The Full Range\n\n"
            + ("shop now for the best fit " * 80)
            + "\n\nBrowse the full collection and shop now."
        )
        report = evaluate_draft(
            title="How To Choose The Best Everyday Product For Your Needs",
            summary="A practical guide that helps shoppers compare options, understand tradeoffs, and decide which product is the best fit before they visit the store.",
            content=content,
            image_count=1,
        )

        assert report.score >= 80
        assert report.publish_blocked is False
        assert report.verdict == "ready"

    async def test_evaluate_draft_blocked_for_thin_content(self):
        from services.quality_service import evaluate_draft

        report = evaluate_draft(
            title="Bad",
            summary="Too short.",
            content="Tiny post.",
            image_count=0,
        )

        assert report.publish_blocked is True
        assert report.verdict == "blocked"
        assert any(check.status == "fail" for check in report.checks)

    async def test_review_draft_detects_internal_duplicate(self, tmp_db):
        from services.quality_service import review_draft
