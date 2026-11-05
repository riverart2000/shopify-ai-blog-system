"""Event types (Comedy, Bloomsbury speakers, …): their store, their own logins, reading codes from their inbox,
Brave Search, and the admin API. No real Keychain, inbox, browser or internet."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
import respx
from pydantic import SecretStr

from task_agent.accounts import AccountStore
from task_agent.api import AppContext, create_app
from task_agent.event_types import EventTypeError, EventTypeStore, codes_and_links, site_words
from task_agent.keychain import get_secret
from task_agent.tools import browser as b
from task_agent.tools import web


@pytest.fixture
def kinds(store) -> EventTypeStore:
    return EventTypeStore(store)


@pytest.fixture
def accounts(store, settings) -> AccountStore:
    return AccountStore(store, settings.path(settings.profiles_dir))


def test_the_four_types_are_there_once(store, settings, kinds):
    assert [k["name"] for k in kinds.list()] == ["Biohacking", "Bloomsbury speakers", "Comedy", "Juggling"]
    kinds.delete("juggling")
    type(store)(settings.path(settings.db_path))  # the worker starting again doesn't bring it back
    assert "juggling" not in kinds.names()


def test_a_type_uses_the_email_account_password_for_the_same_mailbox(store, kinds):
    from task_agent.email_account import EmailStore

    EmailStore(store).save(
        {"username": "comedy@joebains.com", "imap_host": "mail.example.net", "smtp_host": "smtp.example"},
        password="mailbox-pass",
    )
    comedy = kinds.save({"email": "Comedy@joebains.com", "imap_host": "mail.example.net"}, key="comedy")
    assert comedy["has_imap_password"] and comedy["imap_password_from"] == "email_account"
    assert kinds.inbox_ready(comedy) and kinds.imap_password("comedy") == "mailbox-pass"
    assert get_secret("event-type:comedy:imap") is None  # stored once, under the Email account

    other = kinds.save({"email": "info@joebains.com", "imap_host": "mail.example.net"}, key="juggling")
    assert not other["has_imap_password"] and kinds.imap_password("juggling") == ""

    own = kinds.save({}, imap_password="own-pass", key="comedy")  # its own password wins
    assert own["imap_password_from"] == "own" and kinds.imap_password("comedy") == "own-pass"


def test_save_checks_and_keeps_the_inbox_password_in_the_keychain(kinds):
    saved = kinds.save(
        {"email": "info@joebains.com", "imap_host": "imap.gmail.com", "facebook_page": "Joe Bains Comedy"},
        imap_password="app-pass",
        key="comedy",
    )
    assert saved["email"] == "info@joebains.com" and saved["has_imap_password"] and saved["imap_port"] == 993
    assert get_secret("event-type:comedy:imap") == "app-pass"
    assert kinds.inbox_ready(saved)
    new = kinds.save({"name": "Magic shows"})
    assert new["key"] == "magic-shows" and new["enabled"]
    assert kinds.find("Magic Shows")["key"] == "magic-shows"
    with pytest.raises(EventTypeError, match="email"):
        kinds.save({"email": "info at joebains"}, key="comedy")
    with pytest.raises(EventTypeError, match="name"):
        kinds.save({"email": "x@y.com"})


def test_logins_belong_to_a_type_or_are_shared(kinds, accounts):
    accounts.save(label="Facebook", login_url="https://www.facebook.com/login", username="joe")
    comedy = accounts.save(label="Eventbrite", login_url="https://www.eventbrite.co.uk/signin/",
                           username="info@joebains.com", password="pw1", event_type="comedy")  # fmt: skip
    talks = accounts.save(label="Eventbrite", login_url="https://www.eventbrite.co.uk/signin/",
                          username="talks@example.com", password="pw2", event_type="bloomsbury-speakers")  # fmt: skip
    assert comedy["site"] == "eventbrite-comedy" and comedy["event_type_name"] == "Comedy"
    assert talks["site"] == "eventbrite-bloomsbury-speakers"
    assert {a["site"] for a in accounts.list(event_type="comedy")} == {"facebook", "eventbrite-comedy"}
    with pytest.raises(ValueError, match="no event type"):
        accounts.save(label="Meetup", login_url="https://www.meetup.com/login/", username="x", event_type="nope")

    kinds.delete("comedy")  # its own logins and passwords go, shared ones stay
    assert {a["site"] for a in accounts.list()} == {"facebook", "eventbrite-bloomsbury-speakers"}
    assert get_secret("site:eventbrite-comedy") is None


def deps_for(store, accounts, event_type: str = "comedy"):
    payload = SimpleNamespace(event_type=event_type)
    return SimpleNamespace(deps=SimpleNamespace(store=store, accounts=accounts, payload=payload, browser=None,
                                                owner="job:1"))  # fmt: skip


async def test_an_agent_only_sees_and_types_its_types_logins(store, accounts):
    accounts.save(label="Facebook", login_url="https://www.facebook.com/login", username="joe", password="fb")
    accounts.save(label="Meetup", login_url="https://www.meetup.com/login/", username="talks@x.com", password="m",
                  event_type="bloomsbury-speakers")  # fmt: skip
    ctx = deps_for(store, accounts)
    listed = await b.saved_logins(ctx)  # type: ignore[arg-type]
    assert "facebook" in listed and "meetup-bloomsbury-speakers" not in listed
    refused = await b.type_saved_login(ctx, "e1", "meetup-bloomsbury-speakers", "password")  # type: ignore[arg-type]
    assert "another event type" in refused
    made = await b.save_new_login(ctx, "Allevents", "https://allevents.in/login", "info@joebains.com")  # type: ignore[arg-type]
    assert "allevents-comedy" in made and accounts.get("allevents-comedy")["event_type"] == "comedy"


def test_codes_and_site_links_are_all_that_comes_out_of_an_email():
    message = {
        "subject": "Your Meetup verification code",
        "text": "Hi! 482913 is your code. Or confirm here: https://secure.meetup.com/verify-email?token=abc "
        "Unsubscribe: https://www.meetup.com/account/comm/ and see https://evil.example/verify?x=1",
    }
    words = site_words("Meetup", "https://www.meetup.com/create/")
    codes, links = codes_and_links(message, words)
    assert codes == ["482913"]
    assert links == ["https://secure.meetup.com/verify-email?token=abc"]  # other sites' links never come out
    assert site_words("https://www.eventbrite.co.uk/signin/") == {"eventbrite"}


async def test_check_inbox_reads_only_the_sites_email(store, accounts, monkeypatch):
    received = datetime(2026, 11, 4, 15, 30, tzinfo=UTC)

    async def recent(self, key: str, minutes: int = 30, limit: int = 12):
        assert key == "comedy"
        return [
            {"from": "Eventbrite <noreply@eventbrite.com>", "from_address": "noreply@eventbrite.com",
             "subject": "Your sign-in code", "received": received, "text": "Use code 731204 to sign in."},
            {"from": "Bank <alerts@bank.example>", "from_address": "alerts@bank.example",
             "subject": "Your code", "received": received, "text": "Your code is 111222"},
        ]  # fmt: skip

    monkeypatch.setattr(EventTypeStore, "recent_messages", recent)
    out = await b.check_inbox(deps_for(store, accounts), "Eventbrite")  # type: ignore[arg-type]
    assert "731204" in out and "111222" not in out and "<untrusted_data" in out
    no_type = await b.check_inbox(deps_for(store, accounts, event_type=""), "Eventbrite")  # type: ignore[arg-type]
    assert "ask_joe" in no_type


async def test_check_inbox_without_an_inbox_says_ask_joe(store, accounts):
    out = await b.check_inbox(deps_for(store, accounts), "Meetup")  # type: ignore[arg-type]
    assert "isn't set up" in out and "ask_joe" in out


async def test_brave_search_comes_first_when_there_is_a_key(monkeypatch):
    monkeypatch.setattr(web, "brave_key", lambda: "test-key")
    with respx.mock as mock:
        route = mock.get(web.BRAVE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {
                                "title": "Comedy <strong>London</strong>",
                                "url": "https://comedy.example/",
                                "description": "Gigs &amp; more",
                            },
                        ]
                    }
                },
            )  # fmt: skip
        )
        out = await web.search("comedy london")
    assert route.called and route.calls[0].request.headers["x-subscription-token"] == "test-key"
    assert "Comedy London" in out and "Gigs & more" in out and "https://comedy.example/" in out


@pytest.fixture
async def client(store, settings):
    ctx = AppContext.build(store, settings.model_copy(update={"task_agent_api_key": SecretStr("k")}))
    transport = httpx.ASGITransport(app=create_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://agent", headers={"x-api-key": "k"}) as c:
        yield c


async def test_admin_api_for_event_types(client):
    listed = (await client.get("/api/event-types")).json()
    assert len(listed["event_types"]) == 4 and listed["shared_logins"] == []
    saved = await client.post(
        "/api/event-types", json={"key": "comedy", "email": "info@joebains.com", "imap_password": "secret"}
    )
    body = saved.json()
    assert saved.status_code == 200 and body["event_type"]["has_imap_password"] and "secret" not in saved.text
    meetup = {"label": "Meetup", "login_url": "https://www.meetup.com/login/", "username": "info@joebains.com"}
    login = await client.post("/api/accounts", json={**meetup, "event_type": "comedy"})
    assert login.json()["account"]["event_type_name"] == "Comedy"
    comedy = next(k for k in (await client.get("/api/event-types")).json()["event_types"] if k["key"] == "comedy")
    assert [a["site"] for a in comedy["logins"]] == ["meetup-comedy"]
    test = (await client.post("/api/event-types/comedy/test-email")).json()
    assert test["ok"] is False and "IMAP host" in test["message"]
    bad = await client.post("/api/event-types", json={"key": "comedy", "email": "nope"})
    assert bad.status_code == 400 and "email" in bad.json()["detail"]
    gone = await client.post("/api/event-types/juggling/delete")
    assert gone.status_code == 200 and "Juggling" in gone.json()["message"]
    templates = (await client.get("/api/templates")).json()["templates"]
    promote = next(t for t in templates if t["key"] == "promote-event")
    field = next(f for f in promote["fields"] if f["name"] == "event_type")
    assert [o["value"] for o in field["options"]] == ["biohacking", "bloomsbury-speakers", "comedy"]


async def test_job_detail_names_the_event_type(client):
    job = (
        await client.post(
            "/api/jobs",
            json={"template_key": "promote-event", "values": {"event_type": "comedy", "event_urls": "https://x.com/e"}},
        )
    ).json()["job"]
    detail = (await client.get(f"/api/jobs/{job['id']}")).json()["job"]
    assert detail["event_type"] == {"key": "comedy", "name": "Comedy"}
