"""Social Engine publishers: Postiz client/publisher, provider settings, dry run, webhooks.

A fake Postiz (httpx.MockTransport) stands in for the real API; nothing here
touches the network.
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
import pytest_asyncio

from db.social_engine import observability
from services.social_engine import http as se_http
from services.social_engine.context import load_store_context
from services.social_engine.errors import (
    ConfigurationError, ExternalServiceError, RateLimited, UncertainOutcome, ValidationError,
)
from services.social_engine.publishers import (
    DryRunPublisher, OutboundMedia, OutboundPost, OutboundTarget, PartialPublishError, PostizPublisher,
    PublishedTarget, Publisher, build_settings, caption_matches, extract_post_ids, get_publisher,
    payload_fingerprint,
)
from services.social_engine.publishers.postiz import classify_notification, map_post_metrics
from services.social_engine.publishers.postiz_settings import EMPTY_SETTINGS_PROVIDERS, truncate
from services.social_engine.settings import save_settings

API_KEY = "pz_test_key_123"
WHEN = datetime(2026, 11, 2, 18, 30, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fake Postiz
# ---------------------------------------------------------------------------

class FakePostiz:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.bad_auth = 0
        self.posts: list[dict] = []
        self.create_bodies: list[dict] = []
        self.create_status = 200
        self.create_raw: str | None = None
        self.create_drop: set[str] = set()
        self.reverse_response = False
        self.upload_status = "ready"
        self.media_statuses: list[dict] = []
        self.post_analytics: list[dict] = []
        self.platform_analytics: list[dict] = []
        self.notifications: list[dict] = []
        self.video_bodies: list[dict] = []
        self.integrations = [
            {"id": "ch_ig", "name": "Bioluxe IG", "identifier": "instagram", "picture": "https://p/ig.jpg",
             "disabled": False, "profile": "bioluxelab", "customer": {"id": "cust_1", "name": "Bioluxe"}},
            {"id": "ch_pin", "name": "Bioluxe Pins", "identifier": "pinterest", "picture": "", "disabled": True,
             "profile": "bioluxe", "customer": {"id": "cust_1", "name": "Bioluxe"}},
            {"id": "ch_other", "name": "Other brand", "identifier": "x", "picture": "", "disabled": False,
             "profile": "other", "customer": {"id": "cust_2", "name": "Other"}},
        ]

    def calls(self, method: str, path: str) -> int:
        return sum(1 for r in self.requests if r.method == method and self._path(r) == path)

    def last(self, method: str, path: str) -> httpx.Request:
        return [r for r in self.requests if r.method == method and self._path(r) == path][-1]

    @staticmethod
    def _path(request: httpx.Request) -> str:
        return request.url.path.removeprefix("/public/v1")

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != API_KEY:
            self.bad_auth += 1
            return httpx.Response(401, json={"message": "Invalid API key"})
        method, path = request.method, self._path(request)
        query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        parts = path.strip("/").split("/")

        if (method, path) == ("GET", "/is-connected"):
            return httpx.Response(200, json={"connected": True})
        if (method, path) == ("GET", "/me"):
            return httpx.Response(200, json={"organization": {"id": "org_1", "name": "Bioluxe"}, "user": None})
        if (method, path) == ("GET", "/integrations"):
            group = query.get("group")
            items = [i for i in self.integrations if not group or i["customer"]["id"] == group]
            return httpx.Response(200, json=items)
        if method == "GET" and parts[0] == "integration-settings":
            return httpx.Response(200, json={"output": {
                "rules": "Pins need one image", "maxLength": 500, "settings": {"type": "object"},
                "tools": [{"methodName": "boards", "description": "List boards", "dataSchema": []}],
            }})
        if method == "POST" and parts[0] == "integration-trigger":
            return httpx.Response(200, json={"output": [{"id": "board_1", "name": "Serums"}]})
        if method == "GET" and parts[0] == "find-slot":
            return httpx.Response(200, json={"date": "2026-11-03T09:00:00.000Z"})
        if (method, path) == ("GET", "/posts"):
            start = datetime.fromisoformat(query["startDate"])
            end = datetime.fromisoformat(query["endDate"])
            selected = [p for p in self.posts if start <= datetime.fromisoformat(p["publishDate"]) <= end]
            return httpx.Response(200, json={"posts": selected})
        if (method, path) == ("POST", "/posts"):
            return self._create(request)
        if method == "DELETE" and parts[0] == "posts" and len(parts) == 2:
            before = len(self.posts)
            self.posts = [p for p in self.posts if p["id"] != parts[1]]
            if len(self.posts) == before:
                return httpx.Response(404, json={"message": "Post not found"})
            return httpx.Response(200, json={"id": parts[1]})
        if method == "PUT" and parts[0] == "posts" and parts[-1] == "status":
            body = json.loads(request.content)
            return httpx.Response(200, json={"id": parts[1], "state": "DRAFT" if body["status"] == "draft" else "QUEUE"})
        if method == "PUT" and parts[0] == "posts" and parts[-1] == "settings":
            return httpx.Response(200, json={"postId": parts[1], "publishDate": "2026-11-02T18:30:00"})
        if method == "GET" and parts[:2] == ["analytics", "post"]:
            return httpx.Response(200, json=self.post_analytics)
        if method == "GET" and parts[0] == "analytics":
            return httpx.Response(200, json=self.platform_analytics)
        if (method, path) == ("POST", "/upload"):
            return httpx.Response(200, json={
                "id": "media_1", "name": "upload", "path": "https://uploads.postiz.com/media_1.bin",
                "status": self.upload_status,
            })
        if method == "GET" and parts[0] == "media" and parts[-1] == "status":
            return httpx.Response(200, json=self.media_statuses.pop(0))
        if (method, path) == ("GET", "/notifications"):
            return httpx.Response(200, json={"notifications": self.notifications, "total": len(self.notifications),
                                             "page": 0, "limit": 100, "hasMore": False})
        if (method, path) == ("POST", "/generate-video"):
            self.video_bodies.append(json.loads(request.content))
            return httpx.Response(200, json=[{"id": "video_1", "path": "https://uploads.postiz.com/video_1.mp4"}])
        return httpx.Response(404, json={"message": f"no fake route for {method} {path}"})

    def _create(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.create_bodies.append(body)
        if self.create_status != 200:
            return httpx.Response(self.create_status, text="Internal server error")
        if self.create_raw is not None:
            return httpx.Response(200, text=self.create_raw)
        results = []
        for item in body["posts"]:
            post_id = f"pz_{len(self.posts) + 1}"
            self.posts.append({
                "id": post_id, "content": item["value"][0]["content"], "publishDate": body["date"],
                "state": "QUEUE", "releaseURL": "", "error": None, "group": item.get("group"),
                "integration": {"id": item["integration"]["id"], "providerIdentifier": item["settings"]["__type"]},
            })
            if item["integration"]["id"] not in self.create_drop:
                results.append({"postId": post_id, "integration": item["integration"]["id"]})
        if self.reverse_response:
            results.reverse()
        return httpx.Response(200, json=results)


@pytest_asyncio.fixture
async def fake(se_db, monkeypatch):
    monkeypatch.setenv("POSTIZ_API_KEY", API_KEY)
    monkeypatch.setenv("SOCIAL_ENGINE_LIVE_PUBLISHING", "1")  # these tests simulate the live system
    postiz = FakePostiz()
    se_http.TRANSPORT_OVERRIDES["postiz"] = httpx.MockTransport(postiz.handler)
    return postiz


async def _publisher(store_id: str, patch: dict | None = None, **kwargs) -> PostizPublisher:
    if patch:
        await save_settings(store_id, patch)
    sctx = await load_store_context(store_id, correlation_id="corr-test")
    if kwargs:
        return PostizPublisher(sctx, sctx.call_context("corr-test"), **kwargs)
    publisher = get_publisher(sctx, correlation_id="corr-test")
    assert isinstance(publisher, PostizPublisher)
    return publisher


def _media(asset: str, kind: str = "image", uploaded: bool = True) -> OutboundMedia:
    return OutboundMedia(
        asset_id=asset, path=f"/nonexistent/{asset}", mime="video/mp4" if kind == "video" else "image/jpeg",
        kind=kind, external_id=f"pzm_{asset}" if uploaded else "",
        external_path=f"https://uploads.postiz.com/{asset}" if uploaded else "",
    )


def _target(target_id: str, channel_id: str, provider: str, *, caption: str = "Our vitamin C serum, back in stock",
            media: list[OutboundMedia] | None = None, comments: list[str] | None = None,
            settings: dict | None = None, title: str = "", link_url: str = "", when: datetime = WHEN) -> OutboundTarget:
    return OutboundTarget(
        target_id=target_id, channel_id=channel_id, provider=provider, caption=caption, scheduled_at=when,
        media=[_media(f"{target_id}_img")] if media is None else media, comments=comments or [],
        settings=settings or {}, title=title, link_url=link_url,
    )


# ---------------------------------------------------------------------------
# create_posts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_instagram_with_first_comment_payload_and_mapping(fake, se_db):
    publisher = await _publisher(se_db)
    post = OutboundPost(
        post_id="post_1", group="grp1", mode="schedule", tags=["Autopilot", "#autopilot", "Serums"],
        targets=[_target(
            "t_ig", "ch_ig", "instagram", caption="Glow up ✨\n\nLink in bio",
            media=[_media("img1"), _media("img2")],
            comments=["Shop it: https://bioluxelab.com/products/serum?utm_source=instagram", "   "],
        )],
    )
    published = await publisher.create_posts(post)
    await publisher.aclose()

    assert fake.bad_auth == 0
    request = fake.last("POST", "/posts")
    assert request.headers["authorization"] == API_KEY and "bearer" not in request.headers["authorization"].lower()
    body = fake.create_bodies[0]
    assert body["type"] == "schedule" and body["date"] == "2026-11-02T18:30:00.000Z"
    assert body["shortLink"] is False
    assert body["tags"] == [{"value": "Autopilot", "label": "Autopilot"}, {"value": "Serums", "label": "Serums"}]
    assert body["posts"] == [{
        "integration": {"id": "ch_ig"},
        "value": [
            {"content": "Glow up ✨\n\nLink in bio", "image": [
                {"id": "pzm_img1", "path": "https://uploads.postiz.com/img1"},
                {"id": "pzm_img2", "path": "https://uploads.postiz.com/img2"},
            ]},
            {"content": "Shop it: https://bioluxelab.com/products/serum?utm_source=instagram", "image": []},
        ],
        "group": "grp1-t_ig",
        "settings": {"__type": "instagram", "post_type": "post", "is_trial_reel": False, "collaborators": []},
    }]
    assert published == [PublishedTarget("t_ig", "ch_ig", "pz_1", "grp1-t_ig")]
    ledger = await observability.list_api_calls(service="postiz", correlation_id="corr-test")
    assert [(row["operation"], row["status_code"]) for row in ledger] == [("posts.create", 200)]


@pytest.mark.asyncio
async def test_create_several_providers_in_one_request_maps_by_integration(fake, se_db):
    publisher = await _publisher(se_db, {"postiz": {"short_links": True, "tag_posts": False}})
    fake.reverse_response = True   # mapping must not rely on order
    long_title = "Vitamin C Serum " * 12
    post = OutboundPost(post_id="post_2", group="grp2", mode="schedule", tags=["ignored"], targets=[
        _target("t_pin", "ch_pin", "pinterest", settings={"board": "board_9", "channel_name": "Pins"},
                title=long_title, link_url="https://bioluxelab.com/products/serum?utm_source=pinterest"),
        _target("t_tt", "ch_tt", "tiktok", media=[_media("vid1", "video")], title=long_title),
        _target("t_yt", "ch_yt", "youtube", media=[_media("vid2", "video")],
                title="Serum <routine> in 30s", settings={"tags": ["skincare", "#serum"]}),
        _target("t_x", "ch_x", "x", media=[], caption="New: vitamin C serum https://bioluxelab.com/s"),
    ])
    published = await publisher.create_posts(post)
    await publisher.aclose()

    body = fake.create_bodies[0]
    assert body["shortLink"] is True and body["tags"] == []
    settings = {item["integration"]["id"]: item["settings"] for item in body["posts"]}
    pin = settings["ch_pin"]
    assert pin["__type"] == "pinterest" and pin["board"] == "board_9"
    assert pin["link"] == "https://bioluxelab.com/products/serum?utm_source=pinterest"
    assert len(pin["title"]) <= 100 and pin["title"].endswith("…") and "channel_name" not in pin
    assert settings["ch_tt"] == {
        "__type": "tiktok", "privacy_level": "PUBLIC_TO_EVERYONE", "duet": False, "stitch": False,
        "comment": True, "autoAddMusic": "no", "brand_content_toggle": False, "brand_organic_toggle": True,
        "content_posting_method": "DIRECT_POST", "video_made_with_ai": False, "title": truncate(long_title, 90),
    }
    assert len(settings["ch_tt"]["title"]) <= 90
    assert settings["ch_yt"] == {
        "__type": "youtube", "title": "Serum routine in 30s", "type": "public", "selfDeclaredMadeForKids": "no",
        "tags": [{"value": "skincare", "label": "skincare"}, {"value": "serum", "label": "serum"}],
    }
    assert settings["ch_x"] == {"__type": "x", "who_can_reply_post": "everyone"}
    x_item = next(i for i in body["posts"] if i["integration"]["id"] == "ch_x")
    assert x_item["value"] == [{"content": "New: vitamin C serum https://bioluxelab.com/s", "image": []}]
    by_target = {p.target_id: p for p in published}
    assert {t: p.external_id for t, p in by_target.items()} == {"t_pin": "pz_1", "t_tt": "pz_2", "t_yt": "pz_3", "t_x": "pz_4"}
    assert by_target["t_x"].channel_id == "ch_x" and by_target["t_x"].external_group == "grp2-t_x"


@pytest.mark.asyncio
async def test_now_mode_sends_current_time(fake, se_db):
    publisher = await _publisher(se_db)
    before = datetime.now(UTC)
    await publisher.create_posts(OutboundPost("p", "g", "now", [_target("t", "ch_ig", "instagram")]))
    await publisher.aclose()
    sent = datetime.fromisoformat(fake.create_bodies[0]["date"])
    assert fake.create_bodies[0]["type"] == "now" and sent >= before.replace(microsecond=0) - timedelta(seconds=1)


@pytest.mark.asyncio
async def test_create_preconditions_are_checked_before_sending(fake, se_db):
    publisher = await _publisher(se_db)
    mixed = OutboundPost("p", "g", "schedule", [
        _target("t1", "ch_ig", "instagram"),
        _target("t2", "ch_x", "x", when=WHEN + timedelta(minutes=5)),
    ])
    with pytest.raises(ValidationError, match="must share scheduled_at"):
        await publisher.create_posts(mixed)

    not_uploaded = OutboundPost("p", "g", "schedule", [
        _target("t1", "ch_ig", "instagram", media=[_media("raw", uploaded=False)]),
    ])
    with pytest.raises(ValidationError, match="has not been uploaded to Postiz"):
        await publisher.create_posts(not_uploaded)

    pinterest_comment = OutboundPost("p", "g", "schedule", [
        _target("t1", "ch_pin", "pinterest", settings={"board": "b"}, comments=["link below"]),
    ])
    with pytest.raises(ValidationError, match="Pinterest does not support comments"):
        await publisher.create_posts(pinterest_comment)

    no_board = OutboundPost("p", "g", "schedule", [
        _target("t1", "ch_pin", "pinterest", settings={"channel_name": "Bioluxe Pins"}),
    ])
    with pytest.raises(ConfigurationError) as info:
        await publisher.create_posts(no_board)
    assert info.value.message == "Pinterest channel Bioluxe Pins has no board selected — set one in Channels"

    youtube_image = OutboundPost("p", "g", "schedule", [_target("t1", "ch_yt", "youtube", title="Hello")])
    with pytest.raises(ValidationError, match="only accepts video"):
        await publisher.create_posts(youtube_image)

    naive = OutboundPost("p", "g", "schedule", [_target("t1", "ch_ig", "instagram", when=datetime(2026, 11, 2, 18))])
    with pytest.raises(ValidationError, match="timezone-aware"):
        await publisher.create_posts(naive)
    await publisher.aclose()
    assert fake.requests == []


@pytest.mark.asyncio
async def test_hourly_budget_raises_rate_limited_without_sending(fake, se_db):
    publisher = await _publisher(se_db, {"postiz": {"create_post_hourly_budget": 3}})
    started = int(time.time())
    for _ in range(3):
        await observability.record_api_call(
            service="postiz", operation="posts.create", method="POST", url_path="/posts",
            status_code=200, ok=True, duration_ms=5,
        )
    with pytest.raises(RateLimited) as info:
        await publisher.create_posts(OutboundPost("p", "g", "schedule", [_target("t", "ch_ig", "instagram")]))
    await publisher.aclose()
    assert fake.requests == []
    assert started + 3600 <= info.value.retry_at <= int(time.time()) + 3606
    assert "3/3" in info.value.message


@pytest.mark.asyncio
async def test_server_error_on_create_is_uncertain_and_not_retried(fake, se_db):
    publisher = await _publisher(se_db)
    fake.create_status = 500
    with pytest.raises(UncertainOutcome) as info:
        await publisher.create_posts(OutboundPost("p", "g", "schedule", [_target("t", "ch_ig", "instagram")]))
    assert fake.calls("POST", "/posts") == 1
    assert info.value.details["sent_date"] == "2026-11-02T18:30:00.000Z"
    assert info.value.details["targets"] == [{"target_id": "t", "channel_id": "ch_ig", "group": "g-t"}]

    fake.create_status = 200
    fake.create_raw = "<html>gateway hiccup</html>"   # 2xx but unreadable: the post may exist
    with pytest.raises(UncertainOutcome, match="not JSON"):
        await publisher.create_posts(OutboundPost("p", "g", "schedule", [_target("t", "ch_ig", "instagram")]))
    await publisher.aclose()
    assert fake.calls("POST", "/posts") == 2


@pytest.mark.asyncio
async def test_partial_create_response_keeps_returned_ids(fake, se_db):
    publisher = await _publisher(se_db)
    fake.create_drop = {"ch_x"}
    post = OutboundPost("p", "g", "schedule", [
        _target("t_ig", "ch_ig", "instagram"),
        _target("t_x", "ch_x", "x", media=[]),
    ])
    with pytest.raises(PartialPublishError) as info:
        await publisher.create_posts(post)
    await publisher.aclose()
    error = info.value
    assert isinstance(error, ExternalServiceError) and error.retryable is False
    assert error.published == [PublishedTarget("t_ig", "ch_ig", "pz_1", "g-t_ig")]
    assert error.missing_target_ids == ["t_x"]
    assert error.details["published"][0]["external_id"] == "pz_1"
    assert "x ch_x (target t_x)" in error.message


# ---------------------------------------------------------------------------
# Reconciliation, listing and post management
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_existing_post_matches_channel_and_caption_prefix(fake, se_db):
    publisher = await _publisher(se_db, {"postiz": {"customer_id": "cust_1"}})
    caption = "Our vitamin C serum is back in stock!\nShop now: https://bioluxelab.com/products/serum?utm_source=x"
    fake.posts = [
        {"id": "wrong_channel", "content": caption, "publishDate": "2026-11-02T18:30:00.000Z",
         "integration": {"id": "ch_other", "providerIdentifier": "x"}, "state": "QUEUE"},
        {"id": "other_caption", "content": "Something else entirely", "publishDate": "2026-11-02T18:31:00.000Z",
         "integration": {"id": "ch_x", "providerIdentifier": "x"}, "state": "QUEUE"},
        {"id": "match", "content": "<p>Our vitamin C serum is back in stock!</p><p>Shop now: https://dub.sh/abc</p>",
         "publishDate": "2026-11-02T18:30:00.000Z", "integration": {"id": "ch_x", "providerIdentifier": "x"},
         "state": "PUBLISHED", "releaseURL": "https://x.com/bioluxe/status/1"},
        {"id": "too_late", "content": caption, "publishDate": "2026-11-02T19:30:00.000Z",
         "integration": {"id": "ch_x", "providerIdentifier": "x"}, "state": "QUEUE"},
    ]
    found = await publisher.find_existing_post("ch_x", WHEN, caption)
    missing = await publisher.find_existing_post("ch_x", WHEN, "A caption nobody posted")
    await publisher.aclose()
    assert found is not None and found.external_id == "match"
    assert found.state == "PUBLISHED" and found.release_url == "https://x.com/bioluxe/status/1"
    assert found.provider == "x" and found.publish_date == WHEN
    assert missing is None
    query = parse_qs(fake.last("GET", "/posts").url.query.decode())
    assert query["startDate"] == ["2026-11-02T18:20:00.000Z"] and query["endDate"] == ["2026-11-02T18:40:00.000Z"]
    assert query["customer"] == ["cust_1"]


def test_caption_matching_rules():
    assert caption_matches("Hello   world &amp; more", "<p>hello world & more</p>")
    assert caption_matches("Shop https://a.com/x?utm=1 now", "Shop https://dub.sh/q now")
    assert caption_matches("A" * 200, "A" * 80 + "B" * 50)          # only the first 80 chars count
    assert not caption_matches("Hello world", "Goodbye world")
    assert caption_matches("", "") and not caption_matches("", "text")


@pytest.mark.asyncio
async def test_post_management_endpoints(fake, se_db):
    publisher = await _publisher(se_db)
    fake.posts = [{"id": "pz_9", "content": "x", "publishDate": "2026-11-02T18:30:00.000Z",
                   "integration": {"id": "ch_x"}, "state": "QUEUE"}]
    await publisher.set_post_status("pz_9", "draft")
    await publisher.update_post_settings("pz_9", {"__type": "x", "who_can_reply_post": "following"})
    await publisher.delete_post("pz_9")
    await publisher.delete_post("pz_9")   # already gone → no error
    slot = await publisher.find_slot("ch_x")
    with pytest.raises(ValidationError):
        await publisher.set_post_status("pz_9", "published")  # type: ignore[arg-type]
    await publisher.aclose()
    assert json.loads(fake.last("PUT", "/posts/pz_9/status").content) == {"status": "draft"}
    assert json.loads(fake.last("PUT", "/posts/pz_9/settings").content) == {"settings": {"who_can_reply_post": "following"}}
    assert fake.calls("DELETE", "/posts/pz_9") == 2
    assert slot == datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_channels_rules_tools_and_connection(fake, se_db):
    publisher = await _publisher(se_db, {"postiz": {"customer_id": "cust_1"}})
    channels = await publisher.list_channels()
    rules = await publisher.channel_rules("ch_pin")
    output = await publisher.run_channel_tool("ch_pin", "boards", {"q": "serum", "limit": 5, "all": True})
    status = await publisher.check_connection()
    await publisher.aclose()
    assert parse_qs(fake.last("GET", "/integrations").url.query.decode()) == {"group": ["cust_1"]}
    assert [(c.channel_id, c.provider, c.disabled, c.customer_id) for c in channels] == [
        ("ch_ig", "instagram", False, "cust_1"), ("ch_pin", "pinterest", True, "cust_1"),
    ]
    assert channels[0].name == "Bioluxe IG" and channels[0].customer_name == "Bioluxe"
    assert rules.max_length == 500 and rules.rules == "Pins need one image" and rules.tools[0]["methodName"] == "boards"
    assert json.loads(fake.last("POST", "/integration-trigger/ch_pin").content) == {
        "methodName": "boards", "data": {"q": "serum", "limit": "5", "all": "true"},
    }
    assert output == [{"id": "board_1", "name": "Serums"}]
    assert status["connected"] is True and status["organization"]["name"] == "Bioluxe"
    assert status["create_post_budget"] == {"used_last_hour": 0, "hourly_budget": 25}


@pytest.mark.asyncio
async def test_bearer_prefix_is_stripped_and_missing_key_is_configuration_error(fake, se_db, monkeypatch):
    monkeypatch.setenv("POSTIZ_API_KEY", f"Bearer {API_KEY}")
    publisher = await _publisher(se_db)
    assert (await publisher.check_connection())["connected"] is True
    await publisher.aclose()
    assert fake.bad_auth == 0

    monkeypatch.delenv("POSTIZ_API_KEY")
    sctx = await load_store_context(se_db)
    with pytest.raises(ConfigurationError, match="POSTIZ_API_KEY"):
        get_publisher(sctx)


@pytest.mark.asyncio
async def test_get_publisher_selects_dry_run(se_db, monkeypatch):
    monkeypatch.setenv("SOCIAL_ENGINE_LIVE_PUBLISHING", "1")
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    await save_settings(se_db, {"publisher": "dry_run"})
    publisher = get_publisher(await load_store_context(se_db), correlation_id="c")
    assert isinstance(publisher, DryRunPublisher) and isinstance(publisher, Publisher)
    monkeypatch.setenv("POSTIZ_API_KEY", API_KEY)
    await save_settings(se_db, {"publisher": "postiz"})
    postiz = get_publisher(await load_store_context(se_db))
    assert isinstance(postiz, PostizPublisher) and isinstance(postiz, Publisher)
    await postiz.aclose()


# ---------------------------------------------------------------------------
# Media upload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upload_video_multipart_then_polls_until_ready(fake, se_db, tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    publisher = await _publisher(se_db, sleep=fake_sleep)
    fake.upload_status = "processing"
    fake.media_statuses = [
        {"id": "media_1", "status": "processing"},
        {"id": "media_1", "status": "ready", "path": "https://uploads.postiz.com/media_1_adjusted.mp4",
         "warning": "Adjustment skipped, original kept"},
    ]
    uploaded = await publisher.upload_media(OutboundMedia("asset_v", str(video), "video/mp4", kind="video"))
    await publisher.aclose()
    assert uploaded.external_id == "media_1" and uploaded.status == "ready"
    assert uploaded.external_path == "https://uploads.postiz.com/media_1_adjusted.mp4"
    assert uploaded.warning == "Adjustment skipped, original kept"
    assert sleeps == [2, 3] and fake.calls("GET", "/media/media_1/status") == 2
    content = fake.last("POST", "/upload").content
    assert b'name="file"; filename="reel.mp4"' in content and b"Content-Type: video/mp4" in content
    assert b'name="adjust"\r\n\r\ntrue' in content and b"ftypmp42" in content


@pytest.mark.asyncio
async def test_upload_image_without_adjust_and_failures(fake, se_db, tmp_path):
    image = tmp_path / "product"
    image.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    publisher = await _publisher(se_db, sleep=_no_sleep)
    uploaded = await publisher.upload_media(OutboundMedia("asset_i", str(image), "image/jpeg"))
    assert uploaded.status == "ready" and uploaded.external_path == "https://uploads.postiz.com/media_1.bin"
    content = fake.last("POST", "/upload").content
    assert b'filename="product.jpg"' in content and b'name="adjust"\r\n\r\nfalse' in content

    fake.upload_status = "processing"
    fake.media_statuses = [{"id": "media_1", "status": "failed", "error": "Unsupported codec"}]
    video = tmp_path / "clip.mov"
    video.write_bytes(b"\x00" * 10)
    with pytest.raises(ExternalServiceError, match="Unsupported codec") as info:
        await publisher.upload_media(OutboundMedia("asset_f", str(video), "video/quicktime", kind="video"))
    assert info.value.retryable is False

    big = tmp_path / "huge.jpg"
    with open(big, "wb") as handle:
        handle.truncate(11 * 1024 * 1024)
    with pytest.raises(ValidationError, match="at most 10 MB"):
        await publisher.upload_media(OutboundMedia("asset_big", str(big), "image/jpeg"))
    with pytest.raises(ValidationError, match="does not exist"):
        await publisher.upload_media(OutboundMedia("asset_missing", str(tmp_path / "nope.jpg"), "image/jpeg"))
    await publisher.aclose()


async def _no_sleep(seconds: float) -> None:
    return None


@pytest.mark.asyncio
async def test_upload_still_processing_after_bounded_wait(fake, se_db, tmp_path):
    video = tmp_path / "slow.mp4"
    video.write_bytes(b"\x00" * 64)
    publisher = await _publisher(se_db, sleep=_no_sleep, media_poll_delays=(1, 1))
    fake.upload_status = "processing"
    fake.media_statuses = [{"id": "media_1", "status": "processing"}] * 2
    uploaded = await publisher.upload_media(OutboundMedia("asset_s", str(video), "video/mp4", kind="video"))
    fake.media_statuses.append({"id": "media_1", "status": "ready", "path": "https://uploads.postiz.com/done.mp4"})
    later = await publisher.media_status("media_1")
    await publisher.aclose()
    assert uploaded.status == "processing" and uploaded.external_id == "media_1"
    assert later.status == "ready" and later.external_path == "https://uploads.postiz.com/done.mp4"


# ---------------------------------------------------------------------------
# Analytics, notifications, AI video
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_metrics_label_mapping(fake, se_db):
    fake.post_analytics = [
        {"label": "Impressions", "data": [{"total": "100", "date": "2026-10-01"}, {"total": "150", "date": "2026-10-02"}]},
        {"label": "Likes", "data": [{"total": "5", "date": "2026-10-02"}, {"total": "3", "date": "2026-10-01"},
                                    {"total": "2", "date": "2026-10-03"}]},
        {"label": "Video views", "data": [{"total": "1,200", "date": "2026-10-01"}]},
        {"label": "Reposts", "data": [{"total": "2", "date": "2026-10-01"}]},
        {"label": "Quotes", "data": [{"total": "1", "date": "2026-10-01"}]},
        {"label": "Replies", "data": [{"total": "4", "date": "2026-10-01"}, {"total": "1", "date": "2026-10-02"}]},
        {"label": "BOOKMARKS", "data": [{"total": "7", "date": "2026-10-01"}]},
        {"label": "Pin clicks", "data": [{"total": "3", "date": "2026-10-01"}]},
        {"label": "Outbound clicks", "data": [{"total": "2", "date": "2026-10-01"}]},
        {"label": "Engagement", "data": [{"total": "0.042", "date": "2026-10-01"}]},
        {"label": "Followers", "data": [{"total": "1250", "date": "2026-10-01"}]},
        {"label": "Reach", "data": []},
    ]
    publisher = await _publisher(se_db)
    metrics = await publisher.post_metrics("pz_1", days=14)
    await publisher.aclose()
    assert metrics.metrics == {
        "impressions": 150,   # cumulative → last value
        "likes": 10,          # not monotonic → daily values summed
        "views": 1200, "shares": 3, "comments": 5, "saves": 7, "clicks": 5,
    }
    assert metrics.raw == fake.post_analytics
    assert parse_qs(fake.last("GET", "/analytics/post/pz_1").url.query.decode()) == {"date": ["14"]}
    assert map_post_metrics([{"label": "Plays", "data": [{"total": 9}]}]) == {"views": 9}


@pytest.mark.asyncio
async def test_channel_metrics_rows(fake, se_db):
    fake.platform_analytics = [
        {"label": "Followers", "data": [{"total": "1250", "date": "2026-10-01"}, {"total": "1280", "date": "2026-10-02"}]},
        {"label": "Impressions", "data": [{"total": "5000", "date": "2026-10-01"}]},
    ]
    publisher = await _publisher(se_db)
    rows = await publisher.channel_metrics("ch_ig", days=7)
    await publisher.aclose()
    assert rows == [
        {"metric": "followers", "label": "Followers", "day": "2026-10-01", "value": 1250},
        {"metric": "followers", "label": "Followers", "day": "2026-10-02", "value": 1280},
        {"metric": "impressions", "label": "Impressions", "day": "2026-10-01", "value": 5000},
    ]
    assert parse_qs(fake.last("GET", "/analytics/ch_ig").url.query.decode()) == {"date": ["7"]}


@pytest.mark.asyncio
async def test_notifications_are_classified(fake, se_db):
    fake.notifications = [
        {"id": "n1", "content": "Your post to X was published successfully", "link": None,
         "createdAt": "2025-01-15T10:30:00.000Z"},
        {"id": "n2", "content": "Failed to publish post to LinkedIn", "link": "https://app/p/1",
         "createdAt": "2025-01-15T09:15:00.000Z"},
        {"id": "n3", "content": "Channel Instagram disconnected, please reconnect", "link": None, "createdAt": None},
        {"id": "n4", "content": "You are close to your AI credits limit", "link": None, "createdAt": None},
    ]
    publisher = await _publisher(se_db)
    items = await publisher.notifications(page=2)
    await publisher.aclose()
    assert [(n.external_id, n.level) for n in items] == [("n1", "info"), ("n2", "error"), ("n3", "error"), ("n4", "warning")]
    assert items[0].created_at == datetime(2025, 1, 15, 10, 30, tzinfo=UTC) and items[1].link == "https://app/p/1"
    assert parse_qs(fake.last("GET", "/notifications").url.query.decode()) == {"page": ["2"]}
    assert classify_notification("<b>Error</b> refreshing token") == "error"


@pytest.mark.asyncio
async def test_ai_video_budget_and_request_shape(fake, se_db):
    publisher = await _publisher(se_db, {"postiz": {"ai_video_monthly_budget": 0}})
    with pytest.raises(ConfigurationError, match="^AI video is disabled"):
        await publisher.generate_video(kind="veo3", prompt="A serum bottle on marble")
    await publisher.aclose()

    publisher = await _publisher(se_db, {"postiz": {"ai_video_monthly_budget": 2}})
    await observability.record_api_call(service="postiz", operation="video.generate", method="POST",
                                        url_path="/generate-video", status_code=200, ok=True, duration_ms=1)
    videos = await publisher.generate_video(
        kind="veo3", prompt="A serum bottle on marble", orientation="vertical",
        image_urls=["https://cdn.shopify.com/a.jpg", "https://cdn.shopify.com/b.jpg"],
    )
    assert [(v.external_id, v.external_path) for v in videos] == [("video_1", "https://uploads.postiz.com/video_1.mp4")]
    body = fake.video_bodies[0]
    assert body["type"] == "veo3" and body["output"] == "vertical"
    assert body["customParams"]["prompt"] == "A serum bottle on marble"
    assert [img["path"] for img in body["customParams"]["images"]] == ["https://cdn.shopify.com/a.jpg", "https://cdn.shopify.com/b.jpg"]
    assert all(img["id"] for img in body["customParams"]["images"])

    with pytest.raises(RateLimited, match="2/2"):
        await publisher.generate_video(kind="image-text-slides", prompt="Morning routine", voice="v1")
    with pytest.raises(ValidationError):
        await publisher.generate_video(kind="sora", prompt="x")
    await publisher.aclose()
    assert fake.calls("POST", "/generate-video") == 1


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dry_run_round_trip(se_db, tmp_path, monkeypatch):
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    await save_settings(se_db, {"publisher": "dry_run"})
    sctx = await load_store_context(se_db)
    clock = {"now": datetime(2026, 11, 1, 12, 0, tzinfo=UTC)}
    publisher = get_publisher(sctx, correlation_id="dry-corr")
    assert isinstance(publisher, DryRunPublisher)
    publisher._clock = lambda: clock["now"]

    channels = await publisher.list_channels()
    assert {c.provider for c in channels} == {"instagram", "facebook", "pinterest", "tiktok", "x"}
    assert (await publisher.check_connection())["connected"] is True

    image = tmp_path / "img.jpg"
    image.write_bytes(b"\xff\xd8" + b"\x00" * 50)
    uploaded = await publisher.upload_media(OutboundMedia("a1", str(image), "image/jpeg"))
    media = OutboundMedia("a1", str(image), "image/jpeg", external_id=uploaded.external_id,
                          external_path=uploaded.external_path)
    when = clock["now"] + timedelta(hours=2)
    caption = "Dry run caption for the vitamin C serum"
    post = OutboundPost("post_d", "grp_d", "schedule", [
        _target("t_ig", "dry-instagram", "instagram", caption=caption, media=[media], when=when),
        _target("t_x", "dry-x", "x", caption=caption, media=[], when=when),
    ])
    published = await publisher.create_posts(post)
    assert [p.target_id for p in published] == ["t_ig", "t_x"]

    with pytest.raises(ConfigurationError, match="no board selected"):
        await publisher.create_posts(OutboundPost("p2", "g2", "schedule", [
            _target("t_pin", "dry-pinterest", "pinterest", media=[media], when=when)]))

    # Another process sees the same posts through engine state.
    other = DryRunPublisher(sctx, clock=lambda: clock["now"])
    listed = await other.list_posts(when - timedelta(hours=1), when + timedelta(hours=1))
    assert {(p.channel_id, p.state) for p in listed} == {("dry-instagram", "QUEUE"), ("dry-x", "QUEUE")}
    found = await other.find_existing_post("dry-instagram", when, caption)
    assert found is not None and found.external_id == published[0].external_id and found.state == "QUEUE"

    clock["now"] = when + timedelta(minutes=1)
    found = await other.find_existing_post("dry-instagram", when, caption)
    assert found is not None and found.state == "PUBLISHED"
    assert found.release_url.startswith("https://dry-run.invalid/instagram/")
    metrics = await other.post_metrics(found.external_id)
    assert set(metrics.metrics.values()) == {0} and "impressions" in metrics.metrics

    await other.set_post_status(published[1].external_id, "draft")
    await other.delete_post(published[0].external_id)
    remaining = await publisher.list_posts(when - timedelta(hours=1), when + timedelta(hours=1))
    assert [(p.channel_id, p.state) for p in remaining] == [("dry-x", "DRAFT")]

    ledger = await observability.list_api_calls(service="dry_run")
    assert "posts.create" in {row["operation"] for row in ledger}
    assert await observability.list_api_calls(service="postiz") == []


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

def test_webhook_post_id_extraction_from_plausible_shapes():
    single = {"id": "p1", "content": "Hi", "publishDate": "2026-11-02T18:30:00.000Z",
              "integration": {"id": "int_1", "name": "X"}, "releaseURL": "https://x.com/s/1"}
    assert extract_post_ids(single) == ["p1"]
    assert extract_post_ids([{"id": "p1", "state": "PUBLISHED", "integration": {"id": "i1"}},
                             {"id": "p2", "releaseURL": "u"}, {"id": "p1", "content": "dupe"}]) == ["p1", "p2"]
    nested = {"event": "post.published", "organization": {"id": "org_1"},
              "data": {"posts": [{"id": "p3", "integration": {"id": "int_9"}, "content": "x"}, {"postId": "p4"}]}}
    assert extract_post_ids(nested) == ["p3", "p4"]
    assert extract_post_ids({"event": {"type": "published", "post": {"id": "p5"}}}) == ["p5"]
    assert extract_post_ids(json.dumps(single).encode()) == ["p1"]
    assert extract_post_ids({"id": "evt_1", "type": "ping"}) == []
    assert extract_post_ids("not json") == [] and extract_post_ids(None) == []


def test_webhook_payload_fingerprint_is_stable():
    a = {"b": 1, "a": [1, 2, {"y": "z", "x": None}]}
    b = '{ "a": [1, 2, {"x": null, "y": "z"}], "b": 1 }'
    assert payload_fingerprint(a) == payload_fingerprint(b) == payload_fingerprint(b.encode())
    assert payload_fingerprint(a) != payload_fingerprint({"b": 2, "a": [1, 2, {"y": "z", "x": None}]})
    assert len(payload_fingerprint("raw text")) == 64


# ---------------------------------------------------------------------------
# Provider settings (one case per provider)
# ---------------------------------------------------------------------------

SETTINGS_CASES = [
    ("x", {}, {"__type": "x", "who_can_reply_post": "everyone"}),
    ("x", {"settings": {"who_can_reply_post": "FOLLOWING", "community": "https://x.com/i/communities/123"}},
     {"__type": "x", "who_can_reply_post": "following", "community": "https://x.com/i/communities/123"}),
    ("instagram", {"image_count": 1},
     {"__type": "instagram", "post_type": "post", "is_trial_reel": False, "collaborators": []}),
    ("instagram-standalone", {"settings": {"post_type": "story"}, "image_count": 1},
     {"__type": "instagram-standalone", "post_type": "story", "is_trial_reel": False, "collaborators": []}),
    ("instagram", {"settings": {"is_trial_reel": True, "collaborators": ["@friend", {"label": "pal"}]}, "video_count": 1},
     {"__type": "instagram", "post_type": "post", "is_trial_reel": True, "graduation_strategy": "MANUAL",
      "collaborators": [{"label": "friend"}, {"label": "pal"}]}),
    ("instagram", {"settings": {"is_trial_reel": True}, "image_count": 2},
     {"__type": "instagram", "post_type": "post", "is_trial_reel": False, "collaborators": []}),
    ("facebook", {"link_url": "https://shop.test/p"}, {"__type": "facebook", "url": "https://shop.test/p"}),
    ("facebook", {}, {"__type": "facebook"}),
    ("linkedin", {"image_count": 3, "title": "Spring range"},
     {"__type": "linkedin", "post_as_images_carousel": True, "carousel_name": "Spring range"}),
    ("linkedin-page", {"image_count": 1}, {"__type": "linkedin-page", "post_as_images_carousel": False}),
    ("tiktok", {"video_count": 1, "settings": {"privacy_level": "self_only", "duet": "yes", "autoAddMusic": True,
                                                "video_made_with_ai": True}},
     {"__type": "tiktok", "privacy_level": "SELF_ONLY", "duet": True, "stitch": False, "comment": True,
      "autoAddMusic": "yes", "brand_content_toggle": False, "brand_organic_toggle": True,
      "content_posting_method": "DIRECT_POST", "video_made_with_ai": True}),
    ("youtube", {"video_count": 1, "title": "Serum routine", "settings": {"type": "unlisted", "tags": "skincare, serum"}},
     {"__type": "youtube", "title": "Serum routine", "type": "unlisted", "selfDeclaredMadeForKids": "no",
      "tags": [{"value": "skincare", "label": "skincare"}, {"value": "serum", "label": "serum"}]}),
    ("pinterest", {"settings": {"board": {"id": "b1", "label": "Serums"}}, "title": "T", "link_url": "https://shop.test"},
     {"__type": "pinterest", "board": "b1", "title": "T", "link": "https://shop.test"}),
    ("reddit", {"settings": {"subreddits": ["r/skincare"]}, "title": "Our serum", "link_url": "https://shop.test/p",
                "image_count": 1},
     {"__type": "reddit", "subreddit": [{"value": {"subreddit": "skincare", "title": "Our serum", "type": "image",
                                                   "url": "", "is_flair_required": False, "flair": None}}]}),
    ("reddit", {"settings": {"subreddit": [{"value": {"subreddit": "SkincareAddiction", "is_flair_required": True,
                                                      "flair": {"id": "f1", "name": "Product"}}}]},
                "title": "Thoughts?", "link_url": "https://shop.test/p"},
     {"__type": "reddit", "subreddit": [{"value": {"subreddit": "SkincareAddiction", "title": "Thoughts?",
                                                   "type": "link", "url": "https://shop.test/p",
                                                   "is_flair_required": True, "flair": {"id": "f1", "name": "Product"}}}]}),
    ("lemmy", {"settings": {"subreddit": [{"value": {"subreddit": "tech", "id": "42"}}]}, "title": "Hello there"},
     {"__type": "lemmy", "subreddit": [{"value": {"subreddit": "tech", "id": "42", "title": "Hello there"}}]}),
    ("discord", {"settings": {"channel": "123"}}, {"__type": "discord", "channel": "123"}),
    ("slack", {"settings": {"channel": {"id": "C1", "label": "#general"}}}, {"__type": "slack", "channel": "C1"}),
    ("twitch", {"settings": {"messageType": "announcement"}},
     {"__type": "twitch", "messageType": "announcement", "announcementColor": "primary"}),
    ("dribbble", {"title": "Shot", "settings": {"team": "https://dribbble.com/team"}},
     {"__type": "dribbble", "title": "Shot", "team": "https://dribbble.com/team"}),
    ("medium", {"title": "Article", "link_url": "https://blog.test/a", "settings": {"subtitle": "Sub", "tags": list("abcde")}},
     {"__type": "medium", "title": "Article", "subtitle": "Sub", "canonical": "https://blog.test/a",
      "tags": [{"value": t, "label": t} for t in "abcd"]}),
    ("devto", {"title": "Guide", "settings": {"tags": "python, web"}},
     {"__type": "devto", "title": "Guide", "tags": [{"value": "python", "label": "python"}, {"value": "web", "label": "web"}]}),
    ("hashnode", {"title": "Long title", "settings": {"publication": "pub1", "tags": ["dev"]}},
     {"__type": "hashnode", "title": "Long title", "publication": "pub1", "tags": [{"value": "dev", "label": "dev"}]}),
    ("wordpress", {"title": "Post"}, {"__type": "wordpress", "title": "Post", "type": "post"}),
    ("listmonk", {"title": "News", "settings": {"list": "7"}},
     {"__type": "listmonk", "subject": "News", "preview": "", "list": "7"}),
    ("gmb", {"link_url": "https://shop.test/p"},
     {"__type": "gmb", "topicType": "STANDARD", "callToActionType": "LEARN_MORE", "callToActionUrl": "https://shop.test/p"}),
    ("gmb", {}, {"__type": "gmb", "topicType": "STANDARD"}),
    ("gmb", {"title": "Launch night", "settings": {"topicType": "EVENT", "callToActionType": "CALL",
                                                    "eventStartDate": "2026-11-20"}},
     {"__type": "gmb", "topicType": "EVENT", "callToActionType": "CALL", "eventTitle": "Launch night",
      "eventStartDate": "2026-11-20"}),
    ("warpcast", {"settings": {"channels": ["base"]}}, {"__type": "warpcast", "subreddit": [{"value": {"id": "base"}}]}),
    ("moltbook", {"settings": {"submolt": "s1"}}, {"__type": "moltbook", "submolt": "s1"}),
    ("skool", {"title": "T", "settings": {"group": "g", "label": "l"}},
     {"__type": "skool", "group": "g", "label": "l", "title": "T"}),
    ("whop", {"settings": {"company": "c", "experience": "e"}}, {"__type": "whop", "company": "c", "experience": "e"}),
    ("newnet", {"settings": {"foo": 1, "channel_name": "N", "bar": None}}, {"__type": "newnet", "foo": 1}),
    *[(provider, {"settings": {"anything": "dropped"}}, {"__type": provider}) for provider in sorted(EMPTY_SETTINGS_PROVIDERS)],
]


@pytest.mark.parametrize(("provider", "kwargs", "expected"), SETTINGS_CASES)
def test_build_settings_per_provider(provider, kwargs, expected):
    assert build_settings(provider, **kwargs) == expected


SETTINGS_ERRORS = [
    ("pinterest", {"settings": {"channel_name": "Pins"}}, ConfigurationError,
     "Pinterest channel Pins has no board selected — set one in Channels"),
    ("youtube", {"video_count": 1, "title": "x"}, ValidationError, "title of 2–100 characters"),
    ("instagram", {"settings": {"post_type": "story"}, "image_count": 2}, ValidationError, "exactly one image"),
    ("instagram", {"settings": {"collaborators": ["a", "b", "c", "d"]}}, ConfigurationError, "at most 3"),
    ("tiktok", {"settings": {"privacy_level": "SELF_ONLY", "brand_content_toggle": True}}, ConfigurationError, "branded"),
    ("tiktok", {"settings": {"duet": "maybe"}}, ConfigurationError, "true or false"),
    ("tiktok", {"settings": {"content_posting_method": "LATER"}}, ConfigurationError, "DIRECT_POST, UPLOAD"),
    ("x", {"settings": {"community": "https://x.com/somebody"}}, ConfigurationError, "communities"),
    ("x", {"settings": {"who_can_reply_post": "nobody"}}, ConfigurationError, "who_can_reply_post"),
    ("facebook", {"link_url": "not a url"}, ValidationError, "http(s) URL"),
    ("reddit", {"title": "Hi there"}, ConfigurationError, "no subreddit selected"),
    ("reddit", {"settings": {"subreddits": ["x1"], "type": "link"}, "title": "Hi"}, ValidationError, "needs a URL"),
    ("reddit", {"settings": {"subreddit": [{"value": {"subreddit": "ab", "is_flair_required": True}}]}, "title": "Hi",
                "image_count": 1}, ConfigurationError, "flair"),
    ("lemmy", {"settings": {"subreddit": ["tech"]}, "title": "Hi"}, ConfigurationError, "community name and id"),
    ("discord", {}, ConfigurationError, "Discord channel (unnamed) has no channel selected"),
    ("slack", {}, ConfigurationError, "has no channel selected"),
    ("dribbble", {}, ValidationError, "shot title"),
    ("medium", {"title": "Article"}, ValidationError, "subtitle"),
    ("hashnode", {"title": "Long title", "settings": {"tags": ["a"]}}, ConfigurationError, "no publication selected"),
    ("hashnode", {"title": "Long title", "settings": {"publication": "p"}}, ConfigurationError, "at least one tag"),
    ("hashnode", {"title": "Short", "settings": {"publication": "p", "tags": ["a"]}}, ValidationError, "6 characters"),
    ("wordpress", {}, ValidationError, "post title"),
    ("listmonk", {"title": "Hi"}, ConfigurationError, "no mailing list selected"),
    ("listmonk", {"settings": {"list": "1"}}, ValidationError, "subject"),
    ("gmb", {"settings": {"callToActionType": "SHOP"}}, ValidationError, "needs a link"),
    ("gmb", {"settings": {"topicType": "EVENT"}}, ValidationError, "event title"),
    ("moltbook", {}, ConfigurationError, "no submolt selected"),
    ("skool", {"settings": {"group": "g"}, "title": "T"}, ConfigurationError, "no label selected"),
    ("whop", {"settings": {"company": "c"}}, ConfigurationError, "no experience (forum) selected"),
    ("", {}, ValidationError, "no provider"),
]


@pytest.mark.parametrize(("provider", "kwargs", "error", "fragment"), SETTINGS_ERRORS)
def test_build_settings_errors(provider, kwargs, error, fragment):
    with pytest.raises(error) as info:
        build_settings(provider, **kwargs)
    assert fragment in info.value.message


def test_titles_are_truncated_on_word_boundaries():
    text = "Vitamin C brightening serum with hyaluronic acid " * 4
    for limit in (90, 100):
        cut = truncate(text, limit)
        assert len(cut) <= limit and cut.endswith("…") and not cut[:-1].endswith(" ")
    assert truncate("  short   title ", 90) == "short title"
    youtube = build_settings("youtube", title=text, video_count=1)
    tiktok = build_settings("tiktok", settings={"title": text}, video_count=1)
    pinterest = build_settings("pinterest", settings={"board": "b"}, title=text)
    assert len(youtube["title"]) <= 100 and len(tiktok["title"]) <= 90 and len(pinterest["title"]) <= 100


# ---------------------------------------------------------------------------
# Live-publishing guard (dev and live share one machine and one Postiz account)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_writes_are_refused_unless_live_publishing_is_enabled(se_db, monkeypatch):
    from services.social_engine.context import load_store_context
    from services.social_engine.errors import ConfigurationError
    from services.social_engine.publishers import ReadOnlyPublisher, get_publisher

    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    monkeypatch.delenv("SOCIAL_ENGINE_LIVE_PUBLISHING", raising=False)
    sctx = await load_store_context(se_db)
    publisher = get_publisher(sctx)
    assert isinstance(publisher, ReadOnlyPublisher)
    with pytest.raises(ConfigurationError, match="Live publishing is switched off"):
        await publisher.create_posts(None)
    with pytest.raises(ConfigurationError):
        await publisher.delete_post("x")
    assert callable(publisher.list_channels)  # reads are delegated
    await publisher.aclose()

    monkeypatch.setenv("SOCIAL_ENGINE_LIVE_PUBLISHING", "1")
    live = get_publisher(sctx)
    assert not isinstance(live, ReadOnlyPublisher)
    await live.aclose()
