"""Deterministic SEO opportunity rules.

No LLM can invent metrics here. Recommendations are derived only from source
rows and every item retains the measurements that triggered it.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any
from urllib.parse import urlparse


def _severity(score: int) -> str:
    return "high" if score >= 80 else "medium" if score >= 55 else "low"


def _path(value: str) -> str:
    if not value:
        return "/"
    parsed = urlparse(value if "://" in value else f"https://placeholder{value}")
    return parsed.path.rstrip("/") or "/"


def _business_context(intelligence_summary: dict[str, Any]) -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = defaultdict(lambda: {
        "shopify_sessions": 0, "shopify_cart_additions": 0, "shopify_purchases": 0,
        "ga4_sessions": 0, "ga4_engaged_sessions": 0,
    })
    shopify = intelligence_summary.get("shopify") or {}
    for row in shopify.get("landing_pages", []) or []:
        target = output[_path(str(row.get("landing_page_path") or ""))]
        target["shopify_sessions"] += float(row.get("sessions", 0) or 0)
        target["shopify_cart_additions"] += float(row.get("sessions_with_cart_additions", 0) or 0)
        target["shopify_purchases"] += float(row.get("sessions_that_completed_checkout", 0) or 0)
    ga4 = intelligence_summary.get("ga4") or {}
    for row in ga4.get("landing_pages", []) or []:
        target = output[_path(str(row.get("landingPagePlusQueryString") or "").split("?", 1)[0])]
        target["ga4_sessions"] += float(row.get("sessions", 0) or 0)
        target["ga4_engaged_sessions"] += float(row.get("engagedSessions", 0) or 0)
    return dict(output)


def _context_for(page: str, context: dict[str, dict[str, float]]) -> dict[str, float]:
    return context.get(_path(page), {})


def search_opportunities(
    search_console: dict[str, Any],
    intelligence_summary: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    current = search_console.get("current", []) or []
    previous = search_console.get("previous", []) or []
    previous_by_key = {
        (str(row.get("query", "")).lower(), str(row.get("page", ""))): row
        for row in previous
    }
    context = _business_context(intelligence_summary or {})
    opportunities: list[dict[str, Any]] = []

    by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in current:
        query = str(row.get("query") or "").strip()
        page = str(row.get("page") or "").strip()
        if not query or not page:
            continue
        by_query[query.lower()].append(row)
        impressions = float(row.get("impressions", 0) or 0)
        clicks = float(row.get("clicks", 0) or 0)
        ctr = float(row.get("ctr", 0) or 0)
        position = float(row.get("position", 0) or 0)
        business = _context_for(page, context)
        metrics = {
            "clicks": clicks, "impressions": impressions,
            "ctr": round(ctr * 100, 2), "position": round(position, 2), **business,
        }

        if impressions >= 50 and position <= 20 and ctr < 0.015:
            score = min(95, int(65 + min(impressions / 50, 20)))
            opportunities.append({
                "key": f"low-ctr:{query.lower()}:{page}", "kind": "search",
                "category": "click_through", "severity": _severity(score), "score": score,
                "title": f"Improve the search result for ‘{query}’",
                "evidence": f"{int(impressions):,} impressions produced {int(clicks):,} clicks ({ctr * 100:.2f}% CTR) at average position {position:.1f}.",
                "action": "Review search intent, title and meta description. Keep the page URL, make one measured change and compare the next equal period.",
                "page_url": page, "search_query": query, "metrics": metrics,
                "source": "google_search_console",
            })
        elif impressions >= 25 and 4 <= position <= 20:
            score = min(84, int(52 + min(impressions / 40, 20) + max(0, 16 - position)))
            opportunities.append({
                "key": f"striking-distance:{query.lower()}:{page}", "kind": "search",
                "category": "content_refresh", "severity": _severity(score), "score": score,
                "title": f"Move ‘{query}’ from page-two range",
                "evidence": f"The page averages position {position:.1f} with {int(impressions):,} impressions and {int(clicks):,} clicks.",
                "action": "Strengthen the section that answers this exact intent, add relevant internal links and verify the page offers something more useful than competing results.",
                "page_url": page, "search_query": query, "metrics": metrics,
                "source": "google_search_console",
            })

        prior = previous_by_key.get((query.lower(), page))
        if prior:
            previous_clicks = float(prior.get("clicks", 0) or 0)
            previous_impressions = float(prior.get("impressions", 0) or 0)
            if previous_clicks >= 10 and clicks <= previous_clicks * 0.7:
                decline = round((1 - clicks / previous_clicks) * 100, 1) if previous_clicks else 0
                score = min(96, 75 + int(decline / 5))
                metrics.update({
                    "previous_clicks": previous_clicks,
                    "previous_impressions": previous_impressions,
                    "click_decline_percent": decline,
                })
                opportunities.append({
                    "key": f"decline:{query.lower()}:{page}", "kind": "search",
                    "category": "decline", "severity": _severity(score), "score": score,
                    "title": f"Investigate a {decline:.0f}% click decline for ‘{query}’",
                    "evidence": f"Clicks fell from {int(previous_clicks):,} to {int(clicks):,} between equal comparison periods.",
                    "action": "Check ranking, SERP changes, seasonality, stock and page changes before editing. Refresh only after the cause is understood.",
                    "page_url": page, "search_query": query, "metrics": metrics,
                    "source": "google_search_console",
                })

    for query, rows in by_query.items():
        meaningful = [row for row in rows if float(row.get("impressions", 0) or 0) >= 5]
        pages = sorted({str(row.get("page") or "") for row in meaningful})
        total_impressions = sum(float(row.get("impressions", 0) or 0) for row in meaningful)
        if len(pages) < 2 or total_impressions < 30:
            continue
        score = min(95, 75 + len(pages) * 4)
        opportunities.append({
            "key": f"search-cannibalisation:{query}", "kind": "search",
            "category": "cannibalisation", "severity": _severity(score), "score": score,
            "title": f"Review {len(pages)} pages competing for ‘{query}’",
            "evidence": f"Google showed {len(pages)} BioLuxeLab URLs for this query across {int(total_impressions):,} impressions.",
            "action": "Compare intent and conversions. Differentiate genuinely distinct pages or consolidate the weaker page into the strongest; never redirect without review.",
            "page_url": pages[0], "search_query": query,
            "metrics": {"urls": pages, "impressions": total_impressions},
            "source": "google_search_console",
        })
    return opportunities


def merge_and_rank(*groups: list[dict[str, Any]], limit: int = 250) -> list[dict[str, Any]]:
    """Deduplicate by stable evidence key, keeping the highest-scored finding."""
    by_key: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            key = str(item.get("key") or "")
            if not key:
                continue
            current = by_key.get(key)
            if current is None or int(item.get("score", 0)) > int(current.get("score", 0)):
                by_key[key] = item
    return sorted(by_key.values(), key=lambda item: (-int(item.get("score", 0)), item["key"]))[:limit]
