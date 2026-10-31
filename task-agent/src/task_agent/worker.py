"""The worker: takes one job at a time from the queue and runs it to a finished state."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import signal
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
import structlog
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pydantic import ValidationError
from pydantic_ai import (
    ModelConnectionError,
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRateLimitError,
    ModelRequestNode,
    ModelRequestTimeout,
    ModelServerError,
    ModelUnavailableError,
    RunTimedOut,
    ToolCallPart,
    ToolReturnPart,
    UsageLimitExceeded,
    UsageLimits,
)
from pydantic_ai.models import Model

from task_agent import runtime
from task_agent.accounts import AccountStore, prefill
from task_agent.audit import AuditLog, StepRecorder
from task_agent.browser import BrowserHub
from task_agent.config import Settings
from task_agent.db import Job, JobStore, now
from task_agent.llm import build_model, model_settings
from task_agent.promotion import WAIT_FOR_JOE
from task_agent.tasks import TASKS, Finish, JobDeps
from task_agent.tasks.research import normalize_url
from task_agent.tools.browser import NeedsJoe
from task_agent.tools.web import USER_AGENT

log = structlog.get_logger()

# Worth retrying later: the model server was down, busy, or a network hiccup.
TRANSIENT_ERRORS = (
    ModelConnectionError,
    ModelServerError,
    ModelUnavailableError,
    ModelRateLimitError,
    ModelRequestTimeout,
    httpx.TransportError,
)
_SOURCE_ATTR = re.compile(r'<untrusted_data source="([^"]+)"')


class JobCancelled(Exception):
    """Joe cancelled the job from the Task Agent page while it was running."""


def backoff(attempt: int) -> timedelta:
    return timedelta(minutes=min(2**attempt, 60))


def opened_urls_from(history: list[ModelMessage] | None) -> set[str]:
    """Rebuild which pages a resumed job already opened, so its source check still passes."""
    urls: set[str] = set()
    for message in history or []:
        for part in message.parts:
            if isinstance(part, ToolCallPart) and part.tool_name == "fetch_page":
                url = part.args_as_dict().get("url")
                if url:
                    urls.add(normalize_url(str(url)))
            elif isinstance(part, ToolReturnPart) and part.tool_name == "fetch_page" and isinstance(part.content, str):
                urls.update(normalize_url(u) for u in _SOURCE_ATTR.findall(part.content))
    return urls


async def run_job(
    job: Job,
    store: JobStore,
    settings: Settings,
    model: Model | None = None,
    *,
    browser: BrowserHub | None = None,
    accounts: AccountStore | None = None,
) -> str:
    """Run one claimed job. Returns its new status."""
    spec = TASKS.get(job.type)
    owner = f"job:{job.id}"
    finish = Finish(store, settings, job, "failed", browser=browser)
    try:
        status = await _run_job(job, store, settings, model, browser, accounts, finish)
    finally:
        if browser is not None:
            await browser.release(owner)
    finish.status = status
    if spec is not None and spec.on_finish is not None and status != "skipped_at_start":
        try:
            await spec.on_finish(finish)
        except Exception:
            log.exception("job.on_finish_failed", job_id=job.id, type=job.type)
    return "cancelled" if status == "skipped_at_start" else status


async def _run_job(
    job: Job,
    store: JobStore,
    settings: Settings,
    model: Model | None,
    browser: BrowserHub | None,
    accounts: AccountStore | None,
    finish: Finish,
) -> str:
    jlog = log.bind(job_id=job.id, type=job.type, attempt=job.attempts)
    spec = TASKS.get(job.type)
    if spec is None:
        store.fail(job.id, f"Unknown task type '{job.type}'.")
        return "failed"
    try:
        payload = spec.payload_model.model_validate(job.payload)
    except ValidationError as exc:
        store.fail(job.id, f"Bad payload: {exc}")
        return "failed"
    if job.attempts > settings.max_attempts + 2:
        store.fail(job.id, "Interrupted too many times (worker crashed or restarted during this job).")
        return "failed"

    audit = AuditLog(settings.path(settings.log_dir) / str(job.id))
    if job.cancel_requested:
        audit.write("cancelled")
        store.mark_cancelled(job.id)
        return "cancelled"
    if spec.on_start is not None:
        reason = await spec.on_start(store, job, browser)
        if reason:
            audit.write("cancelled", reason=reason)
            store.mark_cancelled(job.id, reason)
            return "skipped_at_start"
    max_steps = spec.max_steps or settings.max_steps
    max_minutes = spec.max_minutes or settings.max_minutes
    history = ModelMessagesTypeAdapter.validate_json(job.checkpoint) if job.checkpoint else None
    audit.write("resume" if history else "start", attempt=job.attempts, model=settings.llm_model)
    jlog.info("job.start", resumed=bool(history))

    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": USER_AGENT}) as http:
        deps = JobDeps(
            job.id,
            settings,
            http,
            opened_urls=opened_urls_from(history),
            store=store,
            browser=browser if spec.uses_browser else None,
            accounts=accounts,
            payload=payload,
        )
        prompt = None
        if not history:
            prompt = spec.render_prompt(payload)
            if spec.prepare is not None:
                try:
                    extra = await spec.prepare(deps, payload)
                except Exception as exc:  # reading the pages first is a help, not a must
                    jlog.warning("job.prepare_failed", error=f"{type(exc).__name__}: {exc}")
                    extra = ""
                if extra:
                    prompt = f"{prompt}\n\n{extra}"
        agent = spec.build_agent(model or build_model(settings), settings)
        recorder = StepRecorder(store, audit, job.id)
        try:
            async with agent.iter(
                prompt,
                message_history=history,
                deps=deps,
                model_settings=model_settings(settings),
                usage_limits=UsageLimits(request_limit=max_steps),
                timeout=max_minutes * 60,
            ) as run:
                async for node in run:
                    recorder.observe(node)
                    if store.cancel_requested(job.id):
                        raise JobCancelled
                    if isinstance(node, ModelRequestNode):
                        # Checkpoint includes the latest tool results, so a resume never redoes them.
                        messages = run.all_messages()
                        if node.request.parts and not any(m is node.request for m in messages):
                            messages = [*messages, node.request]
                        store.save_checkpoint(job.id, ModelMessagesTypeAdapter.dump_json(messages))
            output = run.result.output.model_dump(mode="json")
        except JobCancelled:
            audit.write("cancelled")
            store.mark_cancelled(job.id)
            jlog.info("job.cancelled")
            return "cancelled"
        except NeedsJoe as need:
            # Park the job; the page goes to Joe on the Mac (on_finish) and it starts over when he's done.
            audit.write("needs_joe", site=need.site, url=need.url, reason=need.reason)
            store.wait_for_joe(job.id, now() + WAIT_FOR_JOE, need.reason)
            finish.need = need
            if browser is not None:
                await hand_to_joe(browser, accounts, job, store, need)
            jlog.info("job.needs_joe", site=need.site)
            return "needs_you"
        except (UsageLimitExceeded, RunTimedOut) as exc:
            msg = f"Hit the job budget ({max_steps} steps / {max_minutes} min): {exc}"
            audit.write("failed", error=msg)
            store.fail(job.id, msg)
            finish.error = msg
            jlog.warning("job.budget_exceeded", error=str(exc))
            return "failed"
        except TRANSIENT_ERRORS as exc:
            msg = f"{type(exc).__name__}: {exc}"
            finish.error = msg
            if job.attempts < settings.max_attempts:
                delay = backoff(job.attempts)
                audit.write("retry_later", error=msg, delay_minutes=delay.total_seconds() / 60)
                store.retry_later(job.id, msg, delay)
                jlog.warning("job.retry_later", error=msg, delay=str(delay))
                return "queued"
            audit.write("failed", error=msg)
            store.fail(job.id, f"Gave up after {job.attempts} attempts. Last error: {msg}")
            return "failed"
        except Exception as exc:  # anything else is a real failure: record it, don't retry blindly
            msg = f"{type(exc).__name__}: {exc}"
            finish.error = msg
            audit.write("failed", error=msg)
            store.fail(job.id, msg)
            jlog.exception("job.failed")
            return "failed"

    recorder.record_final(output)
    audit.save_result(output)
    audit.write("done")
    store.finish(job.id, output)
    finish.output = output
    jlog.info("job.done", steps=recorder.n - 1)
    return "done"


async def hand_to_joe(
    browser: BrowserHub, accounts: AccountStore | None, job: Job, store: JobStore, need: NeedsJoe
) -> None:
    """Show the page on the Mac. If it had to be opened again, fill in the saved login for that site, if any."""
    try:
        page, kept = await browser.hand_over(f"job:{job.id}", f"login:{need_owner_for(job, store)}", need.url)
        if not kept and accounts is not None:
            account = accounts.find_by_host(urlsplit(need.url).hostname or "")
            if account:
                await prefill(page, account["username"], accounts.password(account["site"]))
    except Exception as exc:  # the job is parked either way; Joe can open the page from the Logins page
        log.warning("job.hand_over_failed", job_id=job.id, error=f"{type(exc).__name__}: {exc}")


def need_owner_for(job: Job, store: JobStore) -> str:
    """The login tab's name for a job that needs Joe: per place for promotions, else per job."""
    from task_agent.promotion import PromotionStore, need_owner

    target = PromotionStore(store).by_job(job.id)
    return need_owner(target["id"]) if target else f"job-{job.id}"


class Worker:
    """Runs jobs strictly one at a time (APScheduler never starts a second drain while one is running).

    Settings saved on the admin page apply from the next job. The same process serves the admin API.
    """

    def __init__(
        self, store: JobStore, settings: Settings, model: Model | None = None, browser: BrowserHub | None = None
    ) -> None:
        self.store = store
        self.settings = settings  # .env values; the page's saved settings are layered on per job
        self.model = model
        self.current: Job | None = None
        self.accounts = AccountStore(store, settings.path(settings.profiles_dir))
        # One shared browser for every agent, the login tabs and Telegram's "Done" buttons.
        self.browser = browser or BrowserHub(self.accounts.shared_profile)

    async def drain(self) -> int:
        """Run ready jobs until none are left (or the worker is paused). Returns how many ran."""
        count = 0
        while True:
            settings, paused = runtime.effective(self.settings, self.store)
            if paused:
                break
            job = self.store.claim_next()
            if job is None:
                break
            self.current = job
            self.heartbeat()
            try:
                await run_job(job, self.store, settings, self.model, browser=self.browser, accounts=self.accounts)
            finally:
                self.current = None
                self.heartbeat()
            count += 1
        return count

    def heartbeat(self) -> None:
        job = self.current
        self.store.heartbeat(job.id if job else None, job.title if job else None)

    async def run_forever(self, serve_api: bool = True) -> None:
        # The "skipped: maximum number of running instances" notice is the one-at-a-time rule working.
        logging.getLogger("apscheduler").setLevel(logging.ERROR)
        requeued = self.store.requeue_interrupted()
        if requeued:
            log.info("worker.requeued_interrupted", job_ids=requeued)
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            self.drain,
            "interval",
            seconds=self.settings.poll_seconds,
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(),
        )
        scheduler.add_job(self.heartbeat, "interval", seconds=10, next_run_time=datetime.now())
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

        from task_agent.telegram.bot import TelegramBot

        bot_task = asyncio.create_task(
            TelegramBot(self.store, self.settings, browser=self.browser).run(stop), name="telegram"
        )
        server, server_task, ctx = None, None, None
        if serve_api:
            from task_agent.api import AppContext, create_app

            ctx = AppContext.build(self.store, self.settings, self.browser)
            server = _ApiServer(
                uvicorn.Config(
                    create_app(ctx),
                    host=self.settings.api_host,
                    port=self.settings.api_port,
                    log_level="warning",
                    access_log=False,
                )
            )
            server_task = asyncio.create_task(server.serve(), name="api")
            if not self.settings.task_agent_api_key.get_secret_value():
                log.warning("api.no_key", hint="set TASK_AGENT_API_KEY in task-agent/.env; the API refuses requests")

        scheduler.start()
        log.info(
            "worker.started",
            model=runtime.current(self.settings, self.store).llm_model,
            poll_seconds=self.settings.poll_seconds,
            api=f"http://{self.settings.api_host}:{self.settings.api_port}" if serve_api else None,
        )
        await stop.wait()
        # A job cut off here stays 'running'; the next start puts it back in the queue and it resumes.
        scheduler.shutdown(wait=False)
        if server and server_task:
            server.should_exit = True
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server_task, timeout=5)
        bot_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await bot_task
        if ctx:
            await ctx.windows.close_all()
        await self.browser.close()
        log.info("worker.stopped")


class _ApiServer(uvicorn.Server):
    """uvicorn without its own signal handling: the worker owns SIGINT/SIGTERM and stops the server."""

    @contextlib.contextmanager
    def capture_signals(self):  # type: ignore[override]
        yield
