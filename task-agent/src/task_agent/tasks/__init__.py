"""Registry of task types. Each one sees only its own tools (guardrail #1)."""

from __future__ import annotations

from task_agent.tasks.base import Finish, JobDeps, TaskSpec
from task_agent.tasks.promote import PROMOTE_EVENT, PROMOTE_POST
from task_agent.tasks.research import RESEARCH

TASKS: dict[str, TaskSpec] = {spec.type: spec for spec in (RESEARCH, PROMOTE_EVENT, PROMOTE_POST)}

# Task types Joe can pick for a template (promote_post jobs are only made by a promotion).
TEMPLATE_TYPES = ("research", "promote_event")

__all__ = ["TASKS", "TEMPLATE_TYPES", "Finish", "JobDeps", "TaskSpec"]
