"""Full audit trail: every tool call goes to agent_steps and logs/<job_id>/steps.jsonl."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic_ai import (
    CallToolsNode,
    ModelRequestNode,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)

from task_agent.db import JobStore, iso, now

SUMMARY_CHARS = 400


def summarize(content: Any) -> str:
    text = content if isinstance(content, str) else json.dumps(content, default=str)
    text = " ".join(text.split())
    return text if len(text) <= SUMMARY_CHARS else text[:SUMMARY_CHARS] + "…"


class AuditLog:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self._file = folder / "steps.jsonl"

    def write(self, event: str, **data: Any) -> None:
        line = json.dumps({"ts": iso(now()), "event": event, **data}, default=str)
        with self._file.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def save_result(self, result: dict[str, Any]) -> None:
        (self.folder / "result.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")


class StepRecorder:
    """Watches the agent run node by node and records each tool call once its result comes back."""

    def __init__(self, store: JobStore, audit: AuditLog, job_id: int) -> None:
        self.store = store
        self.audit = audit
        self.job_id = job_id
        self.n = store.next_step_number(job_id)
        self._pending: dict[str, tuple[str, Any, float, int | None]] = {}

    def observe(self, node: object) -> None:
        if isinstance(node, CallToolsNode):
            response = node.model_response
            usage = response.usage
            tokens = (usage.input_tokens or 0) + (usage.output_tokens or 0) if usage else None
            for part in response.parts:
                if isinstance(part, ToolCallPart):
                    self._pending[part.tool_call_id] = (part.tool_name, part.args_as_dict(), time.monotonic(), tokens)
                    tokens = None  # count a response's tokens once, on its first call
                elif isinstance(part, TextPart) and part.content.strip():
                    self.audit.write("model_text", text=part.content)
                elif isinstance(part, ThinkingPart) and part.content.strip():
                    self.audit.write("model_thinking", text=part.content)
        elif isinstance(node, ModelRequestNode):
            for part in node.request.parts:
                if isinstance(part, ToolReturnPart | RetryPromptPart) and part.tool_call_id in self._pending:
                    name, args, started, tokens = self._pending.pop(part.tool_call_id)
                    if isinstance(part, RetryPromptPart):
                        summary = "REJECTED: " + summarize(part.model_response())
                    else:
                        # Pydantic AI 2.x marks a rejected call/result with outcome='retried' (or 'failed').
                        outcome = getattr(part, "outcome", None)
                        prefix = {"retried": "REJECTED: ", "failed": "FAILED: ", "denied": "DENIED: "}.get(outcome, "")
                        summary = prefix + summarize(part.content)
                    self._record(name, args, summary, tokens, started)

    def record_final(self, output: dict[str, Any]) -> None:
        self._record("final_result", {}, summarize(output), None, time.monotonic())

    def _record(self, name: str, args: Any, summary: str, tokens: int | None, started: float) -> None:
        duration_ms = int((time.monotonic() - started) * 1000)
        self.store.add_step(self.job_id, self.n, name, args, summary, tokens=tokens, duration_ms=duration_ms)
        self.audit.write("step", n=self.n, tool=name, args=args, result=summary, tokens=tokens, duration_ms=duration_ms)
        self.n += 1
