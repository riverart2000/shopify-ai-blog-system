"""Telegram state in the Task Agent DB: owner binding, sent cards, proposed actions, recent chat."""

from __future__ import annotations

import json
from typing import Any

from task_agent.config import Settings
from task_agent.db import JobStore, iso, now
from task_agent.keychain import delete_secret, get_secret, set_secret

TOKEN_SECRET = "telegram:bot_token"

SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS telegram_sent (
    kind TEXT NOT NULL, ref TEXT NOT NULL, chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
    is_photo INTEGER NOT NULL DEFAULT 0, closed INTEGER NOT NULL DEFAULT 0, sent_at TEXT NOT NULL,
    PRIMARY KEY (kind, ref)
);
CREATE TABLE IF NOT EXISTS telegram_actions (
    id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, kind TEXT NOT NULL, params TEXT NOT NULL,
    summary TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed', result TEXT,
    created_at TEXT NOT NULL, decided_at TEXT
);
CREATE TABLE IF NOT EXISTS telegram_history (
    id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


class TelegramStore:
    def __init__(self, store: JobStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        with store._connect() as db:
            db.executescript(SCHEMA)

    # ── token and owner ────────────────────────────────────────────────────

    def token(self) -> str:
        try:
            saved = get_secret(TOKEN_SECRET)
        except Exception:  # a locked Keychain must not crash the worker
            saved = None
        return saved or self.settings.telegram_bot_token.get_secret_value()

    def save_token(self, token: str) -> None:
        set_secret(TOKEN_SECRET, token)
        self.set("bot_username", None)
        self.set("last_error", None)

    def clear_token(self) -> None:
        delete_secret(TOKEN_SECRET)
        self.set("bot_username", None)

    def owner_username(self) -> str:
        return str(self.get("owner_username") or self.settings.telegram_owner_username or "").lstrip("@").strip()

    def owner(self) -> tuple[int | None, int | None]:
        """(Telegram user id, chat id) once Joe has messaged the bot."""
        user, chat = self.get("owner_user_id"), self.get("owner_chat_id")
        return (int(user) if user else None, int(chat) if chat else None)

    def link_owner(self, user_id: int, chat_id: int, username: str) -> None:
        self.set("owner_user_id", user_id)
        self.set("owner_chat_id", chat_id)
        self.set("owner_linked_username", username)
        self.set("owner_linked_at", iso(now()))

    def unlink_owner(self) -> None:
        for key in ("owner_user_id", "owner_chat_id", "owner_linked_username", "owner_linked_at"):
            self.set(key, None)

    # ── key/value state ────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        with self.store._connect() as db:
            row = db.execute("SELECT value FROM telegram_state WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value: Any) -> None:
        with self.store._connect() as db:
            if value is None:
                db.execute("DELETE FROM telegram_state WHERE key = ?", (key,))
            else:
                db.execute(
                    "INSERT INTO telegram_state (key, value) VALUES (?, ?) "
                    "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(value)),
                )

    # ── cards already sent (so nothing is sent twice) ──────────────────────

    def sent(self, kind: str, ref: str) -> dict[str, Any] | None:
        with self.store._connect() as db:
            row = db.execute("SELECT * FROM telegram_sent WHERE kind = ? AND ref = ?", (kind, ref)).fetchone()
        return dict(row) if row else None

    def mark_sent(self, kind: str, ref: str, chat_id: int, message_id: int, *, is_photo: bool = False) -> None:
        with self.store._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO telegram_sent (kind, ref, chat_id, message_id, is_photo, closed, sent_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?)",
                (kind, ref, chat_id, message_id, int(is_photo), iso(now())),
            )

    def open_cards(self, kind: str) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            rows = db.execute("SELECT * FROM telegram_sent WHERE kind = ? AND closed = 0", (kind,)).fetchall()
        return [dict(r) for r in rows]

    def close_card(self, kind: str, ref: str) -> None:
        with self.store._connect() as db:
            db.execute("UPDATE telegram_sent SET closed = 1 WHERE kind = ? AND ref = ?", (kind, ref))

    # ── proposed actions (run only after Joe presses Confirm) ──────────────

    def propose(self, chat_id: int, kind: str, params: dict[str, Any], summary: str) -> int:
        with self.store._connect() as db:
            cur = db.execute(
                "INSERT INTO telegram_actions (chat_id, kind, params, summary, created_at) VALUES (?, ?, ?, ?, ?)",
                (chat_id, kind, json.dumps(params), summary, iso(now())),
            )
            return int(cur.lastrowid)

    def action(self, action_id: int) -> dict[str, Any] | None:
        with self.store._connect() as db:
            row = db.execute("SELECT * FROM telegram_actions WHERE id = ?", (action_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["params"] = json.loads(data["params"])
        return data

    def decide(self, action_id: int, status: str, result: str = "") -> bool:
        """Move a proposed action to done/failed/cancelled. False if it was already decided."""
        with self.store._connect() as db:
            return bool(
                db.execute(
                    "UPDATE telegram_actions SET status = ?, result = ?, decided_at = ? WHERE id = ? AND status = ?",
                    (status, result, iso(now()), action_id, "running" if status in ("done", "failed") else "proposed"),
                ).rowcount
            )

    def claim(self, action_id: int) -> bool:
        with self.store._connect() as db:
            return bool(
                db.execute(
                    "UPDATE telegram_actions SET status = 'running' WHERE id = ? AND status = 'proposed'", (action_id,)
                ).rowcount
            )

    # ── recent conversation (gives the assistant context) ──────────────────

    def remember(self, chat_id: int, role: str, text: str) -> None:
        with self.store._connect() as db:
            db.execute(
                "INSERT INTO telegram_history (chat_id, role, text, created_at) VALUES (?, ?, ?, ?)",
                (chat_id, role, text[:4000], iso(now())),
            )
            db.execute(
                "DELETE FROM telegram_history WHERE chat_id = ? AND id NOT IN "
                "(SELECT id FROM telegram_history WHERE chat_id = ? ORDER BY id DESC LIMIT 40)",
                (chat_id, chat_id),
            )

    def history(self, chat_id: int, limit: int = 8) -> list[dict[str, str]]:
        with self.store._connect() as db:
            rows = db.execute(
                "SELECT role, text FROM telegram_history WHERE chat_id = ? ORDER BY id DESC LIMIT ?", (chat_id, limit)
            ).fetchall()
        return [dict(r) for r in reversed(rows)]
