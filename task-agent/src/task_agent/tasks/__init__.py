"""Registry of task types. Each one sees only its own tools (guardrail #1)."""

from __future__ import annotations

from task_agent.tasks.base import JobDeps, TaskSpec
from task_agent.tasks.research import RESEARCH

TASKS: dict[str, TaskSpec] = {spec.type: spec for spec in (RESEARCH,)}

__all__ = ["TASKS", "JobDeps", "TaskSpec"]
