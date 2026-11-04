"""Joe's everyday Chrome through the OpenClaw relay (task_agent.chrome), against a pretend `openclaw browser`."""

# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from task_agent.accounts import prefill
from task_agent.browser import BrowserHub
from task_agent.chrome import ChromeHub, Relay, RelayError, make_browser
from task_agent.tools import browser as tools
from task_agent.tools.browser import NeedsJoe


class FakeOpenClaw:
    """Pretends to be `openclaw browser --json`: Joe's own tab t1 is open; agents' tabs are t2, t3, …"""

    def __init__(self) -> None:
        self.tabs: dict[str, str] = {"t1": "https://mail.example.com/"}
        self.next = 2
        self.calls: list[tuple[list[str], str | None]] = []
        self.connected = True
        self.popup_ref = "e9"

    async def __call__(self, cmd: list[str], stdin: str | None, timeout: float) -> tuple[int, str, str]:
        args = cmd[cmd.index("--json") + 1 :]
        self.calls.append((args, stdin))
        tab = args[args.index("--target-id") + 1] if "--target-id" in args else None
        name = args[0]
        if name == "status":
            return 0, json.dumps({"running": self.connected, "cdpReady": self.connected}), ""
        if not self.connected:
            return 1, "", "Error: extension not connected"
        if tab is not None and tab not in self.tabs:
            return 1, json.dumps({"error": f"tab {tab} not found"}), ""
        if name == "open":
            new = f"t{self.next}"
            self.next += 1
            self.tabs[new] = args[1]
            return 0, json.dumps({"suggestedTargetId": new, "url": args[1], "title": "Opened"}), ""
        if name == "navigate":
            self.tabs[tab] = args[1]
            return 0, json.dumps({"ok": True, "url": args[1]}), ""
        if name == "snapshot":
            snap = '- button "Go" [ref=e1]\n- link "Popup" [ref=e9]'
            refs = {"e1": {"role": "button"}, "e9": {"role": "link"}}
            return 0, json.dumps({"ok": True, "url": self.tabs[tab], "snapshot": snap, "refs": refs}), ""
        if name == "click":
            if args[1] == self.popup_ref:
                new = f"t{self.next}"
                self.next += 1
                self.tabs[new] = "https://popup.example.com/"
            else:
                self.tabs[tab] = self.tabs[tab] + "next"
            return 0, json.dumps({"ok": True, "url": self.tabs[tab]}), ""
        if name == "tabs":
            listed = [{"suggestedTargetId": t, "url": u} for t, u in self.tabs.items()]
            return 0, json.dumps({"tabs": listed}), ""
        if name == "close":
            self.tabs.pop(args[1], None)
            return 0, json.dumps({"ok": True}), ""
        if name == "evaluate":
            return 0, json.dumps({"ok": True, "url": self.tabs[tab], "result": {"tag": "input", "type": "text"}}), ""
        return 0, json.dumps({"ok": True, "url": self.tabs.get(tab or "", "")}), ""

    def commands(self, name: str) -> list[list[str]]:
        return [args for args, _ in self.calls if args[0] == name]


@pytest.fixture
def relay() -> FakeOpenClaw:
    return FakeOpenClaw()


def hub_for(fake: FakeOpenClaw) -> ChromeHub:
    return ChromeHub(Relay(Path("/nowhere"), node="node", runner=fake), poll_seconds=0.01)


async def test_agent_tab_opens_reads_and_closes_only_itself(relay: FakeOpenClaw) -> None:
    hub = hub_for(relay)
    page = await hub.page("job:1")
    assert not hub.has_tab("job:1")  # nothing opens in Chrome until there's a page to show
    await page.goto("https://www.meetup.com/create")
    assert hub.has_tab("job:1") and page.tab == "t2"
    view = await page.aria_snapshot(mode="ai")
    assert "[ref=e1]" in view
    assert await page.locator("aria-ref=e1").count() == 1
    assert await page.locator("aria-ref=e77").count() == 0  # not on the page any more
    await page.locator("aria-ref=e1").click()
    assert hub.visited("job:1") == ["https://www.meetup.com/create", "https://www.meetup.com/createnext"]
    await hub.release("job:1")
    assert relay.commands("close") == [["close", "t2"]]
    assert "t1" in relay.tabs  # Joe's own tab is never touched


async def test_typed_text_goes_on_stdin_never_the_command_line(relay: FakeOpenClaw) -> None:
    hub = hub_for(relay)
    page = await hub.page("job:1")
    await page.goto("https://example.com/signup")
    await page.locator("aria-ref=e1").fill("s3cret-Passw0rd!")
    args, stdin = relay.calls[-1]
    assert args[0] == "batch" and "-" in args
    assert "s3cret-Passw0rd!" in (stdin or "")
    assert all("s3cret" not in " ".join(a) for a, _ in relay.calls)


async def test_a_click_that_opens_a_new_tab_is_followed(relay: FakeOpenClaw) -> None:
    hub = hub_for(relay)
    page = await hub.page("job:1")
    await page.goto("https://www.eventbrite.co.uk/")
    await page.locator("aria-ref=e9").click()
    current = await hub.page("job:1")
    assert current is not page and current.url == "https://popup.example.com/"
    await hub.release("job:1")
    assert sorted(a[1] for a in relay.commands("close")) == ["t2", "t3"]  # the popup and the first tab
    assert "t1" in relay.tabs


async def test_hand_over_keeps_the_tab_as_it_is(relay: FakeOpenClaw) -> None:
    hub = hub_for(relay)
    page = await hub.page("job:1")
    await page.goto("https://www.meetup.com/create")
    moved, kept = await hub.hand_over("job:1", "login:need-3", "https://www.meetup.com/create")
    assert kept and moved is page and hub.has_tab("login:need-3") and not hub.has_tab("job:1")
    assert relay.commands("focus")
    await prefill(moved, "info@joebains.com", "pw")  # Chrome's own password manager does this
    assert not relay.commands("batch")
    await hub.release("login:need-3")


async def test_closing_a_login_tab_tells_the_login_windows(relay: FakeOpenClaw) -> None:
    hub = hub_for(relay)
    closed: list[str] = []
    hub.on_login_closed = closed.append
    await hub.show("login:facebook", "https://www.facebook.com/login")
    tab = hub._pages["login:facebook"].tab
    relay.tabs.pop(tab)  # Joe closes the tab when he's logged in
    for _ in range(100):
        if closed:
            break
        await asyncio.sleep(0.01)
    assert closed == ["facebook"] and not hub.has_tab("login:facebook")


async def test_chrome_not_connected_hands_the_task_to_joe(relay: FakeOpenClaw) -> None:
    hub = hub_for(relay)
    deps = SimpleNamespace(browser=hub, owner="job:5")
    page = await hub.page("job:5")
    relay.connected = False
    with pytest.raises(RelayError):
        await page.goto("https://www.meetup.com/")
    assert hub.offline and hub.status()["offline"]
    with pytest.raises(NeedsJoe) as need:
        await tools._page(deps)  # type: ignore[arg-type]
    assert "OpenClaw extension" in need.value.reason
    relay.connected = True
    assert await tools._page(deps) is page  # type: ignore[arg-type]
    assert not hub.offline


async def test_a_closed_tab_is_noticed_and_reopened(relay: FakeOpenClaw) -> None:
    hub = hub_for(relay)
    page = await hub.page("job:1")
    await page.goto("https://example.com/")
    relay.tabs.pop(page.tab)  # Joe closed the agent's tab
    with pytest.raises(RelayError):
        await page.aria_snapshot()
    assert page.is_closed()
    fresh = await hub.page("job:1")
    await fresh.goto("https://example.com/again")
    assert fresh.tab == "t3"


def test_settings_choose_the_browser(tmp_path: Path) -> None:
    chrome = make_browser(SimpleNamespace(browser="chrome", openclaw_dir=tmp_path, openclaw_browser_profile="chrome"),
                          tmp_path / "shared")  # fmt: skip
    own = make_browser(SimpleNamespace(browser="own"), tmp_path / "shared")
    assert isinstance(chrome, ChromeHub) and chrome.status()["profile_saved"] is True
    assert isinstance(own, BrowserHub)


def test_status_needs_no_chrome_call(relay: FakeOpenClaw) -> None:
    status: dict[str, Any] = hub_for(relay).status()
    assert status["mode"] == "chrome" and status["open_tabs"] == 0 and not relay.calls


def tool_deps(hub: ChromeHub) -> SimpleNamespace:
    return SimpleNamespace(browser=hub, owner="job:8", failed_actions=[], seen_domains=set(), payload=None)


async def test_after_a_restart_the_agent_is_told_to_open_the_page_again(relay, monkeypatch) -> None:
    monkeypatch.setattr(tools, "SETTLE_SECONDS", 0)
    ctx = SimpleNamespace(deps=tool_deps(hub_for(relay)))  # a fresh run: no tab yet, old refs in its memory
    reply = await tools.type_text(ctx, "f16e169", "Lyceum Tavern Comedy Show")  # type: ignore[arg-type]
    assert "No page is open in this run" in reply and "open_page" in reply
    assert not relay.commands("batch")  # nothing was typed anywhere


async def test_an_old_ref_after_a_restart_is_caught_and_the_page_shown(relay, monkeypatch) -> None:
    monkeypatch.setattr(tools, "SETTLE_SECONDS", 0)
    hub = hub_for(relay)
    ctx = SimpleNamespace(deps=tool_deps(hub))
    page = await hub.page("job:8")
    await page.goto("https://www.whatsonincityoflondon.com/submit-your-event/")  # opened, not looked at yet
    reply = await tools.type_text(ctx, "f16e169", "Lyceum Tavern Comedy Show")  # type: ignore[arg-type]
    assert "Nothing on the page has ref f16e169" in reply
    assert "The page now:" in reply and "[ref=e1]" in reply  # fresh refs to use instead


async def test_the_real_error_is_shown_and_a_stuck_job_stops(relay, monkeypatch) -> None:
    monkeypatch.setattr(tools, "SETTLE_SECONDS", 0)
    hub = hub_for(relay)
    ctx = SimpleNamespace(deps=tool_deps(hub))
    page = await hub.page("job:8")
    await page.goto("https://example.com/form")
    await page.aria_snapshot()

    async def refuse(actions):
        raise RelayError("Element e1 is not an editable field")

    monkeypatch.setattr(page, "act", refuse)
    first = await tools.type_text(ctx, "e1", "hello")  # type: ignore[arg-type]
    assert "Element e1 is not an editable field" in first and "times the same thing failed" not in first
    await tools.type_text(ctx, "e1", "hello")  # type: ignore[arg-type]
    third = await tools.type_text(ctx, "e1", "hello")  # type: ignore[arg-type]
    assert "3 times the same thing failed" in third

    await tools.click(ctx, "e1")  # type: ignore[arg-type]  # a success resets the count
    assert ctx.deps.failed_actions == []
    for _ in range(tools.FAILURES_TO_STOP - 1):
        await tools.type_text(ctx, "e1", "hello")  # type: ignore[arg-type]
    with pytest.raises(tools.Stuck, match="the last 8 page actions all failed"):
        await tools.type_text(ctx, "e1", "hello")  # type: ignore[arg-type]


async def test_a_failed_login_fill_never_shows_the_error_text(relay, monkeypatch) -> None:
    monkeypatch.setattr(tools, "SETTLE_SECONDS", 0)
    hub = hub_for(relay)
    page = await hub.page("job:8")
    await page.goto("https://example.com/login")
    await page.aria_snapshot()

    async def refuse(actions):
        raise RelayError(f"could not type {actions[0]['text']}")

    monkeypatch.setattr(page, "act", refuse)
    accounts = SimpleNamespace(
        get=lambda site: {"site": site, "label": "Meetup", "username": "joe", "event_type": ""},
        password=lambda site: "s3cret-Passw0rd!",
    )
    deps = tool_deps(hub)
    deps.accounts = accounts
    reply = await tools.type_saved_login(SimpleNamespace(deps=deps), "e1", "meetup", "password")  # type: ignore[arg-type]
    assert "didn't work (RelayError)" in reply and "s3cret" not in reply
