"""Fast, deterministic catalogue preparation for the storefront Wellness Quiz."""
from __future__ import annotations

import json

import db
import shopify_client
from config import StoreConfig
from services.landing_pages.product_prompts.config import Settings as LandingPageSettings


GOALS = {
    "sleep_recovery": {
        "label": "Sleep & recovery",
        "description": "Build a calmer evening and recovery routine.",
        "keywords": ("sleep", "bed", "pillow", "night", "recovery", "eye mask", "massage", "relax", "infrared"),
    },
    "relaxation": {
        "label": "Stress relief & relaxation",
        "description": "Create more calm and decompression in your day.",
        "keywords": ("relax", "stress", "aroma", "diffuser", "essential oil", "calm", "incense", "candle", "sound", "singing bowl"),
    },
    "pain_mobility": {
        "label": "Comfort, posture & mobility",
        "description": "Support easier movement and post-activity comfort.",
        "keywords": ("pain", "posture", "mobility", "back", "neck", "knee", "muscle", "massage", "stretcher", "brace", "fascia", "joint"),
    },
    "fitness": {
        "label": "Strength & home fitness",
        "description": "Build a practical routine for movement and strength.",
        "keywords": ("fitness", "exercise", "strength", "resistance", "training", "gym", "ring", "band", "dumbbell", "yoga", "pilates"),
    },
    "beauty": {
        "label": "Beauty & self-care",
        "description": "Create a simple, consistent personal-care ritual.",
        "keywords": ("beauty", "skin", "facial", "face", "hair", "jade", "gua sha", "anti-age", "serum", "roller", "scalp", "therapy cap"),
    },
    "mindfulness": {
        "label": "Meditation & mindfulness",
        "description": "Make a mindful practice easier to begin and maintain.",
        "keywords": ("meditat", "mindful", "chakra", "singing bowl", "sound healing", "yoga", "breath", "spiritual", "pendant", "altar"),
    },
}

FORMAT_KEYWORDS = {
    "device": ("device", "electric", "electronic", "therapy", "machine", "gun", "massager", "infrared", "led", "cap"),
    "movement": ("fitness", "exercise", "resistance", "yoga", "pilates", "ring", "band", "stretch", "training"),
    "sensory": ("aroma", "diffuser", "essential oil", "incense", "candle", "sound", "singing bowl"),
    "self_care": ("beauty", "skin", "facial", "hair", "jade", "gua sha", "roller", "massage"),
}

GOAL_STEPS = {
    "sleep_recovery": ["Prepare", "Unwind", "Recover"],
    "relaxation": ["Settle", "Reset", "Maintain"],
    "pain_mobility": ["Prepare", "Mobilise", "Recover"],
    "fitness": ["Warm up", "Train", "Recover"],
    "beauty": ["Prepare", "Treat", "Maintain"],
    "mindfulness": ["Arrive", "Practise", "Continue"],
}


def _normalise_domain(value: str) -> str:
    domain = (value or "").strip().rstrip("/")
    if not domain:
        return ""
    return domain if domain.startswith(("http://", "https://")) else f"https://{domain}"


def _landing_pages_by_product() -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        output_dir = LandingPageSettings.load().output_dir
    except Exception:
        return result
    if not output_dir.exists():
        return result
    for path in output_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            product = payload.get("product") if isinstance(payload.get("product"), dict) else {}
            publication = payload.get("landing_page_publication") if isinstance(payload.get("landing_page_publication"), dict) else {}
            page = publication.get("page") if isinstance(publication.get("page"), dict) else {}
            handle = str(product.get("handle") or "").strip()
            url = str(page.get("url") or "").strip()
            if handle and url:
                result[handle] = url
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return result


def classify_product(product: dict, storefront_domain: str = "", landing_page_url: str = "") -> dict:
    collections = (product.get("collections") or {}).get("nodes") or []
    collection_text = " ".join(f"{item.get('title', '')} {item.get('handle', '')}" for item in collections)
    tags = product.get("tags") or []
    tag_text = " ".join(tags) if isinstance(tags, list) else str(tags)
    haystack = " ".join(
        str(value or "") for value in (
            product.get("title"), product.get("description"), product.get("productType"),
            tag_text, collection_text,
        )
    ).lower()
    title = str(product.get("title") or "").lower()

    goal_scores: dict[str, int] = {}
    for goal, definition in GOALS.items():
        score = 0
        for keyword in definition["keywords"]:
            if keyword in title:
                score += 5
            if keyword in haystack:
                score += 2
        goal_scores[goal] = score

    # Keep the quiz commercially focused by not recommending obvious general electronics.
    irrelevant = any(term in haystack for term in ("laptop sleeve", "phone case", "usb cable", "car charger"))
    if irrelevant:
        goal_scores = {goal: 0 for goal in GOALS}

    formats = [
        name for name, keywords in FORMAT_KEYWORDS.items()
        if any(keyword in haystack for keyword in keywords)
    ]
    if not formats:
        formats = ["open"]

    price_node = ((product.get("priceRangeV2") or {}).get("minVariantPrice") or {})
    variants = ((product.get("variants") or {}).get("nodes") or [])
    first_variant = variants[0] if variants else {}
    domain = _normalise_domain(storefront_domain)
    handle = str(product.get("handle") or "")
    online_url = str(product.get("onlineStoreUrl") or "").strip()
    product_url = online_url or (f"{domain}/products/{handle}" if domain and handle else "")
    featured = product.get("featuredImage") or {}
    return {
        "product_id": str(product.get("legacyResourceId") or product.get("id") or ""),
        "handle": handle,
        "title": str(product.get("title") or handle),
        "product_url": product_url,
        "landing_page_url": landing_page_url,
        "guide_url": str((product.get("guideUrl") or {}).get("value") or ""),
        "guide_title": str((product.get("guideTitle") or {}).get("value") or ""),
        "image_url": str(featured.get("url") or ""),
        "price": float(price_node.get("amount") or 0),
        "currency": str(price_node.get("currencyCode") or "GBP"),
        "variant_id": str(first_variant.get("legacyResourceId") or ""),
        "available": bool(
            product.get("status") == "ACTIVE"
            and first_variant.get("availableForSale")
            and product_url
            and max(goal_scores.values(), default=0) > 0
        ),
        "goal_scores": goal_scores,
        "formats": formats,
    }


async def sync_catalogue(store_id: str) -> list[dict]:
    store_row = await db.get_store(store_id)
    if not store_row:
        raise RuntimeError("Store configuration was not found")
    store = StoreConfig.from_row(store_row)
    raw_products = await shopify_client.fetch_wellness_quiz_products(store)
    storefront = store.custom_domain or store.myshopify_domain
    landing_pages = _landing_pages_by_product()
    products = [
        classify_product(
            item,
            storefront,
            landing_pages.get(str(item.get("handle") or ""), "")
            or str(item.get("_landing_page_url") or ""),
        )
        for item in raw_products
    ]
    await db.replace_wellness_quiz_products(store_id, products)
    return products


def public_config() -> dict:
    return {
        "version": 1,
        "goals": [
            {"id": key, "label": value["label"], "description": value["description"]}
            for key, value in GOALS.items()
        ],
        "steps": GOAL_STEPS,
        "questions": [
            {
                "id": "goal", "title": "What would you most like to support?",
                "options": [{"value": key, "label": value["label"]} for key, value in GOALS.items()],
            },
            {
                "id": "format", "title": "What kind of routine feels most natural to you?",
                "options": [
                    {"value": "device", "label": "Helpful wellness technology"},
                    {"value": "movement", "label": "Movement and physical practice"},
                    {"value": "sensory", "label": "A calming sensory ritual"},
                    {"value": "self_care", "label": "Hands-on self-care"},
                    {"value": "open", "label": "I’m open to anything"},
                ],
            },
            {
                "id": "time", "title": "How much time can you realistically give it?",
                "options": [
                    {"value": "5", "label": "About 5 minutes"},
                    {"value": "15", "label": "About 15 minutes"},
                    {"value": "30", "label": "30 minutes or more"},
                ],
            },
            {
                "id": "budget", "title": "What budget feels comfortable today?",
                "options": [
                    {"value": "30", "label": "Up to £30"},
                    {"value": "75", "label": "Up to £75"},
                    {"value": "150", "label": "Up to £150"},
                    {"value": "flexible", "label": "Flexible for the right fit"},
                ],
            },
        ],
    }
