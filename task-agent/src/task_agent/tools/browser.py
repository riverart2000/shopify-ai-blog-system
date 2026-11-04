"""Browser tools for agents, on the one shared browser (see task_agent.browser).

The model sees each page as a compact list of its links, buttons and fields, each with a ref like `e12`, and acts
by ref. Code keeps a few hard lines the model can't cross:
- passwords are only ever typed by code from the Keychain (`type_saved_login`), never seen or invented by the model;
- payment card fields are refused;
- addresses on this Mac or the local network are refused.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import string
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic_ai import RunContext, Tool
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart

from task_agent.guardrails import wrap_untrusted
from task_agent.tasks.base import JobDeps
from task_agent.tools.web import BlockedURL, check_public_url

SNAPSHOT_MAX_CHARS = 12_000
INTERACTIVE = {
    "link", "button", "textbox", "searchbox", "combobox", "checkbox", "radio", "switch", "option", "menuitem",
    "menuitemcheckbox", "menuitemradio", "tab", "slider", "spinbutton", "listbox", "treeitem",
}  # fmt: skip
WRAPPERS = {"generic", "group", "none", "presentation", "list", "listitem", "region", "section", "article"}
_ICONS = re.compile("[-\U000f0000-\U000fffff]")
_REF = re.compile(r"\s*\[ref=[a-z0-9]+\]")
_LINE = re.compile(r"^(\s*)- (.*)$")
_ROLE = re.compile(r"^([a-z]+)\b(.*)$")
_REF_ARG = re.compile(r"(f\d+e\d+|e\d+)")
_PAYMENT = re.compile(r"card.?number|cc-|cvc|cvv|security.?code|expir|iban|sort.?code|account.?number", re.I)


class NeedsJoe(Exception):  # noqa: N818 (it's a hand-over, not an error)
    """The agent can't go on without Joe (a captcha, a code, a verification email, a payment)."""

    def __init__(self, site: str, url: str, reason: str) -> None:
        super().__init__(reason)
        self.site, self.url, self.reason = site, url, reason


# ── page view ───────────────────────────────────────────────────────────────


def compact_snapshot(raw: str) -> str:
    """Shrink Playwright's AI snapshot: keep refs only on things you can use, drop empty wrappers, re-indent."""
    out: list[str] = []
    stack: list[int] = []  # original indents of the kept ancestors
    for line in raw.splitlines():
        line = _ICONS.sub("", line).replace(" [cursor=pointer]", "").rstrip()
        match = _LINE.match(line)
        if not match:
            continue
        indent, body = len(match.group(1)), match.group(2).strip()
        while stack and stack[-1] >= indent:
            stack.pop()
        if body.startswith("/url:"):
            url = body[5:].strip()
            out.append(" " * len(stack) + "- url: " + (url[:140] + "…" if len(url) > 140 else url))
            continue
        role_match = _ROLE.match(body)
        if role_match:
            role, rest = role_match.groups()
            if role not in INTERACTIVE:
                rest = _REF.sub("", rest)
            label = rest.strip().rstrip(":").strip()
            if (role in WRAPPERS and not label) or (role == "img" and not label):
                continue  # an empty wrapper: its children move up a level
            body = f"{role}{rest}"
        if body.strip(" :-") == "":
            continue
        out.append(" " * len(stack) + "- " + body)
        stack.append(indent)
    return "\n".join(out)


async def snapshot(deps: JobDeps, *, note: str = "", max_chars: int = SNAPSHOT_MAX_CHARS) -> str:
    """The page as the agent sees it, wrapped as untrusted data."""
    page = await _page(deps)
    with _suppress():
        await page.wait_for_load_state("domcontentloaded", timeout=10_000)
    try:
        raw = await page.aria_snapshot(mode="ai", timeout=15_000)
    except Exception as exc:
        raw = f"- text: [This page couldn't be read ({type(exc).__name__}). Try wait_for_page or open it again.]"
    view = compact_snapshot(raw)
    deps.seen_domains.update(_domains(view))
    title = ""
    with _suppress():
        title = await page.title()
    lines = view.splitlines()
    shown, size = [], 0
    for line in lines:
        if size + len(line) > max_chars:
            break
        shown.append(line)
        size += len(line) + 1
    body = "\n".join(shown)
    if len(shown) < len(lines):
        body += (
            f"\n[... {len(lines) - len(shown)} more lines below. Use find_on_page('some words') to jump to "
            "something further down, or scroll.]"
        )
    head = (f"Page: {title}\n" if title else "") + f"Address: {page.url}\n"
    if deps.browser and deps.browser.take_reopened(deps.owner):
        note = (
            note + " " if note else ""
        ) + "(The browser restarted and this page was reloaded: check what you typed.)"
    return (note + "\n" if note else "") + wrap_untrusted(page.url, head + body)


def _domains(text: str) -> set[str]:
    found = set()
    for url in re.findall(r"https?://[^\s\"'<>)\]]+", text):
        host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
        if host:
            found.add(host)
    return found


class _suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return True


async def _page(deps: JobDeps) -> Any:
    if deps.browser is None:
        raise RuntimeError("This task has no browser.")
    check = getattr(deps.browser, "ensure_connected", None)  # Joe's Chrome (task_agent.chrome)
    if check is not None:
        reason = await check()
        if reason:
            raise NeedsJoe(
                "Chrome",
                "",
                f"{reason}. Open Chrome on the Mac and check the OpenClaw extension is on, then press Done.",
            )
    return await deps.browser.page(deps.owner)


async def _locate(deps: JobDeps, ref: str) -> Any:
    match = _REF_ARG.search(ref or "")
    if not match:
        raise _ToolProblem(f"'{ref}' isn't a ref. Use the [ref=…] value from the page, e.g. e12.")
    page = await _page(deps)
    if getattr(page, "is_relay", False):
        if not page.opened:
            raise _ToolProblem(
                "No page is open in this run (the job started again, so its tab was closed). Use open_page with "
                "the page's address first"
            )
        if page.refs is None:  # no look at this page yet (e.g. after a restart): read it so old refs are caught
            with _suppress():
                await page.aria_snapshot(mode="ai", timeout=15_000)
    locator = page.locator(f"aria-ref={match.group(1)}")
    try:
        if await locator.count() == 0:
            raise _ToolProblem(f"Nothing on the page has ref {match.group(1)} any more. Look at the page again.")
    except _ToolProblem:
        raise
    except Exception as exc:
        raise _ToolProblem(f"Ref {match.group(1)} didn't work ({_why(exc)}). Look at the page again.") from exc
    return locator


class _ToolProblem(Exception):
    """A tool couldn't do what was asked; the message goes back to the model."""


class Stuck(Exception):
    """The agent kept failing at the page; the job stops instead of using up its whole budget."""


SETTLE_SECONDS = 1.2  # wait after an action before reading the page again
SAME_FAILURE_WARNING = 3  # the same action failing this often: the model is told to change approach
FAILURES_TO_STOP = 8  # this many page actions in a row failing: the job stops


def _why(exc: Exception) -> str:
    """What went wrong, in the tool's own words (OpenClaw's message, not just the error's class)."""
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text[:200]}" if text else type(exc).__name__


async def _failed(deps: JobDeps, action: str, problem: str) -> str:
    """Tell the model what went wrong, with the page as it is now (fresh refs). Stops a job that's stuck."""
    deps.failed_actions.append(action)
    run = deps.failed_actions
    if len(run) >= FAILURES_TO_STOP:
        raise Stuck(f"the last {len(run)} page actions all failed, so the job stopped. Last one: {problem}")
    note = f"[{problem}]"
    if run.count(action) >= SAME_FAILURE_WARNING:
        note += (
            f" [That's {run.count(action)} times the same thing failed. Don't repeat it: use a ref from the page "
            "below, open the page again, or finish and say what's in the way.]"
        )
    return await _show(deps, note + " The page now:")


async def _after_action(deps: JobDeps, note: str) -> str:
    deps.failed_actions.clear()
    return await _show(deps, note)


async def _show(deps: JobDeps, note: str) -> str:
    page = await _page(deps)
    with _suppress():
        await page.wait_for_load_state("domcontentloaded", timeout=8_000)
    await asyncio.sleep(SETTLE_SECONDS)  # let scripts update the page (menus, validation messages)
    return await snapshot(deps, note=note)


async def _attributes(locator: Any) -> dict[str, str]:
    try:
        return await locator.evaluate(
            """e => ({tag: e.tagName.toLowerCase(), type: (e.getAttribute('type') || '').toLowerCase(),
                     auto: e.getAttribute('autocomplete') || '', name: e.getAttribute('name') || '',
                     id: e.id || '', placeholder: e.getAttribute('placeholder') || '',
                     label: e.getAttribute('aria-label') || ''})"""
        )
    except Exception:
        return {}


def _is_payment(attrs: dict[str, str]) -> bool:
    text = " ".join(attrs.get(k, "") for k in ("auto", "name", "id", "placeholder", "label"))
    return bool(_PAYMENT.search(text))


# ── tools ───────────────────────────────────────────────────────────────────


async def open_page(ctx: RunContext[JobDeps], url: str) -> str:
    """Go to a web address in the browser and show the page (its text, links, buttons and fields with refs)."""
    try:
        await check_public_url(url)
    except BlockedURL as exc:
        return f"[Blocked: {exc}]"
    page = await _page(ctx.deps)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    except Exception as exc:
        return f"[Couldn't open {url}: {type(exc).__name__}. Check the address or try again.]"
    await asyncio.sleep(1.5)
    return await snapshot(ctx.deps)


async def look(ctx: RunContext[JobDeps]) -> str:
    """Show the current page again (after it changed by itself, or to check the result of what you did)."""
    return await snapshot(ctx.deps)


async def find_on_page(ctx: RunContext[JobDeps], text: str) -> str:
    """Search the whole current page (including the part not shown) for some words. Returns matching lines with refs."""
    page = await _page(ctx.deps)
    try:
        view = compact_snapshot(await page.aria_snapshot(mode="ai", timeout=15_000))
    except Exception as exc:
        return f"[Couldn't read the page: {type(exc).__name__}]"
    lines = view.splitlines()
    words = text.lower().split()
    hits = [i for i, line in enumerate(lines) if all(w in line.lower() for w in words)]
    if not hits:
        return f"[No line on this page contains: {text}]"
    chunks, last = [], -1
    for i in hits[:15]:
        start, end = max(i - 2, last + 1), min(i + 3, len(lines))
        if start < end:
            chunks.append("\n".join(lines[start:end]))
            last = end - 1
    return wrap_untrusted(page.url, f"Matches for '{text}' ({len(hits)} lines):\n" + "\n…\n".join(chunks))


async def click(ctx: RunContext[JobDeps], ref: str) -> str:
    """Click a link, button, tab, menu item or option by its ref. Returns the page afterwards."""
    try:
        locator = await _locate(ctx.deps, ref)
        await locator.click(timeout=10_000)
    except _ToolProblem as exc:
        return await _failed(ctx.deps, f"click {ref}", str(exc))
    except Exception as exc:
        return await _failed(ctx.deps, f"click {ref}", f"Click on {ref} didn't work ({_why(exc)})")
    return await _after_action(ctx.deps, f"Clicked {ref}.")


async def type_text(
    ctx: RunContext[JobDeps], ref: str, text: str, press_enter: bool = False, key_by_key: bool = False
) -> str:
    """Type into a text box (replacing what's there). key_by_key=True types one key at a time, for date pickers and
    search boxes that only react to real typing. Never use this for passwords: use type_saved_login."""
    if len(text) > 6_000:
        return "[That's too long for one box (6,000 characters at most).]"
    try:
        locator = await _locate(ctx.deps, ref)
    except _ToolProblem as exc:
        return await _failed(ctx.deps, f"type {ref}", str(exc))
    attrs = await _attributes(locator)
    if attrs.get("type") == "password":
        return "[Refused: this is a password box. Use type_saved_login (or save_new_login first for a new account).]"
    if _is_payment(attrs):
        raise NeedsJoe("payment", (await _page(ctx.deps)).url, "This step asks for payment details. Joe pays himself.")
    try:
        if key_by_key:
            await locator.click(timeout=10_000)
            await locator.press("ControlOrMeta+a")
            await locator.press("Backspace")
            await locator.press_sequentially(text, delay=40, timeout=60_000)
        else:
            try:
                await locator.fill(text, timeout=10_000)
            except Exception:
                if getattr(locator, "page", None) is not None and getattr(locator.page, "is_relay", False):
                    raise  # Chrome already typed the way a person does: its error is the one to report
                await locator.click(timeout=10_000)
                await locator.press("ControlOrMeta+a")
                await (await _page(ctx.deps)).keyboard.type(text, delay=10)
        if press_enter:
            await locator.press("Enter")
    except Exception as exc:
        return await _failed(ctx.deps, f"type {ref}", f"Typing into {ref} didn't work ({_why(exc)})")
    return await _after_action(ctx.deps, f"Typed into {ref}.")


async def choose_option(ctx: RunContext[JobDeps], ref: str, option: str) -> str:
    """Pick an option in a drop-down list (a <select>) by its visible text. For custom drop-downs, click them open
    and click the option instead."""
    try:
        locator = await _locate(ctx.deps, ref)
        try:
            await locator.select_option(label=option, timeout=5_000)
        except Exception:
            await locator.select_option(option, timeout=5_000)
    except _ToolProblem as exc:
        return await _failed(ctx.deps, f"choose {ref}", str(exc))
    except Exception as exc:
        return await _failed(
            ctx.deps,
            f"choose {ref}",
            f"Choosing '{option}' in {ref} didn't work ({_why(exc)}). If it isn't a plain "
            "list, click it open and click the option",
        )
    return await _after_action(ctx.deps, f"Chose '{option}' in {ref}.")


async def set_checkbox(ctx: RunContext[JobDeps], ref: str, checked: bool = True) -> str:
    """Tick (checked=True) or untick a checkbox, radio button or switch."""
    try:
        locator = await _locate(ctx.deps, ref)
        await locator.set_checked(checked, timeout=10_000)
    except _ToolProblem as exc:
        return await _failed(ctx.deps, f"tick {ref}", str(exc))
    except Exception as exc:
        return await _failed(ctx.deps, f"tick {ref}", f"Ticking {ref} didn't work ({_why(exc)}). Try clicking it")
    return await _after_action(ctx.deps, f"{'Ticked' if checked else 'Unticked'} {ref}.")


async def press_key(ctx: RunContext[JobDeps], key: str) -> str:
    """Press a key on the page, e.g. Enter, Tab, Escape, ArrowDown."""
    if not re.fullmatch(r"[A-Za-z0-9+]{1,30}", key):
        return "[Use a key name like Enter, Tab, Escape or ArrowDown.]"
    page = await _page(ctx.deps)
    try:
        await page.keyboard.press(key)
    except Exception as exc:
        return await _failed(ctx.deps, f"press {key}", f"Pressing {key} didn't work ({_why(exc)})")
    return await _after_action(ctx.deps, f"Pressed {key}.")


async def scroll(ctx: RunContext[JobDeps], direction: Literal["down", "up"] = "down") -> str:
    """Scroll the page a screen down or up (pages that load more as you scroll)."""
    page = await _page(ctx.deps)
    with _suppress():
        await page.mouse.wheel(0, 800 if direction == "down" else -800)
    return await _after_action(ctx.deps, f"Scrolled {direction}.")


async def go_back(ctx: RunContext[JobDeps]) -> str:
    """Go back to the previous page."""
    page = await _page(ctx.deps)
    with _suppress():
        await page.go_back(wait_until="domcontentloaded", timeout=20_000)
    return await _after_action(ctx.deps, "Went back.")


async def wait_for_page(ctx: RunContext[JobDeps], seconds: int = 3) -> str:
    """Wait a few seconds (1–20) for a slow page, then show it."""
    await asyncio.sleep(max(1, min(int(seconds), 20)))
    return await snapshot(ctx.deps)


async def upload_event_image(ctx: RunContext[JobDeps], ref: str) -> str:
    """Upload the event's picture: give the ref of the file box or the 'upload image' button."""
    path = getattr(ctx.deps.payload, "image_path", "") or ""
    if not path:
        return "[There's no event picture for this event. Skip the image if the site allows it.]"
    try:
        locator = await _locate(ctx.deps, ref)
    except _ToolProblem as exc:
        return await _failed(ctx.deps, f"upload {ref}", str(exc))
    page = await _page(ctx.deps)
    try:
        attrs = await _attributes(locator)
        file_box = attrs.get("tag") == "input" and attrs.get("type") == "file"
        if hasattr(locator, "upload_file"):  # Joe's Chrome: OpenClaw sets the file box or clicks and picks
            await locator.upload_file(path, file_input=file_box)
        elif file_box:
            await locator.set_input_files(path, timeout=10_000)
        else:
            async with page.expect_file_chooser(timeout=10_000) as chooser:
                await locator.click(timeout=10_000)
            await (await chooser.value).set_files(path)
    except Exception as exc:
        return await _failed(
            ctx.deps,
            f"upload {ref}",
            f"Upload didn't work ({_why(exc)}). Give the ref of the file box or the button that opens the file picker",
        )
    return await _after_action(ctx.deps, "Uploaded the event picture.")


# ── logins ──────────────────────────────────────────────────────────────────


def _event_type(deps: JobDeps) -> str | None:
    """The task's event type key, or None for a task without one (then every login is available)."""
    return getattr(deps.payload, "event_type", "") or None


def _allowed(deps: JobDeps, account: dict[str, Any]) -> bool:
    kind = _event_type(deps)
    return kind is None or account.get("event_type", "") in ("", kind)


async def saved_logins(ctx: RunContext[JobDeps]) -> str:
    """List the site logins saved for Joe (site key, login page, username). Passwords are never shown."""
    if ctx.deps.accounts is None:
        return "[No saved logins are available to this task.]"
    rows = ctx.deps.accounts.list(event_type=_event_type(ctx.deps))
    if not rows:
        return "No saved logins yet. The shared browser may still be logged in to some sites: open them and look."
    lines = [
        f"- {a['site']}: {a['label']}, login page {a['login_url']}, username {a['username'] or '(none)'}, "
        f"{'password saved' if a['has_password'] else 'no password saved'}"
        + (f", for {a['event_type_name'] or a['event_type']} events" if a.get("event_type") else ", shared")
        + (f", last worked {a['last_login_ok_at'][:10]}" if a.get("last_login_ok_at") else "")
        for a in rows
    ]
    return "Saved logins (the browser is often already logged in, so check the page first):\n" + "\n".join(lines)


async def type_saved_login(ctx: RunContext[JobDeps], ref: str, site: str, part: Literal["username", "password"]) -> str:
    """Fill the username or password box from a saved login (see saved_logins). You never see the password."""
    if ctx.deps.accounts is None:
        return "[No saved logins are available to this task.]"
    try:
        account = ctx.deps.accounts.get(site)
    except LookupError:
        return f"[No saved login '{site}'. Check saved_logins, or save_new_login when signing up.]"
    if not _allowed(ctx.deps, account):
        return f"[{account['label']} belongs to another event type. Use this task's logins (saved_logins).]"
    value = account["username"] if part == "username" else ctx.deps.accounts.password(site)
    if not value:
        return f"[The saved login for {account['label']} has no {part}.]"
    try:
        locator = await _locate(ctx.deps, ref)
        await locator.fill(value, timeout=10_000)
    except _ToolProblem as exc:
        return await _failed(ctx.deps, f"login {ref}", str(exc))
    except Exception as exc:  # the error's class only: its text could echo what was typed
        return await _failed(ctx.deps, f"login {ref}", f"Filling {ref} didn't work ({type(exc).__name__})")
    return await _after_action(ctx.deps, f"Filled the {part} for {account['label']}.")


def new_password() -> str:
    """Strong and accepted by fussy sign-up forms: letters, digits and a symbol."""
    alphabet = string.ascii_letters + string.digits
    core = "".join(secrets.choice(alphabet) for _ in range(16))
    return f"{core[:8]}-{core[8:]}{secrets.choice('23456789')}!"


async def save_new_login(ctx: RunContext[JobDeps], site_name: str, login_url: str, username: str) -> str:
    """Before signing up on a new site: saves the login (with a new strong password in the Keychain) and returns its
    site key. Then fill the sign-up form's password box with type_saved_login(ref, site, 'password')."""
    if ctx.deps.accounts is None:
        return "[Saving logins isn't available to this task.]"
    kind = _event_type(ctx.deps)
    for account in ctx.deps.accounts.list(event_type=kind):
        if account["label"].lower() == site_name.strip().lower():
            if not account["has_password"]:
                ctx.deps.accounts.save(
                    site=account["site"],
                    label=account["label"],
                    login_url=account["login_url"],
                    username=username or account["username"],
                    password=new_password(),
                    notes=account.get("notes") or "",
                )
            return f"There's already a saved login for {account['label']}: site key '{account['site']}'."
    try:
        account = ctx.deps.accounts.save(
            label=site_name,
            login_url=login_url,
            username=username,
            password=new_password(),
            notes="Made by an agent",
            event_type=kind or "",
        )
    except ValueError as exc:
        return f"[{exc}]"
    return f"Saved. Site key '{account['site']}'. Use type_saved_login(ref, '{account['site']}', 'password')."


async def check_inbox(ctx: RunContext[JobDeps], site_name: str) -> str:
    """Look in this event type's inbox for a code or link the site just sent (last 30 minutes). Returns only codes
    and the site's verify/login links, never whole emails. If nothing yet, wait 20 seconds and check once more."""
    from task_agent.event_types import EventTypeError, EventTypeStore, codes_and_links, matches_site, site_words

    kind = _event_type(ctx.deps)
    if not kind or ctx.deps.store is None:
        return "[This task has no event type inbox. Use ask_joe for the code.]"
    page_url = ""
    if ctx.deps.browser is not None and ctx.deps.browser.has_tab(ctx.deps.owner):
        page_url = (await _page(ctx.deps)).url
    words = site_words(site_name, page_url)
    try:
        messages = await EventTypeStore(ctx.deps.store).recent_messages(kind)
    except (EventTypeError, LookupError) as exc:
        return f"[{exc} Use ask_joe for the code.]"
    except Exception as exc:  # the mail server is down or refused: Joe can still read it himself
        return f"[Couldn't read the inbox ({type(exc).__name__}). Use ask_joe for the code.]"
    lines = []
    for message in messages:
        if not matches_site(message, words):
            continue
        codes, links = codes_and_links(message, words)
        when = message["received"].strftime("%H:%M UTC")
        lines.append(
            f'- {when} from {message["from"]}: "{message["subject"]}"\n'
            f"  codes: {', '.join(codes) or '(none found)'}\n"
            f"  links: {' '.join(links) or '(none)'}"
        )
    if not lines:
        return (
            f"[No email from {site_name} in the last 30 minutes. Wait 20 seconds (wait_for_page 20) and check once "
            "more; if there's still nothing, use ask_joe.]"
        )
    return wrap_untrusted("inbox", "Newest first:\n" + "\n".join(lines[:5]))


async def ask_joe(ctx: RunContext[JobDeps], site_name: str, what_joe_must_do: str) -> str:
    """Hand the current page to Joe when only a person can do the next step: a captcha, a code sent by text or
    email, a verification email to click, a 2-step login, or a payment. The task pauses and starts again after he's
    done it. Say exactly what he needs to do, e.g. "Enter the 6-digit code Meetup emailed to events@…"."""
    page = await _page(ctx.deps)
    raise NeedsJoe(site_name.strip()[:60] or "site", page.url, what_joe_must_do.strip()[:300])


# ── tool sets and history trimming ───────────────────────────────────────────

READ_TOOLS = (open_page, look, find_on_page, click, scroll, go_back, wait_for_page)
ACT_TOOLS = (type_text, choose_option, set_checkbox, press_key, upload_event_image)
LOGIN_TOOLS = (saved_logins, type_saved_login, save_new_login, check_inbox, ask_joe)
BROWSER_TOOL_NAMES = {f.__name__ for f in (*READ_TOOLS, *ACT_TOOLS, *LOGIN_TOOLS)}
VIEW_TOOLS = {f.__name__ for f in (*READ_TOOLS, *ACT_TOOLS, type_saved_login)} - {"find_on_page"}


def tools(*groups: tuple[Any, ...]) -> tuple[Tool[JobDeps], ...]:
    return tuple(Tool(fn, takes_ctx=True) for group in groups for fn in group)


def keep_recent_pages(messages: list[ModelMessage], keep: int = 1, batch: int = 2) -> list[ModelMessage]:
    """Older page views are replaced by a one-line note so long browser tasks fit the model's memory.

    Done in batches: once more than `batch` full page views pile up, all but the newest `keep` are shortened at once.
    In between, the conversation only grows at the end, so oMLX reuses its cached prompt and reads just the new page.
    (Shortening one view every step changed the prompt two pages back, so every step re-read the last two pages.)"""
    views = [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
        and part.tool_name in VIEW_TOOLS
        and isinstance(part.content, str)
        and "<untrusted_data" in part.content
    ]
    if len(views) <= batch:
        return messages
    for part in views[: len(views) - keep]:
        first = part.content.split("\n", 1)[0] if not part.content.startswith("<") else ""
        address = re.search(r"Address: (\S+)", part.content)
        part.content = (
            f"{first} [Old page view removed to save space"
            + (f"; it was {address.group(1)}" if address else "")
            + ". Use look to see the page now.]"
        ).strip()
    return messages
