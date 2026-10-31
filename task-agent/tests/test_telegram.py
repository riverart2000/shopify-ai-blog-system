"""Telegram bot: only Joe gets in, cards and buttons work, and nothing changes without Confirm."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from pydantic import SecretStr
from pydantic_ai import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from task_agent.telegram.bot import TelegramBot

TOKEN = "123:secret-token"
TG = f"https://api.telegram.org/bot{TOKEN}"
SE = "http://social.test/api/social-engine"
JOE = {"id": 42, "username": "JoeBains"}
PRIVATE = {"id": 4242, "type": "private"}

POST = {
    "id": "post_1",
    "title": "Massage gun",
    "status": "pending_approval",
    "scheduled_at": 1793700000,
    "base_caption": "Thirty speeds for your routine.",
    "preview_asset_id": "media_1",
    "targets": [{"provider": "instagram"}, {"provider": "facebook"}],
    "review": {"ai_review": {"score": 5, "issues": ["Caption repeats the title"]}},
}


@pytest.fixture
def bot(settings, store):
    settings = settings.model_copy(
        update={
            "telegram_bot_token": SecretStr(TOKEN),
            "telegram_owner_username": "JoeBains",
            "social_api_url": SE,
            "social_api_key": SecretStr("se-key"),
        }
    )
    return TelegramBot(store, settings)


@pytest.fixture
def tg():
    """Fake Telegram: records every call, returns a message id."""
    with respx.mock(assert_all_called=False) as mock:
        calls: list[tuple[str, dict]] = []

        def record(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            if request.headers.get("content-type", "").startswith("application/json"):
                body = json.loads(request.content or b"{}")
            else:
                body = {"multipart": True}
            calls.append((method, body))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(calls), "username": "joe_bot"}})

        mock.route(url__startswith=TG).mock(side_effect=record)
        mock.calls_made = calls  # type: ignore[attr-defined]
        yield mock


def sent(mock, method: str) -> list[dict]:
    return [body for name, body in mock.calls_made if name == method]


def message(text: str, user=JOE, chat=PRIVATE) -> dict:
    return {"update_id": 1, "message": {"from": user, "chat": chat, "text": text}}


def button(data: str, *, photo: bool = False) -> dict:
    msg = {"message_id": 7, "chat": PRIVATE, "caption" if photo else "text": "card"}
    if photo:
        msg["photo"] = [{"file_id": "x"}]
    return {"update_id": 2, "callback_query": {"id": "cb1", "from": JOE, "data": data, "message": msg}}


async def link(bot) -> None:
    await bot.handle_update(bot.api(), message("/start"))


async def test_only_joe_gets_in_and_is_locked_to_his_id(bot, tg):
    api = bot.api()
    await bot.handle_update(api, message("hi", user={"id": 7, "username": "someone"}))
    assert not tg.calls_made and bot.tg.owner() == (None, None)
    await bot.handle_update(api, message("hi", chat={"id": 1, "type": "group"}))  # groups never count
    assert bot.tg.owner() == (None, None)

    await link(bot)
    assert bot.tg.owner() == (42, 4242) and "Connected" in sent(tg, "sendMessage")[0]["text"]
    # Same username, different account (e.g. a renamed handle) is refused after linking.
    assert not bot.authorised({"id": 99, "username": "JoeBains"}, PRIVATE)
    assert bot.authorised(JOE, PRIVATE)


async def test_post_card_sent_once_then_closed_when_decided_elsewhere(bot, tg):
    await link(bot)
    tg.get(f"{SE}/posts").mock(
        side_effect=[
            httpx.Response(200, json={"posts": [POST], "counts": {}}),
            httpx.Response(200, json={"posts": [POST], "counts": {}}),
            httpx.Response(200, json={"posts": [], "counts": {}}),
        ]
    )
    tg.get(f"{SE}/media/media_1").mock(
        return_value=httpx.Response(200, content=b"jpeg", headers={"content-type": "image/jpeg"})
    )
    tg.get(f"{SE}/posts/post_1").mock(return_value=httpx.Response(200, json={"post": {**POST, "status": "approved"}}))
    api = bot.api()
    assert await bot.notifier.post_cards(api, 4242) == 1
    assert len(sent(tg, "sendPhoto")) == 1
    assert await bot.notifier.post_cards(api, 4242) == 0  # not sent twice
    await bot.notifier.post_cards(api, 4242)  # approved in the admin app meanwhile
    assert "Approved" in sent(tg, "editMessageCaption")[0]["caption"]


async def test_approve_button_approves_the_post(bot, tg):
    await link(bot)
    approve = tg.post(f"{SE}/posts/post_1/approve").mock(return_value=httpx.Response(200, json={"ok": True}))
    await bot.handle_update(bot.api(), button("post:post_1:a", photo=True))
    assert approve.call_count == 1
    assert "Approved" in sent(tg, "editMessageCaption")[0]["caption"]


def proposer(campaign_tool_args: dict):
    def model(messages, info: AgentInfo) -> ModelResponse:
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("propose_pause_campaign", campaign_tool_args)])
        return ModelResponse(parts=[TextPart("I've proposed pausing Halloween 2026.")])

    return FunctionModel(model)


async def test_instructions_need_confirm_and_run_once(bot, tg):
    await link(bot)
    tg.get(f"{SE}/campaigns").mock(
        return_value=httpx.Response(
            200, json={"campaigns": [{"id": "cmp_1", "name": "Halloween 2026", "status": "active"}]}
        )
    )
    pause = tg.post(f"{SE}/campaigns/cmp_1/status").mock(return_value=httpx.Response(200, json={"ok": True}))
    bot.model = proposer({"campaign_id": "cmp_1"})

    await bot.reply(bot.api(), 4242, "pause the halloween campaign")
    assert pause.call_count == 0  # proposing changes nothing
    confirm = sent(tg, "sendMessage")[-1]
    assert confirm["text"] == "Confirm? Pause “Halloween 2026”"
    data = confirm["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

    await bot.handle_update(bot.api(), button(data))
    assert pause.call_count == 1 and json.loads(pause.calls[0].request.content) == {"status": "paused"}
    await bot.handle_update(bot.api(), button(data))  # pressing again does nothing
    assert pause.call_count == 1
    assert sent(tg, "answerCallbackQuery")[-1]["text"] == "Already handled."


async def test_cancel_runs_nothing(bot, tg):
    await link(bot)
    tg.get(f"{SE}/campaigns").mock(
        return_value=httpx.Response(200, json={"campaigns": [{"id": "cmp_1", "name": "Halloween 2026"}]})
    )
    pause = tg.post(f"{SE}/campaigns/cmp_1/status").mock(return_value=httpx.Response(200, json={"ok": True}))
    bot.model = proposer({"campaign_id": "cmp_1"})
    await bot.reply(bot.api(), 4242, "pause it")
    data = sent(tg, "sendMessage")[-1]["reply_markup"]["inline_keyboard"][0][1]["callback_data"]
    await bot.handle_update(bot.api(), button(data))
    assert pause.call_count == 0 and "Cancelled" in sent(tg, "editMessageText")[-1]["text"]


async def test_failed_task_is_reported_once_with_a_retry_button(bot, tg, store):
    await link(bot)
    api = bot.api()
    await bot.notifier.task_updates(api, 4242)  # first run only sets the starting point
    job_id, _ = store.enqueue("research", "Find venues", {"question": "where?"}, [])
    store.claim_next()
    store.fail(job_id, "Gave up after 3 attempts")
    await bot.notifier.task_updates(api, 4242)
    await bot.notifier.task_updates(api, 4242)
    failures = [m for m in sent(tg, "sendMessage") if "failed" in m["text"]]
    assert len(failures) == 1 and failures[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == (
        f"task:{job_id}:retry"
    )


async def test_morning_summary_once_a_day(bot, tg, monkeypatch):
    await link(bot)

    async def summary():
        return "☀️ Good morning"

    monkeypatch.setattr(bot.notifier, "summary", summary)
    monkeypatch.setattr(bot.ctx.settings, "telegram_summary_hour", 0)
    api = bot.api()
    assert await bot.notifier.morning(api, 4242) is True
    assert await bot.notifier.morning(api, 4242) is False


async def test_errors_never_leak_the_token(bot):
    from task_agent.telegram.client import TelegramAPI, TelegramError

    with respx.mock() as mock:
        mock.route(url__startswith=TG).mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(TelegramError) as caught:
            await TelegramAPI(TOKEN).get_me()
    assert TOKEN not in str(caught.value) and "secret" not in str(caught.value)


async def test_place_that_needs_joe_gets_one_card_and_done_requeues_it(bot, tg, store):
    from task_agent.promotion import PromotionStore

    await link(bot)
    promotion, _ = store.enqueue("promote_event", "Promote: Breathwork", {}, [])
    store.claim_next()
    store.finish(promotion, {"event": {"title": "Breathwork"}, "places": [], "notes": ""})
    child, _ = store.enqueue("promote_post", "Breathwork → Meetup", {}, [], parent_id=promotion)
    ps = PromotionStore(store)
    target = ps.add(promotion, kind="listing", name="Meetup", url="https://www.meetup.com/")
    ps.update(target, status="needs_you", job_id=child, need_reason="Enter the code Meetup emailed you")

    api = bot.api()
    await bot.notifier.need_cards(api, 4242)
    await bot.notifier.need_cards(api, 4242)
    cards = [m for m in sent(tg, "sendMessage") if "needs you" in m["text"]]
    assert len(cards) == 1 and "Enter the code" in cards[0]["text"]
    done = cards[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert done == f"need:{target}:d"

    await bot.handle_update(api, button(done))
    assert ps.get(target)["status"] == "queued"
    assert "back in the queue" in sent(tg, "editMessageText")[-1]["text"]


async def test_promotion_plan_and_final_report_are_sent_once(bot, tg, store):
    from task_agent.promotion import PromotionStore

    await link(bot)
    api = bot.api()
    await bot.notifier.task_updates(api, 4242)  # sets the starting point
    promotion, _ = store.enqueue("promote_event", "Promote: Breathwork", {}, [])
    store.claim_next()
    store.finish(promotion, {"event": {"title": "Breathwork"}, "places": [], "notes": ""})
    ps = PromotionStore(store)
    meetup = ps.add(promotion, kind="listing", name="Meetup", url="https://www.meetup.com/", status="queued")

    await bot.notifier.task_updates(api, 4242)  # no generic "done" message for a promotion
    await bot.notifier.promotion_updates(api, 4242)
    await bot.notifier.promotion_updates(api, 4242)
    texts = [m["text"] for m in sent(tg, "sendMessage")]
    assert sum("1 place(s) planned" in t for t in texts) == 1 and not any("finished" in t for t in texts)

    ps.update(meetup, status="posted", result_url="https://www.meetup.com/x/events/1")
    await bot.notifier.promotion_updates(api, 4242)
    await bot.notifier.promotion_updates(api, 4242)
    reports = [m["text"] for m in sent(tg, "sendMessage") if "finished" in m["text"]]
    assert len(reports) == 1 and "Live in 1 of 1" in reports[0] and "events/1" in reports[0]
