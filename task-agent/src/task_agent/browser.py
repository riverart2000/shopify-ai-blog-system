"""The one shared browser every agent uses: Google Chrome with a single saved profile (`profiles/shared`), so a
login done once — by Joe in a login tab, or by an agent — works for every later task.

Chrome runs hidden while agents work alone and is shown on the Mac while Joe has a login tab open. Switching
between the two restarts Chrome on the same profile (logins are kept); an agent's tab is reopened at its last
address. When the last tab is released, Chrome quits.

Each user of the browser is an "owner": `job:<id>` for an agent, `login:<site>` for a tab waiting for Joe.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

CHROME_APP = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
HIDDEN_VIEWPORT = {"width": 1280, "height": 900}
LAUNCH_ARGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-blink-features=AutomationControlled",
]


def chrome_user_agent() -> str | None:
    """The normal Chrome user agent for the installed version (hidden Chrome would say "HeadlessChrome")."""
    if not CHROME_APP.exists():
        return None
    try:
        out = subprocess.run([str(CHROME_APP), "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)\.\d+", out)
    if not match:
        return None
    return (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{match.group(1)}.0.0.0 Safari/537.36"
    )


class BrowserHub:
    def __init__(self, profile_dir: Path, *, channel: str | None = None) -> None:
        self.profile_dir = profile_dir
        self.channel = channel if channel is not None else ("chrome" if CHROME_APP.exists() else "")
        self.on_login_closed: Callable[[str], None] | None = None  # called with the site when Joe closes its tab
        self._pw: Any = None
        self._context: Any = None
        self._visible = False
        self._lock = asyncio.Lock()
        self._pages: dict[str, Any] = {}  # owner -> its current tab
        self._spare: dict[str, list[Any]] = {}  # tabs an owner opened earlier (e.g. before a popup took over)
        self._visited: dict[str, list[str]] = {}  # owner -> addresses its tabs reached
        self._reopened: set[str] = set()  # owners whose tab was reopened after a restart (told to the agent once)
        self._blank: list[Any] = []  # the empty tab Chrome opens with, reused for the first owner
        self._user_agent = chrome_user_agent()

    # ── status ─────────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._context is not None

    @property
    def visible(self) -> bool:
        return self.running and self._visible

    def has_tab(self, owner: str) -> bool:
        page = self._pages.get(owner)
        return bool(page and not page.is_closed())

    def login_sites(self) -> list[str]:
        return [o.split(":", 1)[1] for o in self._pages if o.startswith("login:") and self.has_tab(o)]

    def job_owners(self) -> list[str]:
        return [o for o in self._pages if o.startswith("job:") and self.has_tab(o)]

    def status(self) -> dict[str, Any]:
        jobs = self.job_owners()
        return {
            "running": self.running,
            "visible": self.visible,
            "open_tabs": sum(1 for o in self._pages if self.has_tab(o)),
            "login_tabs": self.login_sites(),
            "profile_saved": self.profile_dir.is_dir() and any(self.profile_dir.iterdir()),
            "busy_job_id": int(jobs[0].split(":", 1)[1]) if jobs else None,
        }

    def visited(self, owner: str) -> list[str]:
        return list(self._visited.get(owner, []))

    def take_reopened(self, owner: str) -> bool:
        """True once after the owner's tab was reopened by a browser restart (its typing may be lost)."""
        if owner in self._reopened:
            self._reopened.discard(owner)
            return True
        return False

    # ── tabs ───────────────────────────────────────────────────────────────

    async def page(self, owner: str) -> Any:
        """The owner's tab, opened if needed (Chrome is started hidden if it isn't running)."""
        async with self._lock:
            page = self._pages.get(owner)
            if page is not None and not page.is_closed() and self._context is not None:
                return page
            await self._ensure(visible=self._visible if self._context else False)
            return await self._new_tab(owner)

    async def show(self, owner: str, url: str) -> Any:
        """Open `url` in a visible tab on the Mac for Joe (restarting Chrome visibly if it was hidden)."""
        async with self._lock:
            await self._ensure(visible=True)
            page = self._pages.get(owner)
            if page is None or page.is_closed():
                page = await self._new_tab(owner)
            with contextlib.suppress(Exception):
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            with contextlib.suppress(Exception):
                await page.bring_to_front()
            return page

    async def hand_over(self, src: str, dst: str, url: str) -> tuple[Any, bool]:
        """Give an agent's tab to Joe. If Chrome is already on screen the tab moves as it is (what was typed is
        kept). Otherwise Chrome restarts on screen and the page is opened again. Returns (tab, kept as it was)."""
        async with self._lock:
            page = self._pages.get(src)
            if self.visible and page is not None and not page.is_closed():
                self._pages.pop(src, None)
                self._spare.pop(src, None)
                self._pages[dst] = page
                page.on("close", lambda _p: self._on_page_closed(dst, page))
                with contextlib.suppress(Exception):
                    await page.bring_to_front()
                return page, True
        await self.release(src)
        return await self.show(dst, url), False

    async def release(self, owner: str) -> None:
        """Close the owner's tabs. Chrome quits when no tabs are left (and goes back to hidden next time)."""
        async with self._lock:
            pages = [self._pages.pop(owner, None), *self._spare.pop(owner, [])]
            self._visited.pop(owner, None)
            self._reopened.discard(owner)
            for page in pages:
                if page is not None and not page.is_closed():
                    with contextlib.suppress(Exception):
                        await page.close()
            if self._context is not None and not any(self.has_tab(o) for o in self._pages):
                await self._shutdown()

    async def close(self) -> None:
        async with self._lock:
            await self._shutdown()
            if self._pw is not None:
                with contextlib.suppress(Exception):
                    await self._pw.stop()
                self._pw = None

    # ── internals ──────────────────────────────────────────────────────────

    async def _ensure(self, *, visible: bool) -> None:
        if self._context is not None and (self._visible == visible or (self._visible and not visible)):
            return  # already right (a visible browser is fine for agents too)
        addresses = {o: p.url for o, p in self._pages.items() if not p.is_closed() and p.url.startswith("http")}
        if self._context is not None:
            await self._shutdown(keep_owners=True)
        await self._launch(visible)
        for owner, url in addresses.items():
            page = await self._new_tab(owner)
            with contextlib.suppress(Exception):
                await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            if owner.startswith("job:"):
                self._reopened.add(owner)

    async def _launch(self, visible: bool) -> None:
        from playwright.async_api import async_playwright

        if self._pw is None:
            self._pw = await async_playwright().start()
        self.profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        options: dict[str, Any] = {
            "headless": not visible,
            "args": LAUNCH_ARGS,
            "ignore_default_args": ["--enable-automation"],
            "locale": "en-GB",
            "timezone_id": "Europe/London",
        }
        if self.channel:
            options["channel"] = self.channel
        if visible:
            options["no_viewport"] = True
        else:
            options["viewport"] = HIDDEN_VIEWPORT
            if self._user_agent:
                options["user_agent"] = self._user_agent
        self._context = await self._pw.chromium.launch_persistent_context(str(self.profile_dir), **options)
        self._visible = visible
        context = self._context
        context.on("close", lambda _c: self._on_context_closed(context))
        context.on("page", self._on_new_page)
        # A persistent profile opens with one blank tab; reuse it for the first owner.
        self._blank = list(context.pages)
        log.info("browser.started", visible=visible, channel=self.channel or "chromium")

    async def _new_tab(self, owner: str) -> Any:
        page = None
        while self._blank:
            candidate = self._blank.pop()
            if not candidate.is_closed():
                page = candidate
                break
        if page is None:
            page = await self._context.new_page()
        self._adopt(owner, page)
        return page

    def _adopt(self, owner: str, page: Any) -> None:
        old = self._pages.get(owner)
        if old is not None and old is not page and not old.is_closed():
            self._spare.setdefault(owner, []).append(old)
        self._pages[owner] = page
        visited = self._visited.setdefault(owner, [])

        def navigated(frame: Any) -> None:
            if frame == page.main_frame and frame.url.startswith("http") and (not visited or visited[-1] != frame.url):
                visited.append(frame.url)
                del visited[:-200]

        page.on("framenavigated", navigated)
        page.on("close", lambda _p: self._on_page_closed(owner, page))
        if owner.startswith("job:"):
            # Agents can't see pop-up dialogs; accept them so pages don't hang ("Leave this page?", alerts).
            page.on("dialog", lambda dialog: asyncio.ensure_future(_accept(dialog)))

    def _on_new_page(self, page: Any) -> None:
        asyncio.ensure_future(self._follow_popup(page))

    async def _follow_popup(self, page: Any) -> None:
        """A link or button in an agent's tab opened a new tab: the agent carries on in the new one."""
        with contextlib.suppress(Exception):
            opener = await page.opener()
            for owner, current in list(self._pages.items()):
                if current is opener and owner.startswith("job:"):
                    self._adopt(owner, page)
                    return

    def _on_page_closed(self, owner: str, page: Any) -> None:
        if self._pages.get(owner) is not page:
            return
        self._pages.pop(owner, None)
        if owner.startswith("login:") and self.on_login_closed:
            with contextlib.suppress(Exception):
                self.on_login_closed(owner.split(":", 1)[1])

    def _on_context_closed(self, context: Any) -> None:
        if self._context is not context:
            return  # an old browser we closed on purpose
        # Joe closed the whole browser window: every tab is gone.
        self._context = None
        for owner in [o for o in self._pages if o.startswith("login:")]:
            self._pages.pop(owner, None)
            if self.on_login_closed:
                with contextlib.suppress(Exception):
                    self.on_login_closed(owner.split(":", 1)[1])
        log.info("browser.closed_by_user")

    async def _shutdown(self, *, keep_owners: bool = False) -> None:
        context, self._context = self._context, None
        if not keep_owners:
            self._pages.clear()
            self._spare.clear()
        if context is not None:
            with contextlib.suppress(Exception):
                await context.close()
            log.info("browser.stopped")


async def _accept(dialog: Any) -> None:
    with contextlib.suppress(Exception):
        await dialog.accept()
