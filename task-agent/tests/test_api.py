"""The admin page's API, called the way the Shopify app calls it (no network, stand-in Keychain)."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from task_agent import api as api_module
from task_agent import email_account
from task_agent.api import AppContext, create_app

KEY = {"x-api-key": "test-key"}


@pytest.fixture
def ctx(settings, store):
    return AppContext.build(store, settings.model_copy(update={"task_agent_api_key": SecretStr("test-key")}))


@pytest.fixture
async def client(ctx, monkeypatch):
    async def models(settings, current):
        return [current, "other-model"]

    monkeypatch.setattr(api_module, "available_models", models)
    transport = httpx.ASGITransport(app=create_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://agent", headers=KEY) as c:
        yield c


async def test_key_is_required(ctx, settings, store):
    transport = httpx.ASGITransport(app=create_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as c:
        assert (await c.get("/api/status")).status_code == 401
        assert (await c.get("/api/status", headers={"x-api-key": "nope"})).status_code == 401
    no_key = AppContext.build(store, settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(no_key)), base_url="http://a") as c:
        response = await c.get("/api/status", headers=KEY)
        assert response.status_code == 503 and "TASK_AGENT_API_KEY" in response.json()["detail"]


async def test_queue_a_task_from_a_template_then_cancel_and_retry(client):
    templates = (await client.get("/api/templates")).json()
    assert {t["key"] for t in templates["templates"]} >= {"research", "find-event-listings"}
    assert templates["task_types"][0] == {
        "type": "research",
        "summary": templates["task_types"][0]["summary"],
        "prompt_field": "question",
    }

    preview = (
        await client.post(
            "/api/templates/preview",
            json={"template_key": "find-event-listings", "values": {"event_name": "Yoga Night"}},
        )
    ).json()
    assert preview["missing"] == ["event_type", "location", "event_date"]

    missing = await client.post("/api/jobs", json={"template_key": "find-event-listings", "values": {}})
    assert missing.status_code == 400 and "Fill in" in missing.json()["detail"]

    created = await client.post(
        "/api/jobs", json={"template_key": "research", "values": {"question": "Is Meetup free for organisers?"}}
    )
    assert created.status_code == 200, created.text
    job = created.json()["job"]
    assert job["status"] == "queued" and job["created_by"] == "admin" and job["template_key"] == "research"
    assert job["title"] == "Is Meetup free for organisers?"

    detail = (await client.get(f"/api/jobs/{job['id']}")).json()
    assert detail["job"]["payload"]["question"] == "Is Meetup free for organisers?"
    assert detail["job"]["tools_allowed"] == ["web_search", "fetch_page"] and detail["steps"] == []

    listed = (await client.get("/api/jobs", params={"status": "queued,running"})).json()
    assert listed["total"] == 1 and listed["counts"]["queued"] == 1

    assert (await client.post(f"/api/jobs/{job['id']}/cancel")).json()["job"]["status"] == "cancelled"
    assert (await client.post(f"/api/jobs/{job['id']}/retry")).json()["job"]["status"] == "queued"
    assert (await client.get("/api/jobs/999")).status_code == 404
    assert (await client.get("/api/jobs", params={"status": "bogus"})).status_code == 400


async def test_edit_a_task_that_has_not_started(client, store):
    created = (
        await client.post(
            "/api/jobs",
            json={"template_key": "research", "values": {"question": "Is Meetup free?"}, "priority": 1},
        )
    ).json()["job"]
    job_id = created["id"]

    edited = await client.post(
        f"/api/jobs/{job_id}/edit",
        json={"template_key": "research", "values": {"question": "Is Eventbrite free?"}, "title": "Eventbrite fees",
              "priority": 3},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["job"]["id"] == job_id and edited.json()["message"].startswith(f"Saved task {job_id}")
    detail = (await client.get(f"/api/jobs/{job_id}")).json()["job"]
    assert detail["title"] == "Eventbrite fees" and detail["priority"] == 3 and detail["status"] == "queued"
    assert detail["payload"]["question"] == "Is Eventbrite free?"
    assert detail["template_values"] == {"question": "Is Eventbrite free?", "_title": "Eventbrite fees"}

    # A failed task can be edited too: it starts over with the new details.
    store.claim_next()
    store.fail(job_id, "boom")
    again = await client.post(
        f"/api/jobs/{job_id}/edit", json={"template_key": "research", "values": {"question": "Third try?"}}
    )
    assert again.status_code == 200 and again.json()["job"]["status"] == "queued"
    detail = (await client.get(f"/api/jobs/{job_id}")).json()["job"]
    assert detail["attempts"] == 0 and detail["error"] is None and detail["payload"]["question"] == "Third try?"

    # Not while it's running, not a different kind of task, and the blanks are still checked.
    store.claim_next()
    running = await client.post(
        f"/api/jobs/{job_id}/edit", json={"template_key": "research", "values": {"question": "Nope"}}
    )
    assert running.status_code == 400 and "is running" in running.json()["detail"]
    store.fail(job_id, "boom")
    other_kind = await client.post(
        f"/api/jobs/{job_id}/edit", json={"template_key": "promote-event", "values": {}}
    )
    assert other_kind.status_code == 400 and "different kind" in other_kind.json()["detail"]
    empty = await client.post(f"/api/jobs/{job_id}/edit", json={"template_key": "research", "values": {}})
    assert empty.status_code == 400 and "Fill in" in empty.json()["detail"]
    assert (await client.post("/api/jobs/999/edit", json={"template_key": "research", "values": {}})).status_code == 404


async def test_own_templates(client):
    body = {
        "name": "Venue search",
        "task_type": "research",
        "prompt": "Find venues in {town}",
        "defaults": {"min_sources": 3},
    }
    saved = (await client.post("/api/templates", json=body)).json()["template"]
    assert saved["key"] == "venue-search" and not saved["builtin"] and saved["fields"][0]["name"] == "town"
    builtin = await client.post("/api/templates", json={**body, "key": "research"})
    assert builtin.status_code == 400
    assert (await client.post("/api/templates/venue-search/delete")).status_code == 200
    assert (await client.post("/api/templates/research/delete")).status_code == 400


async def test_settings_and_pause(client):
    data = (await client.get("/api/settings")).json()
    assert data["settings"]["paused"] is False and data["limits"]["max_steps"] == [5, 200]
    assert data["recommended_model"] == "gemma-4-31b-it-8bit"
    saved = (await client.post("/api/settings", json={"paused": True})).json()
    assert saved["settings"]["paused"] is True and "paused" in saved["message"]
    assert (await client.post("/api/settings", json={"max_steps": 1000})).status_code == 400
    assert (await client.post("/api/settings", json={"colour": "red"})).status_code == 400
    # The model is set in the shared file: the page may send it back unchanged, not change it.
    assert data["model_file"].endswith("local-ai-model.json")
    same = await client.post("/api/settings", json={"llm_model": "gemma-4-31b-it-8bit", "max_steps": 40})
    assert same.status_code == 200 and same.json()["settings"]["max_steps"] == 40
    other = await client.post("/api/settings", json={"llm_model": "gemma-4-26b-a4b-it-8bit"})
    assert other.status_code == 400 and "local-ai-model.json" in other.text
    status = (await client.get("/api/status")).json()
    assert status["worker"]["state"] == "offline"  # no heartbeat yet


async def test_status_reports_running_job(client, store):
    job_id, _ = store.enqueue("research", "Busy", {}, [])
    store.heartbeat(job_id, "Busy")
    status = (await client.get("/api/status")).json()
    assert status["worker"]["state"] == "running" and status["worker"]["current_job"] == {"id": job_id, "title": "Busy"}
    assert status["recent_jobs"][0]["title"] == "Busy"


async def test_site_logins_keep_passwords_in_keychain(client, ctx, memory_keyring, monkeypatch):
    body = {
        "label": "Eventbrite",
        "login_url": "https://www.eventbrite.co.uk/signin/",
        "username": "joe@example.com",
        "password": "hunter2",
    }
    account = (await client.post("/api/accounts", json=body)).json()["account"]
    assert account["site"] == "eventbrite" and account["has_password"] and "password" not in account
    assert memory_keyring.data[("task-agent", "site:eventbrite")] == "hunter2"
    assert "hunter2" not in (await client.get("/api/accounts")).text

    kept = (await client.post("/api/accounts", json={**body, "site": "eventbrite", "password": ""})).json()
    assert kept["account"]["has_password"] and memory_keyring.data[("task-agent", "site:eventbrite")] == "hunter2"

    bad = await client.post("/api/accounts", json={**body, "login_url": "eventbrite.co.uk"})
    assert bad.status_code == 400

    opened: list[str] = []

    async def fake_run(account):
        opened.append(account["site"])

    monkeypatch.setattr(ctx.windows, "_run", fake_run)
    response = await client.post("/api/accounts/eventbrite/login-window")
    assert response.status_code == 200 and "close the tab" in response.json()["message"]

    # Deleting one site's login never wipes the browser profile every agent shares.
    profile = ctx.accounts.shared_profile
    profile.mkdir(parents=True)
    (profile / "Cookies").write_text("x")
    assert (await client.post("/api/accounts/eventbrite/delete")).status_code == 200
    assert profile.exists() and ("task-agent", "site:eventbrite") not in memory_keyring.data
    assert opened == ["eventbrite"]


async def test_email_account_and_connection_test(client, memory_keyring, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(email_account, "_check_imap", lambda account, pw: calls.append(f"imap:{pw}"))
    monkeypatch.setattr(email_account, "_check_smtp", lambda account, pw: calls.append(f"smtp:{pw}"))

    early = (await client.post("/api/email/test")).json()
    assert early["ok"] is False and "Fill in" in early["message"]

    body = {
        "username": "joe@example.com",
        "from_name": "Joe",
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "password": "app-pass",
    }
    saved = (await client.post("/api/email", json=body)).json()["email"]
    assert saved["has_password"] and "password" not in saved
    result = (await client.post("/api/email/test")).json()
    assert result["ok"] is True and calls == ["imap:app-pass", "smtp:app-pass"]
    assert result["email"]["last_test_ok"] is True


async def test_approvals(client, store):
    job_id, _ = store.enqueue("research", "Reply to venue", {}, [])
    with store._connect() as db:
        db.execute(
            "INSERT INTO approvals (job_id, action, preview, created_at) VALUES (?, 'send_email', 'Hi', 't')", (job_id,)
        )
    pending = (await client.get("/api/approvals")).json()
    assert pending["counts"]["pending"] == 1 and pending["approvals"][0]["job_title"] == "Reply to venue"
    approval_id = pending["approvals"][0]["id"]
    decided = (await client.post(f"/api/approvals/{approval_id}/decide", json={"decision": "approve"})).json()
    assert decided["approval"]["status"] == "approved" and decided["approval"]["decided_by"] == "admin"
    again = await client.post(f"/api/approvals/{approval_id}/decide", json={"decision": "reject"})
    assert again.status_code == 400


async def test_telegram_settings(client, ctx, memory_keyring):
    import respx

    me = "https://api.telegram.org/bot123:abc/getMe"
    with respx.mock() as mock:
        mock.post(me).mock(return_value=httpx.Response(200, json={"ok": True, "result": {"username": "joe_bot"}}))
        mock.post("https://api.telegram.org/botbad/getMe").mock(
            return_value=httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
        )
        bad = await client.post("/api/telegram", json={"token": "bad"})
        assert bad.status_code == 400 and "Unauthorized" in bad.json()["detail"]
        saved = (await client.post("/api/telegram", json={"token": "123:abc", "owner_username": "@JoeBains"})).json()
    assert memory_keyring.data[("task-agent", "telegram:bot_token")] == "123:abc"
    view = saved["telegram"]
    assert view["configured"] and view["bot_username"] == "joe_bot" and view["owner_username"] == "JoeBains"
    assert "123:abc" not in json.dumps(saved) and "send /start" in saved["message"]
    not_linked = await client.post("/api/telegram/test")
    assert not_linked.status_code == 400 and "/start" in not_linked.json()["detail"]
    gone = (await client.post("/api/telegram/disconnect")).json()["telegram"]
    assert not gone["configured"] and ("task-agent", "telegram:bot_token") not in memory_keyring.data
