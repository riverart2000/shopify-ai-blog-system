"""The shared model file (~/.config/local-ai/model.json) decides the Task Agent's model and server."""

from __future__ import annotations

import json

from task_agent.config import Settings
from task_agent.local_model import local_model


def test_the_shared_file_wins_over_env(shared_model_file, monkeypatch, tmp_path) -> None:
    shared_model_file.write_text(
        json.dumps({"text_model": "gemma-5-40b", "server_url": "http://127.0.0.1:9000/v1", "max_output_tokens": 4096})
    )
    monkeypatch.setenv("LLM_MODEL", "something-else")
    settings = Settings(_env_file=None, db_path=tmp_path / "j.db")
    assert settings.llm_model == "gemma-5-40b" and settings.llm_base_url == "http://127.0.0.1:9000/v1"
    assert settings.llm_max_tokens == 4096


def test_missing_or_broken_file_falls_back(shared_model_file) -> None:
    shared_model_file.write_text("{not json")
    assert local_model().text_model == "gemma-4-31b-it-8bit" and local_model().source == "built-in defaults"
    shared_model_file.unlink()
    assert local_model().max_output_tokens == 8192
