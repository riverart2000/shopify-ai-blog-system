"""Site logins: a label, login page and username per site, its password in the Keychain. Every site shares the
one saved browser (task_agent.browser), so a login done once — by Joe or by an agent — works for every task.

"Open login window" opens the site's login page as a tab in the shared browser, shown on the Mac. Joe logs in
by hand (2-step codes work); the login is kept in the shared profile. Closing the tab marks it as done.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import structlog

from task_agent.browser import BrowserHub
from task_agent.db import JobStore, iso, now
from task_agent.keychain import delete_secret, get_secret, has_secret, set_secret

log = structlog.get_logger()
_SITE = re.compile(r"[^a-z0-9]+")


class AccountError(ValueError):
    """Shown to the user as-is."""


def secret_name(site: str) -> str:
    return f"site:{site}"


SHARED_PROFILE = "shared"


class AccountStore:
    def __init__(self, store: JobStore, profiles_dir: Path) -> None:
        self.store = store
        self.profiles_dir = profiles_dir

    @property
    def shared_profile(self) -> Path:
        return self.profiles_dir / SHARED_PROFILE

    def has_profile(self, site: str) -> bool:
        """The shared browser has been used and this site's login has worked there at least once."""
        path = self.shared_profile
        with self.store._connect() as db:
            row = db.execute("SELECT last_login_ok_at FROM site_profiles WHERE site = ?", (site,)).fetchone()
        return bool(path.is_dir() and any(path.iterdir()) and row and row["last_login_ok_at"])

    def password(self, site: str) -> str:
        return get_secret(secret_name(site)) or ""

    def find_by_host(self, host: str) -> dict[str, Any] | None:
        host = host.lower().removeprefix("www.")
        for account in self.list():
            login_host = (urlsplit(account["login_url"]).hostname or "").lower().removeprefix("www.")
            if login_host and (
                host == login_host or host.endswith("." + login_host) or login_host.endswith("." + host)
            ):
                return account
        return None

    def list(self, event_type: str | None = None) -> list[dict[str, Any]]:
        """Every login, or (with `event_type`) that type's own logins plus the shared ones."""
        with self.store._connect() as db:
            rows = db.execute("SELECT * FROM site_profiles ORDER BY label COLLATE NOCASE, site").fetchall()
            names = {r["key"]: r["name"] for r in db.execute("SELECT key, name FROM event_types")}
        views = [self._view(dict(r), names) for r in rows]
        if event_type is None:
            return views
        return [a for a in views if a["event_type"] in ("", event_type)]

    def get(self, site: str) -> dict[str, Any]:
        with self.store._connect() as db:
            row = db.execute("SELECT * FROM site_profiles WHERE site = ?", (site,)).fetchone()
            names = {r["key"]: r["name"] for r in db.execute("SELECT key, name FROM event_types")}
        if row is None:
            raise LookupError(f"No saved login for '{site}'.")
        return self._view(dict(row), names)

    def save(
        self,
        *,
        label: str,
        login_url: str,
        username: str,
        password: str = "",
        notes: str = "",
        site: str | None = None,
        event_type: str | None = None,
    ) -> dict[str, Any]:
        """Save a login. `event_type`: the type it belongs to ("" = shared by all; None = keep as it is)."""
        label, login_url, username = label.strip(), login_url.strip(), username.strip()
        if not label:
            raise AccountError("Give the site a name, e.g. Eventbrite.")
        parts = urlsplit(login_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise AccountError("The login page must be a full web address starting with https://")
        stamp = iso(now())
        with self.store._connect() as db:
            if event_type and not db.execute("SELECT 1 FROM event_types WHERE key = ?", (event_type,)).fetchone():
                raise AccountError(f"There's no event type '{event_type}'.")
            if site:
                if not db.execute(
                    """UPDATE site_profiles SET label = ?, login_url = ?, username = ?, notes = ?, updated_at = ?
                       WHERE site = ?""",
                    (label, login_url, username, notes.strip(), stamp, site),
                ).rowcount:
                    raise LookupError(f"No saved login for '{site}'.")
                if event_type is not None:
                    db.execute("UPDATE site_profiles SET event_type = ? WHERE site = ?", (event_type, site))
            else:
                site = self._new_site(db, f"{label} {event_type}" if event_type else label)
                db.execute(
                    """INSERT INTO site_profiles (site, profile_dir, label, login_url, username, notes,
                           created_at, updated_at, event_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (site, str(self.shared_profile), label, login_url, username, notes.strip(), stamp, stamp,
                     event_type or ""),
                )  # fmt: skip
        if password:
            set_secret(secret_name(site), password)
        return self.get(site)

    def delete(self, site: str) -> None:
        """Forget the login and its password. The shared browser keeps its cookies (log out there if needed)."""
        self.get(site)
        delete_secret(secret_name(site))
        with self.store._connect() as db:
            db.execute("DELETE FROM site_profiles WHERE site = ?", (site,))

    def mark_login_saved(self, site: str) -> None:
        with self.store._connect() as db:
            db.execute("UPDATE site_profiles SET last_login_ok_at = ? WHERE site = ?", (iso(now()), site))

    def _view(self, row: dict[str, Any], type_names: dict[str, str] | None = None) -> dict[str, Any]:
        site = row["site"]
        event_type = row.get("event_type") or ""
        return {
            "event_type": event_type,
            "event_type_name": (type_names or {}).get(event_type, "") if event_type else "",
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
    """Login tabs in the shared browser, shown on the Mac. At most one per site; each closes itself after a while."""

    def __init__(self, accounts: AccountStore, browser: BrowserHub, minutes: int = 30) -> None:
        self.accounts = accounts
        self.browser = browser
        self.minutes = minutes
        self._timers: dict[str, asyncio.Task[None]] = {}
        browser.on_login_closed = self._closed

    def is_open(self, site: str) -> bool:
        return self.browser.has_tab(f"login:{site}")

    def open(self, site: str) -> None:
        account = self.accounts.get(site)
        if self.is_open(site):
            raise AccountError(f"A login tab for {account['label']} is already open on the Mac.")
        self._timers[site] = asyncio.create_task(self._run(account), name=f"login-window:{site}")

    async def show(self, owner_site: str, url: str, minutes: int) -> None:
        """Open any page for Joe (used when a task needs him), closed again after `minutes`."""
        await self.browser.show(f"login:{owner_site}", url)
        self._close_later(owner_site, minutes)

    async def close(self, site: str) -> None:
        timer = self._timers.pop(site, None)
        if timer:
            timer.cancel()
        await self.browser.release(f"login:{site}")

    async def close_all(self) -> None:
        for task in list(self._timers.values()):
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        self._timers.clear()

    def _close_later(self, site: str, minutes: int) -> None:
        old = self._timers.pop(site, None)
        if old and old is not asyncio.current_task():
            old.cancel()

        async def later() -> None:
            await asyncio.sleep(minutes * 60)
            log.info("login_window.timeout", site=site)
            await self.browser.release(f"login:{site}")

        self._timers[site] = asyncio.create_task(later(), name=f"login-timeout:{site}")

    def _closed(self, site: str) -> None:
        timer = self._timers.pop(site, None)
        if timer and timer is not asyncio.current_task():
            timer.cancel()
        with suppress(LookupError):
            self.accounts.get(site)
            self.accounts.mark_login_saved(site)
            log.info("login_window.saved", site=site)

    async def _run(self, account: dict[str, Any]) -> None:
        site = account["site"]
        log.info("login_window.open", site=site)
        try:
            page = await self.browser.show(f"login:{site}", account["login_url"])
            await prefill(page, account["username"], self.accounts.password(site))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("login_window.failed", site=site, error=f"{type(exc).__name__}: {exc}")
            return
        self._close_later(site, self.minutes)


async def prefill(page: Any, username: str, password: str) -> None:
    """Best effort: type the saved username and password into the login form. Never submits it."""
    if getattr(page, "is_relay", False):
        return  # Joe's own Chrome: its password manager fills logins in
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
