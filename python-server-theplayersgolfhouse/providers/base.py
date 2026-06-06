"""providers/base.py — Abstract base classes and shared types for AI providers."""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class ProviderError(Exception):
    """A provider call failed. May trigger failover to the next model."""

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable  # False = auth/config error; skip remaining same-provider models


class AllModelsFailedError(Exception):
    """Every configured model for this store was tried and failed."""

    def __init__(self, failures: list[tuple[str, str]]) -> None:
        self.failures = failures
        details = "; ".join(f"{name}: {err}" for name, err in failures)
        super().__init__(f"All models failed — {details}")


@dataclass
class ModelRecord:
    id: str
    store_id: str
    name: str
    provider: str       # deepseek | openai | grok | replicate | local
    model_type: str     # text | image
    model_name: str     # e.g. "deepseek-chat", "gpt-4o-mini", "grok-2-image"
    api_key: str
    endpoint: str
    extra_json: str     # JSON: {"temperature": 0.7, "timeout": 90, ...}
    priority: int
    is_active: bool

    @property
    def extra(self) -> dict[str, Any]:
        try:
            return json.loads(self.extra_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}

    @property
    def resolved_api_key(self) -> str:
        api_key = (self.api_key or "").strip()
        if api_key:
            if api_key.lower().startswith("env:"):
                env_names = [name.strip() for name in api_key[4:].split(",") if name.strip()]
                for env_name in env_names:
                    value = os.environ.get(env_name, "").strip()
                    if value:
                        return value
                return ""
            return api_key

        provider_env_names = {
            "deepseek": ("DEEPSEEK_API_KEY",),
            "grok": ("GROK_API_KEY", "XAI_API_KEY"),
            "openai": ("OPENAI_API_KEY",),
            "replicate": ("REPLICATE_API_TOKEN", "REPLICATE_API_KEY"),
        }
        for env_name in provider_env_names.get(self.provider.strip().lower(), ()):
            value = os.environ.get(env_name, "").strip()
            if value:
                return value
        return ""

    @classmethod
    def from_dict(cls, d: dict) -> "ModelRecord":
        return cls(
            id=d["id"],
            store_id=d["store_id"],
            name=d["name"],
            provider=d["provider"],
            model_type=d["model_type"],
            model_name=d.get("model_name", ""),
            api_key=d.get("api_key", ""),
            endpoint=d.get("endpoint", ""),
            extra_json=d.get("extra_json", "{}"),
            priority=int(d.get("priority", 0)),
            is_active=bool(d.get("is_active", True)),
        )

    def __repr__(self) -> str:
        return (
            f"ModelRecord(name={self.name!r}, provider={self.provider!r}, "
            f"type={self.model_type!r}, model={self.model_name!r})"
        )


class TextProvider(ABC):
    """Base class for text (blog) generation providers."""

    def __init__(self, model: ModelRecord) -> None:
        self.model = model

    @abstractmethod
    async def generate_text(self, prompt: str, system_prompt: str = "", prompt_ending: str = "") -> dict:
        """Generate blog content.
        Must return dict with: title, summary, keywords (list), hashtags (list), content (str).
        Raises ProviderError on failure.
        """

    async def generate_raw(self, prompt: str, system_prompt: str = "") -> str:
        """Call the model with prompt as-is and return the raw string response.
        Used for non-blog tasks (e.g. title generation) that return arbitrary JSON.
        Raises ProviderError on failure.
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement generate_raw")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.model.model_name!r})"


class ImageProvider(ABC):
    """Base class for image generation providers."""

    def __init__(self, model: ModelRecord) -> None:
        self.model = model

    @abstractmethod
    async def generate_images(self, image_prompt: str, count: int = 2) -> list[str]:
        """Generate images. Returns list of public URLs. Raises ProviderError on failure."""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.model.model_name!r})"
