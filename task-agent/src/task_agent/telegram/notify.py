"""What the bot sends without being asked: cards that need a decision, failures, finished tasks
(queued from Telegram), and the morning summary. Each item is sent once (tracked in telegram_sent)."""

from __future__ import annotations

import contextlib
import logging
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from task_agent.db import iso, now
from task_agent.promotion import KIND_LABELS, PromotionStore
from task_agent.social import SocialError
from task_agent.telegram.client import TelegramAPI, TelegramError

if TYPE_CHECKING:
    from task_agent.telegram.bot import TelegramBot

log = logging.getLogger("task_agent.telegram")

APPROVE_REJECT = "post"
EMAIL = "approval"
NEED = "need"


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
        steps = (
            self.post_cards,
            self.approval_cards,
            self.need_cards,
            self.task_updates,
            self.promotion_updates,
            self.social_errors,
            self.morning,
        )
        for step in steps:
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
            if job.parent_id is not None or (job.type == "promote_event" and job.status == "done"):
                continue  # a promotion reports its places itself (promotion_updates)
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

    # ── event promotions ────────────────────────────────────────────────────

    async def need_cards(self, api: TelegramAPI, chat: int) -> None:
        """A place that needs Joe (captcha, code, verification): one card with Done / Skip."""
        ps = PromotionStore(self.ctx.store)
        waiting = {t["id"]: t for t in ps.needing_joe()}
        for target_id, target in waiting.items():
            ref = f"{target_id}:{target['asks']}"
            if self.tg.sent(NEED, ref):
                continue
            promotion = self.ctx.store.get(int(target["promotion_id"]))
            text = (
                f"🔐 {target['name']} needs you\n{target.get('need_reason') or ''}\n\n"
                "It's open in the browser on the Mac. Do that there, then press Done and it carries on. "
                "It waits a day, then skips this place." + (f"\n\n📣 {promotion.title}" if promotion else "")
            )
            buttons = [
                [
                    {"text": "✅ Done", "callback_data": f"need:{target_id}:d"},
                    {"text": "⏭ Skip this place", "callback_data": f"need:{target_id}:s"},
                ]
            ]
            message = await api.send_message(chat, text, buttons)
            self.tg.mark_sent(NEED, ref, chat, message["message_id"])
        for card in self.tg.open_cards(NEED):
            target_id = int(card["ref"].split(":")[0])
            if target_id in waiting and card["ref"] == f"{target_id}:{waiting[target_id]['asks']}":
                continue
            with contextlib.suppress(Exception):
                target = ps.get(target_id)
                await api.finish_card(
                    card["chat_id"], card["message_id"],
                    f"🔐 {target['name']}: {target['status'].replace('_', ' ')} (no longer waiting for you)",
                )  # fmt: skip
            self.tg.close_card(NEED, card["ref"])

    async def promotion_updates(self, api: TelegramAPI, chat: int) -> None:
        """The plan once it's made, and one report when every place has finished."""
        since = self.tg.get("since_task_finished")
        if since is None:
            return
        ps = PromotionStore(self.ctx.store)
        jobs, _ = self.ctx.store.search(["done"], limit=30, include_children=False)
        for job in jobs:
            if job.type != "promote_event" or not job.finished_at or job.finished_at < since:
                continue
            targets = ps.for_promotion(job.id)
            if not self.tg.sent("promo-plan", str(job.id)):
                message = await api.send_message(chat, self.plan_text(job, targets))
                self.tg.mark_sent("promo-plan", str(job.id), chat, message["message_id"])
            if targets and ps.is_finished(job.id) and not self.tg.sent("promo-done", str(job.id)):
                message = await api.send_message(chat, self.report_text(job, targets))
                self.tg.mark_sent("promo-done", str(job.id), chat, message["message_id"])

    def plan_text(self, job: Any, targets: list[dict[str, Any]]) -> str:
        notes = str((job.result or {}).get("notes") or "")
        if not targets:
            return f"📣 {job.title}: I didn't find anywhere suitable to post it.\n{notes}"[:1500]
        queued = [t for t in targets if t["status"] in ("queued", "working")]
        listings = sum(1 for t in queued if t["kind"] == "listing")
        paced = sorted(t["scheduled_at"] for t in queued if t["kind"] in ("group", "forum") and t["scheduled_at"])
        lines = [f"📣 {job.title}: {len(targets)} place(s) planned"]
        if listings:
            lines.append(f"• {listings} listing site(s), starting now")
        if paced:
            first = datetime.fromisoformat(paced[0]).timestamp()
            last = datetime.fromisoformat(paced[-1]).timestamp()
            lines.append(f"• {len(paced)} group/forum post(s), {self.ctx.when(first)} to {self.ctx.when(last)}")
        pages = [t for t in queued if t["kind"] == "page"]
        if pages:
            lines.append("• Your Pages: " + ", ".join(t["name"] for t in pages))
        own = [t for t in targets if t["kind"] == "own_social"]
        for t in own:
            lines.append(f"• {t['name']}: {t['status'].replace('_', ' ')}")
        skipped = [t for t in targets if t["status"] == "skipped"]
        if skipped:
            lines.append(f"• {len(skipped)} skipped: " + "; ".join(f"{t['name']} ({t['note']})" for t in skipped[:3]))
        if notes:
            lines.append(f"\n{notes}")
        lines.append("\nI'll message you if a site needs you, and when it's all done.")
        return "\n".join(lines)[:3500]

    def report_text(self, job: Any, targets: list[dict[str, Any]]) -> str:
        ok = [t for t in targets if t["status"] in ("posted", "already_listed")]
        lines = [f"✅ {job.title}: finished. Live in {len(ok)} of {len(targets)} place(s)."]
        for t in targets:
            mark = {"posted": "✅", "already_listed": "✅", "skipped": "⏭", "failed": "❌"}.get(t["status"], "•")
            detail = t.get("result_url") or t.get("note") or ""
            lines.append(f"{mark} {KIND_LABELS.get(t['kind'], t['kind'])}: {t['name']} {detail}"[:300])
        return "\n".join(lines)[:4000]

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
