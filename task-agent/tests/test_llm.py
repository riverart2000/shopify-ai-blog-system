"""The shared model on oMLX: waiting when another app's model is in the way, and the per-request settings."""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelRequestRejectedError, ModelServerError, UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from task_agent.llm import WaitWhenBusy, model_busy, model_settings

BUSY = ModelServerError(
    "gemma-4-31b-it-8bit",
    "status_code: 507, model_name: gemma-4-31b-it-8bit, body: {'message': 'Cannot load gemma-4-31b-it-8bit: "
    "projected memory 62GB would exceed the metal_cap memory ceiling 51.84GB'}",
)
PREFILL = ModelRequestRejectedError("gemma-4-31b-it-8bit", "status_code: 400", body={"code": "prefill_memory_exceeded"})


def flaky(failures: list[Exception]) -> FunctionModel:
    def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if failures:
            raise failures.pop(0)
        return ModelResponse(parts=[TextPart("done")])

    return FunctionModel(answer)


ABORTED = UnexpectedModelBehavior(
    "Invalid response from openai chat completions endpoint: 4 validation errors for ChatCompletion"
)


def test_busy_errors_are_recognised() -> None:
    assert model_busy(BUSY) and model_busy(PREFILL) and model_busy(ABORTED)
    assert not model_busy(UnexpectedModelBehavior("Exceeded maximum retries (3) for output validation"))
    assert not model_busy(ModelServerError("m", "status_code: 500, model_name: m, body: crash"))
    assert not model_busy(ValueError("status_code: 507"))


async def test_a_busy_model_is_waited_for() -> None:
    naps: list[float] = []

    async def nap(seconds: float) -> None:
        naps.append(seconds)

    agent = Agent(WaitWhenBusy(flaky([BUSY, PREFILL, ABORTED]), sleep=nap))
    result = await agent.run("go")
    assert result.output == "done" and naps == [15.0, 30.0, 60.0]


async def test_waiting_has_a_limit_and_other_errors_go_straight_through() -> None:
    async def nap(seconds: float) -> None:
        return None

    with pytest.raises(ModelServerError, match="507"):
        await Agent(WaitWhenBusy(flaky([BUSY] * 20), max_wait=100, sleep=nap)).run("go")
    crash = ModelServerError("m", "status_code: 500, model_name: m, body: crash")
    with pytest.raises(ModelServerError, match="500"):
        await Agent(WaitWhenBusy(flaky([crash]), sleep=nap)).run("go")


def test_every_request_caps_the_answer(settings) -> None:
    assert model_settings(settings)["max_tokens"] == 8192
