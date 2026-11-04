"""The email account the agent will read and send from (IMAP in, SMTP out). Password in the Keychain."""

from __future__ import annotations

import asyncio
import imaplib
import smtplib
from typing import Any

from task_agent.db import JobStore, iso, now
from task_agent.keychain import get_secret, has_secret, set_secret
from task_agent.tls import describe_tls_error, tls_context

SECRET = "email"
FIELDS = ("username", "from_name", "imap_host", "imap_port", "smtp_host", "smtp_port")


class EmailError(ValueError):
    """Shown to the user as-is."""


class EmailStore:
    def __init__(self, store: JobStore) -> None:
        self.store = store

    def get(self) -> dict[str, Any]:
        with self.store._connect() as db:
            row = db.execute("SELECT * FROM email_account WHERE id = 1").fetchone()
        data = dict(row) if row else {}
        return {
            "username": data.get("username", ""),
            "from_name": data.get("from_name", ""),
            "imap_host": data.get("imap_host", ""),
            "imap_port": int(data.get("imap_port") or 993),
            "smtp_host": data.get("smtp_host", ""),
            "smtp_port": int(data.get("smtp_port") or 587),
            "has_password": has_secret(SECRET),
            "last_test_at": data.get("last_test_at"),
            "last_test_ok": None if data.get("last_test_ok") is None else bool(data["last_test_ok"]),
            "last_test_message": data.get("last_test_message", ""),
        }

    def save(self, values: dict[str, Any], password: str = "") -> dict[str, Any]:
        clean = {k: values[k] for k in FIELDS if k in values}
        for key in ("imap_port", "smtp_port"):
            if key in clean:
                try:
                    clean[key] = int(clean[key])
                except (TypeError, ValueError) as exc:
                    raise EmailError(f"{key.replace('_', ' ').upper()} must be a number.") from exc
                if not 1 <= clean[key] <= 65535:
                    raise EmailError(f"{key.replace('_', ' ').upper()} must be between 1 and 65535.")
        for key in ("username", "from_name", "imap_host", "smtp_host"):
            if key in clean:
                clean[key] = str(clean[key] or "").strip()
        merged = {**{k: v for k, v in self.get().items() if k in FIELDS}, **clean}
        with self.store._connect() as db:
            db.execute(
                """INSERT INTO email_account (id, username, from_name, imap_host, imap_port, smtp_host, smtp_port,
                       updated_at) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET username = excluded.username, from_name = excluded.from_name,
                       imap_host = excluded.imap_host, imap_port = excluded.imap_port,
                       smtp_host = excluded.smtp_host, smtp_port = excluded.smtp_port,
                       updated_at = excluded.updated_at""",
                (*(merged[k] for k in FIELDS), iso(now())),
            )
        if password:
            set_secret(SECRET, password)
        return self.get()

    async def test(self) -> tuple[bool, str]:
        """Log in to IMAP and SMTP, then log out. Sends nothing."""
        account = self.get()
        password = get_secret(SECRET) or ""
        if not (account["username"] and account["imap_host"] and account["smtp_host"] and password):
            ok, message = False, "Fill in the username, password, IMAP host and SMTP host first."
        else:
            try:
                await asyncio.to_thread(_check_imap, account, password)
                await asyncio.to_thread(_check_smtp, account, password)
                ok, message = True, f"Logged in to {account['imap_host']} and {account['smtp_host']}."
            except Exception as exc:
                ok, message = False, _describe(exc)
        with self.store._connect() as db:
            db.execute(
                """INSERT INTO email_account (id, updated_at, last_test_at, last_test_ok, last_test_message)
                   VALUES (1, ?, ?, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET last_test_at = excluded.last_test_at,
                       last_test_ok = excluded.last_test_ok, last_test_message = excluded.last_test_message""",
                (iso(now()), iso(now()), int(ok), message),
            )
        return ok, message


def _check_imap(account: dict[str, Any], password: str) -> None:
    with imaplib.IMAP4_SSL(account["imap_host"], account["imap_port"], timeout=20, ssl_context=tls_context()) as imap:
        imap.login(account["username"], password)


def _check_smtp(account: dict[str, Any], password: str) -> None:
    context = tls_context()
    if account["smtp_port"] == 465:
        with smtplib.SMTP_SSL(account["smtp_host"], 465, timeout=20, context=context) as smtp:
            smtp.login(account["username"], password)
    else:
        with smtplib.SMTP(account["smtp_host"], account["smtp_port"], timeout=20) as smtp:
            smtp.starttls(context=context)
            smtp.login(account["username"], password)


def _describe(exc: Exception) -> str:
    if tls := describe_tls_error(exc):
        return tls
    if isinstance(exc, imaplib.IMAP4.error | smtplib.SMTPAuthenticationError):
        return "The email server refused the username or password. For Gmail, use an app password."
    if isinstance(exc, TimeoutError | OSError):
        return f"Couldn't reach the email server: {exc}"
    return f"{type(exc).__name__}: {exc}"[:300]
