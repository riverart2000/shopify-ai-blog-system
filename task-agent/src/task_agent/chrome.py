"""Agents work in Joe's everyday Google Chrome, where he is already logged in everywhere, through the OpenClaw
browser relay (the OpenClaw extension in that Chrome).

- Each agent opens its own tabs and closes only those. Joe's own tabs, and Chrome itself, are never touched.
- Every call is one `openclaw browser --browser-profile chrome --json …` command, about 4 seconds each.
- Text that is typed (passwords included) goes in on stdin, never on the command line, so it can't be seen in
  the Mac's process list.
- If Chrome or the extension isn't connected, the hub says so (`offline`) and the browser tools hand the task to
  Joe instead of failing.

`RelayPage` and `RelayLocator` copy the small part of Playwright's Page/Locator API that the browser tools use, so
the tools behave the same here as on the separate agent browser (task_agent.browser).
"""

# The `timeout` parameters copy Playwright's API (and set the relay command's time limit).
# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shutil
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

Runner = Callable[[list[str], str | None, float], Awaitable[tuple[int, str, str]]]
UPLOAD_DIR = Path("/tmp/openclaw/uploads")  # the only folder OpenClaw will upload files from
SNAPSHOT_NODES = "3000"
# The dialog on top of the page, as a CSS selector that matches only it ("" when there's none). Sites draw dialogs
# last in the page, so on a page too long to read in full the dialog is the part that gets cut off.
TOP_DIALOG = """() => {
  const seen = (d) => { const r = d.getBoundingClientRect(), s = getComputedStyle(d);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  let found = [...document.querySelectorAll('[aria-modal="true"]')];
  try { found.push(...document.querySelectorAll('dialog:modal')); } catch (e) {}
  found = found.filter(seen).reverse();
  if (!found.length) return '';
  const inside = (v, max) => Math.min(Math.max(v, 0), max - 1);
  const onTop = (d) => { const r = d.getBoundingClientRect();
    const x = inside(r.left + r.width / 2, innerWidth), y = inside(r.top + r.height / 2, innerHeight);
    const top = document.elementFromPoint(x, y);
    return !!top && d.contains(top); };
  const el = found.find(onTop) || found[0];
  const only = (sel) => { try { const m = document.querySelectorAll(sel); return m.length === 1 && m[0] === el; }
    catch (e) { return false; } };
  const quote = (v) => '"' + v.replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"') + '"';
  const tries = el.id ? ['#' + CSS.escape(el.id)] : [];
  for (const name of ['aria-labelledby', 'aria-label', 'data-testid']) {
    const value = el.getAttribute(name);
    if (value) tries.push(el.tagName.toLowerCase() + '[' + name + '=' + quote(value) + ']');
  }
  tries.push('[aria-modal="true"]', 'dialog[open]');
  for (const sel of tries) if (only(sel)) return sel;
  const path = [];
  for (let n = el; n.parentElement; n = n.parentElement)
    path.unshift(n.tagName.toLowerCase() + ':nth-child(' + ([...n.parentElement.children].indexOf(n) + 1) + ')');
  return 'html > ' + path.join(' > ');
}"""
_TAB_GONE = re.compile(r"tab.{0,40}(not found|closed|no longer|unknown|revoked)|no such tab|target closed", re.I)


class RelayError(Exception):
    """OpenClaw refused or failed an action. The message is OpenClaw's own and safe to show the model."""


def find_node() -> str:
    """The Node.js that runs OpenClaw. The background service has a short PATH, so look in nvm too."""
    found = shutil.which("node")
    if found:
        return found

    def version(path: Path) -> tuple[int, ...]:
        return tuple(int(n) for n in re.findall(r"\d+", path.parts[-3]))

    candidates = sorted(Path.home().glob(".nvm/versions/node/v*/bin/node"), key=version)
    return str(candidates[-1]) if candidates else "node"


async def _run(cmd: list[str], stdin: str | None, timeout: float, cwd: Path) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin.encode() if stdin is not None else None), timeout)
    except (TimeoutError, asyncio.CancelledError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


class Relay:
    """Runs `openclaw browser` commands against one OpenClaw browser profile (default: `chrome`, the extension)."""

    def __init__(
        self, openclaw_dir: Path, *, profile: str = "chrome", node: str = "", runner: Runner | None = None
    ) -> None:
        self.openclaw_dir = openclaw_dir
        self.profile = profile
        self.node = node or find_node()
        self._runner = runner
        self._lock = asyncio.Lock()

    async def call(self, *args: str, stdin: str | None = None, timeout: float = 60) -> dict[str, Any]:
        cmd = [
            self.node, str(self.openclaw_dir / "scripts" / "openclaw.mjs"),
            "browser", "--browser-profile", self.profile, "--json", *args,
        ]  # fmt: skip
        async with self._lock:
            try:
                if self._runner is not None:
                    code, out, err = await self._runner(cmd, stdin, timeout)
                else:
                    code, out, err = await _run(cmd, stdin, timeout, self.openclaw_dir)
            except TimeoutError as exc:
                raise RelayError(f"Chrome didn't answer within {int(timeout)} seconds.") from exc
            except OSError as exc:
                raise RelayError(f"OpenClaw couldn't be started ({exc.strerror or exc}).") from exc
        data = _parse(out)
        failed = [r for r in data.get("results") or [] if isinstance(r, dict) and r.get("ok") is False]
        if code != 0 or data.get("ok") is False or ("error" in data and not data.get("ok")) or failed:
            raise RelayError(_error_text(data, failed, out, err, code))
        return data


def _parse(text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        start = text.find("{")  # a banner line before the JSON
        try:
            value = json.loads(text[start:]) if start >= 0 else {}
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {"result": value}


def _error_text(data: dict[str, Any], failed: list[dict[str, Any]], out: str, err: str, code: int) -> str:
    """OpenClaw's own words for what went wrong: its error, or the first failed action of a batch (typing is one)."""
    error = data.get("error")
    if isinstance(error, dict):
        error = error.get("message") or json.dumps(error)
    if not error and failed:
        error = failed[0].get("error")
    text = str(error or _last_line(err) or _last_line(out) or f"exit code {code}")
    # Playwright adds its call log after the message; the first part says it all.
    return text.split("\nCall log", 1)[0].split(" | locator.", 1)[0].strip()[:300]


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _tab_id(data: dict[str, Any]) -> str:
    return str(data.get("suggestedTargetId") or data.get("tabId") or data.get("targetId") or "")


# ── page and locator (the Playwright subset the tools use) ──────────────────────


class RelayPage:
    is_relay = True

    def __init__(self, hub: ChromeHub, owner: str) -> None:
        self._hub = hub
        self.owner = owner
        self.tab: str | None = None  # opened on the first goto
        self.url = "about:blank"
        self._title = ""
        self._closed = False
        self.refs: dict[str, Any] | None = None  # refs in the last snapshot
        self.dialog_only = False  # the last snapshot shows only the dialog that's open on top of the page
        self.keyboard = _Keyboard(self)
        self.mouse = _Mouse(self)

    def is_closed(self) -> bool:
        return self._closed

    @property
    def opened(self) -> bool:
        return self.tab is not None and not self._closed

    async def call(self, *args: str, stdin: str | None = None, timeout: float = 60) -> dict[str, Any]:
        """A command on this tab."""
        if self._closed:
            raise RelayError("This tab was closed.")
        if self.tab is None:
            raise RelayError("No page is open yet. Use open_page first.")
        data = await self._hub._call(*args, "--target-id", self.tab, stdin=stdin, timeout=timeout, page=self)
        self._seen(data)
        return data

    def _seen(self, data: dict[str, Any]) -> None:
        url = data.get("url")
        if isinstance(url, str) and url and url != self.url:
            self.url = url
            self._title = ""
            self.refs = None
        if isinstance(url, str) and url.startswith("http"):
            self._hub._visit(self.owner, url)

    def _mark_closed(self) -> None:
        self._closed = True

    async def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: float = 45_000) -> None:
        seconds = max(30.0, timeout / 1000 + 15)
        if self.tab is None or self._closed:
            data = await self._hub._call("open", url, timeout=seconds)
            self.tab = _tab_id(data)
            if not self.tab:
                raise RelayError("Chrome opened a tab but didn't say which one.")
            self._closed = False
            self._hub._known(self.tab)
            self._seen(data)
            self._title = str(data.get("title") or "")
        else:
            await self.call("navigate", url, timeout=seconds)
        self.refs = None

    async def title(self) -> str:
        return self._title

    async def wait_for_load_state(self, state: str = "load", timeout: float = 0) -> None:
        return None  # open, navigate and click already wait for the page

    async def wait_for_timeout(self, ms: float) -> None:
        await asyncio.sleep(ms / 1000)

    async def aria_snapshot(self, mode: str = "ai", timeout: float = 15_000) -> str:
        if self.tab is None:
            return "- text: [No page is open yet. Use open_page.]"
        seconds = max(30.0, timeout / 1000 + 20)
        data = await self.call("snapshot", "--limit", SNAPSHOT_NODES, timeout=seconds)
        self.dialog_only = False
        if data.get("truncated"):
            data = await self._dialog_on_top(seconds) or data
        self.refs = data.get("refs") if isinstance(data.get("refs"), dict) else {}
        return str(data.get("snapshot") or "")

    async def _dialog_on_top(self, seconds: float) -> dict[str, Any] | None:
        """The page was too long to read in full. If a dialog is open on top of it, read just the dialog: it's
        the only part that can be used, and the part most likely to have been cut off."""
        try:
            selector = await self.evaluate(TOP_DIALOG)
            if not isinstance(selector, str) or not selector:
                return None
            data = await self.call("snapshot", "--selector", selector, "--limit", SNAPSHOT_NODES, timeout=seconds)
        except RelayError:
            return None
        if not data.get("snapshot"):
            return None
        self.dialog_only = True
        return data

    async def evaluate(self, js: str) -> Any:
        return (await self.call("evaluate", "--fn", js)).get("result")

    def locator(self, selector: str) -> RelayLocator:
        if not selector.startswith("aria-ref="):
            raise RelayError("In Chrome, elements are found by their ref from the page (e.g. e12).")
        return RelayLocator(self, selector.split("=", 1)[1])

    async def go_back(self, wait_until: str = "domcontentloaded", timeout: float = 20_000) -> None:
        await self.call("evaluate", "--fn", "history.back(); return true")
        await asyncio.sleep(1.5)

    async def bring_to_front(self) -> None:
        if self.opened:
            await self._hub._call("focus", str(self.tab))

    async def close(self) -> None:
        if self.opened:
            try:
                await self._hub._call("close", str(self.tab))
            finally:
                self._closed = True

    async def act(self, actions: list[dict[str, Any]]) -> dict[str, Any]:
        """Run act requests in one batch, sent on stdin (so typed text never appears on a command line)."""
        return await self.call("batch", "--actions-file", "-", stdin=json.dumps(actions))


class _Keyboard:
    def __init__(self, page: RelayPage) -> None:
        self.page = page

    async def press(self, key: str) -> None:
        await self.page.act([{"kind": "press", "key": key}])

    async def type(self, text: str, delay: float = 0) -> None:
        raise RelayError("Type into a box by its ref instead.")


class _Mouse:
    def __init__(self, page: RelayPage) -> None:
        self.page = page

    async def wheel(self, dx: float, dy: float) -> None:
        await self.page.call("evaluate", "--fn", f"window.scrollBy({int(dx)}, {int(dy)}); return true")


class RelayLocator:
    def __init__(self, page: RelayPage, ref: str) -> None:
        self.page = page
        self.ref = ref

    async def count(self) -> int:
        refs = self.page.refs
        return 0 if refs is not None and refs and self.ref not in refs else 1

    async def click(self, timeout: float = 10_000) -> None:
        await self.page._hub._click(self.page, self.ref)

    async def fill(self, text: str, timeout: float = 10_000) -> None:
        await self.page.act([{"kind": "type", "ref": self.ref, "text": text}])

    async def press_sequentially(self, text: str, delay: float = 40, timeout: float = 60_000) -> None:
        try:
            await self.page.act([{"kind": "type", "ref": self.ref, "text": text, "slowly": True}])
        except RelayError:
            await self.fill(text)

    async def press(self, key: str) -> None:
        await self.page.act([{"kind": "press", "key": key}])

    async def evaluate(self, js: str) -> Any:
        return (await self.page.call("evaluate", "--ref", self.ref, "--fn", js)).get("result")

    async def select_option(
        self, value: str | None = None, *, label: str | None = None, timeout: float = 5_000
    ) -> None:
        wanted = label if label is not None else value
        if wanted is None:
            raise RelayError("Say which option to choose.")
        found = await self.evaluate(
            "(el) => { const want = " + json.dumps(wanted.strip().lower()) + "; "
            "const opts = [...(el.options || [])]; "
            "const o = opts.find(o => o.label.trim().toLowerCase() === want || o.text.trim().toLowerCase() === want)"
            " || opts.find(o => o.value === " + json.dumps(wanted) + "); return o ? o.value : null; }"
        )
        if found is None:
            raise RelayError(f"There's no option '{wanted}' in that list.")
        await self.page.call("select", self.ref, str(found))

    async def set_checked(self, checked: bool, timeout: float = 10_000) -> None:
        state = await self.evaluate(
            "(el) => (typeof el.checked === 'boolean') ? el.checked : el.getAttribute('aria-checked') === 'true'"
        )
        if bool(state) != checked:
            await self.click()

    async def upload_file(self, path: str, *, file_input: bool) -> None:
        """Upload a file: straight into a file box, or by clicking the button that opens the file picker."""
        source = Path(path)
        staged = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}-{source.name}"
        await asyncio.to_thread(_stage, source, staged)
        try:
            flag = "--input-ref" if file_input else "--ref"
            await self.page.call("upload", str(staged), flag, self.ref, timeout=60)
        finally:
            await asyncio.sleep(2)  # let the page read the file before it goes
            with contextlib.suppress(OSError):
                await asyncio.to_thread(staged.unlink)


def _stage(source: Path, staged: Path) -> None:
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, staged)


# ── the hub ────────────────────────────────────────────────────────────────────


def make_browser(settings: Any, profile_dir: Path) -> Any:
    """The browser every agent shares, as chosen in settings (`browser`): Joe's Chrome, or the agents' own."""
    if settings.browser == "chrome":
        return ChromeHub(Relay(settings.openclaw_dir, profile=settings.openclaw_browser_profile))
    from task_agent.browser import BrowserHub

    return BrowserHub(profile_dir)


class ChromeHub:
    """Same interface as task_agent.browser.BrowserHub, on Joe's everyday Chrome."""

    mode = "chrome"

    def __init__(self, relay: Relay, *, poll_seconds: float = 20) -> None:
        self.relay = relay
        self.poll_seconds = poll_seconds
        self.on_login_closed: Callable[[str], None] | None = None
        self.offline: str = ""  # why Chrome can't be used right now ("" = fine, or not tried yet)
        self._pages: dict[str, RelayPage] = {}
        self._spare: dict[str, list[RelayPage]] = {}
        self._visited: dict[str, list[str]] = {}
        self._known_tabs: set[str] | None = None  # every tab seen so far, to spot tabs a click opened
        self._lock = asyncio.Lock()
        self._watcher: asyncio.Task[None] | None = None

    # ── status ─────────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return not self.offline

    @property
    def visible(self) -> bool:
        return True

    def has_tab(self, owner: str) -> bool:
        page = self._pages.get(owner)
        return bool(page and page.opened)

    def login_sites(self) -> list[str]:
        return [o.split(":", 1)[1] for o in self._pages if o.startswith("login:") and self.has_tab(o)]

    def job_owners(self) -> list[str]:
        return [o for o in self._pages if o.startswith("job:") and self.has_tab(o)]

    def status(self) -> dict[str, Any]:
        jobs = self.job_owners()
        return {
            "mode": self.mode,
            "running": self.running,
            "visible": True,
            "open_tabs": sum(1 for o in self._pages if self.has_tab(o)),
            "login_tabs": self.login_sites(),
            "profile_saved": True,  # it's Joe's own Chrome: his logins are already there
            "busy_job_id": int(jobs[0].split(":", 1)[1]) if jobs else None,
            "offline": self.offline or None,
        }

    def visited(self, owner: str) -> list[str]:
        return list(self._visited.get(owner, []))

    def take_reopened(self, owner: str) -> bool:
        return False  # tabs are never reopened: Chrome isn't restarted

    async def ensure_connected(self) -> str:
        """Empty when Chrome can be used, else the reason. Only checks again after something failed."""
        if self.offline:
            await self._check_connection("")
        return self.offline

    # ── tabs ───────────────────────────────────────────────────────────────

    async def page(self, owner: str) -> RelayPage:
        """The owner's tab. It opens on its first goto (so nothing appears in Chrome until there's a page)."""
        async with self._lock:
            page = self._pages.get(owner)
            if page is None or page.is_closed():
                page = RelayPage(self, owner)
                self._pages[owner] = page
            return page

    async def show(self, owner: str, url: str) -> RelayPage:
        """Open `url` for Joe in a tab of his Chrome and bring it to the front."""
        page = await self.page(owner)
        await page.goto(url)
        with contextlib.suppress(RelayError):
            await page.bring_to_front()
        if owner.startswith("login:"):
            self._watch()
        return page

    async def hand_over(self, src: str, dst: str, url: str) -> tuple[RelayPage, bool]:
        """Give an agent's tab to Joe as it is (what was typed is kept). Returns (tab, kept as it was)."""
        async with self._lock:
            page = self._pages.get(src)
            if page is not None and page.opened:
                self._pages.pop(src, None)
                self._spare.pop(src, None)
                page.owner = dst
                self._pages[dst] = page
            else:
                page = None
        if page is not None:
            with contextlib.suppress(RelayError):
                await page.bring_to_front()
            if dst.startswith("login:"):
                self._watch()
            return page, True
        await self.release(src)
        return await self.show(dst, url), False

    async def release(self, owner: str) -> None:
        """Close the owner's tabs (only tabs agents opened; Chrome and Joe's tabs stay)."""
        async with self._lock:
            pages = [self._pages.pop(owner, None), *self._spare.pop(owner, [])]
            self._visited.pop(owner, None)
        for page in pages:
            if page is not None and page.opened:
                with contextlib.suppress(RelayError):
                    await page.close()

    async def close(self) -> None:
        """On shutdown: close the agents' tabs; login tabs stay open for Joe."""
        if self._watcher:
            self._watcher.cancel()
            self._watcher = None
        for owner in self.job_owners():
            await self.release(owner)

    # ── internals ──────────────────────────────────────────────────────────

    async def _call(
        self, *args: str, stdin: str | None = None, timeout: float = 60, page: RelayPage | None = None
    ) -> dict[str, Any]:
        try:
            data = await self.relay.call(*args, stdin=stdin, timeout=timeout)
        except RelayError as exc:
            if page is not None and _TAB_GONE.search(str(exc)):
                page._mark_closed()
            await self._check_connection(str(exc))
            raise
        self.offline = ""
        return data

    async def _check_connection(self, error: str) -> None:
        """After a failure: is Chrome itself still connected? Sets `offline` with the reason if not."""
        try:
            status = await self.relay.call("status", timeout=30)
        except RelayError as exc:
            self.offline = f"OpenClaw didn't answer ({exc})"
        else:
            ready = status.get("running") and status.get("cdpReady", True) and status.get("pageReady", True)
            self.offline = "" if ready else "Chrome isn't connected to the OpenClaw extension"
        if self.offline:
            log.warning("chrome.offline", reason=self.offline, error=error)

    def _visit(self, owner: str, url: str) -> None:
        visited = self._visited.setdefault(owner, [])
        if not visited or visited[-1] != url:
            visited.append(url)
            del visited[:-200]

    def _known(self, tab: str) -> None:
        if self._known_tabs is not None:
            self._known_tabs.add(tab)

    async def _tab_ids(self) -> dict[str, str]:
        data = await self._call("tabs")
        return {_tab_id(t): str(t.get("url") or "") for t in data.get("tabs") or [] if isinstance(t, dict)}

    async def _click(self, page: RelayPage, ref: str) -> None:
        """Click; if the click opened a new tab, the agent carries on in it (its old tab is kept as a spare)."""
        # Looked at just before every click: a tab Joe (or anyone else) opened since the last click is his, not
        # this click's pop-up. Without this the agent took over tabs opened while it was working.
        with contextlib.suppress(RelayError):
            self._known_tabs = set(await self._tab_ids())
        before = page.url
        await page.call("click", ref)
        if page.url != before or self._known_tabs is None:
            return  # the click went to a new page in the same tab
        with contextlib.suppress(RelayError):
            tabs = await self._tab_ids()
            new = [t for t in tabs if t and t not in self._known_tabs and tabs[t].startswith("http")]
            self._known_tabs |= set(tabs)
            if new and page.owner.startswith("job:"):
                popup = RelayPage(self, page.owner)
                popup.tab, popup.url = new[-1], tabs[new[-1]]
                async with self._lock:
                    self._spare.setdefault(page.owner, []).append(page)
                    self._pages[page.owner] = popup
                self._visit(page.owner, popup.url)
                log.info("chrome.followed_new_tab", owner=page.owner, url=popup.url)

    def _watch(self) -> None:
        """While login tabs are open, notice when Joe closes one (that's how he says he's done)."""
        if self._watcher is None or self._watcher.done():
            self._watcher = asyncio.create_task(self._watch_logins(), name="chrome-login-watch")

    async def _watch_logins(self) -> None:
        while any(o.startswith("login:") and self.has_tab(o) for o in self._pages):
            await asyncio.sleep(self.poll_seconds)
            try:
                tabs = await self._tab_ids()
            except RelayError:
                continue
            for owner, page in list(self._pages.items()):
                if page.opened and page.tab not in tabs:
                    page._mark_closed()
                    if owner.startswith("login:"):
                        self._pages.pop(owner, None)
                        if self.on_login_closed:
                            with contextlib.suppress(Exception):
                                self.on_login_closed(owner.split(":", 1)[1])
