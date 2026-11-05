"""The one local AI model setting on this Mac: ~/.config/local-ai/model.json.

OpenClaw, the Task Agent and Social Autopilot all read this file, so changing the model there switches everything.
Two large models don't fit in memory together, so they must never differ. `LOCAL_AI_MODEL_FILE` points elsewhere
(tests). If the file is missing or broken the built-in values below are used and a warning is logged.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger("task_agent.local_model")

DEFAULT_FILE = Path("~/.config/local-ai/model.json")
FALLBACK = {
    "server_url": "http://127.0.0.1:8000/v1",
    "text_model": "gemma-4-31b-it-8bit",
    "vision_model": "gemma-4-31b-it-8bit",  # the 31B reads images too, so nothing else loads
    "context_window": 65536,
    "max_output_tokens": 8192,
}


@dataclass(frozen=True)
class LocalModel:
    server_url: str
    text_model: str
    vision_model: str
    context_window: int
    max_output_tokens: int
    source: str  # the file it came from, or "built-in defaults"


def model_file() -> Path:
    return Path(os.environ.get("LOCAL_AI_MODEL_FILE") or DEFAULT_FILE).expanduser()


def local_model() -> LocalModel:
    path = model_file()
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except FileNotFoundError:
        log.warning("local_model.missing", file=str(path))
        data = {}
    except (OSError, ValueError) as exc:
        log.warning("local_model.unreadable", file=str(path), error=str(exc))
        data = {}
    values = {key: data.get(key) or default for key, default in FALLBACK.items()}
    return LocalModel(
        server_url=str(values["server_url"]),
        text_model=str(values["text_model"]),
        vision_model=str(values["vision_model"]),
        context_window=int(values["context_window"]),
        max_output_tokens=int(values["max_output_tokens"]),
        source=str(path) if data else "built-in defaults",
    )
