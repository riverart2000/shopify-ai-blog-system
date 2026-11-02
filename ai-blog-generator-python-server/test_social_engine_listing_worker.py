"""The directory employee: works a listing end to end through a fake Chrome, logging every step; hands back for what is its owner's."""
from __future__ import annotations

import pytest

from db.social_engine import listings as listing_store
from db.social_engine import observability
from services.social_engine.context import load_store_context
from services.social_engine.errors import ValidationError
from services.social_engine.listings import checks, fill, mail, vault, worker
from services.social_engine.listings.catalogue import get_directory
from services.social_engine.listings.relay import Snapshot
from services.social_engine.listings.worker import Decision
from services.social_engine.settings import save_settings
from test_social_engine_listings import PROFILE


class ChromeStub:
    """A directory site as a sequence of pages: each action moves to the next snapshot."""

    def __init__(self, pages: list[Snapshot], tabs: list[dict] | None = None) -> None:
        self.pages = pages
        self.index = 0
        self.tabs_value = tabs or []
        self.actions: list[tuple] = []
        self.opened: list[str] = []
        self.closed: list[str] = []

    def _advance(self):
        if self.index < len(self.pages) - 1:
            self.index += 1

    async def tabs(self):
        return self.tabs_value

    async def find_tab(self, hosts):
        for tab in self.tabs_value:
            if any(h in tab["url"] for h in hosts):
                return tab
        return None

    async def open(self, url):
        self.opened.append(url)
        return "t1"

    async def close(self, tab):
        self.closed.append(tab)

    async def snapshot(self, tab, *, interactive=True, urls=False, limit=600):
        return self.pages[self.index]

    async def click(self, tab, ref):
        self.actions.append(("click", ref)); self._advance()

    async def type(self, tab, ref, text, *, submit=False):
        self.actions.append(("type", ref, text))

    async def fill(self, tab, fields, *, scratch_dir):
        self.actions.append(("fill", tuple((f["ref"], f["value"]) for f in fields)))

    async def select(self, tab, ref, values):
        self.actions.append(("select", ref, tuple(values)))

    async def press(self, tab, key):
        self.actions.append(("press", key)); self._advance()

    async def navigate(self, tab, url):
        self.actions.append(("navigate", url)); self._advance()

    async def wait(self, tab, *, ms=0, text="", load=""):
        return {}

    async def scroll_into_view(self, tab, ref):
        self.actions.append(("scroll", ref))

    async def js_click(self, tab, ref):
        self.actions.append(("js_click", ref)); self._advance()

    async def js_set_value(self, tab, ref, value):
        self.actions.append(("js_set", ref, value)); return {"result": True}

    async def screenshot(self, tab, *, out_dir):
        return str(out_dir / "shot.png")


SIGNUP = Snapshot(url="https://www.freeindex.co.uk/register.htm", text="""
- heading "Create your free account" [level=1]
- textbox "Email address" [ref=e1]
- textbox "Password" [ref=e2]
- checkbox "I agree to the terms" [ref=e3]
- checkbox "Send me marketing emails" [ref=e4]
- button "Create account" [ref=e5]
""", refs={
    "e1": {"role": "textbox", "name": "Email address"}, "e2": {"role": "textbox", "name": "Password"},
    "e3": {"role": "checkbox", "name": "I agree to the terms"}, "e4": {"role": "checkbox", "name": "Send me marketing emails"},
    "e5": {"role": "button", "name": "Create account"},
})
VERIFY = Snapshot(url="https://www.freeindex.co.uk/verify.htm", text='- heading "Check your email for a 6 digit code"\n- textbox "Verification code" [ref=e1]\n- button "Verify" [ref=e2]',
                  refs={"e1": {"role": "textbox", "name": "Verification code"}, "e2": {"role": "button", "name": "Verify"}})
FORM = Snapshot(url="https://www.freeindex.co.uk/add-business.htm", text='- textbox "Business name" [ref=e1]\n- textbox "Telephone" [ref=e2]\n- textbox "Describe your business" [ref=e3]\n- combobox "Category" [ref=e4]\n- button "Submit listing" [ref=e5]',
                refs={"e1": {"role": "textbox", "name": "Business name"}, "e2": {"role": "textbox", "name": "Telephone"},
                      "e3": {"role": "textbox", "name": "Describe your business"}, "e4": {"role": "combobox", "name": "Category"},
                      "e5": {"role": "button", "name": "Submit listing"}})
LIVE = Snapshot(url="https://www.freeindex.co.uk/profile(bio-luxe-lab)_123.htm", text='- heading "Bio Luxe Lab"\n- text: Your listing is live.', refs={})


def scripted(*decisions: dict):
    """A decider that replies in order, then keeps repeating the last reply."""
    queue = list(decisions)

    async def decide(sctx, run, values, account, snapshot, page):
        d = queue.pop(0) if len(queue) > 1 else queue[0]
        decide.prompts.append(worker._prompt(run, values, account, snapshot, page))
        return Decision(**d)

    decide.prompts = []
    return decide


async def _ready(store_id: str, **extra) -> None:
    await save_settings(store_id, {"listings": {"profile": PROFILE, **extra}, "ai": {"ai_decisions": False}})


@pytest.mark.asyncio
async def test_worker_signs_up_fills_submits_verifies_and_ends_live(se_db, monkeypatch):
    await _ready(se_db, account_email="logins@bioluxelab.com")
    sctx = await load_store_context(se_db)
    chrome = ChromeStub([SIGNUP, VERIFY, FORM, LIVE])
    monkeypatch.setattr(fill, "relay_for", lambda sctx: chrome)

    async def fake_mail(sctx, relay, directory, *, subject_id):
        return {"code": "482913", "link": "", "note": "read from Gmail"}
    monkeypatch.setattr(mail, "read_verification", fake_mail)

    decide = scripted(
        {"action": "fill", "values": [{"ref": "e1", "value": "logins@bioluxelab.com"}, {"ref": "e2", "value": "{{password}}"}], "note": "register"},
        {"action": "click", "ref": "e3", "note": "required terms"},
        {"action": "click", "ref": "e5", "note": "create the account"},
        {"action": "check_email", "note": "the site wants a code"},
        {"action": "type", "ref": "e1", "value": "482913", "note": "code from Gmail"},
        {"action": "click", "ref": "e2", "note": "verify"},
        {"action": "fill", "values": [{"ref": "e1", "value": "Bio Luxe Lab"}, {"ref": "e2", "value": "+44 20 7946 0000"}, {"ref": "e3", "value": PROFILE["description"]}], "note": "the listing"},
        {"action": "select", "ref": "e4", "value": "Health & Wellbeing", "note": "closest category"},
        {"action": "click", "ref": "e5", "note": "submit"},
        {"action": "done", "listing_url": "https://www.freeindex.co.uk/profile(bio-luxe-lab)_123.htm", "note": "live"},
    )
    outcome = await worker.work_directory(sctx, "freeindex", decider=decide)
    assert outcome["state"] == "live" and outcome["listing_url"].endswith("_123.htm")
    assert chrome.opened == ["https://www.freeindex.co.uk/add-business.htm"]

    # the real password was typed, never the token; the model only ever saw the token
    typed = [a for a in chrome.actions if a[0] == "fill"][0][1]
    password = dict(typed)["e2"]
    assert password != "{{password}}" and len(password) >= 20
    assert all(password not in p for p in decide.prompts) and "{{password}}" in decide.prompts[0]
    assert "verification_code" in decide.prompts[4] and "482913" in decide.prompts[4]

    row = await listing_store.get_listing(se_db, "freeindex")
    assert row["status"] == "live" and row["listing_url"].endswith("_123.htm") and row["live_at"]
    account = await listing_store.get_account(se_db, "freeindex")
    assert account["login_email"] == "logins@bioluxelab.com" and vault.decrypt(account["password_enc"]) == password
    steps = await listing_store.list_steps(se_db, "freeindex")
    assert [s["action"] for s in steps] == ["note", "fill", "click", "click", "check_email", "type", "click", "fill", "select", "click", "done"]
    assert all(password not in (s["value"] + s["note"] + s["label"]) for s in steps)
    assert "••••" in steps[1]["value"] and steps[4]["value"] == "482913"
    runs = await listing_store.list_runs(se_db, "freeindex")
    assert runs[0]["steps"] == 11 and runs[0]["outcome"] == "done"
    activity = await observability.list_activity(store_id=se_db, limit=5)
    assert any("listed" in a["message"] for a in activity)


@pytest.mark.asyncio
async def test_worker_hands_back_for_captcha_and_payment_and_never_types_card_details(se_db, monkeypatch):
    await _ready(se_db)
    sctx = await load_store_context(se_db)
    page = Snapshot(url="https://business.yell.com/free-listing/", text='- textbox "Card number" [ref=e1]\n- checkbox "I\'m not a robot" [ref=e2]\n- button "Pay now" [ref=e3]',
                    refs={"e1": {"role": "textbox", "name": "Card number"}, "e2": {"role": "checkbox", "name": "I'm not a robot"}, "e3": {"role": "button", "name": "Pay now"}})
    chrome = ChromeStub([page])
    monkeypatch.setattr(fill, "relay_for", lambda sctx: chrome)
    outcome = await worker.work_directory(sctx, "yell", decider=scripted({"action": "type", "ref": "e1", "value": "4111 1111 1111 1111", "note": "card"}))
    assert outcome["state"] == "needs_owner" and "payment" in outcome["reason"]
    assert chrome.actions == []
    row = await listing_store.get_listing(se_db, "yell")
    assert row["status"] == "needs_owner" and row["fill"]["screenshot"].endswith("shot.png")
    steps = await listing_store.list_steps(se_db, "yell")
    assert steps[-1]["result"].startswith("refused") and "4111" not in steps[-1]["value"] or steps[-1]["value"] == ""

    outcome = await worker.work_directory(sctx, "yell", decider=scripted({"action": "click", "ref": "e2", "note": "tick the robot box"}))
    assert outcome["state"] == "needs_owner" and "CAPTCHA" in outcome["reason"]
    assert chrome.actions == []
    assert len(await listing_store.list_runs(se_db, "yell")) == 2


@pytest.mark.asyncio
async def test_worker_stops_when_it_repeats_itself_or_asks_for_the_owner(se_db, monkeypatch):
    await _ready(se_db)
    sctx = await load_store_context(se_db)
    chrome = ChromeStub([SIGNUP])
    monkeypatch.setattr(fill, "relay_for", lambda sctx: chrome)
    outcome = await worker.work_directory(sctx, "freeindex", decider=scripted({"action": "click", "ref": "e5", "note": "again"}))
    assert outcome["state"] == "needs_owner" and "repeating" in outcome["reason"]
    assert chrome.actions == [("click", "e5"), ("click", "e5")]   # decided three times, acted twice, then stopped

    outcome = await worker.work_directory(sctx, "freeindex", decider=scripted({"action": "need_owner", "reason": "It wants a phone call to verify.", "note": "phone"}))
    assert outcome["state"] == "needs_owner" and outcome["reason"] == "It wants a phone call to verify."
    steps = await listing_store.list_steps(se_db, "freeindex")
    assert steps[-1]["action"] == "need_owner" and steps[-1]["result"] == "It wants a phone call to verify."
    # the next run reuses the stored account and tells the model to sign in
    stored = await listing_store.get_account(se_db, "freeindex")
    assert stored is None   # no password was ever typed, so nothing was stored
    decide = scripted({"action": "submitted", "note": "pending review"})
    outcome = await worker.work_directory(sctx, "freeindex", decider=decide)
    assert outcome["state"] == "submitted" and (await listing_store.get_listing(se_db, "freeindex"))["status"] == "submitted"


@pytest.mark.asyncio
async def test_worker_refuses_what_it_may_not_take_and_picks_the_next_directory(se_db):
    sctx = await load_store_context(se_db)
    with pytest.raises(ValidationError, match="profile first"):
        await worker.work_directory(sctx, "freeindex")
    await _ready(se_db)
    sctx = await load_store_context(se_db)
    with pytest.raises(ValidationError, match="not worked through Chrome"):
        await worker.work_directory(sctx, "google_business")
    assert worker.eligible(get_directory("companies_house"), None, profile_ready=True, has_address=True, business_type="sole_trader")
    assert worker.eligible(get_directory("yelp"), None, profile_ready=True, has_address=False, business_type="sole_trader").startswith("Yelp")
    await listing_store.save_listing(se_db, "trustpilot", status="live", listing_url="https://uk.trustpilot.com/review/bioluxelab.com")
    nxt = await worker.next_directory(sctx)
    assert nxt and nxt.id == "linkedin_company"   # highest-trust directory the worker can take that is still to do
    await listing_store.save_listing(se_db, "linkedin_company", status="needs_owner")
    nxt = await worker.next_directory(sctx)
    assert nxt and nxt.id == "apple_business_connect"   # next by trust; a directory held by the owner is not retaken


def test_sole_traders_have_no_companies_house_row_and_the_score_still_reaches_100():
    full = {"Google Business Profile", "Trustpilot", "LinkedIn company page", "Yell.com"}
    ltd = checks.established_score(True, full, ["instagram.com", "facebook.com"], business_type="limited_company")
    sole = checks.established_score(True, full, ["instagram.com", "facebook.com"], business_type="sole_trader")
    assert ltd["score"] == 95 and sole["score"] == 100
    assert not any("Companies House" in c["label"] for c in sole["checks"])
    row = fill.directory_row(get_directory("companies_house"), None, True, True, "sole_trader")
    assert row["status"] == "skipped" and "sole trader" in row["blocked"]
    assert fill.directory_row(get_directory("companies_house"), None, True, True, "limited_company")["status"] == "todo"


def test_vault_round_trips_and_passwords_are_strong(se_db):
    password = vault.generate_password()
    assert len(password) == 20 and any(c.isdigit() for c in password) and any(c in "!#%+-=?@" for c in password)
    token = vault.encrypt(password)
    assert token != password and vault.decrypt(token) == password and vault.decrypt("") == ""


def test_mail_extracts_an_obvious_code_or_link_without_a_model():
    d = get_directory("freeindex")
    snap = Snapshot(url="https://mail.google.com/mail/u/0/#inbox/abc", text='- text: FreeIndex - Your verification code is 915204. Enter it within 10 minutes.')
    assert mail.extract(snap, d) == {"code": "915204", "link": ""}
    snap = Snapshot(url="https://mail.google.com/", text='- link "Confirm your email" [ref=e1]\n  - /url: https://www.freeindex.co.uk/confirm.htm?token=abc123\n- text: https://www.freeindex.co.uk/verify?key=xyz')
    assert mail.extract(snap, d)["link"] == "https://www.freeindex.co.uk/confirm.htm?token=abc123"   # the first verification link wins
    assert mail.gmail_search_url(d).startswith("https://mail.google.com/mail/u/0/#search/")
    assert mail.extract(Snapshot(text="- text: Order 12345678 shipped"), d) == {"code": "", "link": ""}


def test_snapshot_is_trimmed_to_what_the_model_needs():
    text = '- generic [ref=e1]:\n  - heading "Create account" [level=1] [ref=e2]\n  - generic:\n    - textbox "Email" [ref=e3]\n  - /url: https://x\n  - text: Already a member?'
    out = worker.trim_snapshot(Snapshot(text=text))
    assert 'heading "Create account"' in out and 'textbox "Email" [ref=e3]' in out and "Already a member" in out
    assert "/url" not in out and "- generic [ref=e1]:" not in out


@pytest.mark.asyncio
async def test_verification_mail_is_read_over_imap_when_the_mailbox_is_configured(se_db, monkeypatch):
    await _ready(se_db)
    sctx = await load_store_context(se_db)
    d = get_directory("freeindex")
    monkeypatch.setenv("LISTINGS_MAIL_IMAP_HOST", "mail.example.test")
    monkeypatch.setenv("LISTINGS_MAIL_USER", "info@bioluxelab.com")
    monkeypatch.setenv("LISTINGS_MAIL_PASSWORD", "x")
    calls = []

    def fake_fetch(directory, settings, *, days=2, limit=5):
        calls.append((directory.id, settings["host"], settings["port"]))
        return [{"subject": "Your FreeIndex verification code", "from": "noreply@freeindex.co.uk",
                 "text": "Hi, your verification code is 338201. It expires in 15 minutes."}]
    monkeypatch.setattr(mail, "fetch_imap_mail", fake_fetch)
    found = await mail.read_verification(sctx, relay=None, directory=d, subject_id="t")
    assert found["code"] == "338201" and calls == [("freeindex", "mail.example.test", "993")]
    assert "info@bioluxelab.com" in found["note"]

    # an HTML mail with the confirmation link
    import email.message
    msg = email.message.EmailMessage()
    msg["Subject"] = "Confirm your FreeIndex account"
    msg.set_content("plain fallback")
    msg.add_alternative('<p>Welcome.</p><a href="https://www.freeindex.co.uk/confirm.htm?token=Q1">Confirm</a>', subtype="html")
    text = mail._message_text(msg)
    assert "https://www.freeindex.co.uk/confirm.htm?token=Q1" in text and "<a" not in text
    monkeypatch.delenv("LISTINGS_MAIL_IMAP_HOST")
    assert mail.imap_settings() == {}


@pytest.mark.asyncio
async def test_a_failed_relay_action_is_logged_and_the_model_tries_another_way(se_db, monkeypatch):
    from services.social_engine.errors import ExternalServiceError

    await _ready(se_db)
    sctx = await load_store_context(se_db)
    chrome = ChromeStub([FORM, LIVE])

    async def failing_select(tab, ref, values):
        raise ExternalServiceError("chrome_relay", 'Element "e4" not found or not a select')
    chrome.select = failing_select
    monkeypatch.setattr(fill, "relay_for", lambda sctx: chrome)
    decide = scripted(
        {"action": "select", "ref": "e4", "value": "Health", "note": "category"},
        {"action": "click", "ref": "e4", "note": "open the dropdown instead"},
        {"action": "done", "listing_url": "https://www.freeindex.co.uk/profile(bio-luxe-lab)_123.htm", "note": "live"},
    )
    outcome = await worker.work_directory(sctx, "freeindex", decider=decide)
    assert outcome["state"] == "live"
    steps = await listing_store.list_steps(se_db, "freeindex")
    assert steps[1]["action"] == "select" and steps[1]["result"].startswith("error:") and "not a select" in steps[1]["result"]
    facts = lambda i: decide.prompts[i].split("FACTS: ", 1)[1].split("\n", 1)[0]
    assert "last_error" in facts(1) and "click the control first" in facts(1)
    assert "last_error" not in facts(2)          # cleared once an action succeeds
    assert chrome.actions == [("click", "e4")]


@pytest.mark.asyncio
async def test_a_run_killed_by_a_restart_is_handed_back_not_left_working(se_db, monkeypatch):
    import socket

    from db.social_engine import jobs as job_store

    await _ready(se_db)
    await listing_store.save_listing(se_db, "yell", status="working", fill={"state": "running", "run_id": "r1"})
    await listing_store.add_step(se_db, "yell", "r1", 1, action="note", note="started")
    await listing_store.save_listing(se_db, "freeindex", status="working", fill={"state": "running", "run_id": "r2"})
    # yell's job is held by a process that no longer exists; freeindex's by this very process
    dead = f"{socket.gethostname()}:999999:x"
    live = f"{socket.gethostname()}:{__import__('os').getpid()}:x"
    for did, owner in (("yell", dead), ("freeindex", live)):
        job_id, _ = await job_store.enqueue("listings.work", {"directory_id": did}, store_id=se_db, dedupe_key=f"t:{did}")
        from db.social_engine.connection import connect
        async with connect() as conn:
            await conn.execute("UPDATE social_jobs SET status='running', lease_owner=? WHERE id=?", (owner, job_id))
            await conn.commit()
    recovered = await worker.recover_interrupted(se_db)
    assert recovered == ["yell"]
    yell = await listing_store.get_listing(se_db, "yell")
    assert yell["status"] == "needs_owner" and "interrupted" in yell["fill"]["message"] and yell["fill"]["run_id"] == "r1"
    assert (await listing_store.list_steps(se_db, "yell"))[-1]["result"] == "interrupted"
    assert (await listing_store.get_listing(se_db, "freeindex"))["status"] == "working"
    assert await worker.recover_interrupted(se_db) == []


@pytest.mark.asyncio
async def test_worker_never_picks_an_address_when_the_profile_has_none(se_db, monkeypatch):
    await save_settings(se_db, {"listings": {"profile": {**PROFILE, "address_line1": "", "postcode": ""}}, "ai": {"ai_decisions": False}})
    sctx = await load_store_context(se_db)
    page = Snapshot(url="https://business.yell.com/free-listing/", text="", refs={
        "e1": {"role": "combobox", "name": "Select an address"}, "e2": {"role": "option", "name": "12 Example Street, Norwich, NR1 1AA"},
        "e3": {"role": "textbox", "name": "Postcode"}, "e4": {"role": "textbox", "name": "Business name"}})
    chrome = ChromeStub([page])
    monkeypatch.setattr(fill, "relay_for", lambda sctx: chrome)
    for decision in ({"action": "select", "ref": "e1", "value": "12 Example Street, Norwich, NR1 1AA", "note": "pick"},
                     {"action": "click", "ref": "e2", "note": "choose the suggestion"},
                     {"action": "type", "ref": "e3", "value": "NR5 8TU", "note": "postcode"},
                     {"action": "fill", "values": [{"ref": "e4", "value": "Bio Luxe Lab, NW1 6XE"}], "note": "name with a postcode"}):
        outcome = await worker.work_directory(sctx, "yell", decider=scripted(decision))
        assert outcome["state"] == "needs_owner" and "address" in outcome["reason"], decision
    assert chrome.actions == []
    prompt = scripted({"action": "need_owner", "reason": "x"})
    await worker.work_directory(sctx, "yell", decider=prompt)
    assert "NO public address" in prompt.prompts[0]
    # with an address in the profile the same picks are allowed through to the relay
    await save_settings(se_db, {"listings": {"profile": PROFILE}})
    sctx = await load_store_context(se_db)
    chrome2 = ChromeStub([page])
    monkeypatch.setattr(fill, "relay_for", lambda sctx: chrome2)
    await worker.work_directory(sctx, "yell", decider=scripted({"action": "type", "ref": "e3", "value": "SW1A 1AA", "note": "postcode"},
                                                               {"action": "need_owner", "reason": "done testing"}))
    assert chrome2.actions == [("fill", (("e3", "SW1A 1AA"),))]   # `type` goes through fill, with the DOM fallback behind it


@pytest.mark.asyncio
async def test_owner_can_stop_a_run_from_the_page(se_db, monkeypatch):
    await _ready(se_db)
    sctx = await load_store_context(se_db)
    chrome = ChromeStub([SIGNUP])
    monkeypatch.setattr(fill, "relay_for", lambda sctx: chrome)

    async def stop_then_click(sctx_, run, values, account, snapshot, page):
        await listing_store.save_listing(se_db, "freeindex", status="skipped")   # the owner presses Skip mid-run
        return Decision(action="click", ref="e5", note="go on")
    outcome = await worker.work_directory(sctx, "freeindex", decider=stop_then_click)
    assert outcome["state"] == "stopped" and chrome.actions == [("click", "e5")]
    assert (await listing_store.get_listing(se_db, "freeindex"))["status"] == "skipped"
    assert (await listing_store.list_steps(se_db, "freeindex"))[-1]["result"] == "stopped"


@pytest.mark.asyncio
async def test_a_closed_tab_is_found_again_or_reopened(se_db, monkeypatch):
    from services.social_engine.errors import ExternalServiceError

    await _ready(se_db)
    sctx = await load_store_context(se_db)
    chrome = ChromeStub([FORM, LIVE], tabs=[{"tabId": "t9", "url": "https://www.freeindex.co.uk/dashboard"}])
    calls = {"n": 0}
    real_snapshot = chrome.snapshot

    async def flaky_snapshot(tab, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ExternalServiceError("chrome_relay", 'Chrome relay snapshot failed: tab not found: browser tab "t9" not found')
        return await real_snapshot(tab, **kw)
    chrome.snapshot = flaky_snapshot
    monkeypatch.setattr(fill, "relay_for", lambda sctx: chrome)
    outcome = await worker.work_directory(sctx, "freeindex", decider=scripted(
        {"action": "click", "ref": "e5", "note": "submit"},
        {"action": "done", "listing_url": "https://www.freeindex.co.uk/profile(bio-luxe-lab)_123.htm", "note": "live"}))
    assert outcome["state"] == "live" and chrome.opened == []          # the open directory tab was reused
    steps = await listing_store.list_steps(se_db, "freeindex")
    assert any(s["result"] == "recovered" and "tab was closed" in s["note"] for s in steps)


@pytest.mark.asyncio
async def test_covered_buttons_and_uneditable_fields_fall_back_to_the_dom(se_db, monkeypatch):
    from services.social_engine.errors import ExternalServiceError

    await _ready(se_db)
    sctx = await load_store_context(se_db)
    chrome = ChromeStub([SIGNUP, FORM, LIVE])

    async def covered_click(tab, ref):
        chrome.actions.append(("click", ref))
        raise ExternalServiceError("chrome_relay", 'Element "e5" is not interactable (covered; another element intercepts pointer events).')

    async def uneditable_fill(tab, fields, *, scratch_dir):
        chrome.actions.append(("fill", tuple((f["ref"], f["value"]) for f in fields)))
        raise ExternalServiceError("chrome_relay", 'Element "e2" is not editable (for example, read-only). Use an editable control.')
    chrome.click = covered_click
    chrome.fill = uneditable_fill
    monkeypatch.setattr(fill, "relay_for", lambda sctx: chrome)
    outcome = await worker.work_directory(sctx, "freeindex", decider=scripted(
        {"action": "fill", "values": [{"ref": "e1", "value": "a@b.com"}, {"ref": "e2", "value": "{{password}}"}], "note": "register"},
        {"action": "click", "ref": "e5", "note": "create"},
        {"action": "done", "listing_url": "https://www.freeindex.co.uk/profile(bio-luxe-lab)_123.htm", "note": "live"}))
    assert outcome["state"] == "live"
    kinds = [a[0] for a in chrome.actions]
    assert kinds == ["fill", "fill", "js_set", "fill", "js_set", "click", "scroll", "click", "js_click"]
    password = chrome.actions[4][2]
    assert password != "{{password}}" and (await listing_store.get_account(se_db, "freeindex"))
    steps = await listing_store.list_steps(se_db, "freeindex")
    assert "via DOM" in steps[1]["result"] and "via DOM click" in steps[2]["result"]
    assert password not in steps[1]["value"] + steps[1]["result"]


def test_mail_search_words_use_the_brand_not_the_catalogue_name():
    assert mail.search_words(get_directory("hotfrog")) == ["hotfrog"]          # not "Hotfrog UK": the mail comes "via Localboard"
    assert mail.search_words(get_directory("trustpilot")) == ["trustpilot"]
    assert mail.search_words(get_directory("linkedin_company")) == ["linkedin"]
    assert mail.search_words(get_directory("apple_business_connect"))[0] == "businessconnect"


@pytest.mark.asyncio
async def test_repeating_the_same_button_on_a_changing_page_is_progress_not_a_loop(se_db, monkeypatch):
    await _ready(se_db)
    sctx = await load_store_context(se_db)
    step1 = Snapshot(url="https://www.freeindex.co.uk/add", text='- heading "Step 1"\n- button "Next" [ref=e1]', refs={"e1": {"role": "button", "name": "Next"}})
    step2 = Snapshot(url="https://www.freeindex.co.uk/add", text='- heading "Step 2"\n- button "Next" [ref=e1]', refs={"e1": {"role": "button", "name": "Next"}})
    step3 = Snapshot(url="https://www.freeindex.co.uk/add", text='- heading "Step 3"\n- button "Next" [ref=e1]', refs={"e1": {"role": "button", "name": "Next"}})
    chrome = ChromeStub([step1, step2, step3, LIVE])
    monkeypatch.setattr(fill, "relay_for", lambda sctx: chrome)
    outcome = await worker.work_directory(sctx, "freeindex", decider=scripted(
        {"action": "click", "ref": "e1", "note": "next"}, {"action": "click", "ref": "e1", "note": "next"}, {"action": "click", "ref": "e1", "note": "next"},
        {"action": "done", "listing_url": "https://www.freeindex.co.uk/profile(bio-luxe-lab)_123.htm", "note": "live"}))
    assert outcome["state"] == "live" and chrome.actions == [("click", "e1")] * 3
