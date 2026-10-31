"""Settings changed from the admin page. They're saved in the DB and override `.env` for new jobs."""

from __future__ import annotations

from pydantic import BaseModel, Field

from task_agent.config import Settings
from task_agent.db import JobStore

RECOMMENDED_MODEL = "gemma-4-31b-it-8bit"  # the Social Engine's model too: sharing one avoids memory swaps

LIMITS = {"max_steps": (5, 200), "max_minutes": (1, 120), "max_attempts": (1, 10)}


class RuntimeSettings(BaseModel):
    llm_model: str = Field(min_length=1, max_length=200)
    llm_thinking: bool
    max_steps: int = Field(ge=LIMITS["max_steps"][0], le=LIMITS["max_steps"][1])
    max_minutes: int = Field(ge=LIMITS["max_minutes"][0], le=LIMITS["max_minutes"][1])
    max_attempts: int = Field(ge=LIMITS["max_attempts"][0], le=LIMITS["max_attempts"][1])
    paused: bool = False


def current(base: Settings, store: JobStore) -> RuntimeSettings:
    values = {
        "llm_model": base.llm_model,
        "llm_thinking": bool(base.llm_thinking),
        "max_steps": base.max_steps,
        "max_minutes": base.max_minutes,
        "max_attempts": base.max_attempts,
        "paused": False,
    }
    saved = {k: v for k, v in store.setting_overrides().items() if k in RuntimeSettings.model_fields}
    try:
        return RuntimeSettings.model_validate({**values, **saved})
    except ValueError:
        return RuntimeSettings.model_validate(values)  # a bad saved value never stops the worker


def save(base: Settings, store: JobStore, changes: dict) -> RuntimeSettings:
    """Validate the changed fields together with the rest, then save only what was sent."""
    unknown = set(changes) - set(RuntimeSettings.model_fields)
    if unknown:
        raise ValueError(f"Unknown setting(s): {', '.join(sorted(unknown))}")
    merged = RuntimeSettings.model_validate({**current(base, store).model_dump(), **changes})
    store.save_setting_overrides({k: getattr(merged, k) for k in changes})
    return merged


def effective(base: Settings, store: JobStore) -> tuple[Settings, bool]:
    """The settings a new job runs with, and whether the worker is paused."""
    rt = current(base, store)
    settings = base.model_copy(
        update={
            "llm_model": rt.llm_model,
            "llm_thinking": rt.llm_thinking,
            "max_steps": rt.max_steps,
            "max_minutes": rt.max_minutes,
            "max_attempts": rt.max_attempts,
        }
    )
    return settings, rt.paused
