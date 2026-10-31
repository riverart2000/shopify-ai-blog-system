"""Site logins: a label, login page and username per site, its password in the Keychain, and a saved
browser profile (cookies), so the agent can work on the site as Joe without logging in every time.

"Open login window" shows a real browser on the Mac with that site's profile. Joe logs in by hand
(2-step codes work); closing the window saves the login.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import structlog

from task_agent.db import JobStore, iso, now
from task_agent.keychain import delete_secret, get_secret, has_secret, set_secret

log = structlog.get_logger()
_SITE = re.compile(r"[^a-z0-9]+")


class AccountError(ValueError):
    """Shown to the user as-is."""


def secret_name(site: str) -> str:
    return f"site:{site}"


class AccountStore:
    def __init__(self, store: JobStore, profiles_dir: Path) -> None:
        self.store = store
        self.profiles_dir = profiles_dir

    def profile_dir(self, site: str) -> Path:
        path = (self.profiles_dir / site).resolve()
        if path.parent != self.profiles_dir.resolve():
            raise AccountError("Bad site name.")
        return path

    def has_profile(self, site: str) -> bool:
        path = self.profile_dir(site)
        return path.is_dir() and any(path.iterdir())

    def list(self) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            rows = db.execute("SELECT * FROM site_profiles ORDER BY label COLLATE NOCASE, site").fetchall()
        return [self._view(dict(r)) for r in rows]

    def get(self, site: str) -> dict[str, Any]:
        with self.store._connect() as db:
            row = db.execute("SELECT * FROM site_profiles WHERE site = ?", (site,)).fetchone()
        if row is None:
            raise LookupError(f"No saved login for '{site}'.")
        return self._view(dict(row))

    def save(
        self,
        *,
        label: str,
        login_url: str,
        username: str,
        password: str = "",
        notes: str = "",
        site: str | None = None,
    ) -> dict[str, Any]:
        label, login_url, username = label.strip(), login_url.strip(), username.strip()
        if not label:
            raise AccountError("Give the site a name, e.g. Eventbrite.")
        parts = urlsplit(login_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise AccountError("The login page must be a full web address starting with https://")
        stamp = iso(now())
        with self.store._connect() as db:
            if site:
                if not db.execute(
                    """UPDATE site_profiles SET label = ?, login_url = ?, username = ?, notes = ?, updated_at = ?
                       WHERE site = ?""",
                    (label, login_url, username, notes.strip(), stamp, site),
                ).rowcount:
                    raise LookupError(f"No saved login for '{site}'.")
            else:
                site = self._new_site(db, label)
                db.execute(
                    """INSERT INTO site_profiles (site, profile_dir, label, login_url, username, notes,
                           created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (site, str(self.profile_dir(site)), label, login_url, username, notes.strip(), stamp, stamp),
                )
        if password:
            set_secret(secret_name(site), password)
        return self.get(site)

    def delete(self, site: str) -> None:
        self.get(site)
        delete_secret(secret_name(site))
        path = self.profile_dir(site)
        if path.exists():
            shutil.rmtree(path)
        with self.store._connect() as db:
            db.execute("DELETE FROM site_profiles WHERE site = ?", (site,))

    def mark_login_saved(self, site: str) -> None:
        with self.store._connect() as db:
            db.execute("UPDATE site_profiles SET last_login_ok_at = ? WHERE site = ?", (iso(now()), site))

    def _view(self, row: dict[str, Any]) -> dict[str, Any]:
        site = row["site"]
        return {
            "site": site,
            "label": row.get("label") or site,
            "login_url": row.get("login_url") or "",
            "username": row.get("username") or "",
            "has_password": has_secret(secret_name(site)),
            "notes": row.get("notes") or "",
            "has_profile": self.has_profile(site),
            "last_login_ok_at": row.get("last_login_ok_at"),
        }

    @staticmethod
    def _new_site(db: Any, label: str) -> str:
        base = _SITE.sub("-", label.lower()).strip("-")[:40] or "site"
        site, n = base, 2
        while db.execute("SELECT 1 FROM site_profiles WHERE site = ?", (site,)).fetchone():
            site, n = f"{base}-{n}", n + 1
        return site


class LoginWindows:
    """At most one visible login window per site, each in its own saved browser profile."""

    def __init__(self, accounts: AccountStore, minutes: int = 30) -> None:
        self.accounts = accounts
        self.minutes = minutes
        self._open: dict[str, asyncio.Task[None]] = {}

    def is_open(self, site: str) -> bool:
        task = self._open.get(site)
        return bool(task and not task.done())

    def open(self, site: str) -> None:
        account = self.accounts.get(site)
        if self.is_open(site):
            raise AccountError(f"A login window for {account['label']} is already open on the Mac.")
        self._open[site] = asyncio.create_task(self._run(account), name=f"login-window:{site}")

    async def close_all(self) -> None:
        for task in list(self._open.values()):
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task

    async def _run(self, account: dict[str, Any]) -> None:
        from playwright.async_api import async_playwright

        site = account["site"]
        profile = self.accounts.profile_dir(site)
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        log.info("login_window.open", site=site)
        try:
            async with async_playwright() as pw:
                context = await pw.chromium.launch_persistent_context(
                    str(profile), headless=False, viewport=None, args=["--no-first-run", "--no-default-browser-check"]
                )
                closed = asyncio.Event()
                context.on("close", lambda _ctx: closed.set())
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(account["login_url"], wait_until="domcontentloaded", timeout=60_000)
                await _prefill(page, account["username"], get_secret(secret_name(site)) or "")
                try:
                    await asyncio.wait_for(closed.wait(), timeout=self.minutes * 60)
                except TimeoutError:
                    log.info("login_window.timeout", site=site)
                    await context.close()
            self.accounts.mark_login_saved(site)
            log.info("login_window.saved", site=site)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("login_window.failed", site=site, error=f"{type(exc).__name__}: {exc}")


async def _prefill(page: Any, username: str, password: str) -> None:
    """Best effort: type the saved username and password into the login form. Never submits it."""
    with suppress(Exception):
        if username:
            box = page.locator(
                "input[type=email]:visible, input[autocomplete=username]:visible, input[name*=email i]:visible, "
                "input[name*=user i]:visible, input[id*=email i]:visible"
            ).first
            if await box.count():
                await box.fill(username, timeout=3_000)
    with suppress(Exception):
        if password:
            box = page.locator("input[type=password]:visible").first
            if await box.count():
                await box.fill(password, timeout=3_000)
