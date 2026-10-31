"""The one local text model every task uses (Gemma 4 31B on oMLX, shared with the Social Engine)."""

from __future__ import annotations

from pydantic_ai import ModelSettings
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from task_agent.config import Settings


def build_model(settings: Settings) -> OpenAIChatModel:
    provider = OpenAIProvider(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value() or "not-needed",
    )
    return OpenAIChatModel(settings.llm_model, provider=provider)


def model_settings(settings: Settings) -> ModelSettings:
    result = ModelSettings(temperature=settings.llm_temperature, timeout=settings.llm_timeout_seconds)
    if settings.llm_thinking is not None:
        # Per request, so a model shared with the Social Engine keeps its own default there.
        result["extra_body"] = {"chat_template_kwargs": {"enable_thinking": settings.llm_thinking}}
    return result
