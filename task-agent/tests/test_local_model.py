"""The shared model file (~/.config/local-ai/model.json) sets the Task Agent's model and server; no code names one."""

from __future__ import annotations

import json

import pytest

from task_agent.config import Settings
from task_agent.local_model import LocalModelError, local_model


def test_the_shared_file_wins_over_env(shared_model_file, monkeypatch, tmp_path) -> None:
    shared = json.loads(shared_model_file.read_text())
    shared |= {"text_model": "gemma-5-40b", "server_url": "http://127.0.0.1:9000/v1", "max_output_tokens": 4096}
    shared_model_file.write_text(json.dumps(shared))
    monkeypatch.setenv("LLM_MODEL", "something-else")
    settings = Settings(_env_file=None, db_path=tmp_path / "j.db")
    assert settings.llm_model == "gemma-5-40b" and settings.llm_base_url == "http://127.0.0.1:9000/v1"
    assert settings.llm_max_tokens == 4096


def test_no_separate_vision_model_means_the_text_model(shared_model_file) -> None:
    shared = json.loads(shared_model_file.read_text())
    del shared["vision_model"]
    shared_model_file.write_text(json.dumps(shared))
    assert local_model().vision_model == shared["text_model"]


@pytest.mark.parametrize(
    ("contents", "problem"),
    [
        (None, "is missing"),
        ("{not json", "can't be read"),
        ('["a list"]', "must hold a JSON object"),
        ({"text_model": ""}, "needs text_model"),
        ({"context_window": "big"}, "needs context_window"),
        ({"max_output_tokens": 70000}, "max_output_tokens must be below context_window"),
    ],
)
def test_a_missing_or_broken_file_stops_with_its_path(shared_model_file, tmp_path, contents, problem) -> None:
    if contents is None:
        shared_model_file.unlink()
    elif isinstance(contents, dict):
        shared_model_file.write_text(json.dumps(json.loads(shared_model_file.read_text()) | contents))
    else:
        shared_model_file.write_text(contents)
    with pytest.raises(LocalModelError, match=problem) as caught:
        Settings(_env_file=None, db_path=tmp_path / "j.db")
    assert str(shared_model_file) in str(caught.value)
