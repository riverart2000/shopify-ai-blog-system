"""The one local AI model setting on this Mac: ~/.config/local-ai/model.json.

OpenClaw, the Task Agent and Social Autopilot all read this file, and no code names a model, so changing the model there
switches everything. Two large models don't fit in memory together, so they must never differ. There are no built-in
values: a missing or incomplete file stops the Task Agent with a message that names the file and the problem (OpenClaw
does the same). `LOCAL_AI_MODEL_FILE` points elsewhere (tests).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_FILE = Path("~/.config/local-ai/model.json")


class LocalModelError(RuntimeError):
    """The shared model file is missing, unreadable or incomplete."""


@dataclass(frozen=True)
class LocalModel:
    server_url: str
    text_model: str
    vision_model: str  # the text model when the file doesn't name a separate one
    context_window: int
    max_output_tokens: int
    source: str  # the file it came from


def model_file() -> Path:
    return Path(os.environ.get("LOCAL_AI_MODEL_FILE") or DEFAULT_FILE).expanduser()


def local_model() -> LocalModel:
    path = model_file()
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise LocalModelError(
            f"The shared model file {path} is missing. It names the local AI model that OpenClaw, the Task Agent "
            "and Social Autopilot use."
        ) from None
    except (OSError, ValueError) as exc:
        raise LocalModelError(f"The shared model file {path} can't be read: {exc}") from exc
    if not isinstance(data, dict):
        raise LocalModelError(f"The shared model file {path} must hold a JSON object.")

    def text(key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise LocalModelError(f"The shared model file {path} needs {key}.")
        return value.strip()

    def count(key: str) -> int:
        value = data.get(key)
        if type(value) is not int or value <= 0:
            raise LocalModelError(f"The shared model file {path} needs {key} as a whole number above 0.")
        return value

    text_model = text("text_model")
    vision = data.get("vision_model")
    context_window, max_output_tokens = count("context_window"), count("max_output_tokens")
    if max_output_tokens >= context_window:
        raise LocalModelError(f"The shared model file {path}: max_output_tokens must be below context_window.")
    return LocalModel(
        server_url=text("server_url").rstrip("/"),
        text_model=text_model,
        vision_model=vision.strip() if isinstance(vision, str) and vision.strip() else text_model,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        source=str(path),
    )
