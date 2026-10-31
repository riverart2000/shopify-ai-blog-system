"""What the bot sends without being asked: cards that need a decision, failures, finished tasks
(queued from Telegram), and the morning summary. Each item is sent once (tracked in telegram_sent)."""

from __future__ import annotations

import contextlib
import logging
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from task_agent.db import iso, now
from task_agent.social import SocialError
from task_agent.telegram.client import TelegramAPI, TelegramError

if TYPE_CHECKING:
    from task_agent.telegram.bot import TelegramBot

log = logging.getLogger("task_agent.telegram")

APPROVE_REJECT = "post"
EMAIL = "approval"


def post_buttons(post_id: str) -> list[list[dict[str, str]]]:
    return [
        [
            {"text": "✅ Approve", "callback_data": f"post:{post_id}:a"},
            {"text": "✖ Reject", "callback_data": f"post:{post_id}:r"},
        ]
    ]


def why_held(post: dict[str, Any]) -> str:
    review = post.get("review") or {}
    guard = review.get("guard") or {}
    if guard.get("hold"):
        issues = [i.get("message", "") for i in guard.get("issues") or [] if isinstance(i, dict)]
        return "Held by the content guard" + (f": {issues[0]}" if issues else "")
    ai = review.get("ai_review") or {}
    if ai.get("error"):
        return "The AI review didn't run, so it needs a person"
    if ai.get("score") is not None:
        issues = ai.get("issues") or []
        return f"Review score {ai['score']}/10" + (f": {issues[0]}" if issues else "")
    return "This campaign asks you to approve every post"


class Notifier:
    def __init__(self, bot: TelegramBot) -> None:
        self.bot = bot
        self.tg = bot.tg
        self.ctx = bot.ctx

    async def tick(self, api: TelegramAPI) -> None:
        _, chat = self.tg.owner()
        if chat is None:
            return
        for step in (self.post_cards, self.approval_cards, self.task_updates, self.social_errors, self.morning):
            try:
                await step(api, chat)
            except (SocialError, TelegramError) as exc:
                log.warning("telegram notify %s: %s", step.__name__, exc)
            except Exception:
                log.exception("telegram notify %s failed", step.__name__)

    # ── posts waiting for approval ──────────────────────────────────────────

    def post_card_text(self, post: dict[str, Any]) -> str:
        channels = ", ".join(sorted({t.get("provider", "") for t in post.get("targets") or []})) or "no channels"
        caption = " ".join(str(post.get("base_caption") or "").split())
        return (
            f"📝 Needs your approval\n{post.get('title') or 'Post'}\n"
            f"{self.ctx.when(post.get('scheduled_at'))} · {channels}\n{why_held(post)}\n\n{caption[:600]}"
        )

    async def send_post_card(self, api: TelegramAPI, chat: int, post: dict[str, Any]) -> None:
        text = self.post_card_text(post)
        image = post.get("preview_asset_id") or next(
            (m.get("id") for m in post.get("media") or [] if m.get("kind") == "image"), None
        )
        message, is_photo = None, False
        if image:
            try:
                message = await api.send_photo(chat, await self.ctx.social.media(image), text, post_buttons(post["id"]))
                is_photo = True
            except (SocialError, TelegramError) as exc:
                log.info("post card without image (%s)", exc)
        if message is None:
            message = await api.send_message(chat, text, post_buttons(post["id"]))
        self.tg.mark_sent(APPROVE_REJECT, post["id"], chat, message["message_id"], is_photo=is_photo)

    async def post_cards(self, api: TelegramAPI, chat: int, *, resend: bool = False) -> int:
        data = await self.ctx.social.get("/posts", status="pending_approval", page_size=50, order="scheduled")
        pending = {p["id"]: p for p in data.get("posts") or []}
        sent = 0
        for post_id, post in pending.items():
            if self.tg.sent(APPROVE_REJECT, post_id) and not resend:
                continue
            await self.send_post_card(api, chat, post)
            sent += 1
        for card in self.tg.open_cards(APPROVE_REJECT):  # decided elsewhere (admin page or auto review)
            if card["ref"] in pending:
                continue
            status = "handled elsewhere"
            with contextlib.suppress(SocialError):
                status = (await self.ctx.social.get(f"/posts/{card['ref']}")).get("post", {}).get("status", status)
            with contextlib.suppress(TelegramError):
                await api.finish_card(
                    card["chat_id"],
                    card["message_id"],
                    f"📝 {status.replace('_', ' ').capitalize()} (no longer waiting for you)",
                    is_photo=bool(card["is_photo"]),
                )
            self.tg.close_card(APPROVE_REJECT, card["ref"])
        return sent

    # ── emails and other Task Agent approvals ───────────────────────────────

    async def approval_cards(self, api: TelegramAPI, chat: int) -> None:
        pending = {row["id"]: row for row in self.ctx.store.approvals("pending")}
        for approval_id, row in pending.items():
            if self.tg.sent(EMAIL, str(approval_id)):
                continue
            text = (
                f"✉️ Waiting for your OK: {row['action'].replace('_', ' ')}\nTask {row['job_id']}: "
                f"{row['job_title']}\n\n{row['preview']}"
            )
            buttons = [
                [
                    {"text": "✅ Approve", "callback_data": f"appr:{approval_id}:a"},
                    {"text": "✖ Reject", "callback_data": f"appr:{approval_id}:r"},
                ]
            ]
            message = await api.send_message(chat, text, buttons)
            self.tg.mark_sent(EMAIL, str(approval_id), chat, message["message_id"])
        for card in self.tg.open_cards(EMAIL):
            if int(card["ref"]) not in pending:
                self.tg.close_card(EMAIL, card["ref"])

    # ── Task Agent: failures, and results for tasks queued from Telegram ────

    async def task_updates(self, api: TelegramAPI, chat: int) -> None:
        since = self.tg.get("since_task_finished")
        if since is None:  # first run: start from now, don't replay history
            self.tg.set("since_task_finished", iso(now()))
            return
        jobs, _ = self.ctx.store.search(["failed", "done"], limit=50)
        for job in sorted(jobs, key=lambda j: j.finished_at or ""):
            if not job.finished_at or job.finished_at < since or self.tg.sent("task", f"{job.id}:{job.finished_at}"):
                continue
            if job.status == "failed":
                message = await api.send_message(
                    chat,
                    f"❌ Task {job.id} failed: {job.title}\n{job.error or ''}"[:1500],
                    [[{"text": "🔁 Try again", "callback_data": f"task:{job.id}:retry"}]],
                )
            elif job.created_by == "telegram":
                answer = str((job.result or {}).get("answer") or "")
                message = await api.send_message(chat, f"✅ Task {job.id} done: {job.title}\n\n{answer[:3000]}")
            else:
                continue
            self.tg.mark_sent("task", f"{job.id}:{job.finished_at}", chat, message["message_id"])

    # ── Social Autopilot errors ─────────────────────────────────────────────

    async def social_errors(self, api: TelegramAPI, chat: int) -> None:
        data = await self.ctx.social.get("/activity", level="error", limit=20)
        items = sorted(data.get("items") or [], key=lambda a: a.get("id", 0))
        last = self.tg.get("since_activity_id")
        if last is None:
            self.tg.set("since_activity_id", max([a.get("id", 0) for a in items] or [0]))
            return
        fresh = [a for a in items if a.get("id", 0) > last]
        for item in fresh[:5]:
            await api.send_message(chat, f"⚠️ Social Autopilot: {item.get('message', '')}"[:1500])
        if len(fresh) > 5:
            await api.send_message(chat, f"…and {len(fresh) - 5} more errors. Ask me “what went wrong today?”.")
        if fresh:
            self.tg.set("since_activity_id", max(a.get("id", 0) for a in fresh))

    # ── morning summary ─────────────────────────────────────────────────────

    async def morning(self, api: TelegramAPI, chat: int, *, force: bool = False) -> bool:
        local = datetime.now(self.ctx.tz)
        today = local.date().isoformat()
        if not force and (local.hour < self.ctx.settings.telegram_summary_hour or self.tg.get("summary_date") == today):
            return False
        await api.send_message(chat, await self.summary())
        if not force:
            self.tg.set("summary_date", today)
        return True

    async def summary(self) -> str:
        tz = self.ctx.tz
        start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        lines = [f"☀️ Good morning — {start:%A %-d %B}"]
        social = self.ctx.social
        try:
            cal = await social.get(
                "/calendar",
                start=int((start - timedelta(days=1)).timestamp()),
                end=int((start + timedelta(days=1)).timestamp()),
            )
            items = cal.get("items") or []
            today_items = [i for i in items if (i.get("scheduled_at") or 0) >= start.timestamp()]
            yesterday = [i for i in items if (i.get("scheduled_at") or 0) < start.timestamp()]
            by_post: dict[str, dict[str, Any]] = {}
            for item in sorted(today_items, key=lambda i: i.get("scheduled_at") or 0):
                entry = by_post.setdefault(str(item.get("post_id")), {"item": item, "channels": set()})
                entry["channels"].add(item.get("provider"))
            lines.append(f"\nToday: {len(by_post)} post(s) going out")
            for entry in list(by_post.values())[:8]:
                item, count = entry["item"], len(entry["channels"])
                lines.append(
                    f"• {self.ctx.when(item.get('scheduled_at'))[-5:]} {item.get('title') or 'Post'}"[:80]
                    + f" ({count} channel{'s' if count != 1 else ''})"
                )
            published = len({i.get("post_id") for i in yesterday if i.get("status") == "published"})
            failed = sum(1 for i in yesterday if i.get("status") == "failed")
            lines.append(
                f"Yesterday: {published} post(s) published" + (f", {failed} channel post(s) failed" if failed else "")
            )

            pending = (await social.get("/posts", status="pending_approval", page_size=1)).get("counts", {})
            waiting_posts = int(pending.get("pending_approval", 0))
            promos = await social.get("/promotions")
            running = [p for p in promos.get("promotions") or [] if p.get("status") == "running"]
            if running:
                lines.append("\nPromotions running:")
                lines += [
                    f"• {p['name']}"
                    + (f", code {p['coupon_code']}" if p.get("coupon_code") else "")
                    + f" (until {p.get('end_date')})"
                    for p in running
                ]
            nxt = next(
                (u for u in (promos.get("auto") or {}).get("upcoming") or [] if u.get("status") == "planned"), None
            )
            if nxt:
                lines.append(f"Next automatic promotion: {nxt['label']} from {nxt['start']} ({nxt['code']})")
            errors = (await social.get("/activity", level="error", limit=50)).get("items") or []
            recent_errors = sum(1 for e in errors if (e.get("created_at") or 0) >= time.time() - 86400)
        except SocialError as exc:
            lines.append(f"\n(Social Autopilot didn't answer: {exc})")
            waiting_posts, recent_errors = 0, 0
        waiting_emails = len(self.ctx.store.approvals("pending"))
        counts = self.ctx.store.counts()
        lines.append(f"\nWaiting for you: {waiting_posts} post(s), {waiting_emails} email(s)")
        lines.append(
            f"Task Agent: {counts['queued'] + counts['running']} in progress, {counts['failed']} failed overall"
        )
        if recent_errors:
            lines.append(f"Errors in the last 24 h: {recent_errors}. Ask me “what went wrong?”.")
        return "\n".join(lines)
