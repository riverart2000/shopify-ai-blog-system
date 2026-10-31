"""What a task type is: its instructions, the only tools it may use, its input and its result shape."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel
from pydantic_ai import Agent, Tool
from pydantic_ai.models import Model

from task_agent.config import Settings
from task_agent.guardrails import BASE_RULES


@dataclass
class JobDeps:
    """Per-job state the tools can see. Never holds secrets the model could read back."""

    job_id: int
    settings: Settings
    http: httpx.AsyncClient
    opened_urls: set[str] = field(default_factory=set)  # pages actually fetched this job (for source checks)


@dataclass(frozen=True)
class TaskSpec:
    type: str
    summary: str
    instructions: str
    payload_model: type[BaseModel]
    output_type: type[BaseModel]
    tools: tuple[Tool[JobDeps], ...]
    render_prompt: Callable[[Any], str]
    configure: Callable[[Agent[JobDeps, Any]], None] | None = None  # extra validators, etc.
    needs_approval: bool = False
    prompt_field: str = "question"  # the payload field a template's prompt fills in

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]

    def build_agent(self, model: Model | str, settings: Settings) -> Agent[JobDeps, Any]:
        agent: Agent[JobDeps, Any] = Agent(
            model,
            output_type=self.output_type,
            deps_type=JobDeps,
            # `instructions` are re-sent on every request and never stored in history,
            # so the rules can't be trimmed away or overwritten by page content.
            instructions=f"{BASE_RULES}\n{self.instructions}",
            tools=list(self.tools),
            retries={"tools": 2, "output": settings.output_retries},
            name=self.type,
        )
        if self.configure:
            self.configure(agent)
        return agent
