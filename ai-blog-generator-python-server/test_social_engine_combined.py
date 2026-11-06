"""An extra Postiz server beside the main one (publishers/combined.py).

Two fake Postiz servers stand in for Postiz Cloud and a self-hosted Postiz; nothing here touches the network.
"""
from __future__ import annotations

import json
import time
from datetime import timedelta

import httpx
import pytest
import pytest_asyncio

from db.social_engine import config_store
from db.social_engine import posts as post_store
from services.social_engine import http as se_http
from services.social_engine.context import load_store_context
from services.social_engine.errors import ConfigurationError, ValidationError
from services.social_engine.pipeline.channels import sync_channels
from services.social_engine.pipeline.reconcile import reconcile_store
from services.social_engine.publishers import CombinedPublisher, OutboundPost, PostizPublisher, get_publisher
from services.social_engine.settings import EngineSettings, public_settings, save_settings
from test_social_engine_postiz import API_KEY, WHEN, FakePostiz, _media, _target

LOCAL = {"name": "local", "label": "This Mac", "base_url": "http://postiz.test/public/v1",
         "api_key_env": "POSTIZ_LOCAL_API_KEY"}
BLUESKY = {"id": "ch_bsky", "name": "bioluxelab.bsky.social", "identifier": "bluesky", "picture": "",
           "disabled": False, "profile": "bioluxelab.bsky.social"}


def _down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


@pytest_asyncio.fixture
async def two(se_db, monkeypatch):
    monkeypatch.setenv("POSTIZ_API_KEY", API_KEY)
    monkeypatch.setenv("POSTIZ_LOCAL_API_KEY", API_KEY)
    monkeypatch.setenv("SOCIAL_ENGINE_LIVE_PUBLISHING", "1")
    main, local = FakePostiz(), FakePostiz()
    local.integrations = [dict(BLUESKY)]
    se_http.TRANSPORT_OVERRIDES["postiz"] = httpx.MockTransport(main.handler)
    se_http.TRANSPORT_OVERRIDES["postiz:local"] = httpx.MockTransport(local.handler)
    await save_settings(se_db, {"postiz": {"servers": [LOCAL]}})
    return main, local


async def _publisher(store_id: str) -> CombinedPublisher:
    publisher = get_publisher(await load_store_context(store_id, correlation_id="corr-test"), correlation_id="corr-test")
    assert isinstance(publisher, CombinedPublisher)
    return publisher


@pytest.mark.asyncio
async def test_channels_of_the_extra_server_carry_its_name(two, se_db):
    main, local = two
    publisher = await _publisher(se_db)
    channels = await publisher.list_channels()
    rules = await publisher.channel_rules("local:ch_bsky")
    await publisher.aclose()

    assert [c.channel_id for c in channels] == ["ch_ig", "ch_pin", "ch_other", "local:ch_bsky"]
    assert rules.max_length == 500
    assert local.last("GET", "/integration-settings/ch_bsky")          # asked the extra server, without the prefix
    assert not [r for r in main.requests if "ch_bsky" in r.url.path]    # and never the main one


@pytest.mark.asyncio
async def test_a_post_goes_to_the_server_its_channel_is_on(two, se_db):
    main, local = two
    publisher = await _publisher(se_db)
    post = OutboundPost(post_id="post_1", group="post_1", mode="schedule", targets=[
        _target("t_bsky", "local:ch_bsky", "bluesky", media=[_media("img1")]),
    ])
    published = await publisher.create_posts(post)
    await publisher.delete_post(published[0].external_id)
    await publisher.aclose()

    assert not main.create_bodies and len(local.create_bodies) == 1
    sent = local.create_bodies[0]["posts"][0]
    assert sent["integration"] == {"id": "ch_bsky"}
    # The picture is the main Postiz's public address: a private server can't hand files to the networks.
    assert sent["value"][0]["image"] == [{"id": "pzm_img1", "path": "https://uploads.postiz.com/img1"}]
    assert published[0].channel_id == "local:ch_bsky" and published[0].external_id.startswith("local:")
    assert local.posts == []                                            # the delete reached the extra server


@pytest.mark.asyncio
async def test_one_request_never_mixes_servers(two, se_db):
    publisher = await _publisher(se_db)
    post = OutboundPost(post_id="post_1", group="post_1", mode="schedule", targets=[
        _target("t_ig", "ch_ig", "instagram"), _target("t_bsky", "local:ch_bsky", "bluesky"),
    ])
    with pytest.raises(ValidationError, match="one Postiz server"):
        await publisher.create_posts(post)
    await publisher.aclose()


@pytest.mark.asyncio
async def test_an_extra_server_that_is_down_does_not_stop_the_main_one(two, se_db):
    se_http.TRANSPORT_OVERRIDES["postiz:local"] = httpx.MockTransport(_down)
    publisher = await _publisher(se_db)
    channels = await publisher.list_channels()
    posts = await publisher.list_posts(WHEN - timedelta(days=1), WHEN + timedelta(days=1))
    info = await publisher.check_connection()
    await publisher.aclose()

    assert [c.channel_id for c in channels] == ["ch_ig", "ch_pin", "ch_other"] and posts == []
    assert "local" in publisher.unreachable
    assert info["connected"] is True and info["servers"]["local"]["connected"] is False


@pytest.mark.asyncio
async def test_a_missing_key_only_stops_that_servers_channels(two, se_db, monkeypatch):
    monkeypatch.delenv("POSTIZ_LOCAL_API_KEY")
    publisher = await _publisher(se_db)
    channels = await publisher.list_channels()
    with pytest.raises(ConfigurationError, match="POSTIZ_LOCAL_API_KEY"):
        await publisher.channel_rules("local:ch_bsky")
    await publisher.aclose()
    assert [c.channel_id for c in channels] == ["ch_ig", "ch_pin", "ch_other"]


@pytest.mark.asyncio
async def test_without_extra_servers_nothing_changes(two, se_db):
    await save_settings(se_db, {"postiz": {"servers": [{**LOCAL, "enabled": False}]}})
    publisher = get_publisher(await load_store_context(se_db))
    assert isinstance(publisher, PostizPublisher)
    await publisher.aclose()


@pytest.mark.asyncio
async def test_channel_sync_keeps_an_unreachable_servers_channels(two, se_db):
    await save_settings(se_db, {"postiz": {"customer_id": "cust_1"}})   # the main Postiz's filter, not the extra one's
    first = await sync_channels(se_db, with_rules=False)
    assert "bioluxelab.bsky.social (bluesky)" in first["new"]

    se_http.TRANSPORT_OVERRIDES["postiz:local"] = httpx.MockTransport(_down)
    second = await sync_channels(se_db, with_rules=False)
    channels = {c["channel_id"]: c for c in await config_store.list_channels(se_db)}
    assert second["missing"] == 0 and not channels["local:ch_bsky"].get("missing_at")
    assert any("local" in error for error in second["rule_errors"])


@pytest.mark.asyncio
async def test_reconcile_leaves_an_unreachable_servers_posts_alone(two, se_db):
    old = int(time.time()) - 7200
    post_id, _ = await post_store.create_post(se_db, {"content_type": "product_spotlight", "status": "scheduled",
                                                      "scheduled_at": old})
    await post_store.replace_targets(se_db, post_id, [
        {"channel_id": "local:ch_bsky", "provider": "bluesky", "caption": "Hello", "scheduled_at": old},
    ])
    target = (await post_store.list_targets_for_post(post_id))[0]
    await post_store.update_target(target["id"], status="scheduled", external_id="local:pz_1")

    se_http.TRANSPORT_OVERRIDES["postiz:local"] = httpx.MockTransport(_down)
    counts = await reconcile_store(se_db)
    assert counts["missing"] == 0
    assert (await post_store.list_targets_for_post(post_id))[0]["status"] == "scheduled"

    # Back up, and the post really isn't there: now it is reported.
    se_http.TRANSPORT_OVERRIDES["postiz:local"] = httpx.MockTransport(FakePostiz().handler)
    assert (await reconcile_store(se_db))["missing"] == 1


def test_extra_server_settings_are_checked(monkeypatch):
    with pytest.raises(ValueError, match="its own name"):
        EngineSettings.model_validate({"postiz": {"servers": [LOCAL, LOCAL]}})
    for bad in ({**LOCAL, "name": "Local Mac"}, {**LOCAL, "base_url": "postiz.test"}, {**LOCAL, "api_key_env": "key"}):
        with pytest.raises(ValueError):
            EngineSettings.model_validate({"postiz": {"servers": [bad]}})
    assert EngineSettings().postiz.servers == []                       # nothing is built in

    monkeypatch.setenv("POSTIZ_LOCAL_API_KEY", "secret-value")
    shown = public_settings(EngineSettings.model_validate({"postiz": {"servers": [LOCAL]}}))
    assert shown["postiz"]["servers"][0]["api_key_configured"] is True
    assert "secret-value" not in json.dumps(shown)
