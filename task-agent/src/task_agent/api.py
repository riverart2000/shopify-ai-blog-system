"""Private HTTP API for the Shopify admin app's Task Agent page. Contract: ../../API.md.

Served by the worker process on 127.0.0.1 only, behind a shared key. Passwords are write-only.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from task_agent import runtime
from task_agent.accounts import AccountError, AccountStore, LoginWindows
from task_agent.browser import BrowserHub
from task_agent.chrome import make_browser
from task_agent.config import Settings
from task_agent.db import JOB_STATUSES, Job, JobError, JobStore, now
from task_agent.email_account import EmailError, EmailStore
from task_agent.event_types import EventTypeError, EventTypeStore
from task_agent.local_model import model_file
from task_agent.promotion import PromotionStore, target_done, target_retry, target_skip
from task_agent.tasks import TASKS, TEMPLATE_TYPES
from task_agent.telegram.client import TelegramAPI, TelegramError
from task_agent.telegram.store import TelegramStore
from task_agent.templates import TemplateError, TemplateStore, default_title, render
from task_agent.tools.web import BlockedURL, check_public_url

NON_CHAT_MODELS = ("tts", "whisper", "bge", "embed", "asr", "rerank")


@dataclass
class AppContext:
    store: JobStore
    settings: Settings
    templates: TemplateStore
    accounts: AccountStore
    windows: LoginWindows
    email: EmailStore
    telegram: TelegramStore
    browser: BrowserHub
    promotions: PromotionStore
    event_types: EventTypeStore

    @classmethod
    def build(cls, store: JobStore, settings: Settings, browser: BrowserHub | None = None) -> AppContext:
        accounts = AccountStore(store, settings.path(settings.profiles_dir))
        browser = browser or make_browser(settings, accounts.shared_profile)
        return cls(
            store=store,
            settings=settings,
            templates=TemplateStore(store),
            accounts=accounts,
            windows=LoginWindows(accounts, browser, settings.login_window_minutes),
            email=EmailStore(store),
            telegram=TelegramStore(store, settings),
            browser=browser,
            promotions=PromotionStore(store),
            event_types=EventTypeStore(store),
        )


# ── Request bodies ──────────────────────────────────────────────────────────


class JobEdit(BaseModel):
    """A task's details as the New task form sends them: the template and the blanks filled in."""

    template_key: str
    values: dict[str, Any] = Field(default_factory=dict)
    title: str = Field("", max_length=120)
    priority: int = Field(0, ge=-10, le=10)


class JobCreate(JobEdit):
    clone_of: int | None = None


class BrowserOpen(BaseModel):
    url: str = Field("", max_length=1_000)


class TemplatePreview(BaseModel):
    template_key: str
    values: dict[str, Any] = Field(default_factory=dict)


class TemplateSave(BaseModel):
    key: str | None = None
    name: str = Field(max_length=120)
    description: str = Field("", max_length=500)
    task_type: str
    prompt: str = Field(max_length=4_000)
    defaults: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class Decision(BaseModel):
    decision: str


class AccountSave(BaseModel):
    site: str | None = None
    label: str = Field(max_length=80)
    login_url: str = Field(max_length=500)
    username: str = Field("", max_length=200)
    password: str = Field("", max_length=500)
    notes: str = Field("", max_length=1_000)
    event_type: str | None = Field(None, max_length=60)  # a type key, "" = shared by every type, None = unchanged


class EventTypeSave(BaseModel):
    key: str | None = None
    name: str | None = Field(None, max_length=80)
    email: str | None = Field(None, max_length=200)
    imap_host: str | None = Field(None, max_length=200)
    imap_port: int | None = None
    imap_username: str | None = Field(None, max_length=200)
    imap_password: str = Field("", max_length=500)  # "" keeps the saved one
    facebook_page: str | None = Field(None, max_length=500)
    linkedin_page: str | None = Field(None, max_length=500)
    tag_handles: str | None = Field(None, max_length=1_000)
    audience: str | None = Field(None, max_length=3_000)
    enabled: bool | None = None


class TelegramSave(BaseModel):
    token: str = Field("", max_length=200)
    owner_username: str | None = Field(None, max_length=64)


class EmailSave(BaseModel):
    username: str = Field("", max_length=200)
    from_name: str = Field("", max_length=120)
    imap_host: str = Field("", max_length=200)
    imap_port: int = 993
    smtp_host: str = Field("", max_length=200)
    smtp_port: int = 587
    password: str = Field("", max_length=500)


# ── Views ───────────────────────────────────────────────────────────────────


def job_summary(job: Job, step_count: int = 0) -> dict[str, Any]:
    return {
        "id": job.id,
        "type": job.type,
        "title": job.title,
        "status": job.status,
        "priority": job.priority,
        "attempts": job.attempts,
        "run_at": job.run_at,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error": job.error,
        "template_key": job.template_key,
        "created_by": job.created_by,
        "cancel_requested": job.cancel_requested,
        "step_count": step_count,
        "parent_id": job.parent_id,
    }


def summaries(store: JobStore, jobs: list[Job]) -> list[dict[str, Any]]:
    counts = store.step_counts([j.id for j in jobs])
    return [job_summary(j, counts.get(j.id, 0)) for j in jobs]


def approval_view(row: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "job_id", "job_title", "action", "preview", "status", "created_at", "decided_at", "decided_by")
    return {k: row.get(k) for k in keys}


def worker_status(ctx: AppContext) -> dict[str, Any]:
    state = ctx.store.worker_state()
    rt = runtime.current(ctx.settings, ctx.store)
    stale_after = max(60, ctx.settings.poll_seconds * 3)
    if not state or (now() - datetime.fromisoformat(state["last_seen_at"])).total_seconds() > stale_after:
        label = "offline"
    elif state.get("current_job_id"):
        label = "running"
    elif rt.paused:
        label = "paused"
    else:
        label = "idle"
    current = None
    if state and state.get("current_job_id") and label == "running":
        current = {"id": state["current_job_id"], "title": state.get("current_job_title") or ""}
    return {"state": label, "last_seen_at": state["last_seen_at"] if state else None, "current_job": current}


async def available_models(settings: Settings, current: str) -> list[str]:
    models: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{settings.llm_base_url.rstrip('/')}/models")
            response.raise_for_status()
            models = [m["id"] for m in response.json().get("data", []) if isinstance(m, dict) and m.get("id")]
    except (httpx.HTTPError, ValueError, KeyError):
        models = []
    models = [m for m in models if not any(word in m.lower() for word in NON_CHAT_MODELS)]
    if current not in models:
        models.insert(0, current)
    return models


def _validation_message(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())


# ── App ─────────────────────────────────────────────────────────────────────


def create_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(title="Task Agent", docs_url=None, redoc_url=None, openapi_url=None)

    def require_key(x_api_key: str = Header("")) -> None:
        expected = ctx.settings.task_agent_api_key.get_secret_value()
        if not expected:
            raise HTTPException(503, "The Task Agent has no API key set (TASK_AGENT_API_KEY in task-agent/.env).")
        if not hmac.compare_digest(x_api_key.encode(), expected.encode()):
            raise HTTPException(401, "Wrong Task Agent API key.")

    @app.exception_handler(LookupError)
    async def not_found(_: Request, exc: LookupError) -> JSONResponse:
        return JSONResponse({"detail": str(exc).strip("'\"")}, status_code=404)

    @app.exception_handler(ValidationError)
    async def invalid(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse({"detail": _validation_message(exc)}, status_code=400)

    for error_type in (JobError, TemplateError, AccountError, EmailError, EventTypeError):

        @app.exception_handler(error_type)
        async def bad_request(_: Request, exc: Exception) -> JSONResponse:
            return JSONResponse({"detail": str(exc)}, status_code=400)

    auth = [Depends(require_key)]

    # Status ------------------------------------------------------------------

    @app.get("/api/status", dependencies=auth)
    async def status() -> dict[str, Any]:
        rt = runtime.current(ctx.settings, ctx.store)
        jobs, _ = ctx.store.search(limit=5, include_children=False)
        return {
            "worker": worker_status(ctx),
            "model": rt.llm_model,
            "thinking": rt.llm_thinking,
            "counts": ctx.store.counts(),
            "pending_approvals": ctx.store.approval_counts()["pending"],
            "recent_jobs": summaries(ctx.store, jobs),
        }

    # Jobs ----------------------------------------------------------------------

    @app.get("/api/jobs", dependencies=auth)
    async def list_jobs(
        status: str = "", q: str = "", page: int = 1, page_size: int = 20, include_children: bool = False
    ) -> dict[str, Any]:
        wanted = [s for s in (part.strip() for part in status.split(",")) if s]
        unknown = [s for s in wanted if s not in JOB_STATUSES]
        if unknown:
            raise JobError(f"Unknown status: {', '.join(unknown)}")
        page, page_size = max(1, page), min(max(1, page_size), 100)
        jobs, total = ctx.store.search(
            wanted or None, q, limit=page_size, offset=(page - 1) * page_size, include_children=include_children
        )
        return {
            "jobs": summaries(ctx.store, jobs),
            "total": total,
            "page": page,
            "page_size": page_size,
            "counts": ctx.store.counts(),
        }

    def job_event_type(job: Any) -> dict[str, str] | None:
        key = str((job.payload or {}).get("event_type") or "")
        if not key:
            return None
        return {"key": key, "name": ctx.event_types.names().get(key, key)}

    @app.get("/api/jobs/{job_id}", dependencies=auth)
    async def get_job(job_id: int) -> dict[str, Any]:
        job = ctx.store.require(job_id)
        steps = ctx.store.steps(job_id)
        detail = {
            **job_summary(job, len(steps)),
            "payload": job.payload,
            "result": job.result,
            "tools_allowed": job.tools_allowed,
            "template_values": job.template_values,
            "promotion": ctx.promotions.promotion_view(job) if job.type == "promote_event" else None,
            "event_type": job_event_type(job),
        }
        keys = ("n", "tool", "args", "result_summary", "tokens", "duration_ms", "created_at")
        return {"job": detail, "steps": [{k: s.get(k) for k in keys} for s in steps]}

    def job_fields(body: JobEdit, *, clone_of: int | None = None) -> dict[str, Any]:
        """Turn the form's template and values into the job's title, payload, tools and saved values."""
        template = ctx.templates.get(body.template_key)
        if not template.enabled:
            raise TemplateError(f"The template '{template.name}' is switched off.")
        spec = TASKS[template.task_type]
        values = dict(body.values)
        if clone_of is not None:
            ctx.store.require(clone_of)
            if "reuse_from" in spec.value_fields:
                values["reuse_from"] = clone_of
        title, payload = ctx.templates.build_job(template, values, body.title)
        saved = {k: v for k, v in body.values.items() if k != "reuse_from"}
        if body.title.strip():
            saved["_title"] = body.title.strip()  # copied by Clone only when Joe typed it
        return {
            "type": spec.type, "title": title, "payload": payload, "tools_allowed": spec.tool_names,
            "priority": body.priority, "template_key": template.key, "template_values": saved,
        }  # fmt: skip

    @app.post("/api/jobs", dependencies=auth)
    async def create_job(body: JobCreate) -> dict[str, Any]:
        fields = job_fields(body, clone_of=body.clone_of)
        job_id, _ = ctx.store.enqueue(
            fields["type"],
            fields["title"],
            fields["payload"],
            fields["tools_allowed"],
            priority=fields["priority"],
            created_by="admin",
            template_key=fields["template_key"],
            template_values=fields["template_values"],
        )
        paused = runtime.current(ctx.settings, ctx.store).paused
        message = (
            f"Queued task {job_id}. The worker is paused, so it starts when you resume it."
            if paused
            else f"Queued task {job_id}. It starts as soon as the worker is free."
        )
        return {"job": summaries(ctx.store, [ctx.store.require(job_id)])[0], "message": message}

    @app.post("/api/jobs/{job_id}/edit", dependencies=auth)
    async def edit_job(job_id: int, body: JobEdit) -> dict[str, Any]:
        """Change a task that hasn't started (queued, failed or cancelled). It keeps its number and is queued again."""
        job = ctx.store.editable(job_id)
        if job.type != TASKS[ctx.templates.get(body.template_key).task_type].type:
            raise JobError(f"Task {job_id} is a different kind of task from the template '{body.template_key}'.")
        fields = job_fields(body)
        fields.pop("type")
        saved = ctx.store.replace(job_id, **fields)
        paused = runtime.current(ctx.settings, ctx.store).paused
        message = (
            f"Saved task {job_id}. The worker is paused, so it starts when you resume it."
            if paused
            else f"Saved task {job_id}. It starts as soon as the worker is free."
        )
        return {"job": summaries(ctx.store, [saved])[0], "message": message}

    @app.post("/api/jobs/{job_id}/cancel", dependencies=auth)
    async def cancel_job(job_id: int) -> dict[str, Any]:
        job = ctx.store.cancel(job_id)
        message = (
            f"Task {job_id} will stop at its next step." if job.status == "running" else f"Task {job_id} is cancelled."
        )
        return {"job": summaries(ctx.store, [job])[0], "message": message}

    @app.post("/api/jobs/{job_id}/retry", dependencies=auth)
    async def retry_job(job_id: int) -> dict[str, Any]:
        job = ctx.store.retry(job_id)
        return {"job": summaries(ctx.store, [job])[0], "message": f"Task {job_id} is back in the queue."}

    # Templates -----------------------------------------------------------------

    @app.get("/api/templates", dependencies=auth)
    async def list_templates() -> dict[str, Any]:
        options = ctx.templates.field_options()
        return {
            "templates": [t.to_dict(options) for t in ctx.templates.list()],
            "task_types": [
                {"type": s.type, "summary": s.summary, "prompt_field": s.prompt_field}
                for s in TASKS.values()
                if s.type in TEMPLATE_TYPES
            ],
        }

    @app.post("/api/templates/preview", dependencies=auth)
    async def preview_template(body: TemplatePreview) -> dict[str, Any]:
        template = ctx.templates.get(body.template_key)
        text, missing = render(template.prompt, body.values)
        return {"title": default_title(template, body.values, text)[:120], "prompt": text, "missing": missing}

    @app.post("/api/templates", dependencies=auth)
    async def save_template(body: TemplateSave) -> dict[str, Any]:
        template = ctx.templates.save(
            key=body.key,
            name=body.name,
            description=body.description,
            task_type=body.task_type,
            prompt=body.prompt,
            defaults=body.defaults,
            enabled=body.enabled,
        )
        return {"template": template.to_dict(), "message": f"Saved the template '{template.name}'."}

    @app.post("/api/templates/{key}/delete", dependencies=auth)
    async def delete_template(key: str) -> dict[str, Any]:
        name = ctx.templates.get(key).name
        ctx.templates.delete(key)
        return {"message": f"Deleted the template '{name}'."}

    # Approvals -----------------------------------------------------------------

    @app.get("/api/approvals", dependencies=auth)
    async def list_approvals(status: str = "pending") -> dict[str, Any]:
        if status not in ("pending", "approved", "rejected", "all"):
            raise JobError("Status must be pending, approved, rejected or all.")
        rows = ctx.store.approvals(None if status == "all" else status)
        return {"approvals": [approval_view(r) for r in rows], "counts": ctx.store.approval_counts()}

    @app.post("/api/approvals/{approval_id}/decide", dependencies=auth)
    async def decide(approval_id: int, body: Decision) -> dict[str, Any]:
        row = ctx.store.decide_approval(approval_id, body.decision, by="admin")
        return {"approval": approval_view(row), "message": "Approved." if row["status"] == "approved" else "Rejected."}

    # Settings ------------------------------------------------------------------

    @app.get("/api/settings", dependencies=auth)
    async def get_settings() -> dict[str, Any]:
        rt = runtime.current(ctx.settings, ctx.store)
        return {
            "settings": rt.model_dump(),
            "limits": {k: list(v) for k, v in runtime.LIMITS.items()},
            "models": await available_models(ctx.settings, rt.llm_model),
            "recommended_model": rt.llm_model,  # the shared model (older pages compare against it)
            "model_file": str(model_file()),
        }

    @app.post("/api/settings", dependencies=auth)
    async def save_settings(body: dict[str, Any]) -> dict[str, Any]:
        try:
            rt = runtime.save(ctx.settings, ctx.store, body)
        except ValidationError:
            raise
        except ValueError as exc:
            raise JobError(str(exc)) from exc
        if set(body) == {"paused"}:
            message = (
                "The worker is paused. A task already running finishes first."
                if rt.paused
                else "The worker is running again."
            )
        else:
            message = "Settings saved. New tasks use them."
        return {"settings": rt.model_dump(), "message": message}

    # Site logins and email -------------------------------------------------------

    def account_view(account: dict[str, Any]) -> dict[str, Any]:
        return {**account, "login_window_open": ctx.windows.is_open(account["site"])}

    @app.get("/api/accounts", dependencies=auth)
    async def list_accounts() -> dict[str, Any]:
        return {
            "accounts": [account_view(a) for a in ctx.accounts.list()],
            "email": ctx.email.get(),
            "browser": ctx.browser.status(),
        }

    @app.post("/api/accounts", dependencies=auth)
    async def save_account(body: AccountSave) -> dict[str, Any]:
        account = ctx.accounts.save(
            site=body.site,
            label=body.label,
            login_url=body.login_url,
            username=body.username,
            password=body.password,
            notes=body.notes,
            event_type=body.event_type,
        )
        return {"account": account_view(account), "message": f"Saved the login for {account['label']}."}

    @app.post("/api/accounts/{site}/delete", dependencies=auth)
    async def delete_account(site: str) -> dict[str, Any]:
        if ctx.windows.is_open(site):
            raise HTTPException(409, "Close the login tab on the Mac first.")
        label = ctx.accounts.get(site)["label"]
        ctx.accounts.delete(site)
        return {
            "message": (
                f"Removed the login for {label} and its saved password. The shared browser may still be logged "
                "in there: log out in the browser if you want that gone too."
            )
        }

    @app.post("/api/accounts/{site}/login-window", dependencies=auth)
    async def open_login_window(site: str) -> dict[str, Any]:
        if ctx.windows.is_open(site):
            raise HTTPException(409, "A login tab for this site is already open on the Mac.")
        ctx.windows.open(site)
        account = ctx.accounts.get(site)
        return {
            "account": account_view(account),
            "message": (
                f"{account['label']}'s login page is opening in Chrome on the Mac. Log in there (including any "
                "2-step code), then close the tab. Every task can use the login after that."
            ),
        }

    # Shared browser and promotion places ------------------------------------------

    @app.get("/api/browser", dependencies=auth)
    async def browser_status() -> dict[str, Any]:
        return {"browser": ctx.browser.status()}

    @app.post("/api/browser/open", dependencies=auth)
    async def browser_open(body: BrowserOpen | None = None) -> dict[str, Any]:
        url = (body.url if body else "").strip() or "https://www.google.com/"
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            await check_public_url(url)
        except BlockedURL as exc:
            raise JobError(str(exc)) from exc
        await ctx.windows.show("manual", url, ctx.settings.login_window_minutes)
        return {
            "browser": ctx.browser.status(),
            "message": (
                "The page is open in Chrome on the Mac. Log in to any sites you like; every task can use those "
                f"logins. Close the tab when you're done (it closes itself after {ctx.settings.login_window_minutes} "
                "minutes)."
            ),
        }

    def target_view(target: dict[str, Any]) -> dict[str, Any]:
        return PromotionStore.view(target)

    @app.post("/api/targets/{target_id}/done", dependencies=auth)
    async def target_is_done(target_id: int) -> dict[str, Any]:
        target, message = await target_done(ctx.store, target_id, ctx.browser)
        return {"target": target_view(target), "message": message}

    @app.post("/api/targets/{target_id}/skip", dependencies=auth)
    async def target_is_skipped(target_id: int) -> dict[str, Any]:
        target, message = await target_skip(ctx.store, target_id, ctx.browser)
        return {"target": target_view(target), "message": message}

    @app.post("/api/targets/{target_id}/retry", dependencies=auth)
    async def target_again(target_id: int) -> dict[str, Any]:
        target, message = await target_retry(ctx.store, target_id, ctx.settings)
        return {"target": target_view(target), "message": message}

    # Event types -----------------------------------------------------------------

    def event_type_view(kind: dict[str, Any], logins: list[dict[str, Any]]) -> dict[str, Any]:
        return {**kind, "logins": [account_view(a) for a in logins if a["event_type"] == kind["key"]]}

    @app.get("/api/event-types", dependencies=auth)
    async def list_event_types() -> dict[str, Any]:
        logins = ctx.accounts.list()
        return {
            "event_types": [event_type_view(k, logins) for k in ctx.event_types.list()],
            "shared_logins": [account_view(a) for a in logins if not a["event_type"]],
        }

    @app.post("/api/event-types", dependencies=auth)
    async def save_event_type(body: EventTypeSave) -> dict[str, Any]:
        values = body.model_dump(exclude={"key", "imap_password"}, exclude_none=True)
        kind = ctx.event_types.save(values, imap_password=body.imap_password, key=body.key)
        return {
            "event_type": event_type_view(kind, ctx.accounts.list()),
            "message": f"Saved the event type {kind['name']}.",
        }

    @app.post("/api/event-types/{key}/delete", dependencies=auth)
    async def delete_event_type(key: str) -> dict[str, Any]:
        name = ctx.event_types.get(key)["name"]
        ctx.event_types.delete(key)
        return {"message": f"Removed the event type {name}, its inbox password and its own logins."}

    @app.post("/api/event-types/{key}/test-email", dependencies=auth)
    async def test_event_type_email(key: str) -> dict[str, Any]:
        ok, message = await ctx.event_types.test_email(key)
        return {
            "ok": ok,
            "message": message,
            "event_type": event_type_view(ctx.event_types.get(key), ctx.accounts.list()),
        }

    @app.post("/api/email", dependencies=auth)
    async def save_email(body: EmailSave) -> dict[str, Any]:
        values = body.model_dump(exclude={"password"})
        email = ctx.email.save(values, password=body.password)
        return {"email": email, "message": "Saved the email account."}

    @app.post("/api/email/test", dependencies=auth)
    async def test_email() -> dict[str, Any]:
        ok, message = await ctx.email.test()
        return {"ok": ok, "message": message, "email": ctx.email.get()}

    # Telegram ------------------------------------------------------------------

    def telegram_view() -> dict[str, Any]:
        tg = ctx.telegram
        user_id, _ = tg.owner()
        return {
            "configured": bool(tg.token()),
            "bot_username": tg.get("bot_username"),
            "owner_username": tg.owner_username(),
            "linked": user_id is not None,
            "linked_username": tg.get("owner_linked_username"),
            "linked_at": tg.get("owner_linked_at"),
            "summary_hour": ctx.settings.telegram_summary_hour,
            "social_connected": bool(ctx.settings.social_api_key.get_secret_value()),
            "last_error": tg.get("last_error"),
        }

    @app.get("/api/telegram", dependencies=auth)
    async def telegram_status() -> dict[str, Any]:
        return {"telegram": telegram_view()}

    @app.post("/api/telegram", dependencies=auth)
    async def telegram_save(body: TelegramSave) -> dict[str, Any]:
        tg = ctx.telegram
        notes = []
        token = body.token.strip()
        if token:
            try:
                me = await TelegramAPI(token).get_me()
            except TelegramError as exc:
                raise JobError(f"Telegram didn't accept that token: {exc}") from exc
            tg.save_token(token)
            tg.set("bot_username", me.get("username"))
            notes.append(f"Connected to @{me.get('username')}.")
        if body.owner_username is not None:
            wanted = body.owner_username.strip().lstrip("@")
            if wanted.lower() != tg.owner_username().lower():
                tg.set("owner_username", wanted)
                tg.unlink_owner()
                notes.append(f"Only @{wanted} can use the bot." if wanted else "No one can use the bot.")
        user_id, _ = tg.owner()
        if tg.token() and user_id is None and tg.owner_username():
            notes.append(f"Now open @{tg.get('bot_username')} in Telegram as @{tg.owner_username()} and send /start.")
        return {"telegram": telegram_view(), "message": " ".join(notes) or "Saved."}

    @app.post("/api/telegram/test", dependencies=auth)
    async def telegram_test() -> dict[str, Any]:
        tg = ctx.telegram
        _, chat = tg.owner()
        if not tg.token():
            raise JobError("Add the bot token first.")
        if chat is None:
            raise JobError(f"Send /start to the bot from @{tg.owner_username()} first.")
        try:
            await TelegramAPI(tg.token()).send_message(chat, "👋 Test from the Task Agent page. All working.")
        except TelegramError as exc:
            raise JobError(str(exc)) from exc
        return {"telegram": telegram_view(), "message": "Sent. Check Telegram."}

    @app.post("/api/telegram/unlink", dependencies=auth)
    async def telegram_unlink() -> dict[str, Any]:
        ctx.telegram.unlink_owner()
        return {
            "telegram": telegram_view(),
            "message": f"Unlinked. The next /start from @{ctx.telegram.owner_username()} links it again.",
        }

    @app.post("/api/telegram/disconnect", dependencies=auth)
    async def telegram_disconnect() -> dict[str, Any]:
        ctx.telegram.clear_token()
        ctx.telegram.unlink_owner()
        return {"telegram": telegram_view(), "message": "Disconnected. The bot token was removed from the Keychain."}

    return app
