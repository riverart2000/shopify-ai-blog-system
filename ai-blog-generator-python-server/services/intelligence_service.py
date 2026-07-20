"""Evidence-led customer behaviour and store conversion intelligence."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import db
import providers
import shopify_client
from config import StoreConfig
from services.ga4_service import GA4ConfigurationError, collect_ga4

logger = logging.getLogger("ai_blog_server.intelligence")


def _number(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return 0.0
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _int(value: Any) -> int:
    return int(round(_number(value)))


def _first(rows: list[dict]) -> dict:
    return rows[0] if rows else {}


def _percent(numerator: float, denominator: float) -> float:
    return round((numerator / denominator * 100), 2) if denominator else 0.0


def _store_config(row: dict) -> StoreConfig:
    return StoreConfig.from_row(row)


async def _safe_shopifyql(store: StoreConfig, query: str) -> list[dict]:
    try:
        return await shopify_client.run_shopifyql_query(store, query)
    except Exception as exc:
        logger.warning("ShopifyQL query failed store=%s query=%r error=%s", store.id, query, exc)
        return []


async def _catalog_health(store: StoreConfig) -> dict:
    query = """
      query IntelligenceProducts {
        products(first: 250) {
          nodes {
            title status vendor productType totalInventory
            seo { title description }
            variants(first: 20) { nodes { compareAtPrice } }
          }
        }
      }
    """
    data = await shopify_client.graphql_request(store, query)
    products = ((data.get("products") or {}).get("nodes") or [])
    active = [item for item in products if item.get("status") == "ACTIVE"]
    missing_seo_titles = sum(not (item.get("seo") or {}).get("title") for item in products)
    missing_seo_descriptions = sum(not (item.get("seo") or {}).get("description") for item in products)
    templated_titles = sum(
        bool(re.search(r"\b(this|these)\b", item.get("title", ""), re.IGNORECASE))
        for item in products
    )
    invalid_vendors = sum(str(item.get("vendor", "")).strip() in {"", "1"} for item in products)
    invalid_types = sum(str(item.get("productType", "")).strip() in {"", "1"} for item in products)
    zero_stock_active = sum(_int(item.get("totalInventory")) <= 0 for item in active)
    compare_at_products = sum(
        any(variant.get("compareAtPrice") is not None for variant in (item.get("variants") or {}).get("nodes", []))
        for item in products
    )
    return {
        "products": len(products),
        "active_products": len(active),
        "draft_products": sum(item.get("status") == "DRAFT" for item in products),
        "missing_seo_titles": missing_seo_titles,
        "missing_seo_descriptions": missing_seo_descriptions,
        "templated_titles": templated_titles,
        "invalid_vendors": invalid_vendors,
        "invalid_product_types": invalid_types,
        "active_zero_stock": zero_stock_active,
        "products_with_compare_at_price": compare_at_products,
    }


async def _content_health(store: StoreConfig) -> dict:
    query = """
      query IntelligenceContent {
        blogs(first: 100) { nodes { articlesCount { count } } }
      }
    """
    data = await shopify_client.graphql_request(store, query)
    blogs = ((data.get("blogs") or {}).get("nodes") or [])
    return {
        "blogs": len(blogs),
        "articles": sum(_int((blog.get("articlesCount") or {}).get("count")) for blog in blogs),
    }


async def _policy_health(store: StoreConfig) -> dict:
    query = """
      query IntelligencePolicies {
        shop {
          shopPolicies { type url }
        }
      }
    """
    try:
        data = await shopify_client.graphql_request(store, query)
        shop = data.get("shop") or {}
        types = {policy.get("type") for policy in shop.get("shopPolicies", [])}
        return {
            "privacy": "PRIVACY_POLICY" in types,
            "refund": "REFUND_POLICY" in types,
            "shipping": "SHIPPING_POLICY" in types,
            "terms": "TERMS_OF_SERVICE" in types,
        }
    except Exception as exc:
        logger.warning("Policy health unavailable store=%s: %s", store.id, exc)
        return {"privacy": None, "refund": None, "shipping": None, "terms": None}


async def _collect_shopify(store: StoreConfig, period_days: int) -> dict:
    since = f"SINCE -{period_days}d"
    funnel_q = (
        "FROM sessions SHOW sessions, sessions_with_cart_additions, "
        "sessions_that_reached_checkout, sessions_that_completed_checkout, conversion_rate " + since
    )
    sources_q = (
        "FROM sessions SHOW sessions, sessions_that_completed_checkout "
        "GROUP BY referrer_source " + since + " ORDER BY sessions DESC LIMIT 25"
    )
    landing_q = (
        "FROM sessions SHOW sessions, sessions_with_cart_additions, sessions_that_completed_checkout "
        "GROUP BY landing_page_path " + since + " ORDER BY sessions DESC LIMIT 25"
    )
    sales_q = "FROM sales SHOW total_sales, orders, average_order_value " + since
    product_sales_q = (
        "FROM sales SHOW total_sales, orders GROUP BY product_title "
        + since + " ORDER BY total_sales DESC LIMIT 25"
    )
    funnel_rows, sources, landing_pages, sales_rows, product_sales, catalog, content, policies = await asyncio.gather(
        _safe_shopifyql(store, funnel_q), _safe_shopifyql(store, sources_q),
        _safe_shopifyql(store, landing_q), _safe_shopifyql(store, sales_q),
        _safe_shopifyql(store, product_sales_q), _catalog_health(store),
        _content_health(store), _policy_health(store),
    )
    funnel_row = _first(funnel_rows)
    sessions = _int(funnel_row.get("sessions"))
    cart_additions = _int(funnel_row.get("sessions_with_cart_additions"))
    checkouts = _int(funnel_row.get("sessions_that_reached_checkout"))
    purchases = _int(funnel_row.get("sessions_that_completed_checkout"))
    sales_row = _first(sales_rows)
    funnel = {
        "sessions": sessions,
        "cart_additions": cart_additions,
        "checkouts": checkouts,
        "purchases": purchases,
        "conversion_rate": round(_number(funnel_row.get("conversion_rate")) * 100, 2),
        "add_to_cart_rate": _percent(cart_additions, sessions),
        "checkout_completion_rate": _percent(purchases, checkouts),
        "total_sales": round(_number(sales_row.get("total_sales")), 2),
        "orders": _int(sales_row.get("orders")),
        "average_order_value": round(_number(sales_row.get("average_order_value")), 2),
    }
    for row in sources:
        row["sessions"] = _int(row.get("sessions"))
        row["purchases"] = _int(row.get("sessions_that_completed_checkout"))
        row["conversion_rate"] = _percent(row["purchases"], row["sessions"])
    for row in landing_pages:
        row["sessions"] = _int(row.get("sessions"))
        row["cart_additions"] = _int(row.get("sessions_with_cart_additions"))
        row["purchases"] = _int(row.get("sessions_that_completed_checkout"))
        row["conversion_rate"] = _percent(row["purchases"], row["sessions"])
    return {
        "funnel": funnel,
        "sources": sources,
        "landing_pages": landing_pages,
        "product_sales": product_sales,
        "catalog": catalog,
        "content": content,
        "policies": policies,
    }


def build_recommendations(summary: dict) -> list[dict]:
    """Create only recommendations supported by minimum sample sizes."""
    shopify = summary.get("shopify", {})
    funnel = shopify.get("funnel", {})
    catalog = shopify.get("catalog", {})
    content = shopify.get("content", {})
    policies = shopify.get("policies", {})
    sessions = _int(funnel.get("sessions"))
    purchases = _int(funnel.get("purchases"))
    recs: list[dict] = []

    def add(metric_key: str, category: str, severity: str, title: str, evidence: str,
            action: str, confidence: str = "high", impact: str = "high", effort: str = "medium") -> None:
        recs.append({
            "metric_key": metric_key, "category": category, "severity": severity,
            "title": title, "evidence": evidence, "action": action,
            "confidence": confidence, "impact": impact, "effort": effort, "source": "verified-rules",
        })

    if sessions >= 100 and funnel.get("conversion_rate", 0) < 0.5:
        add("store_conversion", "conversion", "high", "Fix the store conversion path before buying more traffic",
            f"{sessions:,} sessions produced {purchases} purchase(s): {funnel.get('conversion_rate', 0):.2f}% conversion.",
            "Run a focused product-page and checkout experiment: clarify delivery and returns beside Add to cart, remove conflicting product details, and track the result for at least 14 days.")

    cart_rate = _number(funnel.get("add_to_cart_rate"))
    if sessions >= 100 and cart_rate < 2:
        add("add_to_cart", "conversion", "high", "Product interest is not becoming cart intent",
            f"Only {funnel.get('cart_additions', 0)} of {sessions:,} sessions added to cart ({cart_rate:.2f}%).",
            "Prioritise the highest-traffic product and landing pages. Test clearer benefits, credible proof, delivery timing, returns reassurance and a single primary offer above the fold.",
            effort="medium")

    checkouts = _int(funnel.get("checkouts"))
    if checkouts >= 5 and funnel.get("checkout_completion_rate", 0) < 30:
        add("checkout_completion", "checkout", "high", "Investigate checkout abandonment",
            f"{checkouts} sessions reached checkout but {purchases} completed it ({funnel.get('checkout_completion_rate', 0):.1f}%).",
            "Review unexpected shipping costs, payment failures, delivery estimates and mobile checkout friction; compare checkout-start and purchase events in GA4.")

    sources = shopify.get("sources", [])
    social = next((r for r in sources if str(r.get("referrer_source", "")).lower() == "social"), None)
    if social and _int(social.get("sessions")) >= 100 and _int(social.get("purchases")) == 0:
        add("social_conversion", "acquisition", "high", "Meta/social traffic is not producing purchases",
            f"Shopify attributed {social.get('sessions', 0):,} sessions to social and 0 purchases in this period.",
            "Split Meta traffic by campaign and landing page in GA4, verify purchase attribution, then pause or change ads that send visitors to low-intent generic pages.",
            confidence="medium", effort="medium")

    search = next((r for r in sources if str(r.get("referrer_source", "")).lower() == "search"), None)
    search_sessions = _int((search or {}).get("sessions"))
    article_count = _int(content.get("articles"))
    if sessions >= 500 and article_count >= 50 and search_sessions < max(25, article_count // 5):
        add("content_search_return", "content", "medium", "Audit search visibility before creating more content",
            f"The store has {article_count:,} articles but Shopify attributed only {search_sessions:,} sessions to search in {summary.get('period_days', 90)} days.",
            "Check Google Search Console indexing, crawlability, canonical tags and query cannibalisation. Consolidate overlapping articles and measure clicks to product pages.",
            confidence="medium", impact="high", effort="high")

    landing_pages = shopify.get("landing_pages", [])
    home = next((r for r in landing_pages if r.get("landing_page_path") in ("/", "")), None)
    if home and _int(home.get("sessions")) >= 100 and _int(home.get("purchases")) == 0:
        add("homepage_conversion", "landing-page", "high", "The homepage is a high-traffic conversion leak",
            f"The homepage received {home.get('sessions', 0):,} sessions and recorded 0 purchases.",
            "Give the homepage one clear customer promise and route visitors to 3–5 proven collections/products; add delivery, returns and trust evidence before promotional content.")

    products = max(_int(catalog.get("products")), 1)
    missing_seo = _int(catalog.get("missing_seo_descriptions"))
    if missing_seo / products >= 0.5:
        add("product_seo_fields", "catalog", "medium", "Complete explicit product search snippets",
            f"{missing_seo} of {products} products have no custom SEO description.",
            "Generate unique, truthful search descriptions for active products in priority order based on impressions and revenue; do not duplicate the visible product copy.",
            confidence="high", impact="medium", effort="medium")

    invalid_types = _int(catalog.get("invalid_product_types"))
    invalid_vendors = _int(catalog.get("invalid_vendors"))
    if invalid_types or invalid_vendors:
        add("catalog_taxonomy", "catalog", "medium", "Repair product taxonomy used by feeds and reporting",
            f"{invalid_types} products have a blank/invalid product type and {invalid_vendors} have a blank/invalid vendor.",
            "Replace placeholder values with consistent product types and vendor names. Recheck Shopify, Google and Meta catalogue diagnostics afterwards.",
            impact="medium", effort="low")

    if _int(catalog.get("active_zero_stock")):
        count = _int(catalog.get("active_zero_stock"))
        add("active_zero_stock", "catalog", "medium", "Remove dead ends caused by unavailable products",
            f"{count} active product(s) have zero recorded inventory.",
            "Confirm whether each item can still be fulfilled. Hide unavailable items from campaigns and featured areas, or show an honest restock path.",
            impact="medium", effort="low")

    if policies.get("shipping") is False:
        add("shipping_policy", "trust", "high", "Publish a clear shipping policy",
            "Shopify does not report a published shipping policy for this store.",
            "Publish destinations, dispatch times, delivery estimates, tracking and delay handling; link it from product pages, checkout reassurance and the footer.",
            impact="high", effort="low")

    ga4 = summary.get("ga4", {})
    if not ga4.get("connected"):
        add("ga4_connection", "measurement", "medium", "Connect GA4 to unlock serious behaviour diagnosis",
            f"GA4 is not available to this report: {ga4.get('status', 'not configured')}.",
            "Add the GA4 property ID and a read-only service account on this page. The report will then compare channels, devices and landing pages without collecting personal data.",
            confidence="high", impact="high", effort="low")

    return recs


async def _grok_rank(store_id: str, summary: dict, recs: list[dict]) -> tuple[list[dict], str]:
    """Use the existing Grok text model to rank verified candidates, never create metrics."""
    rows = [row for row in await db.get_active_text_models(store_id) if row.get("provider") == "grok"]
    if not rows or not recs:
        return recs, ""
    candidates = [
        {"id": item["metric_key"], "title": item["title"], "evidence": item["evidence"],
         "impact": item["impact"], "effort": item["effort"]}
        for item in recs
    ]
    prompt = (
        "Rank these already-verified ecommerce recommendations for commercial value. "
        "You must only use the supplied candidate IDs and evidence. Do not add or infer any metric. "
        "Return strict JSON with keys priority_order (array of candidate IDs) and executive_summary "
        "(maximum 70 words, plain English, explicitly distinguish facts from suggested tests).\n\n"
        + json.dumps({"period_days": summary.get("period_days"), "candidates": candidates})
    )
    try:
        model = providers.ModelRecord.from_dict(rows[0])
        raw = await providers.get_text_provider(model).generate_raw(
            prompt,
            "You are a cautious ecommerce analyst. Evidence first; never fabricate numbers or causal claims.",
        )
        raw = raw.strip().removeprefix("```json").removesuffix("```").strip()
        result = json.loads(raw)
        order = [str(value) for value in result.get("priority_order", [])]
        rank = {key: index for index, key in enumerate(order)}
        severity_rank = {"high": 0, "medium": 1, "low": 2}
        ranked = sorted(
            recs,
            key=lambda item: (
                severity_rank.get(item.get("severity", "medium"), 1),
                rank.get(item["metric_key"], len(rank)),
            ),
        )
        executive_summary = str(result.get("executive_summary", ""))[:700].strip()
        if not executive_summary or any(item["metric_key"] in executive_summary for item in recs):
            funnel = summary.get("shopify", {}).get("funnel", {})
            lead = ranked[0]["title"].rstrip(".") if ranked else "collect more reliable data"
            executive_summary = (
                f"Across {summary.get('period_days', 90)} days, Shopify recorded "
                f"{_int(funnel.get('sessions')):,} sessions, {_int(funnel.get('cart_additions'))} cart additions "
                f"and {_int(funnel.get('purchases'))} purchase(s) "
                f"({_number(funnel.get('conversion_rate')):.2f}% conversion). "
                f"The highest-priority finding is: {lead}. Treat the recommended changes as tests, "
                "then compare the next 14–30 days with this baseline."
            )
        return ranked, executive_summary
    except Exception as exc:
        logger.warning("Grok intelligence ranking failed store=%s: %s", store_id, exc)
        return recs, ""


async def run_analysis(store_id: str, period_days: int = 90, trigger_type: str = "manual") -> str:
    period_days = min(max(int(period_days), 30), 365)
    run_id = await db.create_intelligence_run(store_id, period_days, trigger_type)
    try:
        store_row = await db.get_store(store_id)
        if not store_row:
            raise RuntimeError("Store configuration was not found")
        store = _store_config(store_row)
        settings = await db.get_all_store_settings(store_id)
        shopify = await _collect_shopify(store, period_days)
        ga4: dict = {"connected": False, "status": "not configured"}
        property_id = settings.get("ga4_property_id", "").strip()
        credentials_json = settings.get("ga4_service_account_json", "").strip()
        if property_id and credentials_json:
            try:
                ga4 = await collect_ga4(property_id, credentials_json, period_days)
            except GA4ConfigurationError as exc:
                ga4 = {"connected": False, "status": str(exc)}
        elif property_id:
            ga4["status"] = "property saved; service account credentials required"

        summary = {
            "store_id": store_id, "store_name": store.name, "period_days": period_days,
            "generated_at": int(time.time()), "shopify": shopify, "ga4": ga4,
            "privacy": "Aggregate store and analytics metrics only; no customer identifiers sent to Grok.",
        }
        recommendations = build_recommendations(summary)
        recommendations, executive_summary = await _grok_rank(store_id, summary, recommendations)
        summary["executive_summary"] = executive_summary
        summary["data_sufficiency"] = {
            "level": "strong" if shopify["funnel"]["sessions"] >= 1000 else "directional" if shopify["funnel"]["sessions"] >= 100 else "limited",
            "sessions": shopify["funnel"]["sessions"],
            "message": (
                "Strong sample for store-level prioritisation. Validate changes with controlled tests."
                if shopify["funnel"]["sessions"] >= 1000 else
                "Enough traffic for directional findings; avoid declaring causation from small segments."
                if shopify["funnel"]["sessions"] >= 100 else
                "Not enough traffic for firm conversion conclusions. Collect more data before major decisions."
            ),
        }
        await db.complete_intelligence_run(run_id, summary, recommendations)
        return run_id
    except Exception as exc:
        await db.fail_intelligence_run(run_id, str(exc))
        logger.exception("Intelligence analysis failed store=%s run=%s", store_id, run_id)
        raise


async def run_scheduled_scans() -> None:
    now = int(time.time())
    stores = await db.get_stores_due_for_intelligence(now, interval_hours=24)
    for store in stores:
        try:
            days = int(await db.get_store_setting(store["id"], "intelligence_period_days", "90"))
            await run_analysis(store["id"], days, "scheduled")
        except Exception:
            logger.exception("Scheduled intelligence scan failed store=%s", store.get("id"))
