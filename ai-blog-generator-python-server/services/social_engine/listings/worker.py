"""The directory employee: works one listing end to end in the owner's Chrome and writes down every step.

Each turn: snapshot the page, ask the model (Gemma, Grok as fallback) for ONE
action, check it against the rules, do it through the relay, log it. It creates
the account when a site needs one (password from the vault, never shown to the
model or the log), fills from the profile, submits, reads verification mail
from Gmail, and ends with the live URL, "submitted" (pending review) or
"needs_owner" with a plain reason: a CAPTCHA, a phone call, a postcard, a
payment page, or being stuck.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from db.social_engine import listings as listing_store

from ..activity import record
from ..ai.base import LLMRequest
from ..ai.llm import get_router
from ..context import StoreContext
from ..errors import ConfigurationError, ExternalServiceError, ValidationError, describe
from . import fill as fill_mod
from . import mail as mail_mod
from . import vault
from .catalogue import BY_ID, Directory, get_directory
from .profile import missing_fields, seeded_profile, values_for
from .relay import Snapshot

logger = logging.getLogger("ai_blog_server.social_engine.listings")

PASSWORD_TOKEN = "{{password}}"
MASK = "••••••••"
PAYMENT = re.compile(r"card number|cardholder|cvc|cvv|expiry|sort code|account number|iban|billing|credit card|debit card|"
                     r"pay now|checkout|upgrade now|buy now|direct debit", re.I)
IDENTITY = re.compile(r"passport|driving licence|national insurance|date of birth|\bdob\b|utr\b", re.I)
CAPTCHA = re.compile(r"captcha|i'm not a robot|i am not a robot|hcaptcha|recaptcha", re.I)
ADDRESS = re.compile(r"address|street|postcode|post code|zip|town|city|locality|building|premises|unit number|flat|house number", re.I)
POSTCODE = re.compile(r"\b[A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2}\b", re.I)
ACTIONS = ("click", "type", "fill", "select", "press", "navigate", "wait", "check_email", "done", "submitted", "need_owner")
WORKABLE = ("todo", "working", "filled", "needs_owner", "submitted", "verifying", "broken")


class _FieldValue(BaseModel):
    ref: str
    value: str = ""


class Decision(BaseModel):
    action: Literal["click", "type", "fill", "select", "press", "navigate", "wait", "check_email", "done", "submitted", "need_owner"]
    ref: str = ""
    value: str = ""
    values: list[_FieldValue] = Field(default_factory=list)
    url: str = ""
    key: str = ""
    note: str = Field("", max_length=400)      # why, one line, shown to the owner
    listing_url: str = ""
    reason: str = Field("", max_length=400)    # for need_owner: what the owner must do


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())[:20] or "business"


def trim_snapshot(snapshot: Snapshot, *, max_chars: int = 14000) -> str:
    """The page as the model sees it: lines with a name, text or a ref; nothing purely structural."""
    out: list[str] = []
    size = 0
    for raw in snapshot.text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("/url:"):
            continue
        if "[ref=" not in line and '"' not in line and "text:" not in stripped and not stripped.startswith("- heading"):
            continue
        if re.match(r"^\s*-\s+generic\s*(\[ref=[^\]]+\])?:?\s*$", line):
            continue
        line = line[:200]
        out.append(line)
        size += len(line) + 1
        if size > max_chars:
            out.append("… (page cut here)")
            break
    return "\n".join(out)


def eligible(directory: Directory, listing: dict | None, *, profile_ready: bool, has_address: bool, business_type: str) -> str:
    """'' when the worker may take this directory, else the reason it may not."""
    status = (listing or {}).get("status") or "todo"
    if directory.fill != "relay":
        return f"{directory.name} is not worked through Chrome; follow its note."
    if directory.requires_company and business_type != "limited_company":
        return f"{directory.name} is for limited companies only."
    if directory.requires_address and not has_address:
        return f"{directory.name} needs a public address; add one to the profile or skip it."
    if not profile_ready:
        return "Fill in the profile first."
    if status == "live":
        return f"{directory.name} is already live."
    if status == "skipped":
        return f"{directory.name} is skipped; un-skip it first."
    return ""


class _Run:
    def __init__(self, sctx: StoreContext, directory: Directory) -> None:
        self.sctx = sctx
        self.directory = directory
        self.run_id = uuid.uuid4().hex[:12]
        self.step = 0
        self.history: list[str] = []
        self.signatures: list[str] = []
        self.facts: dict[str, str] = {}
        self.password = ""
        self.account_saved = False
        self.subject = f"listing:{directory.id}"

    async def log(self, action: str, *, url: str = "", ref: str = "", label: str = "", value: str = "", note: str = "",
                  result: str = "ok", screenshot: str = "") -> None:
        self.step += 1
        if self.password:
            value = value.replace(self.password, MASK)
            note = note.replace(self.password, MASK)
        value = value.replace(PASSWORD_TOKEN, MASK)
        await listing_store.add_step(self.sctx.store_id, self.directory.id, self.run_id, self.step, action=action, url=url,
                                     ref=ref, label=label, value=value, note=note, result=result, screenshot=screenshot)
        shown = f'{self.step}. {action}'
        if label:
            shown += f' "{label[:60]}"'
        if value:
            shown += f" = {value[:60]}"
        if note:
            shown += f" ({note[:100]})"
        shown += f" → {result[:80]}"
        self.history.append(shown)


def _system_prompt() -> str:
    return (
        "You are a careful employee listing a small UK business on a web directory, working in the owner's own Chrome. "
        "You see the current page as an accessibility snapshot with element refs. Reply with exactly ONE next action as JSON. "
        "Be literal: copy profile text word for word, never invent facts, never promise health results."
    )


def _prompt(run: _Run, values: dict[str, str], account: dict[str, str], snapshot: Snapshot, page: str) -> str:
    d = run.directory
    lines = [
        f'GOAL: get "{values.get("business_name", "")}" listed on {d.name} ({d.url}). {d.why}',
        f"Site notes: {d.notes}" if d.notes else "",
        "Plan: dismiss cookie banners (reject non-essential if offered) → sign in or create a FREE account if the site needs one → "
        "reach the add-business / claim form → fill it from PROFILE → submit → complete email verification → "
        'finish with action "done" and the public URL of the listing. If the site says the listing is pending review or '
        'moderation, finish with action "submitted".',
        "",
        "RULES",
        f"- Account: login email {account['login_email']}; password exactly {PASSWORD_TOKEN} (the engine substitutes the real one); "
        f"username or display name {account['username']} if asked; the account is for the business, owner {values.get('founder_name', '')}.",
        "- If the site says the email already has an account and FACTS do not say an earlier run registered it, the password you have "
        "is NOT that account's password: sign in through the site's email link / magic link / one-time code route (then check_email). "
        "Never guess a password; two rejections → need_owner saying the account pre-exists.",
        "- Free plan only. Never type card, bank or payment details, never pick a paid plan or a trial that wants a card: reply need_owner.",
        "- Never try to solve a CAPTCHA or 'I am not a robot' check: reply need_owner saying so.",
        "- Phone call, SMS code or postcard verification: reply need_owner and say which.",
        '- Email verification code or link needed: reply check_email; the engine reads the inbox and adds the code to FACTS, then type it.',
        "- Required terms or privacy checkboxes may be ticked. Leave marketing and newsletter opt-ins unticked.",
        ("- ADDRESS: the profile has NO public address. Never type, pick or confirm a street address, building or postcode, "
         "and never accept an address the site suggests. If the form cannot be completed without one, reply need_owner. "
         "Where a site offers 'I serve customers at their location' / 'hide my address' / online-only, choose that."
         if not values.get("address_line1") else
         "- ADDRESS: use exactly the profile address; never accept a different suggestion."),
        "- Text fields: copy PROFILE values exactly. Category: the closest option to the profile categories. "
        "Descriptions: the longest profile variant that fits the field's limit.",
        "- Use only refs that are on the current page. Prefer one `fill` with several values over many `type` actions.",
        "- `select` works only on a real <select>. A styled dropdown (combobox, button with a chevron, listbox) is opened with "
        "`click`, then the option is clicked. If an action failed (see FACTS last_error), do not repeat it the same way.",
        "- Do not repeat an action that changed nothing; try another route. Truly stuck: need_owner with what you see.",
        "- Never navigate away from the directory's own site except through its own links.",
        "",
        "PROFILE (JSON)",
        json.dumps({k: v for k, v in values.items() if k not in {"full_address"}}, ensure_ascii=False),
        "",
        "FACTS: " + (json.dumps(run.facts, ensure_ascii=False) if run.facts else "none yet"),
        "",
        "HISTORY (this run, newest last)",
        *(run.history[-14:] or ["(nothing yet)"]),
        "",
        f"PAGE {snapshot.url or page}",
        trim_snapshot(snapshot),
        "",
        'Reply JSON with one of: {"action":"click","ref":"e12","note":"…"} | {"action":"type","ref":"e3","value":"…","note":"…"} | '
        '{"action":"fill","values":[{"ref":"e3","value":"…"},…],"note":"…"} | {"action":"select","ref":"e5","value":"option text or value","note":"…"} | '
        '{"action":"press","key":"Enter","note":"…"} | {"action":"navigate","url":"https://…","note":"…"} | {"action":"wait","value":"3000","note":"…"} | '
        '{"action":"check_email","note":"…"} | {"action":"done","listing_url":"https://…","note":"…"} | {"action":"submitted","note":"…"} | '
        '{"action":"need_owner","reason":"…","note":"…"}',
    ]
    return "\n".join(l for l in lines if l is not None)


async def decide(sctx: StoreContext, run: _Run, values: dict[str, str], account: dict[str, str], snapshot: Snapshot, page: str) -> Decision:
    """One model call for the next action. Separate so tests can script it."""
    request = LLMRequest(system=_system_prompt(), prompt=_prompt(run, values, account, snapshot, page), json_mode=True,
                         temperature=0.1, max_tokens=700)
    decision, meta = await get_router(sctx).structured(
        "listings.work", request, Decision, ctx=sctx.call_context(run.subject), decision_type="listings.work",
        subject_id=f"{run.subject}:{run.run_id}:{run.step + 1}",
    )
    return decision


def _label(snapshot: Snapshot, ref: str) -> str:
    meta = snapshot.refs.get(ref) or {}
    return str(meta.get("name") or "")


def _guard(snapshot: Snapshot, decision: Decision, *, has_address: bool = True, known: dict[str, str] | None = None) -> str:
    """'' when the action may run; otherwise the reason it is refused (recorded, then the worker asks again)."""
    refs = [decision.ref] if decision.ref else []
    refs += [v.ref for v in decision.values]
    writes = decision.action in ("type", "fill", "select")
    for ref in refs:
        if ref and ref not in snapshot.refs:
            return f"ref {ref} is not on this page"
        label = _label(snapshot, ref)
        if PAYMENT.search(label) or IDENTITY.search(label):
            return f"'{label[:60]}' asks for payment or identity details"
        if CAPTCHA.search(label):
            return "that is a CAPTCHA"
        if writes and not has_address and ADDRESS.search(label):
            return f"'{label[:60]}' is an address field and the profile has no public address"
    if writes:
        texts = [decision.value] + [v.value for v in decision.values]
        if any(PAYMENT.search(t) for t in texts if t):
            return "payment text refused"
        if not has_address and any(POSTCODE.search(t) for t in texts if t):
            return "that value contains a postcode and the profile has no public address"
    if decision.action == "click" and not has_address:
        label = _label(snapshot, decision.ref)
        if POSTCODE.search(label) or re.search(r"\d+\s+\w+\s+(road|street|lane|avenue|close|drive|way)\b", label, re.I):
            return f"'{label[:60]}' looks like an address suggestion and the profile has no public address"
    if decision.action == "navigate" and not decision.url.startswith(("http://", "https://")):
        return "navigate needs a full URL"
    return ""


async def work_directory(sctx: StoreContext, directory_id: str, *, tab: str = "", decider=None) -> dict[str, Any]:
    """Run the employee on one directory until it is live, submitted, or needs the owner. Returns the outcome."""
    directory = get_directory(directory_id)
    if not directory:
        raise ValidationError(f"Unknown directory '{directory_id}'.")
    profile = seeded_profile(sctx)
    listing = await listing_store.get_listing(sctx.store_id, directory.id)
    gaps = missing_fields(profile)
    why_not = eligible(directory, listing, profile_ready=not gaps["required"],
                       has_address=bool(profile.address_line1 and profile.postcode), business_type=profile.business_type)
    if why_not:
        raise ValidationError(why_not)
    settings = sctx.settings.listings
    decider = decider or decide
    has_address = bool(profile.address_line1 and profile.postcode)
    run = _Run(sctx, directory)
    values = values_for(profile, directory)
    stored = await listing_store.get_account(sctx.store_id, directory.id)
    account = {
        "login_email": (stored or {}).get("login_email") or settings.account_email or profile.email,
        "username": (stored or {}).get("username") or _slug(profile.business_name),
    }
    run.password = vault.decrypt((stored or {}).get("password_enc") or "") if stored else vault.generate_password()
    run.account_saved = bool(stored)
    if stored:
        run.facts["account"] = (f"an earlier run typed a password for {account['login_email']} on {stored.get('login_url') or 'this site'}; "
                                f"the sign-up may or may not have completed. Try signing in with {PASSWORD_TOKEN} first; if the site rejects it, "
                                "register again with the same email and password; if it then says the email is taken, use the email link / code route.")

    relay = fill_mod.relay_for(sctx)
    scratch = fill_mod._scratch(sctx) / "runs" / run.run_id
    deadline = time.monotonic() + 1500
    await listing_store.save_listing(sctx.store_id, directory.id, status="working",
                                     fill={"state": "running", "run_id": run.run_id, "message": "Working in Chrome…"})
    outcome: dict[str, Any] = {"state": "needs_owner", "reason": "stopped before finishing", "run_id": run.run_id}
    page = directory.submit_url
    try:
        if not tab:
            found = await relay.find_tab(directory.hosts)
            if found:
                tab = str(found.get("tabId") or found.get("suggestedTargetId") or "")
        if not tab:
            tab = await relay.open(directory.submit_url)
        await run.log("note", url=directory.submit_url, note=f"Started on {directory.name}; tab {tab}; login {account['login_email']}")
        refusals = 0
        action_errors = 0
        for _ in range(settings.max_steps_per_run):
            if time.monotonic() > deadline:
                outcome = {"state": "needs_owner", "reason": "Ran out of time for this run; press Continue to carry on.", "run_id": run.run_id}
                break
            current = await listing_store.get_listing(sctx.store_id, directory.id)
            if current and current.get("status") != "working":
                await run.log("note", url=page, note=f"stopped: status changed to {current.get('status')} from the page", result="stopped")
                return {"state": "stopped", "run_id": run.run_id, "status": current.get("status")}
            try:
                snapshot = await relay.snapshot(tab, interactive=False, limit=500)
            except ExternalServiceError as exc:
                if "tab" not in str(exc).lower():
                    raise
                # The site replaced or closed the tab (auth redirects do): pick up the directory's tab again, or reopen.
                found = await relay.find_tab(directory.hosts)
                tab = str((found or {}).get("tabId") or (found or {}).get("suggestedTargetId") or "") or await relay.open(directory.submit_url)
                await run.log("note", url=page, note=f"the tab was closed; continuing in tab {tab}", result="recovered")
                snapshot = await relay.snapshot(tab, interactive=False, limit=500)
            page = snapshot.url or page
            try:
                decision = await decider(sctx, run, values, account, snapshot, page)
            except Exception as exc:
                kind, message = describe(exc)
                await run.log("note", url=page, note="the model could not decide", result=f"error: {message[:200]}")
                outcome = {"state": "needs_owner", "reason": f"The model could not decide the next step ({message[:160]}).", "run_id": run.run_id}
                break
            refused = _guard(snapshot, decision, has_address=has_address)
            if refused:
                refusals += 1
                await run.log(decision.action, url=page, ref=decision.ref, label=_label(snapshot, decision.ref), note=decision.note,
                              result=f"refused: {refused}")
                if "payment" in refused or "CAPTCHA" in refused:
                    outcome = {"state": "needs_owner", "reason": f"The site {refused}; that part is yours.", "run_id": run.run_id}
                    break
                if "address" in refused:
                    outcome = {"state": "needs_owner", "run_id": run.run_id,
                               "reason": "The site insists on a street address and the profile has none. Add a public address to the "
                                         "profile (or a registered/virtual office) and press Continue, or skip this directory."}
                    break
                if refusals >= 4:
                    outcome = {"state": "needs_owner", "reason": "Kept choosing elements that are not on the page.", "run_id": run.run_id}
                    break
                continue
            # "Stuck" means the same action, with the same values, on a page that has not changed: a multi-field fill with
            # different values, or the same Next button on a new step, is progress.
            page_hash = hashlib.sha1(snapshot.text[:4000].encode("utf-8", "replace")).hexdigest()[:10]
            values_sig = ";".join(f"{v.ref}={v.value[:30]}" for v in decision.values)
            signature = f"{decision.action}|{decision.ref}|{decision.value[:40]}|{values_sig}|{decision.url}|{decision.key}|{page_hash}"
            run.signatures.append(signature)
            if len(run.signatures) >= 3 and len(set(run.signatures[-3:])) == 1:
                await run.log("note", url=page, note="the same action three times in a row", result="stopped")
                outcome = {"state": "needs_owner", "reason": f"Stuck repeating '{decision.action}' on {page}.", "run_id": run.run_id}
                break
            try:
                finished = await _execute(run, relay, tab, snapshot, decision, account, scratch, page)
            except ExternalServiceError as exc:
                # One action the relay could not do (a custom dropdown, a detached element) is a step result, not the end:
                # the model sees it in HISTORY and FACTS and tries another way.
                kind, message = describe(exc)
                action_errors += 1
                run.facts["last_error"] = (f"'{decision.action}' on \"{_label(snapshot, decision.ref)[:50]}\" failed: {message[:140]}. "
                                           "Try another way: click the control first, then click the option; or type into it.")
                await run.log(decision.action, url=page, ref=decision.ref, label=_label(snapshot, decision.ref), value=decision.value,
                              note=decision.note, result=f"error: {message[:200]}")
                if action_errors >= 4:
                    outcome = {"state": "needs_owner", "reason": f"Four actions in a row failed on {page}; the last: {message[:160]}", "run_id": run.run_id}
                    break
                continue
            action_errors = 0
            run.facts.pop("last_error", None)
            if finished:
                outcome = finished
                break
        else:
            outcome = {"state": "needs_owner", "reason": f"Used all {settings.max_steps_per_run} steps; press Continue to carry on.", "run_id": run.run_id}
    except (ConfigurationError, ValidationError):
        await listing_store.save_listing(sctx.store_id, directory.id, status="needs_owner", fill={"state": "error", "message": "see activity", "run_id": run.run_id})
        raise
    except Exception as exc:
        kind, message = describe(exc)
        await run.log("note", url=page, note="run failed", result=f"error: {message[:300]}")
        outcome = {"state": "needs_owner", "reason": f"Something broke: {message[:200]}", "run_id": run.run_id}
    await _finish(run, relay, tab, outcome, page, scratch)
    return outcome


async def _execute(run: _Run, relay, tab: str, snapshot: Snapshot, d: Decision, account: dict[str, str], scratch, page: str) -> dict | None:
    """Do one decided action; returns the outcome when the run ends here."""
    label = _label(snapshot, d.ref)
    if d.action == "click":
        how = await _click(relay, tab, d.ref)
        await relay.wait(tab, ms=1500)
        await run.log("click", url=page, ref=d.ref, label=label, note=d.note, result=how)
    elif d.action == "type":
        text = await _substitute(run, d.value, account, page, label)
        how = await _set(relay, tab, d.ref, text, scratch)
        await run.log("type", url=page, ref=d.ref, label=label, value=d.value, note=d.note, result=how)
    elif d.action == "fill":
        fields = []
        for v in d.values:
            fields.append({"ref": v.ref, "value": await _substitute(run, v.value, account, page, _label(snapshot, v.ref))})
        hows = []
        if fields:
            try:
                await relay.fill(tab, fields, scratch_dir=scratch)
                hows.append(f"ok: {len(fields)} fields")
            except ExternalServiceError as exc:
                if not _recoverable(exc):
                    raise
                for f in fields:   # one at a time, each with its own fallback
                    hows.append(await _set(relay, tab, f["ref"], f["value"], scratch))
        await run.log("fill", url=page, ref=",".join(f["ref"] for f in fields)[:60],
                      label="; ".join(_label(snapshot, v.ref)[:30] for v in d.values)[:200],
                      value=" | ".join(v.value[:40] for v in d.values), note=d.note, result="; ".join(hows)[:300] or "ok: 0 fields")
    elif d.action == "select":
        await relay.select(tab, d.ref, [d.value])
        await run.log("select", url=page, ref=d.ref, label=label, value=d.value, note=d.note)
    elif d.action == "press":
        await relay.press(tab, d.key or "Enter")
        await relay.wait(tab, ms=1500)
        await run.log("press", url=page, value=d.key or "Enter", note=d.note)
    elif d.action == "navigate":
        await relay.navigate(tab, d.url)
        await relay.wait(tab, ms=2000)
        await run.log("navigate", url=page, value=d.url, note=d.note)
    elif d.action == "wait":
        ms = min(int(re.sub(r"\D", "", d.value) or 2000), 8000)
        await relay.wait(tab, ms=ms)
        await run.log("wait", url=page, value=str(ms), note=d.note)
    elif d.action == "check_email":
        found = await mail_mod.read_verification(run.sctx, relay, run.directory, subject_id=run.subject)
        if found.get("code"):
            run.facts["verification_code"] = found["code"]
        if found.get("link"):
            run.facts["verification_link"] = found["link"]
            await relay.navigate(tab, found["link"])
            await relay.wait(tab, ms=2500)
        await run.log("check_email", url=page, value=found.get("code") or found.get("link") or "", note=d.note,
                      result=found.get("note") or ("found" if found.get("code") or found.get("link") else "nothing found"))
        if not (found.get("code") or found.get("link")):
            run.facts["mail"] = found.get("note") or "no verification mail yet"
    elif d.action == "done":
        url = d.listing_url.strip() or page
        await run.log("done", url=page, value=url, note=d.note)
        return {"state": "live", "listing_url": url, "run_id": run.run_id}
    elif d.action == "submitted":
        await run.log("submitted", url=page, note=d.note)
        return {"state": "submitted", "run_id": run.run_id, "note": d.note}
    elif d.action == "need_owner":
        await run.log("need_owner", url=page, note=d.note, result=d.reason or d.note)
        return {"state": "needs_owner", "reason": d.reason or d.note or "The worker asked for you.", "run_id": run.run_id}
    return None


def _recoverable(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(k in text for k in ("covered", "not interactable", "intercepts pointer", "not editable", "read-only", "not visible", "outside of the viewport"))


async def _click(relay, tab: str, ref: str) -> str:
    """Click; if an overlay covers the element, scroll to it and retry, then click through the DOM."""
    try:
        await relay.click(tab, ref)
        return "ok"
    except ExternalServiceError as exc:
        if not _recoverable(exc):
            raise
    try:
        await relay.scroll_into_view(tab, ref)
        await relay.click(tab, ref)
        return "ok after scrolling into view"
    except ExternalServiceError:
        pass
    await relay.js_click(tab, ref)
    return "ok via DOM click (something covered it)"


async def _set(relay, tab: str, ref: str, value: str, scratch) -> str:
    """Type into a field; if the relay says it is not editable, set the value through the DOM."""
    try:
        await relay.fill(tab, [{"ref": ref, "value": value}], scratch_dir=scratch)
        return "ok"
    except ExternalServiceError as exc:
        if not _recoverable(exc):
            raise
    result = await relay.js_set_value(tab, ref, value)
    accepted = result.get("result") if isinstance(result, dict) else result
    if accepted is False:
        raise ExternalServiceError("chrome_relay", f"Element {ref} did not accept a value even through the DOM.")
    return "ok via DOM (the relay could not type into it)"


async def _substitute(run: _Run, text: str, account: dict[str, str], page: str, label: str) -> str:
    if PASSWORD_TOKEN in text:
        text = text.replace(PASSWORD_TOKEN, run.password)
        if not run.account_saved:
            await listing_store.save_account(run.sctx.store_id, run.directory.id, login_email=account["login_email"],
                                             username=account["username"], password_enc=vault.encrypt(run.password), login_url=page)
            run.account_saved = True
            run.facts["account"] = f"account created on {page} for {account['login_email']}"
    return text


async def _finish(run: _Run, relay, tab: str, outcome: dict[str, Any], page: str, scratch) -> None:
    d = run.directory
    state = outcome.get("state")
    shot = ""
    if state == "needs_owner" and tab:
        shot = await relay.screenshot(tab, out_dir=scratch)
    if state == "live":
        await listing_store.save_listing(run.sctx.store_id, d.id, status="live", listing_url=outcome["listing_url"],
                                         fill={"state": "done", "run_id": run.run_id, "steps": run.step,
                                               "message": f"Live: {outcome['listing_url']}"})
        await record(run.sctx.store_id, "listing", d.id, f"{d.name}: listed; {outcome['listing_url']}", stage="listings.work",
                     data={"run_id": run.run_id, "steps": run.step}, correlation_id=run.subject)
    elif state == "submitted":
        await listing_store.save_listing(run.sctx.store_id, d.id, status="submitted",
                                         fill={"state": "submitted", "run_id": run.run_id, "steps": run.step,
                                               "message": f"Submitted; the site is reviewing it. {outcome.get('note', '')}".strip()})
        await record(run.sctx.store_id, "listing", d.id, f"{d.name}: submitted, awaiting the site's review", stage="listings.work",
                     correlation_id=run.subject)
    else:
        reason = str(outcome.get("reason") or "needs you")
        await listing_store.save_listing(run.sctx.store_id, d.id, status="needs_owner",
                                         fill={"state": "needs_owner", "run_id": run.run_id, "steps": run.step, "message": reason,
                                               "page": page, "tab": tab, "screenshot": shot})
        await record(run.sctx.store_id, "listing", d.id, f"{d.name}: needs you — {reason}", level="warning", stage="listings.work",
                     data={"run_id": run.run_id, "page": page}, correlation_id=run.subject)


def _lease_alive(owner: str) -> bool:
    """Is the process that holds this lease still running on this machine? ('host:pid:…'; unknown formats count as alive)."""
    import os
    import socket

    parts = str(owner or "").split(":")
    if len(parts) < 2 or not parts[1].isdigit() or parts[0] != socket.gethostname():
        return True
    try:
        os.kill(int(parts[1]), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


async def recover_interrupted(store_id: str) -> list[str]:
    """Listings left 'working' by a run that died (server restart): hand them back with a plain reason.

    Called when the page is built and before the autopilot picks the next directory,
    so a dead run never blocks a directory for good.
    """
    from db.social_engine import jobs as job_store

    rows = await listing_store.list_listings(store_id)
    stuck = [did for did, row in rows.items() if row.get("status") == "working"]
    if not stuck:
        return []
    running = await job_store.list_jobs(store_id=store_id, status="running", job_type="listings.work", limit=50)
    alive: set[str] = set()
    for job in running:
        did = str((job.get("payload") or {}).get("directory_id") or "")
        if did and _lease_alive(str(job.get("lease_owner") or "")):
            alive.add(did)
    recovered: list[str] = []
    for did in stuck:
        if did in alive:
            continue
        fill = (rows[did].get("fill") or {})
        run_id = str(fill.get("run_id") or "")
        message = "The run was interrupted (the server restarted). Press Continue and it carries on from the open tab."
        if run_id:
            steps = await listing_store.list_steps(store_id, did, run_id=run_id, limit=200)
            await listing_store.add_step(store_id, did, run_id, len(steps) + 1, action="note", note="run interrupted", result="interrupted")
        await listing_store.save_listing(store_id, did, status="needs_owner", fill={**fill, "state": "needs_owner", "message": message})
        await record(store_id, "listing", did, f"{BY_ID[did].name if did in BY_ID else did}: run interrupted; press Continue",
                     level="warning", stage="listings.work", correlation_id=f"listing:{did}")
        recovered.append(did)
    return recovered


async def next_directory(sctx: StoreContext) -> Directory | None:
    """The highest-trust directory the worker has not finished with and the owner is not holding."""
    from .catalogue import ranked

    profile = seeded_profile(sctx)
    gaps = missing_fields(profile)
    rows = await listing_store.list_listings(sctx.store_id)
    for directory in ranked():
        listing = rows.get(directory.id)
        status = (listing or {}).get("status") or "todo"
        if status not in ("todo", "filled"):
            continue
        if eligible(directory, listing, profile_ready=not gaps["required"],
                    has_address=bool(profile.address_line1 and profile.postcode), business_type=profile.business_type):
            continue
        return directory
    return None
