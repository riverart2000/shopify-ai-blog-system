"""Telegram Bot API over httpx. The token is part of every URL, so errors never include the URL."""

from __future__ import annotations

import json
from typing import Any

import httpx

API = "https://api.telegram.org"
TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024

Buttons = list[list[dict[str, str]]]


class TelegramError(RuntimeError):
    """Telegram refused a call or couldn't be reached. Never contains the bot token."""


def keyboard(rows: Buttons | None) -> dict[str, Any] | None:
    return {"inline_keyboard": rows} if rows else None


def trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class TelegramAPI:
    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base = f"{API}/bot{token}"
        self._transport = transport

    async def call(
        self, method: str, params: dict[str, Any] | None = None, *, http_timeout: float = 30, files: dict | None = None
    ) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            async with httpx.AsyncClient(timeout=http_timeout, transport=self._transport) as client:
                if files:
                    data = {k: json.dumps(v) if isinstance(v, dict | list) else str(v) for k, v in clean.items()}
                    response = await client.post(f"{self._base}/{method}", data=data, files=files)
                else:
                    response = await client.post(f"{self._base}/{method}", json=clean)
        except httpx.HTTPError as exc:
            raise TelegramError(f"Couldn't reach Telegram ({type(exc).__name__}).") from None
        try:
            body = response.json()
        except ValueError:
            raise TelegramError(f"Telegram returned HTTP {response.status_code}.") from None
        if not body.get("ok"):
            raise TelegramError(f"Telegram refused {method}: {body.get('description', response.status_code)}")
        return body.get("result")

    async def get_me(self) -> dict[str, Any]:
        return await self.call("getMe")

    async def get_updates(self, offset: int | None, wait_seconds: int = 50) -> list[dict[str, Any]]:
        # Telegram holds the request open up to `wait_seconds` until a message arrives (long polling).
        return await self.call(
            "getUpdates",
            {"offset": offset, "timeout": wait_seconds, "allowed_updates": ["message", "callback_query"]},
            http_timeout=wait_seconds + 15,
        )

    async def send_message(self, chat_id: int, text: str, buttons: Buttons | None = None) -> dict[str, Any]:
        return await self.call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": trim(text, TEXT_LIMIT),
                "reply_markup": keyboard(buttons),
                "link_preview_options": {"is_disabled": True},
            },
        )

    async def send_photo(
        self, chat_id: int, photo: bytes, caption: str, buttons: Buttons | None = None
    ) -> dict[str, Any]:
        return await self.call(
            "sendPhoto",
            {"chat_id": chat_id, "caption": trim(caption, CAPTION_LIMIT), "reply_markup": keyboard(buttons)},
            http_timeout=60,
            files={"photo": ("post.jpg", photo, "image/jpeg")},
        )

    async def finish_card(self, chat_id: int, message_id: int, text: str, *, is_photo: bool = False) -> None:
        """Replace a card's text and remove its buttons (after a decision)."""
        method, field, limit = (
            ("editMessageCaption", "caption", CAPTION_LIMIT) if is_photo else ("editMessageText", "text", TEXT_LIMIT)
        )
        await self.call(
            method,
            {
                "chat_id": chat_id,
                "message_id": message_id,
                field: trim(text, limit),
                "reply_markup": {"inline_keyboard": []},
            },
        )

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": trim(text, 190)})

    async def typing(self, chat_id: int) -> None:
        await self.call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
