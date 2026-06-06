"""providers/__init__.py — Provider factory and registry."""
from __future__ import annotations

from .base import (
    AllModelsFailedError,
    ImageProvider,
    ModelRecord,
    ProviderError,
    TextProvider,
)
from .deepseek import DeepSeekProvider, _DEFAULT_PROMPT_ENDING as DEFAULT_PROMPT_ENDING
from .grok import GrokProvider
from .local import OllamaProvider
from .openai_provider import OpenAIImageProvider, OpenAITextProvider
from .replicate import ReplicateImageProvider, ReplicateTextProvider

__all__ = [
    "ModelRecord",
    "ProviderError",
    "AllModelsFailedError",
    "TextProvider",
    "ImageProvider",
    "DEFAULT_PROMPT_ENDING",
    "get_text_provider",
    "get_image_provider",
]

_TEXT_PROVIDERS: dict[str, type[TextProvider]] = {
    "deepseek": DeepSeekProvider,
    "openai": OpenAITextProvider,
    "grok": OpenAITextProvider,   # xAI Grok — OpenAI-compatible chat API
    "local": OllamaProvider,
    "replicate": ReplicateTextProvider,
}

_IMAGE_PROVIDERS: dict[str, type[ImageProvider]] = {
    "grok": GrokProvider,
    "openai": OpenAIImageProvider,
    "replicate": ReplicateImageProvider,
}


def get_text_provider(model: ModelRecord) -> TextProvider:
    cls = _TEXT_PROVIDERS.get(model.provider)
    if cls is None:
        raise ValueError(
            f"Unknown text provider {model.provider!r}. "
            f"Available: {list(_TEXT_PROVIDERS)}"
        )
    return cls(model)


def get_image_provider(model: ModelRecord) -> ImageProvider:
    cls = _IMAGE_PROVIDERS.get(model.provider)
    if cls is None:
        raise ValueError(
            f"Unknown image provider {model.provider!r}. "
            f"Available: {list(_IMAGE_PROVIDERS)}"
        )
    return cls(model)
