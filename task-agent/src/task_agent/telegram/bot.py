"""The Telegram bot: long-polls Telegram from this Mac (no public address needed), answers only Joe,
sends notifications, and runs a change only after Joe presses its button.

Runs inside the worker process. It picks up a new token or owner from the Settings page by itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime
from typing import Any

import httpx
from pydantic_ai.models import Model

from task_agent.config import Settings
from task_agent.db import JobStore, now
from task_agent.promotion import target_done, target_skip
from task_agent.social import SocialClient
from task_agent.telegram.actions import BotContext, execute
from task_agent.telegram.assistant import AssistantDeps, answer
from task_agent.telegram.client import TelegramAPI, TelegramError
from task_agent.telegram.notify import APPROVE_REJECT, EMAIL, Notifier
from task_agent.telegram.store import TelegramStore

log = logging.getLogger("task_agent.telegram")

HELP = """I'm your BioluxeLab assistant. You can:
• Ask anything: “what's posting tomorrow?”, “how did last week go?”, “which codes are running?”
• Give instructions: “pause the Halloween campaign”, “research free listing sites in Leeds”.
  I'll show what I'm about to do, and nothing happens until you press Confirm.
• Approve or reject posts and emails with the buttons on their cards.

Commands: /summary (morning summary now) · /queue (everything waiting for you) · /help"""

PROPOSAL_TTL_HOURS = 24


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


class TelegramBot:
    def __init__(
        self,
        store: JobStore,
        settings: Settings,
        *,
        model: Model | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        social_transport: httpx.AsyncBaseTransport | None = None,
        browser: Any = None,
    ) -> None:
        self.browser = browser  # the shared browser, so Done / Skip can close the page Joe used
        self.tg = TelegramStore(store, settings)
        self.ctx = BotContext(store, settings, SocialClient(settings, social_transport))
        self.model = model
        self.transport = transport
        self.notifier = Notifier(self)
        self._chat_lock = asyncio.Lock()
        self._replies: set[asyncio.Task[None]] = set()

    def api(self) -> TelegramAPI | None:
        token = self.tg.token()
        return TelegramAPI(token, self.transport) if token else None

    # ── main loop ──────────────────────────────────────────────────────────

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            token = self.tg.token()
            if not token:
                await _sleep(stop, 30)  # set on the Settings page; picked up without a restart
                continue
            api = TelegramAPI(token, self.transport)
            try:
                me = await api.get_me()
            except TelegramError as exc:
                self.tg.set("last_error", str(exc))
                await _sleep(stop, 60)
                continue
            self.tg.set("bot_username", me.get("username"))
            self.tg.set("last_error", None)
            log.info("telegram.connected bot=@%s", me.get("username"))
            loops = [
                asyncio.create_task(self._poll(api, token, stop)),
                asyncio.create_task(self._notify(api, token, stop)),
            ]
            await asyncio.wait(loops, return_when=asyncio.FIRST_COMPLETED)
            for task in loops:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def _poll(self, api: TelegramAPI, token: str, stop: asyncio.Event) -> None:
        while not stop.is_set() and self.tg.token() == token:
            try:
                updates = await api.get_updates(self.tg.get("update_offset"), wait_seconds=50)
            except TelegramError as exc:
                self.tg.set("last_error", str(exc))
                await _sleep(stop, 10)
                continue
            for update in updates:
                self.tg.set("update_offset", update["update_id"] + 1)
                try:
                    await self.handle_update(api, update)
                except Exception:
                    log.exception("telegram update failed")

    async def _notify(self, api: TelegramAPI, token: str, stop: asyncio.Event) -> None:
        while not stop.is_set() and self.tg.token() == token:
            await self.notifier.tick(api)
            await _sleep(stop, self.ctx.settings.telegram_notify_seconds)

    # ── who may talk to the bot ────────────────────────────────────────────

    def authorised(self, user: dict[str, Any], chat: dict[str, Any]) -> bool:
        owner_id, _ = self.tg.owner()
        if owner_id is not None:
            return user.get("id") == owner_id and chat.get("type") == "private"
        wanted = self.tg.owner_username().lower()
        return bool(wanted) and str(user.get("username") or "").lower() == wanted and chat.get("type") == "private"

    async def handle_update(self, api: TelegramAPI, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            callback = update["callback_query"]
            message = callback.get("message") or {}
            if not self.authorised(callback.get("from") or {}, message.get("chat") or {}):
                await api.answer_callback(callback["id"], "This bot is private.")
                return
            await self.handle_button(api, callback)
            return
        message = update.get("message") or {}
        user, chat = message.get("from") or {}, message.get("chat") or {}
        if not self.authorised(user, chat):
            log.info("telegram.ignored user=%s", user.get("username") or user.get("id"))
            return
        if self.tg.owner()[0] is None:  # first message from Joe: lock to his permanent id
            self.tg.link_owner(user["id"], chat["id"], user.get("username") or "")
            await api.send_message(chat["id"], "Connected ✅ This bot now only answers you.\n\n" + HELP)
            return
        text = str(message.get("text") or "").strip()
        if not text:
            await api.send_message(chat["id"], "I can only read text messages for now.")
            return
        await self.handle_text(api, chat["id"], text)

    # ── text: commands and questions ───────────────────────────────────────

    async def handle_text(self, api: TelegramAPI, chat_id: int, text: str) -> None:
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
        if command in ("/start", "/help"):
            await api.send_message(chat_id, HELP)
        elif command == "/summary":
            await self.notifier.morning(api, chat_id, force=True)
        elif command == "/queue":
            sent = await self.notifier.post_cards(api, chat_id, resend=True)
            emails = len(self.ctx.store.approvals("pending"))
            if not sent and not emails:
                await api.send_message(chat_id, "Nothing is waiting for you. 🎉")
        else:
            task = asyncio.create_task(self.reply(api, chat_id, text))
            self._replies.add(task)
            task.add_done_callback(self._replies.discard)

    async def reply(self, api: TelegramAPI, chat_id: int, text: str) -> None:
        async with self._chat_lock:  # one question at a time, in order
            typing = asyncio.create_task(self._keep_typing(api, chat_id))
            deps = AssistantDeps(self.ctx, self.tg, chat_id)
            try:
                reply = await answer(deps, text, self.model)
            except Exception as exc:
                log.exception("telegram answer failed")
                reply = f"Sorry, that didn't work ({type(exc).__name__}). Try again in a minute."
            finally:
                typing.cancel()
            self.tg.remember(chat_id, "user", text)
            self.tg.remember(chat_id, "assistant", reply)
            await api.send_message(chat_id, reply)
            for action_id in deps.proposals:
                action = self.tg.action(action_id)
                if action:
                    await api.send_message(
                        chat_id,
                        f"Confirm? {action['summary']}",
                        [
                            [
                                {"text": "✅ Confirm", "callback_data": f"act:{action_id}:y"},
                                {"text": "✖ Cancel", "callback_data": f"act:{action_id}:n"},
                            ]
                        ],
                    )

    async def _keep_typing(self, api: TelegramAPI, chat_id: int) -> None:
        with contextlib.suppress(TelegramError, asyncio.CancelledError):
            while True:
                await api.typing(chat_id)
                await asyncio.sleep(4.5)

    # ── buttons ────────────────────────────────────────────────────────────

    async def handle_button(self, api: TelegramAPI, callback: dict[str, Any]) -> None:
        message = callback.get("message") or {}
        chat_id, message_id = message.get("chat", {}).get("id"), message.get("message_id")
        is_photo = bool(message.get("photo"))
        original = str(message.get("caption") or message.get("text") or "")
        kind, ref, choice = (str(callback.get("data") or "").split(":") + ["", "", ""])[:3]

        async def finish(outcome: str) -> None:
            await api.answer_callback(callback["id"], outcome)
            with contextlib.suppress(TelegramError):
                await api.finish_card(chat_id, message_id, f"{original}\n\n{outcome}", is_photo=is_photo)

        try:
            if kind == "post":
                action = "approve_post" if choice == "a" else "reject_post"
                result = await execute(self.ctx, action, {"post_id": ref})
                self.tg.close_card(APPROVE_REJECT, ref)
                await finish(("✅ " if choice == "a" else "✖ ") + result)
            elif kind == "appr":
                result = await execute(
                    self.ctx,
                    "decide_approval",
                    {"approval_id": int(ref), "decision": "approve" if choice == "a" else "reject"},
                )
                self.tg.close_card(EMAIL, ref)
                await finish(("✅ " if choice == "a" else "✖ ") + result)
            elif kind == "task" and choice == "retry":
                await finish("🔁 " + await execute(self.ctx, "retry_task", {"job_id": int(ref)}))
            elif kind == "need":
                if choice == "d":
                    _, result = await target_done(self.ctx.store, int(ref), self.browser)
                    await finish("✅ " + result)
                else:
                    _, result = await target_skip(self.ctx.store, int(ref), self.browser)
                    await finish("⏭ " + result)
            elif kind == "act":
                await self._confirm(int(ref), choice == "y", finish)
            else:
                await api.answer_callback(callback["id"], "That button has expired.")
        except Exception as exc:
            log.warning("telegram button %s failed: %s", callback.get("data"), exc)
            await finish(f"❌ Didn't work: {str(exc)[:200]}")

    async def _confirm(self, action_id: int, confirmed: bool, finish: Any) -> None:
        action = self.tg.action(action_id)
        if action is None or action["status"] != "proposed":
            await finish("Already handled.")
            return
        age = now() - datetime.fromisoformat(action["created_at"])
        if age.total_seconds() > PROPOSAL_TTL_HOURS * 3600:
            self.tg.decide(action_id, "cancelled", "expired")
            await finish("This expired. Ask me again if you still want it.")
            return
        if not confirmed:
            self.tg.decide(action_id, "cancelled")
            await finish("✖ Cancelled. Nothing changed.")
            return
        if not self.tg.claim(action_id):
            await finish("Already handled.")
            return
        try:
            result = await execute(self.ctx, action["kind"], action["params"])
        except Exception as exc:
            self.tg.decide(action_id, "failed", str(exc)[:500])
            raise
        self.tg.decide(action_id, "done", result)
        await finish(f"✅ Done. {result}")
