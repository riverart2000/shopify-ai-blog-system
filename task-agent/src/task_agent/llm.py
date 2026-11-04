"""The one local text model every task uses (Gemma 4 31B on oMLX, shared with the Social Engine)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable

import structlog
from pydantic_ai import ModelSettings
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.openai import OpenAIProvider

from task_agent.config import Settings

log = structlog.get_logger("task_agent.llm")

BUSY_MAX_WAIT_SECONDS = 15 * 60  # then the job's normal retry takes over
# oMLX answers 507 at once when another app's model is busy and both don't fit in memory, and rejects a prompt
# (400 prefill_memory_exceeded) while memory is short. Both clear when the other model finishes.
_BUSY = re.compile(r"status_code: 507\b|prefill_memory_exceeded|InsufficientMemory", re.IGNORECASE)


def model_busy(exc: BaseException) -> bool:
    return isinstance(exc, ModelAPIError) and bool(_BUSY.search(f"{exc} {getattr(exc, 'body', '')}"))


class WaitWhenBusy(WrapperModel):
    """Waits for oMLX when another model is in the way, so a job keeps its place and its Chrome tab instead of
    failing an attempt and starting again (OpenClaw's agents use a different Gemma on the same server)."""

    def __init__(
        self,
        wrapped: Model,
        *,
        max_wait: float = BUSY_MAX_WAIT_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        super().__init__(wrapped)
        self.max_wait = max_wait
        self._sleep = sleep

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        waited, delay = 0.0, 15.0
        while True:
            try:
                return await super().request(messages, model_settings, model_request_parameters)
            except ModelAPIError as exc:
                if not model_busy(exc) or waited + delay > self.max_wait:
                    raise
                log.warning("model.busy_waiting", wait_seconds=delay, waited_seconds=waited)
                await self._sleep(delay)
                waited += delay
                delay = min(delay * 2, 120.0)


def build_model(settings: Settings) -> Model:
    provider = OpenAIProvider(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value() or "not-needed",
    )
    return WaitWhenBusy(OpenAIChatModel(settings.llm_model, provider=provider))


def model_settings(settings: Settings) -> ModelSettings:
    result = ModelSettings(
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_tokens=settings.llm_max_tokens,  # oMLX's default is 32K: one runaway answer would hold the only slot
    )
    if settings.llm_thinking is not None:
        # Per request, so a model shared with the Social Engine keeps its own default there.
        result["extra_body"] = {"chat_template_kwargs": {"enable_thinking": settings.llm_thinking}}
    return result
