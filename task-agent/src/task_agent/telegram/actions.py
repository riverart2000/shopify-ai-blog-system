"""Changes the Telegram assistant can propose. Each runs only after Joe presses Confirm (or presses
Approve/Reject on a card, which is the same thing), and is executed by code — never by the model."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from task_agent.config import Settings
from task_agent.db import JobStore
from task_agent.social import SocialClient
from task_agent.tasks import TASKS
from task_agent.templates import TemplateStore


@dataclass
class BotContext:
    store: JobStore
    settings: Settings
    social: SocialClient
    templates: TemplateStore = field(init=False)

    def __post_init__(self) -> None:
        self.templates = TemplateStore(self.store)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.settings.store_timezone)

    def when(self, epoch: int | float | None) -> str:
        if not epoch:
            return "not scheduled"
        return datetime.fromtimestamp(float(epoch), self.tz).strftime("%a %-d %b %H:%M")


Executor = Callable[[BotContext, dict[str, Any]], Awaitable[str]]


async def _approve_post(ctx: BotContext, p: dict[str, Any]) -> str:
    await ctx.social.post(f"/posts/{p['post_id']}/approve")
    return "Approved. It goes out at its scheduled time."


async def _reject_post(ctx: BotContext, p: dict[str, Any]) -> str:
    await ctx.social.post(f"/posts/{p['post_id']}/reject", {"reason": p.get("reason") or "Rejected in Telegram"})
    return "Rejected. It won't be posted."


async def _campaign_status(ctx: BotContext, p: dict[str, Any]) -> str:
    await ctx.social.post(f"/campaigns/{p['campaign_id']}/status", {"status": p["status"]})
    return "Paused. Nothing new will be planned for it." if p["status"] == "paused" else "Running again."


async def _auto_promotions(ctx: BotContext, p: dict[str, Any]) -> str:
    await ctx.social.post("/settings", {"patch": {"auto_promotions": {"enabled": bool(p["enabled"])}}})
    return "Automatic occasion promotions are on." if p["enabled"] else "Automatic occasion promotions are off."


async def _queue_task(ctx: BotContext, p: dict[str, Any]) -> str:
    template = ctx.templates.get(p["template_key"])
    title, payload = ctx.templates.build_job(template, p.get("values") or {}, p.get("title") or "")
    spec = TASKS[template.task_type]
    job_id, _ = ctx.store.enqueue(
        spec.type, title, payload, spec.tool_names, created_by="telegram", template_key=template.key
    )
    return f"Queued task {job_id}. I'll message you when it's done."


async def _cancel_task(ctx: BotContext, p: dict[str, Any]) -> str:
    job = ctx.store.cancel(int(p["job_id"]))
    return "It will stop at its next step." if job.status == "running" else "Cancelled."


async def _retry_task(ctx: BotContext, p: dict[str, Any]) -> str:
    ctx.store.retry(int(p["job_id"]))
    return "Back in the queue."


async def _decide_approval(ctx: BotContext, p: dict[str, Any]) -> str:
    row = ctx.store.decide_approval(int(p["approval_id"]), p["decision"], by="telegram")
    return "Approved." if row["status"] == "approved" else "Rejected."


ACTIONS: dict[str, Executor] = {
    "approve_post": _approve_post,
    "reject_post": _reject_post,
    "campaign_status": _campaign_status,
    "auto_promotions": _auto_promotions,
    "queue_task": _queue_task,
    "cancel_task": _cancel_task,
    "retry_task": _retry_task,
    "decide_approval": _decide_approval,
}


async def execute(ctx: BotContext, kind: str, params: dict[str, Any]) -> str:
    executor = ACTIONS.get(kind)
    if executor is None:
        raise ValueError(f"Unknown action '{kind}'.")
    return await executor(ctx, params)
