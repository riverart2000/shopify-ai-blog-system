"""The daily check on how Google and Bing see the shop.

Every `run_every_hours` (rules/search_health.json; the code has no values of
its own) the check, for each store with Search Console connected:

* **Google** — reads the sitemap's status, and asks Google's URL Inspection API
  about `inspect_posts_per_run` blog posts (those checked longest ago first), so
  the system always knows which posts are indexed, declined or unknown;
* **Bing** — reads the sitemap's status and the crawl figures through the Bing
  Webmaster API; a sitemap Bing could not read is submitted again at once.

What it cannot put right is reported as an alert on System Health (an error in
the activity log, stage `search`), once per problem per `repeat_after_days`.
It never changes or unpublishes a post.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field

import db
import shopify_client
from config import StoreConfig

from .search_console import _access_token, normalise_site_url

logger = logging.getLogger("ai_blog_server.seo_growth")

RULES_FILE = Path(__file__).resolve().parents[2] / "rules" / "search_health.json"
RULES_FILE_ENV = "SEO_SEARCH_HEALTH_RULES_FILE"     # tests point this at their own copy
LAST_RUN_KEY = "search_health_last_run"
ALERTS_KEY = "search_health_alerts"
INDEXED = "Submitted and indexed"
_BING = "https://ssl.bing.com/webmaster/api.svc/json/"
_GOOGLE_SITEMAPS = "https://www.googleapis.com/webmasters/v3/sites/{site}/sitemaps"
_GOOGLE_INSPECT = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoogleRules(_Strict):
    enabled: bool
    inspect_posts_per_run: int = Field(ge=0, le=1800)      # Google allows 2,000 inspections a day per site
    sitemap_stale_days: int = Field(ge=1, le=90)
    seconds_between_inspections: float = Field(ge=0, le=10)


class BingRules(_Strict):
    enabled: bool
    api_key_setting: str = Field(min_length=1)     # store settings that hold the Bing key and the site's address
    site_setting: str = Field(min_length=1)
    resubmit_sitemap_on_error: bool
    server_errors_alert_above: int = Field(ge=0)


class AlertRules(_Strict):
    indexed_drop_percent: int = Field(ge=1, le=100)
    compare_with_days_ago: int = Field(ge=1, le=90)
    repeat_after_days: int = Field(ge=1, le=90)


class SearchHealthRules(_Strict):
    about: str = ""
    enabled: bool
    run_every_hours: int = Field(ge=1, le=720)
    keep_days: int = Field(ge=7, le=3650)
    google: GoogleRules
    bing: BingRules
    alerts: AlertRules


_CACHE: tuple[str, float, SearchHealthRules] | None = None


def load_rules() -> SearchHealthRules:
    global _CACHE
    path = Path(os.environ.get(RULES_FILE_ENV) or RULES_FILE).expanduser()
    stamp = path.stat().st_mtime
    if _CACHE is None or _CACHE[0] != str(path) or _CACHE[1] != stamp:
        _CACHE = (str(path), stamp, SearchHealthRules.model_validate(json.loads(path.read_text())))
    return _CACHE[2]


# ---------------------------------------------------------------------------
# Google
# ---------------------------------------------------------------------------

_ARTICLES = """query($after:String){ articles(first:250, after:$after){ pageInfo{hasNextPage endCursor}
  nodes{ handle isPublished blog{handle} } } }"""


async def published_post_paths(store: StoreConfig) -> list[str]:
    paths, after = [], None
    while True:
        page = (await shopify_client.graphql_request(store, _ARTICLES, {"after": after}))["articles"]
        paths += [f"/blogs/{n['blog']['handle']}/{n['handle']}" for n in page["nodes"] if n["isPublished"]]
        if not page["pageInfo"]["hasNextPage"]:
            return paths
        after = page["pageInfo"]["endCursor"]


def _date(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


async def check_google(store_id: str, store: StoreConfig, rules: SearchHealthRules, problems: list[dict]) -> dict[str, Any]:
    site_setting = await db.get_store_setting(store_id, "seo_gsc_site_url", "")
    credentials = await db.get_store_setting(store_id, "seo_gsc_service_account_json", "")
    if not credentials and await db.get_store_setting(store_id, "seo_use_ga4_credentials", "") == "1":
        credentials = await db.get_store_setting(store_id, "ga4_service_account_json", "")
    if not site_setting or not credentials:
        return {"connected": False}
    site = normalise_site_url(site_setting)
    headers = {"Authorization": f"Bearer {await _access_token(credentials)}"}
    out: dict[str, Any] = {"connected": True}
    origin = f"https://{store.custom_domain or store.myshopify_domain}".rstrip("/")
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(_GOOGLE_SITEMAPS.format(site=quote(site, safe="")), headers=headers)
        response.raise_for_status()
        sitemaps = response.json().get("sitemap") or []
        out["sitemaps"] = [{"path": s.get("path"), "errors": int(s.get("errors") or 0), "warnings": int(s.get("warnings") or 0),
                            "last_downloaded": s.get("lastDownloaded") or ""} for s in sitemaps]
        if not sitemaps:
            problems.append({"key": "google:no_sitemap", "message": "Google has no sitemap for the shop. Submit "
                             f"{origin}/sitemap.xml in Search Console → Sitemaps."})
        for item in out["sitemaps"]:
            read = _date(item["last_downloaded"])
            age = (datetime.now(timezone.utc) - read).days if read else None
            if item["errors"]:
                problems.append({"key": f"google:sitemap_errors:{item['path']}",
                                 "message": f"Google reports {item['errors']} error(s) in the sitemap {item['path']}."})
            elif age is None or age > rules.google.sitemap_stale_days:
                problems.append({"key": f"google:sitemap_stale:{item['path']}",
                                 "message": f"Google has not read the sitemap {item['path']} for "
                                            f"{age if age is not None else 'an unknown number of'} days."})

        paths = await published_post_paths(store)
        known = {row["path"]: row for row in await db.list_index_status(store_id)}
        await db.forget_index_status(store_id, [p for p in known if p not in set(paths)])   # unpublished or deleted
        queue = sorted(paths, key=lambda p: (known[p]["checked_at"] if p in known else 0, p))
        inspected = 0
        for path in queue[:rules.google.inspect_posts_per_run]:
            answer = await client.post(_GOOGLE_INSPECT, headers=headers, json={"inspectionUrl": origin + path, "siteUrl": site})
            if answer.status_code == 429:       # today's allowance is used up: carry on tomorrow
                break
            answer.raise_for_status()
            status = (answer.json().get("inspectionResult") or {}).get("indexStatusResult") or {}
            await db.set_index_status(store_id, path, coverage_state=str(status.get("coverageState") or "Unknown"),
                                      verdict=str(status.get("verdict") or ""), last_crawl=str(status.get("lastCrawlTime") or ""))
            inspected += 1
            if rules.google.seconds_between_inspections:
                await asyncio.sleep(rules.google.seconds_between_inspections)
    states = Counter(row["coverage_state"] for row in await db.list_index_status(store_id))
    out.update(posts=len(paths), inspected=inspected, known=sum(states.values()), indexed=states.get(INDEXED, 0),
               states=dict(states.most_common()))
    return out


# ---------------------------------------------------------------------------
# Bing
# ---------------------------------------------------------------------------

def _bing_time(value: str) -> int:
    found = re.search(r"/Date\((\d+)", value or "")
    return int(found.group(1)) // 1000 if found else 0


async def check_bing(store_id: str, rules: SearchHealthRules, problems: list[dict]) -> dict[str, Any]:
    key = await db.get_store_setting(store_id, rules.bing.api_key_setting, "")
    site = await db.get_store_setting(store_id, rules.bing.site_setting, "")
    if not key or not site:
        return {"connected": False}
    out: dict[str, Any] = {"connected": True}
    async with httpx.AsyncClient(timeout=60) as client:
        async def call(method: str) -> Any:
            response = await client.get(_BING + method, params={"siteUrl": site, "apikey": key})
            response.raise_for_status()
            return response.json().get("d")

        feeds = await call("GetFeeds") or []
        out["sitemaps"] = [{"path": f.get("Url"), "status": f.get("Status"), "urls": f.get("UrlCount")} for f in feeds]
        if not feeds:
            problems.append({"key": "bing:no_sitemap", "message": "Bing has no sitemap for the shop. Submit "
                             f"{site}sitemap.xml in Bing Webmaster Tools → Sitemaps."})
        resubmitted = []
        for feed in out["sitemaps"]:
            if feed["status"] in ("Success", "Processing", "Pending", "Submitted"):
                continue
            if rules.bing.resubmit_sitemap_on_error:
                sent = await client.post(_BING + "SubmitFeed", params={"apikey": key},
                                         json={"siteUrl": site, "feedUrl": feed["path"]})
                if sent.is_success:
                    resubmitted.append(feed["path"])
                    continue
            problems.append({"key": f"bing:sitemap:{feed['path']}",
                             "message": f"Bing could not read the sitemap {feed['path']} (status {feed['status']})."})
        out["resubmitted"] = resubmitted
        if resubmitted:     # said once if it is still failing at the next check
            earlier = await db.list_health_runs(store_id, limit=1)
            again = set(resubmitted) & set(((earlier[0].get("bing") or {}).get("resubmitted") or []) if earlier else [])
            for path in sorted(again):
                problems.append({"key": f"bing:sitemap:{path}",
                                 "message": f"Bing still cannot read the sitemap {path} after it was submitted again."})

        crawl = sorted(await call("GetCrawlStats") or [], key=lambda row: _bing_time(row.get("Date", "")))
        if crawl:
            latest = crawl[-1]
            out.update(indexed=int(latest.get("InIndex") or 0), not_found=int(latest.get("Code4xx") or 0),
                       server_errors=int(latest.get("Code5xx") or 0), crawled=int(latest.get("CrawledPages") or 0))
            if out["server_errors"] > rules.bing.server_errors_alert_above:
                problems.append({"key": "bing:server_errors",
                                 "message": f"Bing met {out['server_errors']} server errors on the shop on its last crawl day."})
        out["crawl_issues"] = len(await call("GetCrawlIssues") or [])
    return out


# ---------------------------------------------------------------------------
# Alerts and the run
# ---------------------------------------------------------------------------

def _drop(now_value: int, then_value: int, percent: int) -> bool:
    return then_value > 0 and now_value < then_value * (100 - percent) / 100


async def raise_alerts(store_id: str, problems: list[dict], rules: SearchHealthRules) -> list[str]:
    """Report each problem once per `repeat_after_days`; returns the messages reported now."""
    from services.social_engine.activity import record

    now = int(time.time())
    try:
        said = json.loads(await db.get_store_setting(store_id, ALERTS_KEY, "") or "{}")
    except json.JSONDecodeError:
        said = {}
    current = {p["key"] for p in problems}
    said = {key: when for key, when in said.items() if key in current}      # a problem that went away may be said again
    reported = []
    for problem in problems:
        if now - int(said.get(problem["key"], 0)) < rules.alerts.repeat_after_days * 86400:
            continue
        await record(store_id, "store", store_id, f"Search health: {problem['message']}", level="error", stage="search",
                     data={"key": problem["key"]})
        said[problem["key"]] = now
        reported.append(problem["message"])
    await db.set_store_settings(store_id, {ALERTS_KEY: json.dumps(said)})
    return reported


async def run_check(store_id: str) -> dict[str, Any]:
    rules = load_rules()
    if not rules.enabled:
        return {"status": "off"}
    store = StoreConfig.from_row(await db.get_store(store_id))
    problems: list[dict] = []
    summary: dict[str, Any] = {"status": "done"}
    for name, check in (("google", lambda: check_google(store_id, store, rules, problems)),
                        ("bing", lambda: check_bing(store_id, rules, problems))):
        if not getattr(rules, name).enabled:
            continue
        try:
            summary[name] = await check()
        except Exception as exc:     # one search engine being unreachable never hides the other
            logger.warning("Search health: %s check failed for %s: %s: %s", name, store_id, type(exc).__name__, exc)
            summary[name] = {"connected": True, "error": f"{type(exc).__name__}: {exc}"[:300]}
            problems.append({"key": f"{name}:unreachable", "message": f"The {name.title()} check could not be made "
                             f"({type(exc).__name__}: {str(exc)[:160]})."})

    earlier = await db.health_run_before(store_id, int(time.time()) - rules.alerts.compare_with_days_ago * 86400)
    google_now, google_then = summary.get("google") or {}, (earlier or {}).get("google") or {}
    # Compare like with like: only once every post has a verdict both then and now.
    if google_now.get("known") and google_now.get("known") == google_now.get("posts") and google_then.get("known") == google_then.get("posts"):
        if _drop(int(google_now.get("indexed") or 0), int(google_then.get("indexed") or 0), rules.alerts.indexed_drop_percent):
            problems.append({"key": "google:indexed_drop", "message":
                             f"Google has {google_now['indexed']} blog posts indexed, down from {google_then['indexed']} "
                             f"{rules.alerts.compare_with_days_ago} days ago."})
    bing_now, bing_then = summary.get("bing") or {}, (earlier or {}).get("bing") or {}
    if _drop(int(bing_now.get("indexed") or 0), int(bing_then.get("indexed") or 0), rules.alerts.indexed_drop_percent):
        problems.append({"key": "bing:indexed_drop", "message":
                         f"Bing has {bing_now['indexed']} pages indexed, down from {bing_then['indexed']} "
                         f"{rules.alerts.compare_with_days_ago} days ago."})

    summary["problems"] = [p["message"] for p in problems]
    summary["alerted"] = await raise_alerts(store_id, problems, rules)
    await db.add_health_run(store_id, summary, keep_days=rules.keep_days)
    await db.set_store_settings(store_id, {LAST_RUN_KEY: str(int(time.time()))})
    logger.info("Search health for %s: %s", store_id, json.dumps(summary, ensure_ascii=False)[:1200],
                extra={"operation": "search_health", "store_id": store_id})
    return summary


async def run_scheduled() -> None:
    """Called by the scheduler: one check per store every `run_every_hours`."""
    try:
        rules = load_rules()
    except Exception as exc:
        logger.error("Search health rules could not be read: %s", exc)
        return
    if not rules.enabled:
        return
    now = int(time.time())
    for store in await db.get_stores():
        store_id = str(store["id"])
        if not await db.get_store_setting(store_id, "seo_gsc_site_url", "") \
                and not await db.get_store_setting(store_id, rules.bing.api_key_setting, ""):
            continue
        if now - int(await db.get_store_setting(store_id, LAST_RUN_KEY, "") or 0) < rules.run_every_hours * 3600:
            continue
        try:
            await run_check(store_id)
        except Exception as exc:
            logger.error("Search health check failed for %s: %s: %s", store_id, type(exc).__name__, exc)
            await db.set_store_settings(store_id, {LAST_RUN_KEY: str(now)})


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Search health: how Google and Bing see the shop.")
    parser.add_argument("--store", default="store_1")
    parser.add_argument("--run", action="store_true", help="make the check now (without it, the last results are shown)")
    parser.add_argument("--declined", action="store_true", help="list the posts Google has not indexed, longest first")
    args = parser.parse_args()

    async def _main() -> None:
        await db.init_db()
        if args.run:
            out: Any = await run_check(args.store)
        elif args.declined:
            rows = [r for r in await db.list_index_status(args.store) if r["coverage_state"] != INDEXED]
            out = [{"path": r["path"], "state": r["coverage_state"], "days": (int(time.time()) - r["state_since"]) // 86400}
                   for r in sorted(rows, key=lambda r: r["state_since"])]
        else:
            out = await db.list_health_runs(args.store, limit=7)
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))

    asyncio.run(_main())
