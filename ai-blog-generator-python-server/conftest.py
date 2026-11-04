"""Shared pytest fixtures. Only `shared_model_file` is autouse: it keeps tests off the Mac's real model file."""
from __future__ import annotations

import pytest
import pytest_asyncio

import db


@pytest.fixture(autouse=True)
def shared_model_file(tmp_path, monkeypatch):
    """Tests read their own copy of ~/.config/local-ai/model.json, never the Mac's."""
    import json

    path = tmp_path / "local-ai-model.json"
    path.write_text(json.dumps({
        "server_url": "http://127.0.0.1:8000/v1",
        "text_model": "gemma-4-31b-it-8bit",
        "vision_model": "mlx-community--Qwen3-VL-8B-Instruct-8bit",
        "context_window": 65536,
        "max_output_tokens": 8192,
    }))
    monkeypatch.setenv("LOCAL_AI_MODEL_FILE", str(path))
    return path


@pytest_asyncio.fixture
async def se_db(tmp_path, monkeypatch):
    """Fresh SQLite database with one store (`store_1`) for Social Engine tests."""
    previous = db.get_db_path()
    db.set_db_path(str(tmp_path / "social_engine.db"))
    monkeypatch.setenv("SOCIAL_ENGINE_MEDIA_ROOT", str(tmp_path / "media"))
    await db.init_db()
    await db.upsert_store({
        "id": "store_1",
        "name": "BioluxeLab",
        "myshopify_domain": "bioluxe-test.myshopify.com",
        "custom_domain": "bioluxelab.com",
        "client_id": "cid",
        "client_secret": "secret",
        "default_blog_handle": "news",
        "default_author": "Team",
        "sort_order": 0,
    })
    from services.social_engine import http as se_http

    se_http.TRANSPORT_OVERRIDES.clear()
    try:
        yield "store_1"
    finally:
        se_http.TRANSPORT_OVERRIDES.clear()
        db.set_db_path(previous)


@pytest.fixture
def anyio_backend():
    return "asyncio"
