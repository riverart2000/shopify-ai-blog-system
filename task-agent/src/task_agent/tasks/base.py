"""What a task type is: its instructions, the only tools it may use, its input and its result shape."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel
from pydantic_ai import Agent, Tool
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.models import Model

from task_agent.config import Settings
from task_agent.guardrails import BASE_RULES

if TYPE_CHECKING:
    from task_agent.accounts import AccountStore
    from task_agent.browser import BrowserHub
    from task_agent.db import Job, JobStore


@dataclass
class JobDeps:
    """Per-job state the tools can see. Never holds secrets the model could read back."""

    job_id: int
    settings: Settings
    http: httpx.AsyncClient
    opened_urls: set[str] = field(default_factory=set)  # pages actually fetched this job (for source checks)
    store: JobStore | None = None
    browser: BrowserHub | None = None  # the one shared browser (None in tests and for web-only tasks)
    accounts: AccountStore | None = None  # saved site logins (passwords stay in the Keychain)
    seen_domains: set[str] = field(default_factory=set)  # sites that showed up in searches and pages this job
    payload: Any = None  # the job's validated payload, for tools that need it (e.g. the event image)
    failed_actions: list[str] = field(default_factory=list)  # page actions failing in a row (to catch a stuck job)

    @property
    def owner(self) -> str:
        return f"job:{self.job_id}"


@dataclass
class Finish:
    """How a job ended, handed to its task type's `on_finish` hook."""

    store: JobStore
    settings: Settings
    job: Job
    status: str  # done | failed | cancelled | needs_you | queued (retrying later)
    output: dict[str, Any] | None = None
    error: str | None = None
    need: Any = None  # the NeedsJoe that stopped it
    browser: BrowserHub | None = None


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
    # Template values copied into the payload as they are (not only into the prompt), e.g. event links.
    value_fields: tuple[str, ...] = ()
    sample_values: dict[str, str] = field(default_factory=dict)  # valid examples, used to check a new template
    multiline_fields: tuple[str, ...] = ()  # shown as a text box on the New task page
    # Extra text for the first prompt, worked out by code before the model starts (e.g. reading the event pages).
    prepare: Callable[[JobDeps, Any], Awaitable[str]] | None = None
    # Called when the job starts; a returned message cancels the run (e.g. a place Joe skipped meanwhile).
    on_start: Callable[[JobStore, Job], str | None] | None = None
    on_finish: Callable[[Finish], Awaitable[None]] | None = None
    history_processors: tuple[Callable[[list[Any]], list[Any]], ...] = ()
    max_steps: int | None = None  # overrides the worker setting for this task type
    max_minutes: int | None = None
    uses_browser: bool = False

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
            capabilities=[ProcessHistory(fn) for fn in self.history_processors] or None,
        )
        if self.configure:
            self.configure(agent)
        return agent
