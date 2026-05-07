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
import os
import sys
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
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
        rows = await db.get_recent_generations(store_id="s1", limit=10)
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

        assert len(urls) == 2
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
                "timezone": "UTC",
            },
        )
        assert resp.status_code == 200
        jobs = await db.get_scheduled_jobs("s1")
        names = [j["name"] for j in jobs]
        assert "HTTP Job" in names

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
             patch("routes.generate.image_service.generate_images",
                   new_callable=AsyncMock, return_value=[]):
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
             patch("shopify_client._set_related_product_guide_metafields", new_callable=AsyncMock) as guide_mock:
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

        content = (
            "## Coffee Brewing Tips\n\n"
            + ("coffee brewing guidance for shoppers " * 80)
            + "\n\n## Choosing Beans\n\n"
            + ("choose beans that match taste preferences " * 80)
            + "\n\n## Shop The Collection\n\n"
            + ("shop now for more coffee gear " * 60)
        )
        await db.log_generation(
            store_id="s1",
            store_name="Store One",
            blog_handle="news",
            prompt_id="pid1",
            prompt_text="Write about coffee",
            title="Coffee Brewing Tips For Better Mornings",
            summary="A practical guide for shoppers who want better coffee at home.",
            content_text=content,
            keywords=["coffee"],
            hashtags=["#coffee"],
            image_count=1,
            article_id="art-2",
            article_url="https://shop.com/blog/coffee",
            status="published",
        )

        report = await review_draft(
            store_id="s1",
            title="Coffee Brewing Tips For Better Mornings",
            summary="A practical guide for shoppers who want better coffee at home.",
            content=content,
            image_count=1,
        )

        dup_check = next(check for check in report.checks if check.key == "internal_duplication")
        assert dup_check.status == "fail"
        assert report.publish_blocked is True
        assert report.duplicate_similarity >= 0.88
