"""The one local AI model setting on this Mac: ~/.config/local-ai/model.json.

OpenClaw, the Task Agent and Social Autopilot all read this file, so changing the model there switches everything.
Two large models don't fit in memory together, so they must never differ. `LOCAL_AI_MODEL_FILE` points elsewhere
(tests). If the file is missing or broken the built-in values below are used and a warning is logged.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("ai_blog_server.local_model")

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


_cache: dict[tuple[str, float], LocalModel] = {}


def local_model() -> LocalModel:
    """Read on every settings load, cached until the file changes."""
    path = model_file()
    try:
        stamp = path.stat().st_mtime
    except OSError:
        stamp = -1.0
    cached = _cache.get((str(path), stamp))
    if cached is not None:
        return cached
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except FileNotFoundError:
        logger.warning("Shared model file %s is missing; using built-in defaults", path)
        data = {}
    except (OSError, ValueError) as exc:
        logger.warning("Shared model file %s can't be read (%s); using built-in defaults", path, exc)
        data = {}
    values = {key: data.get(key) or default for key, default in FALLBACK.items()}
    result = LocalModel(
        server_url=str(values["server_url"]),
        text_model=str(values["text_model"]),
        vision_model=str(values["vision_model"]),
        context_window=int(values["context_window"]),
        max_output_tokens=int(values["max_output_tokens"]),
        source=str(path) if data else "built-in defaults",
    )
    _cache.clear()
    _cache[(str(path), stamp)] = result
    return result
