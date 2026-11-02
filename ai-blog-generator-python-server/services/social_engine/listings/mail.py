"""Read a verification code or link for a directory sign-up.

Two routes, tried in this order:
1. IMAP on the business mailbox (LISTINGS_MAIL_IMAP_HOST / _PORT / _USER / _PASSWORD
   in the server's .env): the newest mail from the directory is parsed directly.
2. The owner's Gmail in their own Chrome, through the relay: the inbox search
   for the directory's mail is read and the tab closed. Nothing is ever sent.
"""
from __future__ import annotations

import asyncio
import email
import email.policy
import imaplib
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel

from ..ai.base import LLMRequest
from ..ai.llm import get_router
from ..context import StoreContext
from .catalogue import Directory
from .relay import Relay, Snapshot

logger = logging.getLogger("ai_blog_server.social_engine.listings")

_CODE = re.compile(r"(?<![\d-])(\d{4,8})(?![\d-])")
_CODE_NEAR = re.compile(r"(code|verify|verification|confirm|pin|otp)", re.I)
_LINK_WORDS = re.compile(r"verify|confirm|activate|validate|complete|token=|key=", re.I)


class _Found(BaseModel):
    code: str = ""
    link: str = ""


def search_words(directory: Directory) -> list[str]:
    """What to look for in From/Subject: the brand word(s), not the catalogue's full name.

    "Hotfrog UK" mails come from "Hotfrog via Localboard <noreply-hotfrog@localboard…>", so the full name and the
    site's own host both miss; the brand word "hotfrog" does not.
    """
    words: list[str] = []
    for host in directory.hosts:
        label = host.split(".")[0] if host else ""
        if label and label not in ("www", "business", "uk", "en") and len(label) >= 4:
            words.append(label)
    for word in re.split(r"[\s.,/()-]+", directory.name):
        if len(word) >= 4 and word.lower() not in ("company", "page", "business", "connect", "profile", "local", "small"):
            words.append(word)
    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        if w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out or [directory.name]


def gmail_search_url(directory: Directory) -> str:
    host = directory.hosts[0] if directory.hosts else directory.name
    query = f'newer_than:2d ("{directory.name}" OR {host})'
    return "https://mail.google.com/mail/u/0/#search/" + quote(query, safe="")


def extract(snapshot: Snapshot, directory: Directory) -> dict[str, str]:
    """Code or link straight from the text, without a model, when it is obvious."""
    text = snapshot.text
    hosts = tuple(h for h in directory.hosts if h)
    for line in text.splitlines():
        for m in re.finditer(r"https?://\S+", line):
            url = m.group(0).rstrip('")]\\.,')
            if any(h in url for h in hosts) and _LINK_WORDS.search(url):
                return {"code": "", "link": url}
    for line in text.splitlines():
        if _CODE_NEAR.search(line):
            m = _CODE.search(line)
            if m:
                return {"code": m.group(1), "link": ""}
    return {"code": "", "link": ""}


def imap_settings() -> dict[str, str]:
    """The business mailbox, when the .env has it; empty dict otherwise."""
    host = os.environ.get("LISTINGS_MAIL_IMAP_HOST", "").strip()
    user = os.environ.get("LISTINGS_MAIL_USER", "").strip()
    password = os.environ.get("LISTINGS_MAIL_PASSWORD", "")
    if not (host and user and password):
        return {}
    return {"host": host, "port": os.environ.get("LISTINGS_MAIL_IMAP_PORT", "993").strip() or "993", "user": user, "password": password}


def _message_text(msg: email.message.EmailMessage) -> str:
    """Plain text of a mail, with HTML reduced to text and links kept as bare URLs."""
    parts: list[str] = []
    for part in msg.walk():   # every text part: a code often sits in the plain part, the link in the HTML one
        if part.get_content_type() not in ("text/plain", "text/html"):
            continue
        try:
            content = part.get_content()
        except Exception:
            continue
        if part.get_content_type() == "text/html":
            content = re.sub(r"<a\s[^>]*href=[\"']([^\"']+)[\"'][^>]*>", r" \1 ", content, flags=re.I)
            content = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", content, flags=re.I | re.S)
            content = re.sub(r"<[^>]+>", " ", content)
        parts.append(str(content))
    return re.sub(r"[ \t]+", " ", "\n".join(parts))


def fetch_imap_mail(directory: Directory, settings: dict[str, str], *, days: int = 2, limit: int = 5) -> list[dict[str, str]]:
    """Newest mails from the directory (sender or subject mentions it), as [{subject, from, text}]. Blocking."""
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%d-%b-%Y")
    words = search_words(directory)
    found: list[dict[str, str]] = []
    with imaplib.IMAP4_SSL(settings["host"], int(settings["port"])) as box:
        box.login(settings["user"], settings["password"])
        box.select("INBOX", readonly=True)
        ids: list[bytes] = []
        for word in words:
            for field in ("FROM", "SUBJECT"):
                status, data = box.search(None, "SINCE", since, field, f'"{word}"')
                if status == "OK" and data and data[0]:
                    ids += data[0].split()
        for uid in sorted(set(ids), key=lambda b: int(b), reverse=True)[:limit]:
            status, data = box.fetch(uid, "(RFC822)")
            if status != "OK" or not data or not isinstance(data[0], tuple):
                continue
            msg = email.message_from_bytes(data[0][1], policy=email.policy.default)
            found.append({"subject": str(msg.get("Subject", "")), "from": str(msg.get("From", "")), "text": _message_text(msg)[:20000]})
    return found


async def read_verification_imap(sctx: StoreContext, directory: Directory, *, subject_id: str) -> dict[str, Any]:
    settings = imap_settings()
    try:
        mails = await asyncio.to_thread(fetch_imap_mail, directory, settings)
    except Exception as exc:
        logger.warning("IMAP read failed for %s: %s", directory.id, exc, extra={"store_id": sctx.store_id})
        return {"code": "", "link": "", "note": f"Could not read the mailbox ({type(exc).__name__})."}
    for m in mails:
        snap = Snapshot(text=m["subject"] + "\n" + m["text"])
        found = extract(snap, directory)
        if not (found["code"] or found["link"]):
            found = await _ask_model(sctx, snap, directory, subject_id=subject_id)
        if found["code"] or found["link"]:
            return {**found, "note": f"read from {settings['user']}: {m['subject'][:80]}"}
    return {"code": "", "link": "", "note": f"No mail from {directory.name} in {settings['user']} in the last two days."}


async def read_verification(sctx: StoreContext, relay: Relay, directory: Directory, *, subject_id: str) -> dict[str, Any]:
    """The business mailbox over IMAP when configured, else the Gmail search in Chrome. Returns {code, link, note}."""
    if imap_settings():
        return await read_verification_imap(sctx, directory, subject_id=subject_id)
    return await read_verification_gmail(sctx, relay, directory, subject_id=subject_id)


async def read_verification_gmail(sctx: StoreContext, relay: Relay, directory: Directory, *, subject_id: str) -> dict[str, Any]:
    """Open the Gmail search for the directory's mail, open the newest one, return {code, link, note}."""
    tab = await relay.open(gmail_search_url(directory))
    try:
        await relay.wait(tab, ms=4000)
        snapshot = await relay.snapshot(tab, interactive=False, limit=400)
        if "mail.google.com" not in (snapshot.url or "") and "accounts.google" in (snapshot.url or ""):
            return {"code": "", "link": "", "note": "Gmail is not signed in in Chrome."}
        found = extract(snapshot, directory)
        if not (found["code"] or found["link"]):
            # Open the first matching conversation (rows carry the sender and subject in their name).
            wanted = [directory.name.lower(), *(h.lower() for h in directory.hosts)]
            for ref, meta in snapshot.refs.items():
                name = str(meta.get("name") or "").lower()
                if str(meta.get("role") or "").lower() in {"row", "link", "listitem"} and any(w in name for w in wanted):
                    await relay.click(tab, ref)
                    await relay.wait(tab, ms=2500)
                    snapshot = await relay.snapshot(tab, interactive=False, limit=600)
                    found = extract(snapshot, directory)
                    break
        if not (found["code"] or found["link"]):
            found = await _ask_model(sctx, snapshot, directory, subject_id=subject_id)
        if found["code"] or found["link"]:
            return {**found, "note": "read from Gmail"}
        return {"code": "", "link": "", "note": f"No mail from {directory.name} in the last two days."}
    finally:
        await relay.close(tab)


async def _ask_model(sctx: StoreContext, snapshot: Snapshot, directory: Directory, *, subject_id: str) -> dict[str, str]:
    text = snapshot.text[:12000]
    prompt = (
        f"Below is the text of a Gmail page. Find the verification code or the verification/confirmation link that "
        f"{directory.name} ({', '.join(directory.hosts)}) sent. Return JSON {{\"code\": \"...\", \"link\": \"...\"}}; "
        "leave both empty if there is none.\n\n" + text
    )
    try:
        found, _ = await get_router(sctx).structured(
            "listings.mail", LLMRequest(system="You read a mailbox page and extract one code or link.", prompt=prompt,
                                        json_mode=True, temperature=0, max_tokens=200),
            _Found, ctx=sctx.call_context(subject_id), decision_type="listings.mail", subject_id=subject_id,
        )
    except Exception as exc:
        logger.info("Mail reading without the model: %s", exc, extra={"store_id": sctx.store_id})
        return {"code": "", "link": ""}
    link = found.link.strip()
    if link and not any(h in link for h in directory.hosts):
        link = ""
    return {"code": re.sub(r"\D", "", found.code)[:8], "link": link}
