"""Event types: Joe runs several kinds of events (Comedy, Bloomsbury speakers, Biohacking, Juggling). Each has its
own email (read over IMAP for sign-up codes), its own Eventbrite and Meetup logins (site logins tagged with the
type), and its own Facebook Page and LinkedIn Page. A promotion task picks one type; the agent then uses only that
type's email, logins and Pages. Facebook and LinkedIn themselves are one shared personal login (logins with no type).

The inbox password lives in the Keychain (`event-type:<key>:imap`), never in the DB, prompts or logs.
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import imaplib
import re
from datetime import UTC, timedelta
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from task_agent.db import JobStore, iso, now
from task_agent.keychain import delete_secret, get_secret, has_secret, set_secret

FIELDS = (
    "name", "email", "imap_host", "imap_port", "imap_username", "facebook_page", "linkedin_page", "tag_handles",
    "audience", "enabled",
)  # fmt: skip
_KEY = re.compile(r"[^a-z0-9]+")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_CODE_NEAR = re.compile(
    r"(?:code|verif\w*|confirm\w*|one[- ]time|otp|passcode|pin|security)\W{0,40}?\b([A-Z0-9]{4,8})\b"
    r"|\b(\d{4,8})\b\W{0,40}?(?:is your|code|verif\w*|confirm\w*)",
    re.I,
)
_LINK = re.compile(r"https?://[^\s<>\"')\]]+")
_LINK_WORDS = re.compile(r"verif|confirm|activat|magic|login|log-in|signin|sign-in|token|auth|validate|email", re.I)


class EventTypeError(ValueError):
    """Shown to the user as-is."""


def secret_name(key: str) -> str:
    return f"event-type:{key}:imap"


class EventTypeStore:
    def __init__(self, store: JobStore) -> None:
        self.store = store

    def list(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            rows = db.execute("SELECT * FROM event_types ORDER BY name COLLATE NOCASE").fetchall()
        types = [self._view(dict(r)) for r in rows]
        return [t for t in types if t["enabled"]] if enabled_only else types

    def get(self, key: str) -> dict[str, Any]:
        with self.store._connect() as db:
            row = db.execute("SELECT * FROM event_types WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise LookupError(f"No event type '{key}'.")
        return self._view(dict(row))

    def find(self, value: str) -> dict[str, Any] | None:
        """A type by its key or name (as typed in a template or on Telegram)."""
        value = (value or "").strip()
        if not value:
            return None
        for kind in self.list():
            if value.lower() in (kind["key"], kind["name"].lower()):
                return kind
        return None

    def names(self) -> dict[str, str]:
        return {t["key"]: t["name"] for t in self.list()}

    def save(self, values: dict[str, Any], *, imap_password: str = "", key: str | None = None) -> dict[str, Any]:
        clean = {k: values[k] for k in FIELDS if k in values and values[k] is not None}
        for name in ("name", "email", "imap_host", "imap_username", "facebook_page", "linkedin_page"):
            if name in clean:
                clean[name] = str(clean[name]).strip()
        for name in ("tag_handles", "audience"):
            if name in clean:
                clean[name] = str(clean[name]).strip()[:3_000]
        if "imap_port" in clean:
            try:
                clean["imap_port"] = int(clean["imap_port"] or 993)
            except (TypeError, ValueError) as exc:
                raise EventTypeError("The IMAP port must be a number, usually 993.") from exc
        if "enabled" in clean:
            clean["enabled"] = int(bool(clean["enabled"]))
        if key is None and not clean.get("name"):
            raise EventTypeError("Give the event type a name, e.g. Comedy.")
        if "name" in clean and not clean["name"]:
            raise EventTypeError("The name can't be empty.")
        if clean.get("email") and not _EMAIL.fullmatch(clean["email"]):
            raise EventTypeError("That email address doesn't look right.")
        stamp = iso(now())
        with self.store._connect() as db:
            if key:
                if not db.execute("SELECT 1 FROM event_types WHERE key = ?", (key,)).fetchone():
                    raise LookupError(f"No event type '{key}'.")
                if clean:
                    sets = ", ".join(f"{k} = ?" for k in clean)
                    db.execute(
                        f"UPDATE event_types SET {sets}, updated_at = ? WHERE key = ?",  # noqa: S608 (fixed names)
                        (*clean.values(), stamp, key),
                    )
            else:
                key = self._new_key(db, clean["name"])
                columns = ["key", *clean, "created_at", "updated_at"]
                db.execute(
                    f"INSERT INTO event_types ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",  # noqa: S608
                    (key, *clean.values(), stamp, stamp),
                )
        if imap_password:
            set_secret(secret_name(key), imap_password)
        return self.get(key)

    def delete(self, key: str) -> None:
        """Remove the type, its inbox password and its own logins (shared logins stay)."""
        from task_agent.accounts import secret_name as login_secret

        self.get(key)
        with self.store._connect() as db:
            sites = [r["site"] for r in db.execute("SELECT site FROM site_profiles WHERE event_type = ?", (key,))]
            db.execute("DELETE FROM site_profiles WHERE event_type = ?", (key,))
            db.execute("DELETE FROM event_types WHERE key = ?", (key,))
        for site in sites:
            delete_secret(login_secret(site))
        delete_secret(secret_name(key))

    def imap_password(self, key: str) -> str:
        return get_secret(secret_name(key)) or ""

    def inbox_ready(self, kind: dict[str, Any]) -> bool:
        return bool(kind["imap_host"] and (kind["imap_username"] or kind["email"]) and kind["has_imap_password"])

    async def test_email(self, key: str) -> tuple[bool, str]:
        """Log in to the type's inbox, then log out. Reads nothing."""
        kind = self.get(key)
        if not self.inbox_ready(kind):
            ok, message = False, "Fill in the email, IMAP host and IMAP password first."
        else:
            try:
                await asyncio.to_thread(_login_only, kind, self.imap_password(key))
                ok, message = True, f"Logged in to {kind['imap_host']} as {_imap_user(kind)}."
            except Exception as exc:
                ok, message = False, _describe(exc)
        with self.store._connect() as db:
            db.execute(
                "UPDATE event_types SET last_test_at = ?, last_test_ok = ?, last_test_message = ? WHERE key = ?",
                (iso(now()), int(ok), message, key),
            )
        return ok, message

    async def recent_messages(self, key: str, *, minutes: int = 30, limit: int = 12) -> list[dict[str, Any]]:
        """The newest messages in the type's inbox from the last `minutes` (left unread)."""
        kind = self.get(key)
        if not self.inbox_ready(kind):
            raise EventTypeError(f"The inbox for {kind['name']} isn't set up (Task Agent Settings → Event types).")
        return await asyncio.to_thread(_fetch_recent, kind, self.imap_password(key), minutes, limit)

    @staticmethod
    def _view(row: dict[str, Any]) -> dict[str, Any]:
        key = row["key"]
        return {
            "key": key,
            "name": row.get("name") or key,
            "email": row.get("email") or "",
            "imap_host": row.get("imap_host") or "",
            "imap_port": int(row.get("imap_port") or 993),
            "imap_username": row.get("imap_username") or "",
            "has_imap_password": has_secret(secret_name(key)),
            "facebook_page": row.get("facebook_page") or "",
            "linkedin_page": row.get("linkedin_page") or "",
            "tag_handles": row.get("tag_handles") or "",
            "audience": row.get("audience") or "",
            "enabled": bool(row.get("enabled", 1)),
            "last_test_at": row.get("last_test_at"),
            "last_test_ok": None if row.get("last_test_ok") is None else bool(row["last_test_ok"]),
            "last_test_message": row.get("last_test_message") or "",
            "updated_at": row.get("updated_at"),
        }

    @staticmethod
    def _new_key(db: Any, name: str) -> str:
        base = _KEY.sub("-", name.lower()).strip("-")[:40] or "event-type"
        key, n = base, 2
        while db.execute("SELECT 1 FROM event_types WHERE key = ?", (key,)).fetchone():
            key, n = f"{base}-{n}", n + 1
        return key


def describe_for_agent(kind: dict[str, Any], logins: list[dict[str, Any]]) -> str:
    """The event type as the agent is told it (no passwords)."""
    lines = [f"Event type: {kind['name']}"]
    if kind["email"]:
        lines.append(f"This type's email (for sign-ups and its accounts): {kind['email']}")
    if kind["facebook_page"]:
        lines.append(f"This type's Facebook Page: {kind['facebook_page']}")
    if kind["linkedin_page"]:
        lines.append(f"This type's LinkedIn Page: {kind['linkedin_page']}")
    if kind["tag_handles"]:
        lines.append(f"Handles to tag: {' '.join(kind['tag_handles'].split())}")
    if kind["audience"]:
        lines.append(f"Who it's for and notes: {kind['audience']}")
    own = [a for a in logins if a.get("event_type") == kind["key"]]
    shared = [a for a in logins if not a.get("event_type")]
    if own:
        lines.append(
            "This type's own logins: "
            + "; ".join(f"{a['label']} (site key '{a['site']}', username {a['username'] or '-'})" for a in own)
        )
    if shared:
        lines.append("Shared logins (every type): " + "; ".join(f"{a['label']} ('{a['site']}')" for a in shared))
    lines.append(
        "Inbox for codes: "
        + ("check_inbox reads it" if kind["has_imap_password"] and kind["imap_host"] else "not set up (use ask_joe)")
    )
    return "\n".join(lines)


# ── reading the inbox ───────────────────────────────────────────────────────


def _imap_user(kind: dict[str, Any]) -> str:
    return kind["imap_username"] or kind["email"]


def _login_only(kind: dict[str, Any], password: str) -> None:
    with imaplib.IMAP4_SSL(kind["imap_host"], kind["imap_port"], timeout=20) as imap:
        imap.login(_imap_user(kind), password)


def _fetch_recent(kind: dict[str, Any], password: str, minutes: int, limit: int) -> list[dict[str, Any]]:
    since = now() - timedelta(minutes=minutes)
    found: list[dict[str, Any]] = []
    with imaplib.IMAP4_SSL(kind["imap_host"], kind["imap_port"], timeout=20) as imap:
        imap.login(_imap_user(kind), password)
        imap.select("INBOX", readonly=True)  # read-only: nothing is marked as read
        day = (since - timedelta(days=1)).strftime("%d-%b-%Y")
        status, data = imap.search(None, "SINCE", day)
        if status != "OK" or not data or not data[0]:
            return []
        ids = data[0].split()[-limit * 3 :]
        for msg_id in reversed(ids):
            status, parts = imap.fetch(msg_id, "(BODY.PEEK[])")
            if status != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            message = email.message_from_bytes(parts[0][1], policy=email.policy.default)
            try:
                received = parsedate_to_datetime(str(message.get("Date", "")))
            except (TypeError, ValueError):
                continue
            if received.tzinfo is None:
                received = received.replace(tzinfo=UTC)
            if received < since:
                continue
            found.append(
                {
                    "from": str(message.get("From", ""))[:200],
                    "from_address": parseaddr(str(message.get("From", "")))[1].lower(),
                    "subject": str(message.get("Subject", ""))[:300],
                    "received": received.astimezone(UTC),
                    "text": _body_text(message)[:20_000],
                }
            )
            if len(found) >= limit:
                break
    return found


def _body_text(message: Any) -> str:
    plain = message.get_body(preferencelist=("plain",))
    html = message.get_body(preferencelist=("html",))
    parts: list[str] = []
    if plain is not None:
        parts.append(plain.get_content())
    if html is not None:
        soup = BeautifulSoup(html.get_content(), "html.parser")
        links = " ".join(a.get("href", "") for a in soup.find_all("a") if a.get("href"))
        parts.append(soup.get_text(" ") + " " + links)
    return "\n".join(parts)


def codes_and_links(message: dict[str, Any], site_words: set[str]) -> tuple[list[str], list[str]]:
    """Only what a sign-up needs from an email: likely codes, and links to the site that verify or log in."""
    text = f"{message['subject']}\n{message['text']}"
    codes: list[str] = []
    for match in _CODE_NEAR.finditer(text):
        code = (match.group(1) or match.group(2) or "").strip()
        if code and any(ch.isdigit() for ch in code) and code not in codes:
            codes.append(code)
    links: list[str] = []
    for url in _LINK.findall(text):
        url = url.rstrip(".,;")
        host = (urlsplit(url).hostname or "").lower()
        if any(w in host for w in site_words) and _LINK_WORDS.search(url) and url not in links:
            links.append(url[:500])
    return codes[:5], links[:5]


def site_words(*names: str) -> set[str]:
    """Words that identify a site in an email's sender, subject or links: 'meetup', 'eventbrite', …"""
    words: set[str] = set()
    for name in names:
        name = (name or "").lower().strip()
        host = urlsplit(name).hostname if name.startswith("http") else None
        if host:
            parts = host.removeprefix("www.").split(".")
            words.add(parts[0] if len(parts) <= 2 else parts[-3] if parts[-2] in ("co", "com", "org") else parts[-2])
        else:
            words.update(w for w in re.split(r"[^a-z0-9]+", name) if len(w) >= 4)
    return {w for w in words if w}


def matches_site(message: dict[str, Any], words: set[str]) -> bool:
    hay = f"{message['from']} {message['from_address']} {message['subject']}".lower()
    return any(w in hay for w in words)


def _describe(exc: Exception) -> str:
    if isinstance(exc, imaplib.IMAP4.error):
        return "The email server refused the username or password. For Gmail, use an app password."
    if isinstance(exc, TimeoutError | OSError):
        return f"Couldn't reach the email server: {exc}"
    return f"{type(exc).__name__}: {exc}"[:300]
