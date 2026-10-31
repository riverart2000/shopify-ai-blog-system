"""End-to-end worker tests with a scripted fake model (no oMLX, no internet)."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
import respx
from pydantic_ai import ModelConnectionError, ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from task_agent.tasks import TASKS
from task_agent.tools import web
from task_agent.worker import Worker, run_job

PAGE = "https://events.example/yoga-night"


@pytest.fixture(autouse=True)
def offline_web(monkeypatch):
    async def no_check(url: str) -> None:
        return None

    async def fake_search(query: str, max_results: int = 8) -> str:
        return web.wrap_untrusted(f"search: {query}", f"1. Yoga Night\n   {PAGE}\n   Friday 7pm")

    monkeypatch.setattr(web, "check_public_url", no_check)
    monkeypatch.setattr(web, "search", fake_search)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(PAGE).mock(return_value=httpx.Response(200, html="<title>Yoga Night</title><p>Friday 7pm, £10</p>"))
        yield mock


def tool_returns(messages, name):
    return [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name == name]


def retry_prompts(messages):
    return [
        p
        for m in messages
        for p in m.parts
        if isinstance(p, RetryPromptPart) or getattr(p, "outcome", None) == "retried"
    ]


def final(info: AgentInfo, cite: str, confidence: str = "high") -> ModelResponse:
    return ModelResponse(
        parts=[
            ToolCallPart(
                info.output_tools[0].name,
                {
                    "answer": "Yoga Night is on Friday at 7pm and costs £10.",
                    "key_points": ["Friday 7pm", "£10"],
                    "sources": [{"url": cite, "title": "Yoga Night", "used_for": "time and price"}],
                    "confidence": confidence,
                },
            )
        ]
    )


def researcher(messages, info: AgentInfo) -> ModelResponse:
    """Search, open the page, then answer citing it."""
    if not tool_returns(messages, "web_search"):
        return ModelResponse(parts=[ToolCallPart("web_search", {"query": "yoga night"})])
    if not tool_returns(messages, "fetch_page"):
        return ModelResponse(parts=[ToolCallPart("fetch_page", {"url": PAGE})])
    return final(info, PAGE)


def enqueue(store, payload=None):
    job_id, _ = store.enqueue(
        "research", "t", payload or {"question": "When is yoga night?"}, TASKS["research"].tool_names
    )
    return job_id


async def test_research_job_end_to_end(store, settings):
    job_id = enqueue(store)
    ran = await Worker(store, settings, FunctionModel(researcher)).drain()
    job = store.get(job_id)
    assert ran == 1
    assert job.status == "done", job.error
    assert job.result["sources"][0]["url"] == PAGE
    assert [s["tool"] for s in store.steps(job_id)] == ["web_search", "fetch_page", "final_result"]
    log_dir = settings.path(settings.log_dir) / str(job_id)
    assert (log_dir / "steps.jsonl").exists() and (log_dir / "result.json").exists()
    assert job.checkpoint  # history saved for resume


async def test_agent_sees_only_its_own_tools(store, settings):
    seen: list[str] = []

    def model(messages, info: AgentInfo) -> ModelResponse:
        seen.extend(t.name for t in info.function_tools)
        return researcher(messages, info)

    enqueue(store)
    await Worker(store, settings, FunctionModel(model)).drain()
    assert set(seen) == {"web_search", "fetch_page"}


async def test_citing_an_unopened_page_is_rejected_then_fixed(store, settings):
    def model(messages, info: AgentInfo) -> ModelResponse:
        if not tool_returns(messages, "fetch_page"):
            return ModelResponse(parts=[ToolCallPart("fetch_page", {"url": PAGE})])
        if not retry_prompts(messages):
            return final(info, "https://made-up.example/never-opened")
        return final(info, PAGE)

    job_id = enqueue(store)
    await Worker(store, settings, FunctionModel(model)).drain()
    job = store.get(job_id)
    assert job.status == "done", job.error
    assert any(s["result_summary"].startswith("REJECTED") for s in store.steps(job_id))


async def test_budget_exceeded_fails_the_job(store, settings):
    def endless(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("web_search", {"query": f"again {len(messages)}"})])

    job_id = enqueue(store)
    await Worker(store, settings, FunctionModel(endless)).drain()
    job = store.get(job_id)
    assert job.status == "failed"
    assert "budget" in job.error


async def test_model_server_down_retries_later_then_gives_up(store, settings):
    def down(messages, info: AgentInfo) -> ModelResponse:
        raise ModelConnectionError("qwen", "connection refused")

    job_id = enqueue(store)
    model = FunctionModel(down)
    for expected_attempt in range(1, settings.max_attempts + 1):
        job = store.claim_next()
        assert job is not None and job.attempts == expected_attempt
        status = await run_job(job, store, settings, model)
        if expected_attempt < settings.max_attempts:
            assert status == "queued"
            store.retry_later(job_id, "make due now", timedelta(0))
    assert status == "failed"
    assert "Gave up" in store.get(job_id).error


async def test_resume_after_crash_does_not_redo_finished_steps(store, settings, offline_web):
    calls = {"n": 0}

    def crashes_after_fetch(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if tool_returns(messages, "fetch_page"):
            raise ModelConnectionError("qwen", "oMLX restarted")
        return researcher(messages, info)

    job_id = enqueue(store)
    first = await run_job(store.claim_next(), store, settings, FunctionModel(crashes_after_fetch))
    assert first == "queued"
    assert offline_web.routes[0].call_count == 1

    store.retry_later(job_id, "make due now", timedelta(0))
    second = await run_job(store.claim_next(), store, settings, FunctionModel(researcher))
    job = store.get(job_id)
    assert second == "done", job.error
    assert offline_web.routes[0].call_count == 1  # the page was not fetched again
    tools = [s["tool"] for s in store.steps(job_id)]
    assert tools == ["web_search", "fetch_page", "final_result"]  # no duplicate steps either


async def test_bad_payload_and_unknown_type_fail_cleanly(store, settings):
    bad, _ = store.enqueue("research", "t", {"question": "?"}, [])
    unknown, _ = store.enqueue("teleport", "t", {}, [])
    await Worker(store, settings, FunctionModel(researcher)).drain()
    assert store.get(bad).status == "failed" and "Bad payload" in store.get(bad).error
    assert store.get(unknown).status == "failed" and "Unknown task type" in store.get(unknown).error


def test_thinking_switch_is_sent_per_request(settings):
    from task_agent.llm import model_settings

    assert model_settings(settings)["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}
    assert "extra_body" not in model_settings(settings.model_copy(update={"llm_thinking": None}))


def test_source_may_be_just_a_link():
    from task_agent.tasks.research import ResearchResult

    result = ResearchResult.model_validate(
        {"answer": "a", "key_points": [], "sources": [{"url": "https://x.example/"}], "confidence": "low", "gaps": []}
    )
    assert result.sources[0].title == "" and result.sources[0].used_for == ""


async def test_cancel_stops_a_running_job_at_its_next_step(store, settings):
    job_id = enqueue(store)

    def cancelled_mid_run(messages, info: AgentInfo) -> ModelResponse:
        store.cancel(job_id)  # Joe presses Cancel while the job is running
        return ModelResponse(parts=[ToolCallPart("web_search", {"query": "yoga night"})])

    status = await run_job(store.claim_next(), store, settings, FunctionModel(cancelled_mid_run))
    job = store.require(job_id)
    assert status == "cancelled" and job.status == "cancelled" and job.finished_at


async def test_paused_worker_takes_no_new_jobs(store, settings):
    job_id = enqueue(store)
    store.save_setting_overrides({"paused": True})
    assert await Worker(store, settings, FunctionModel(researcher)).drain() == 0
    assert store.require(job_id).status == "queued"
    store.save_setting_overrides({"paused": False})
    assert await Worker(store, settings, FunctionModel(researcher)).drain() == 1
    assert store.require(job_id).status == "done"
    assert store.worker_state()["current_job_id"] is None


async def test_page_settings_apply_to_the_next_job(store, settings):
    enqueue(store)
    store.save_setting_overrides({"max_steps": 5})

    def endless(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("web_search", {"query": "again"})])

    await Worker(store, settings, FunctionModel(endless)).drain()
    assert "5 steps" in store.list()[0].error
