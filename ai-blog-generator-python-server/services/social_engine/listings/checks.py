"""Upkeep: are the live listings still there, and what does a client find when they search?"""
from __future__ import annotations

import html
import logging
import re
import time
from typing import Any, Iterable
from urllib.parse import urlsplit

from db.social_engine import listings as listing_store
from db.social_engine import observability

from ..activity import record
from ..context import StoreContext
from ..http import ServiceClient
from .catalogue import BY_ID, DIRECTORIES, ranked
from . import fill as fill_mod
from .profile import seeded_profile

logger = logging.getLogger("ai_blog_server.social_engine.listings")

PRESENCE_KEY = "listings:presence:{store_id}"
_TAG = re.compile(r"<[^>]+>")
_GOOGLE_HOSTS = ("google.", "googleusercontent.com", "gstatic.com", "youtube.com/intl", "accounts.google")


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


async def check_listing(sctx: StoreContext, listing: dict) -> dict[str, Any]:
    """Fetch the listing page: does it still exist and still show the right name and phone?"""
    url = str(listing.get("listing_url") or "").strip()
    profile = seeded_profile(sctx)
    result: dict[str, Any] = {"url": url, "ok": False, "status_code": None, "name_found": False, "phone_found": None,
                              "checked_at": int(time.time()), "message": ""}
    if not url.startswith(("http://", "https://")):
        result["message"] = "No listing link to check."
        return result
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    try:
        async with ServiceClient("listing_check", origin, timeout=30, max_attempts=2,
                                 headers={"user-agent": "Mozilla/5.0 (compatible; BioLuxeLab listing check)"}) as client:
            response = await client.request("GET", path, ctx=sctx.call_context(f"listing:{listing.get('directory_id')}"),
                                            operation="listings.check", ok_statuses=(401, 403, 404, 410, 429))
    except Exception as exc:
        result["message"] = f"Could not fetch: {exc}"[:300]
        return result
    status = int(getattr(response, "status_code", 0) or 0)
    result["status_code"] = status
    text = html.unescape(_TAG.sub(" ", getattr(response, "text", "") or ""))
    flat = re.sub(r"\s+", " ", text).lower()
    result["name_found"] = bool(profile.business_name) and profile.business_name.lower() in flat
    digits = _digits(profile.phone)
    if digits:
        result["phone_found"] = digits[-9:] in _digits(flat)
    result["ok"] = 200 <= status < 300 and result["name_found"]
    if status in (404, 410):
        result["message"] = "The listing page is gone."
    elif not (200 <= status < 300):
        result["message"] = f"The page answered HTTP {status}."
    elif not result["name_found"]:
        result["message"] = "The page loads but the business name is not on it (sign-in wall, or the listing was removed)."
    elif result["phone_found"] is False:
        result["message"] = "Live, but the phone number on the page does not match the profile."
    else:
        result["message"] = "Live and matching."
    return result


async def check_due(store_id: str, *, max_checks: int = 20) -> dict[str, Any]:
    from ..context import load_store_context

    sctx = await load_store_context(store_id, correlation_id="listings_check")
    interval = sctx.settings.listings.check_interval_days * 86400
    due = await listing_store.due_for_check(store_id, older_than=int(time.time()) - interval)
    checked = broken = 0
    for listing in due[:max_checks]:
        result = await check_listing(sctx, listing)
        checked += 1
        status = listing["status"]
        if result["status_code"] in (404, 410) or (result["status_code"] and not result["name_found"] and listing["status"] == "live"):
            status = "broken"
            broken += 1
        elif result["ok"] and listing["status"] == "broken":
            status = "live"
        await listing_store.save_listing(store_id, listing["directory_id"], status=status, check=result,
                                         checked_at=result["checked_at"])
        if status == "broken" and listing["status"] != "broken":
            await record(store_id, "listing", listing["directory_id"], f"Listing needs attention: {result['message']}",
                         level="warning", stage="listings.check", correlation_id=f"listing:{listing['directory_id']}")
    return {"checked": checked, "broken": broken, "due": len(due)}


# ---------------------------------------------------------------- what a client finds

def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def classify_links(links: list[dict], sctx: StoreContext, *, live: Iterable[str] = ()) -> dict[str, Any]:
    """Group search-result links: the store, each catalogue directory, social profiles, and everything else.

    `live` names directories already marked live in the table: Google shows its
    own business panel as a card, not a link, so a confirmed listing counts even
    when the result page does not link to it.
    """
    own = _host(sctx.storefront_url)
    myshopify = str(sctx.store_config.myshopify_domain or "").lower()
    results: list[dict] = []
    seen_hosts: set[str] = set()
    for link in links:
        url = str(link.get("url") or "")
        host = _host(url)
        if not host or any(g in host for g in _GOOGLE_HOSTS) or "/search?" in url:
            continue
        who = "other"
        label = host
        if host == own or host.endswith("." + own) or (myshopify and host == myshopify):
            who, label = "store", "Your store"
        else:
            for directory in DIRECTORIES:
                if any(host == h or host.endswith("." + h) for h in directory.hosts):
                    who, label = "directory", directory.name
                    break
            else:
                if any(s in host for s in ("instagram.", "facebook.", "tiktok.", "youtube.", "pinterest.", "x.com", "twitter.", "substack.", "threads.")):
                    who, label = "social", host
        key = f"{who}:{label}"
        results.append({"title": link.get("title") or "", "url": url, "host": host, "who": who, "label": label,
                        "first": key not in seen_hosts})
        seen_hosts.add(key)
    found = {r["label"] for r in results if r["who"] == "directory"} | {str(n) for n in live if n}
    store_found = any(r["who"] == "store" for r in results)
    social = sorted({r["label"] for r in results if r["who"] == "social"})
    missing = [d.name for d in ranked() if d.trust >= 6 and d.name not in found and d.fill != "manual" or
               (d.id == "google_business" and d.name not in found)]
    return {"results": results[:40], "store_found": store_found, "directories_found": sorted(found),
            "missing": missing[:8], "social": social, "established": established_score(store_found, found, social)}


def established_score(store_found: bool, directories_found: set[str], social: list[str]) -> dict[str, Any]:
    """How established the brand looks to a buyer who searches it, 0-100, with the next things to add.

    Weighted by what a careful buyer checks: the store itself, Google's own
    panel, an independent review site, a professional page, and a few social
    profiles that agree with each other.
    """
    checks = [
        ("Your store ranks for its own name", 25, store_found, "Keep the home page title as the business name."),
        ("Google Business Profile shows", 20, "Google Business Profile" in directories_found,
         "Keep the Google Business Profile complete and ask for Google reviews."),
        ("Independent reviews (Trustpilot)", 20, "Trustpilot" in directories_found,
         "Claim the Trustpilot profile and invite every customer after delivery."),
        ("LinkedIn page", 10, "LinkedIn company page" in directories_found,
         "Finish the LinkedIn company page and link the founder profile."),
        ("Two or more social profiles", 10, len(social) >= 2, "Make sure Instagram, Facebook and YouTube use the same name and logo."),
        ("Listed in a UK directory", 10, any(n in directories_found for n in ("Yell.com", "FreeIndex", "Cylex UK", "Hotfrog UK", "Scoot", "Thomson Local")),
         "Add the free Yell and FreeIndex listings with the same name and phone."),
        ("Company record (Companies House)", 5, "Companies House" in directories_found,
         "If it is a limited company, add the number to the footer; if not, say who runs it on the Meet Joe page."),
    ]
    score = sum(points for _, points, ok, _ in checks if ok)
    return {
        "score": score,
        "checks": [{"label": label, "points": points, "ok": bool(ok)} for label, points, ok, _ in checks],
        "next": [advice for _, _, ok, advice in checks if not ok][:4],
    }


async def search_presence(sctx: StoreContext) -> dict[str, Any]:
    """Search the brand (and the founder) in the owner's Chrome and record what shows up."""
    profile = seeded_profile(sctx)
    queries = [f'"{profile.business_name}"'] if profile.business_name else []
    if profile.founder_name:
        queries.append(f'"{profile.founder_name}" coach')
    if not queries:
        return {"error": "Add the business name to the profile first."}
    relay = fill_mod.relay_for(sctx)
    rows = await listing_store.list_listings(sctx.store_id)
    live = [BY_ID[did].name for did, row in rows.items() if row.get("status") == "live" and did in BY_ID]
    live += [d.name for d in DIRECTORIES if d.fill == "done" and d.id not in rows]
    report: dict[str, Any] = {"checked_at": int(time.time()), "queries": []}
    for query in queries:
        tab = await relay.open("https://www.google.com/search?q=" + query.replace(" ", "+"))
        try:
            snapshot = await relay.snapshot(tab, interactive=False, urls=True, limit=400)
        finally:
            await relay.close(tab)
        classified = classify_links(snapshot.links, sctx, live=live)
        report["queries"].append({"query": query, **classified})
    first = report["queries"][0]
    report["summary"] = {
        "store_found": first["store_found"], "directories_found": first["directories_found"],
        "missing": first["missing"], "social": first["social"], "results": len(first["results"]),
        "established": first["established"],
    }
    await observability.set_state(PRESENCE_KEY.format(store_id=sctx.store_id), report)
    await record(sctx.store_id, "listing", "presence",
                 f"Brand search: {len(first['results'])} results; store {'found' if first['store_found'] else 'NOT found'}; "
                 f"directories: {', '.join(first['directories_found']) or 'none'}; missing: {', '.join(first['missing'][:4]) or 'none'}",
                 stage="listings.presence", correlation_id="listings:presence")
    return report


async def last_presence(store_id: str) -> dict | None:
    value = await observability.get_state(PRESENCE_KEY.format(store_id=store_id), None)
    return value if isinstance(value, dict) else None
