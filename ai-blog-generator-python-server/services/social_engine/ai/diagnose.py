"""Ask the model what to do about a refused post that no rule recognises.

The model only chooses between responses the engine already knows how to carry
out (see pipeline/problems.py); the wait it suggests is clamped to the bounds
in rules/platform_problems.json. It never changes a post itself: when it says
the text should be fixed, ai/repair.py does that and checks the result.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..context import StoreContext
from . import prompts
from .base import LLMRequest
from .llm import get_router

_TEXT_MAX = 300


class Diagnosis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    response: Literal["retry_later", "retry_next_day", "fix_post", "needs_person"]
    wait_minutes: int = 0
    cause: str = Field(min_length=3)
    advice: str = ""
    fix: str = ""           # for fix_post: what to change in the text

    @field_validator("wait_minutes", mode="before")
    @classmethod
    def _minutes(cls, value: Any) -> int:
        try:
            return max(0, int(float(value or 0)))
        except (TypeError, ValueError):
            return 0

    @field_validator("cause", "advice", "fix", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return " ".join(str(value or "").split())[:_TEXT_MAX]


async def diagnose_failure(
    sctx: StoreContext,
    *,
    provider: str,
    message: str,
    error_type: str,
    stage: str,
    attempts: int,
    scheduled_local: str,
    recent_same_network: list[dict],
    min_minutes: int,
    max_minutes: int,
    content: dict,
    can_fix: bool,
    subject_id: str = "",
) -> Diagnosis:
    def _bounded(draft: Diagnosis) -> Diagnosis:
        if draft.response == "fix_post" and not (can_fix and draft.fix):
            raise ValueError('"fix_post" needs "fix" (what to change in the text) and is only allowed when offered')
        if draft.response != "fix_post":
            draft.fix = ""
        if draft.response == "retry_later":
            draft.wait_minutes = max(min_minutes, min(draft.wait_minutes or min_minutes, max_minutes))
        else:
            draft.wait_minutes = 0
        return draft

    draft, _ = await get_router(sctx).structured(
        "diagnose.failure",
        LLMRequest(
            system=prompts.failure_diagnosis_system(),
            prompt=prompts.failure_diagnosis_prompt(
                provider=provider, message=message, error_type=error_type, stage=stage, attempts=attempts,
                scheduled_local=scheduled_local, recent_same_network=recent_same_network,
                min_minutes=min_minutes, max_minutes=max_minutes, content=content, can_fix=can_fix,
            ),
            json_mode=True, temperature=sctx.settings.ai.decision_temperature, max_tokens=400,
        ),
        Diagnosis,
        ctx=sctx.call_context(),
        decision_type="failure_diagnosis",
        subject_id=subject_id[:80],
        postprocess=_bounded,
    )
    return _bounded(draft)
