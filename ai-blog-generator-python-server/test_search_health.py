"""Search health: the daily check on Google and Bing."""
from __future__ import annotations

import json
import time

import httpx
import pytest

import db
from db.social_engine import observability
from services.seo_growth import search_health as sh

POSTS = [f"/blogs/hub/post-{i}" for i in range(1, 6)]


@pytest.fixture
def world(se_db, monkeypatch):
    """Google and Bing answered by a pretend server; `state` says what they report."""
    state = {"bing_status": "Success", "google_errors": 0, "inspected": [], "submitted": [], "declined": {POSTS[1]},
             "downloaded": "2099-01-01T00:00:00Z"}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/sitemaps" in url:
            return httpx.Response(200, json={"sitemap": [{"path": "https://bioluxelab.com/sitemap.xml", "errors": str(state["google_errors"]),
                                                          "warnings": "0", "lastDownloaded": state["downloaded"]}]})
        if "urlInspection" in url:
            path = json.loads(request.content)["inspectionUrl"].replace("https://bioluxelab.com", "")
            state["inspected"].append(path)
            coverage = "Crawled - currently not indexed" if path in state["declined"] else sh.INDEXED
            return httpx.Response(200, json={"inspectionResult": {"indexStatusResult": {"coverageState": coverage, "verdict": "PASS"}}})
        if "GetFeeds" in url:
            return httpx.Response(200, json={"d": [{"Url": "https://bioluxelab.com/sitemap.xml", "Status": state["bing_status"], "UrlCount": 11}]})
        if "SubmitFeed" in url:
            state["submitted"].append(json.loads(request.content)["feedUrl"])
            return httpx.Response(200, json={"d": None})
        if "GetCrawlStats" in url:
            return httpx.Response(200, json={"d": [{"Date": "/Date(1794700800000)/", "InIndex": state.get("bing_indexed", 1030),
                                                    "Code4xx": 43, "Code5xx": state.get("bing_5xx", 0), "CrawledPages": 127}]})
        if "GetCrawlIssues" in url:
            return httpx.Response(200, json={"d": []})
        raise AssertionError(url)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(sh.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler)))

    async def token(credentials):
        return "token"

    async def posts(store):
        return list(state.get("posts", POSTS))

    monkeypatch.setattr(sh, "_access_token", token)
    monkeypatch.setattr(sh, "published_post_paths", posts)
    return state


async def _connect(store_id):
    await db.set_store_settings(store_id, {
        "seo_gsc_site_url": "sc-domain:bioluxelab.com", "seo_gsc_service_account_json": "{}",
        "bing_webmaster_api_key": "k" * 32, "bing_site_url": "https://bioluxelab.com/",
    })


async def _alerts(store_id):
    return [a["message"] for a in await observability.list_activity(store_id=store_id, limit=50) if a["stage"] == "search"]


def test_shipped_rules_file_is_valid():
    rules = sh.load_rules()
    assert rules.google.inspect_posts_per_run <= 1800 and rules.run_every_hours >= 1


@pytest.mark.asyncio
async def test_a_healthy_day_records_the_figures_and_alerts_nothing(se_db, world):
    await _connect(se_db)
    summary = await sh.run_check(se_db)
    assert summary["google"]["indexed"] == 4 and summary["google"]["known"] == 5
    assert summary["google"]["states"]["Crawled - currently not indexed"] == 1
    assert summary["bing"]["indexed"] == 1030 and summary["bing"]["resubmitted"] == []
    assert summary["problems"] == [] and await _alerts(se_db) == []
    assert (await db.list_health_runs(se_db))[0]["google"]["indexed"] == 4


@pytest.mark.asyncio
async def test_posts_checked_longest_ago_go_first_and_unpublished_ones_are_forgotten(se_db, world, monkeypatch, tmp_path):
    await _connect(se_db)
    rules = json.loads(sh.RULES_FILE.read_text())
    rules["google"]["inspect_posts_per_run"], rules["google"]["seconds_between_inspections"] = 2, 0
    path = tmp_path / "search_health.json"
    path.write_text(json.dumps(rules))
    monkeypatch.setenv(sh.RULES_FILE_ENV, str(path))
    for _ in range(3):
        await sh.run_check(se_db)
    assert world["inspected"] == [POSTS[0], POSTS[1], POSTS[2], POSTS[3], POSTS[4], POSTS[0]]
    world["posts"] = POSTS[:3]
    await sh.run_check(se_db)
    assert sorted(r["path"] for r in await db.list_index_status(se_db)) == POSTS[:3]


@pytest.mark.asyncio
async def test_bing_sitemap_error_is_submitted_again_and_only_reported_if_it_persists(se_db, world):
    await _connect(se_db)
    world["bing_status"] = "Error"
    first = await sh.run_check(se_db)
    assert world["submitted"] == ["https://bioluxelab.com/sitemap.xml"] and first["problems"] == []
    second = await sh.run_check(se_db)
    assert any("still cannot read" in p for p in second["problems"])
    await sh.run_check(se_db)
    assert len([m for m in await _alerts(se_db) if "still cannot read" in m]) == 1      # said once, not daily


@pytest.mark.asyncio
async def test_google_sitemap_problems_and_an_unreachable_engine_are_reported(se_db, world, monkeypatch):
    await _connect(se_db)
    world["google_errors"] = 3
    assert any("3 error(s)" in p for p in (await sh.run_check(se_db))["problems"])
    world["google_errors"], world["downloaded"] = 0, "2020-01-01T00:00:00Z"
    assert any("has not read the sitemap" in p for p in (await sh.run_check(se_db))["problems"])

    async def down(store_id, rules, problems):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(sh, "check_bing", down)
    summary = await sh.run_check(se_db)
    assert summary["google"]["connected"] and any("Bing check could not be made" in p for p in summary["problems"])


@pytest.mark.asyncio
async def test_a_fall_in_indexed_pages_against_a_week_ago_is_reported(se_db, world):
    await _connect(se_db)
    await sh.run_check(se_db)
    import aiosqlite
    async with aiosqlite.connect(db.get_db_path()) as conn:      # make that run eight days old
        await conn.execute("UPDATE seo_health_runs SET created_at=?", (int(time.time()) - 8 * 86400,))
        await conn.commit()
    world["declined"] = set(POSTS[1:])
    world["bing_indexed"] = 600
    problems = (await sh.run_check(se_db))["problems"]
    assert any("Google has 1 blog posts indexed, down from 4" in p for p in problems)
    assert any("Bing has 600 pages indexed, down from 1030" in p for p in problems)


@pytest.mark.asyncio
async def test_a_store_without_either_connection_is_left_alone(se_db, world):
    summary = await sh.run_check(se_db)
    assert summary["google"] == {"connected": False} and summary["bing"] == {"connected": False}
    assert world["inspected"] == [] and summary["problems"] == []
