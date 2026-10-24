"""Prompts for captions, review and auto-pilot decisions.

Design notes
* The system prompt carries the brand voice and the UK compliance rules; the
  user prompt carries only facts (products, offer, networks) and the JSON
  format. Compliance rules come first and say they override everything else.
* The model never writes links, hashtag blocks or "link in bio": those are
  added deterministically per network afterwards, so the model can focus on
  copy and cannot break link policy or length limits.
* Facts are passed as compact labelled blocks; the product description is the
  only source of benefit wording (food-supplement claims must be authorised).
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

from ..models import CONTENT_TYPE_LABELS, ContentType, ProviderProfile
from ..settings import BrandKit, CaptionSettings
from .text import strip_html

# ---------------------------------------------------------------------------
# Language, tone, emoji
# ---------------------------------------------------------------------------

LANGUAGE_NAMES: dict[str, str] = {
    "en-gb": "British English", "en-us": "American English", "en": "English", "en-au": "Australian English",
    "es": "Spanish", "fr": "French", "de": "German", "it": "Italian", "pt": "Portuguese",
    "pt-br": "Brazilian Portuguese", "nl": "Dutch", "sv": "Swedish", "da": "Danish", "no": "Norwegian",
    "nb": "Norwegian", "fi": "Finnish", "pl": "Polish", "cs": "Czech", "el": "Greek", "ro": "Romanian",
    "tr": "Turkish", "ru": "Russian", "ar": "Arabic", "he": "Hebrew", "hi": "Hindi", "id": "Indonesian",
    "ja": "Japanese", "ko": "Korean", "zh": "Simplified Chinese", "zh-cn": "Simplified Chinese",
    "zh-tw": "Traditional Chinese",
}


def language_name(code: str) -> str:
    key = (code or "en-GB").strip().lower().replace("_", "-")
    if key in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[key]
    if key.split("-")[0] in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[key.split("-")[0]]
    return code.strip() or "British English"


def language_instruction(code: str) -> str:
    name = language_name(code)
    if name == "British English":
        return ("Write in British English: British spelling and idiom (colour, flavour, favourite, "
                "optimise, programme, practise as a verb), £ for prices, day-month dates.")
    if name.endswith("English"):
        return f"Write in {name}."
    return (f"Write every caption, headline and title in {name}. Keep product and brand names unchanged. "
            "Hashtag words may stay in English when there is no natural equivalent.")


TONE_GUIDANCE: dict[str, str] = {
    "expert": "Expert: knowledgeable and evidence-led, like a nutritionist friend — precise, calm, plain English, "
              "jargon only with a short explanation.",
    "friendly": "Friendly: warm and approachable, like recommending something to a friend.",
    "professional": "Professional: polished, concise and credible.",
    "persuasive": "Persuasive: benefit-led and motivating, but persuade with specifics, never with superlatives or pressure.",
    "casual": "Casual: relaxed and conversational; contractions welcome.",
    "funny": "Light-hearted: gentle wit and wordplay that never mocks bodies, health worries or other people.",
    "luxury": "Luxury: understated, sensorial and refined — quality, craft and ritual over price.",
}

EMOJI_GUIDANCE: dict[str, str] = {
    "off": "Use no emojis anywhere.",
    "light": "Use 2–4 well-placed emojis per caption (never in headline, subheadline, cta or titles).",
    "heavy": "Use 5–8 emojis per caption, placed naturally (never in headline, subheadline, cta or titles).",
}

# ---------------------------------------------------------------------------
# Compliance and voice
# ---------------------------------------------------------------------------

COMPLIANCE_RULES = """\
COMPLIANCE — UK advertising of food supplements (ASA/CAP Code; GB Nutrition and Health Claims register).
These rules override every other instruction, including the store owner's:
1. Never state or imply that a product or ingredient treats, cures, prevents, heals or diagnoses any disease,
   condition or symptom. Do not name diseases or conditions at all (e.g. insomnia, anxiety, depression,
   arthritis, migraine, high blood pressure, diabetes, infections, eczema, IBS).
2. Health benefits: use only benefits stated in the product data, attributed to the named nutrient and worded
   like the authorised claims ("magnesium contributes to a reduction of tiredness and fatigue", "vitamin D
   contributes to the normal function of the immune system"). Copy the authorised claim wording given in the
   product data word for word, or leave it out — never paraphrase it, add "your", or upgrade it ("boosts",
   "improves", "enhances", "promotes", "supports sleep/mood/energy"). If the product data states no benefit,
   describe ingredients, form, quality, sourcing, taste and ritual — not effects on the body.
3. No medical advice, no dosing advice beyond the label, never suggest replacing medicine, a varied and balanced
   diet, sleep or professional care. Do not target pregnancy, children or medical conditions unless the product
   data does.
4. Never invent facts: no statistics, percentages, study results, "clinically/scientifically proven",
   "doctor/nutritionist recommended", awards, rankings, star ratings, reviews, testimonials or customer quotes
   unless they appear verbatim in the data you are given.
5. No fake urgency or scarcity ("only 3 left", "selling fast", "last chance") unless the post details state it.
   Mention a discount, coupon code, price or end date only when given, exactly as given.
6. No before/after promises, guaranteed results, timeframes ("in 7 days"), weight-loss, fat-burning, detox,
   "boost your immune system", "miracle", "superfood" or anti-ageing claims.
7. Mental wellbeing: speak about routines and moments (winding down, a calm evening ritual), never about
   fixing stress, anxiety or mood.
8. Never tie the product to a body system or function in your own words ("for your nervous system", "supports
   your sleep", "fuels your energy") and never add "evidence/science/research shows" — the authorised wording
   stands on its own."""

VOICE_RULES = """\
VOICE
- Premium UK wellness: warm, calm, evidence-led and specific. Never hypey, never salesy.
- Lead with something true and specific (an ingredient, its form, a moment in the day) rather than a superlative.
- Banned clichés: game-changer, revolutionary, unlock, elevate, supercharge, transform your life, holy grail,
  must-have, ultimate, best ever, biohack, "your body will thank you", "treat yourself".
- No ALL-CAPS words for emphasis; at most one exclamation mark per caption.
- Short sentences and short paragraphs; blank lines between ideas on long-form networks.
- Never write URLs, web addresses, hashtags or "link in bio" inside captions — links and hashtags are added
  automatically for each network."""

CONTENT_TYPE_GUIDANCE: dict[str, str] = {
    ContentType.SINGLE_PRODUCT: "Product spotlight. Open with the most compelling true detail (key ingredient, its form, "
                                "or the moment in the day it fits), say plainly what it is and who it suits, then one "
                                "supporting detail from the description.",
    ContentType.NEW_ARRIVAL: "New arrival. Quiet 'new in' energy: what it is, what's in it, why it was made. "
                             "Never claim it is the first, only or best.",
    ContentType.SALE_PROMO: "Promotion. State the offer exactly as given (discount and coupon code verbatim; end date "
                            "only if given). The product stays the hero; the offer is simply the reason to act now. "
                            "No invented urgency or 'lowest price ever'.",
    ContentType.BACK_IN_STOCK: "Back in stock. A warm, matter-of-fact 'it's back' for people who've been waiting. "
                               "Do not invent waiting lists, sell-out stories or scarcity.",
    ContentType.PRODUCT_OF_THE_DAY: "Product of the day. One product, one interesting true detail (an ingredient, its "
                                    "form, how and when it fits into the day).",
    ContentType.COLLAGE: "Several products in one image. Name the theme that connects them, then one short line per "
                         "product (name + what makes it distinct).",
    ContentType.COLLECTION_ROUNDUP: "Collection round-up. Give the edit a clear theme and one line on each product so "
                                    "people can choose between them.",
    ContentType.COUNTDOWN: "Countdown. Say exactly what ends or starts and when, using only the date in the post "
                           "details. If no date is given, do not mention one.",
    ContentType.CAROUSEL: "Carousel. Invite the swipe ('Swipe through…') and say what each slide shows, in order.",
    ContentType.HOLIDAY: "Seasonal / occasion post (occasion in the post details). Link the occasion to everyday "
                         "self-care, ritual or thoughtful gifting; no medical framing, no pressure to buy.",
    ContentType.QUOTE: "Brand content: an original, uplifting line about routine, balance or everyday wellbeing. "
                       "Never attribute words to a real person. No health claims.",
    ContentType.PRODUCT_VIDEO: "Short vertical video (reel/short). The hook lands in the first second (max 8 words, "
                               "no question-bait). Keep the caption short and scannable and reference what is on screen.",
    ContentType.AI_IMAGE_AD: "Polished ad image. One message, headline-led, clear call to action.",
    ContentType.AI_VIDEO: "Short product video. Hook in the first second; describe the product's look, texture and "
                          "ritual rather than effects.",
    ContentType.BLOG_ARTICLE: "Blog article share (title/excerpt in the post details). Tease the single most useful "
                              "takeaway and invite people to read; add no claims the article does not make.",
}

BRAND_CONTENT_GUIDANCE: dict[str, str] = {
    "quote": "An original short line (max 18 words) about routine, balance, rest or small daily rituals. "
             "Written for the brand — never attributed to a real person. No health claims.",
    "fact": "A general, well-established, non-medical fact connected to the brand's world (e.g. where a nutrient "
            "is found in food, how a botanical has been used in cooking or tradition, the science of a daily ritual "
            "in plain words). No disease, no effect claims, no statistics unless universally known.",
    "poll": "A light, inclusive two-to-four option poll about habits or preferences (e.g. morning vs evening "
            "routine). No health questions, no personal medical data.",
    "quiz": "A friendly multiple-choice quiz question (2–4 options, one correct answer) on a general, verifiable, "
            "non-medical topic (nutrition in food, ingredient origins, wellbeing habits).",
    "brand_awareness": "Introduce the brand: who it is for, what it makes and how (quality, sourcing, testing, "
                       "UK-based) using only the brand description provided.",
    "brand_value": "One brand value (e.g. transparency, quality ingredients, sustainability) explained concretely "
                   "using only facts from the brand description provided.",
}

TARGET_CAPTION_LENGTH: dict[str, int] = {
    "instagram": 900, "facebook": 600, "x": 210, "linkedin": 1100, "threads": 380, "bluesky": 230,
    "mastodon": 380, "pinterest": 380, "tiktok": 300, "youtube": 600, "gmb": 700, "telegram": 800,
    "reddit": 900,
}


def _brand_lines(brand: BrandKit) -> str:
    lines = [f"Brand: {brand.business_name or 'the brand'}"]
    if brand.what_you_sell:
        lines.append(f"What we sell: {brand.what_you_sell}")
    else:
        lines.append("What we sell: premium wellness products and food supplements (UK)")
    if brand.description:
        lines.append(f"About the brand: {brand.description[:600]}")
    if brand.slogan:
        lines.append(f"Slogan: {brand.slogan}")
    return "\n".join(lines)


def caption_system_prompt(brand: BrandKit, captions: CaptionSettings) -> str:
    parts = [
        f"You are the senior social media copywriter for {brand.business_name or 'a premium UK wellness brand'}, "
        "a premium UK wellness and food-supplement brand. You write scroll-stopping but trustworthy captions "
        "that respect every platform's style.",
        COMPLIANCE_RULES,
        VOICE_RULES,
        f"TONE\n- {TONE_GUIDANCE.get(captions.tone, TONE_GUIDANCE['expert'])}",
        f"LANGUAGE\n- {language_instruction(captions.language)}",
        f"EMOJIS\n- {EMOJI_GUIDANCE[captions.emoji_level]}",
        "OUTPUT\n- Reply with one JSON object only, exactly in the requested format.",
    ]
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Facts blocks
# ---------------------------------------------------------------------------

def format_money(value: float, symbol: str = "£") -> str:
    if value <= 0:
        return ""
    return f"{symbol}{value:,.0f}" if abs(value - round(value)) < 0.005 else f"{symbol}{value:,.2f}"


def product_price(product: dict, brand: BrandKit) -> tuple[float, float, float]:
    """(price, price_max, compare_at) after the brand's include-taxes adjustment."""
    factor = 1 + (brand.include_taxes_pct or 0) / 100

    def _num(key: str) -> float:
        try:
            return float(product.get(key) or 0) * factor
        except (TypeError, ValueError):
            return 0.0

    return _num("price_min"), _num("price_max"), _num("compare_at_max")


def price_text(product: dict, brand: BrandKit) -> str:
    price, price_max, compare = product_price(product, brand)
    if price <= 0:
        return ""
    symbol = brand.currency_symbol or "£"
    text = format_money(price, symbol)
    if price_max > price + 0.005:
        text = f"from {text}"
    if compare > price + 0.005:
        text += f" (was {format_money(compare, symbol)})"
    return text


def collection_names(product: dict) -> list[str]:
    names: list[str] = []
    for item in product.get("collections") or []:
        if isinstance(item, dict):
            name = item.get("title") or item.get("handle") or ""
        else:
            name = str(item)
            if name.startswith("gid://") or name.isdigit():
                name = ""
        if name:
            names.append(str(name))
    return names


def products_block(products: list[dict], brand: BrandKit, *, include_price: bool, description_chars: int = 900) -> str:
    if not products:
        return "PRODUCTS\n(none — this post is not about a specific product)"
    per_product = description_chars if len(products) == 1 else max(220, description_chars // len(products))
    blocks = []
    for index, product in enumerate(products, start=1):
        lines = [f"PRODUCT {index}: {str(product.get('title') or '').strip()}"]
        kind = " · ".join(str(product.get(key) or "").strip() for key in ("product_type", "vendor") if product.get(key))
        if kind:
            lines.append(f"- Type: {kind}")
        if include_price:
            price = price_text(product, brand)
            if price:
                lines.append(f"- Price: {price}")
                discount = float(product.get("discount_pct") or 0)
                if product.get("on_sale") and discount > 0:
                    lines.append(f"- On sale: {discount:.0f}% off")
        names = collection_names(product)
        if names:
            lines.append(f"- Collections: {', '.join(names[:4])}")
        tags = [str(tag) for tag in (product.get("tags") or []) if str(tag).strip()][:10]
        if tags:
            lines.append(f"- Tags: {', '.join(tags)}")
        if product.get("available") is False:
            lines.append("- Currently out of stock")
        description = strip_html(str(product.get("description") or ""))
        claims = authorised_claims(description)
        if claims:
            lines.append("- Authorised claim wording (copy word for word, or leave out): " + " | ".join(claims))
        if description:
            if len(description) > per_product:
                description = description[:per_product].rsplit(" ", 1)[0] + " …"
            lines.append(f"- Description (the only source for benefit wording): {description}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


_CLAIM_SENTENCE = re.compile(
    r"\b(?:contribut\w*\s+to|supports?\s+(?:the\s+)?normal|helps?\s+(?:to\s+)?maintain\s+(?:the\s+)?normal|"
    r"is\s+needed\s+for\s+(?:the\s+)?normal|has\s+a\s+role\s+in)\b",
    re.I,
)


def authorised_claims(description: str, limit: int = 4) -> list[str]:
    """Sentences from the product data that carry authorised-style claim wording."""
    found: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", description or ""):
        sentence = " ".join(sentence.split())
        if sentence and _CLAIM_SENTENCE.search(sentence) and len(sentence) <= 300 and sentence not in found:
            found.append(sentence)
    return found[:limit]


def details_block(texts: dict[str, str]) -> str:
    labels = {
        "promo_text": "Promotion headline", "discount_text": "Discount (quote exactly)",
        "coupon_code": "Coupon code (quote exactly, in capitals)", "ends_at": "Offer ends",
        "end_date": "Ends", "occasion": "Occasion", "quote": "Quote text", "blog_title": "Article title",
        "blog_excerpt": "Article excerpt", "theme": "Theme",
    }
    lines = []
    for key, value in (texts or {}).items():
        value = str(value or "").strip()
        if not value or key in {"campaign", "utm_content"}:
            continue
        lines.append(f"- {labels.get(key, key.replace('_', ' ').capitalize())}: {value[:600]}")
    return "POST DETAILS\n" + "\n".join(lines) if lines else ""


_STYLE_PHRASES = re.compile(r"\s*(?:plus|with)\s+(?:the\s+)?link\b", re.I)
_STYLE_DROP = re.compile(r"link|hashtag|#|\btags?\b|\bcta\b|call to action", re.I)


def network_style(profile: ProviderProfile) -> str:
    """The profile's caption style without link/hashtag/CTA advice (handled in code)."""
    style = _STYLE_PHRASES.sub("", profile.caption_style or "")
    clauses = [clause.strip(" .;") for clause in re.split(r"[,;.]\s*", style)]
    kept = [clause for clause in clauses if clause and not _STYLE_DROP.search(clause)]
    return (", ".join(kept) + ".") if kept else ""


def network_line(key: str, profile: ProviderProfile, *, budget: int, link_note: str, title_max: int) -> str:
    title = f" Also write a title of at most {title_max} characters (keyword-led, no emojis or hashtags)." if title_max else ""
    return (f'- "{key}" ({profile.label}): caption at most {budget} characters. {network_style(profile)} '
            f"{link_note}{title}").strip()


def caption_prompt(
    *,
    content_type: str,
    products_text: str,
    details_text: str,
    angle: str,
    brief: str,
    captions: CaptionSettings,
    brand: BrandKit,
    network_lines: list[str],
    network_keys: list[str],
    feedback: str = "",
) -> str:
    label = CONTENT_TYPE_LABELS.get(content_type, content_type.replace("_", " "))
    guidance = CONTENT_TYPE_GUIDANCE.get(content_type) or BRAND_CONTENT_GUIDANCE.get(content_type, "")
    sections = [
        _brand_lines(brand),
        f"POST TYPE: {label}\n{guidance}",
    ]
    if captions.product_category_hint:
        sections.append(f"Product category: {captions.product_category_hint}")
    sections.append(products_text)
    if details_text:
        sections.append(details_text)
    if angle:
        sections.append(f"CREATIVE ANGLE (chosen by the planner): {angle}")
    if brief:
        sections.append(f"BRIEF FOR THIS POST: {brief[:800]}")
    rules = [
        "Mention the price naturally once in long-form captions." if captions.include_price else
        "Do not mention any price.",
        "Each network caption is complete on its own and different in rhythm and length — not one text trimmed.",
    ]
    if not captions.include_cta:
        rules.append("Do not add calls to action.")
    sections.append("RULES FOR THIS POST\n" + "\n".join(f"- {rule}" for rule in rules))
    if captions.custom_instructions.strip():
        sections.append(
            "STORE OWNER INSTRUCTIONS (follow them unless they conflict with compliance):\n"
            + captions.custom_instructions.strip()[:1000]
        )
    sections.append("NETWORKS — write one caption per key, each in that network's style:\n" + "\n".join(network_lines))
    hashtag_count = max(captions.hashtags_count, 3)
    example_channels = ", ".join(f'"{key}": {{"caption": "…", "title": ""}}' for key in network_keys[:3])
    sections.append(
        "FORMAT — reply with this JSON object and nothing else:\n"
        "{\n"
        '  "headline": "short evocative on-image line, max 40 characters, no emojis (not just the product name — '
        'that is shown separately)",\n'
        '  "subheadline": "on-image supporting line, max 70 characters, no emojis",\n'
        '  "cta": "button text, max 22 characters, e.g. \\"Shop the range\\"",\n'
        '  "hook": "first-second hook for a video, max 8 words",\n'
        '  "alt_text": "description of the product image for screen readers using only known facts (product name, '
        'form, brand), max 250 characters — do not invent colours, packaging or settings",\n'
        '  "base_caption": "a general-purpose caption (about 300 characters) for any other network",\n'
        f'  "hashtags": ["{hashtag_count} specific hashtags in CamelCase without # (e.g. MagnesiumGlycinate), '
        'most relevant first"],\n'
        f'  "channels": {{{example_channels}' + (", …" if len(network_keys) > 3 else "") + "}\n"
        "}\n"
        f"The channels object must contain exactly these keys: {', '.join(network_keys)}. "
        "Captions contain no URLs, no hashtags and no 'link in bio'. Leave title empty unless asked for."
    )
    if feedback:
        sections.append(f"IMPORTANT — FIX FROM THE PREVIOUS DRAFT:\n{feedback}")
    return "\n\n".join(section for section in sections if section)


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------

REVIEW_SYSTEM = (
    "You are the senior brand and compliance editor for a premium UK wellness and food-supplement brand. "
    "You review social posts before they are published and you are strict about UK ASA/CAP rules and the GB "
    "nutrition and health claims register, factual accuracy against the product data, tone, platform fit and "
    "formatting.\n\n" + COMPLIANCE_RULES + "\n\nReply with one JSON object only."
)


def review_prompt(
    *,
    brand: BrandKit,
    post: dict,
    products_text: str,
    on_image: dict[str, str],
    channel_texts: list[str],
    guard_notes: list[str],
    language: str,
) -> str:
    content_type = str(post.get("content_type") or "")
    sections = [
        _brand_lines(brand),
        f"POST TYPE: {CONTENT_TYPE_LABELS.get(content_type, content_type or 'unknown')}",
        f"Expected language: {language_name(language)}",
    ]
    if post.get("angle"):
        sections.append(f"Planned angle: {post['angle']}")
    sections.append(products_text)
    image_lines = [f"- {key}: {value}" for key, value in on_image.items() if value]
    if image_lines:
        sections.append("ON-IMAGE TEXT\n" + "\n".join(image_lines))
    sections.append("CAPTIONS PER NETWORK\n" + "\n\n".join(channel_texts))
    if guard_notes:
        sections.append("AUTOMATED CHECKS ALREADY FOUND\n" + "\n".join(f"- {note}" for note in guard_notes))
    sections.append(
        "Score the post 0–10: 9–10 publish as is; 7–8 good with minor polish; 5–6 needs edits; 0–4 do not publish.\n"
        "Authorised-style wording taken from the product data ('contributes to the normal …', or the paraphrase "
        "'supports normal …') is compliant — do not flag it. Using only some of the authorised claims, or one "
        "claim per caption, is fine.\n"
        "Critical issues (each one blocks publishing): any disease/medical or unauthorised health claim; any fact, "
        "price, discount, statistic, review or urgency not supported by the data; wrong product details; "
        "placeholder text left in (e.g. PRODUCT_TITLE); wrong language; offensive or insensitive content.\n"
        "Reply with:\n"
        '{"score": 0-10, "critical_issues": ["…"], "issues": ["minor issues"], '
        '"suggestions": ["concrete edits, max 5"], "summary": "one sentence"}'
    )
    return "\n\n".join(sections)


VISION_SYSTEM = "You check social media images for a wellness brand. Reply with one JSON object only."


def vision_prompt(*, product_titles: list[str], headline: str, expects_product: bool) -> str:
    subject = ", ".join(product_titles[:4]) if product_titles else "the brand"
    product_line = (
        f"Is the product ({subject}) clearly visible and recognisable?" if expects_product
        else "Is the image on-brand and appropriate (no product needs to be visible)?"
    )
    headline_line = f' The intended headline is "{headline}".' if headline else ""
    return (
        f"Look at this social media image.{headline_line}\n"
        f"1. {product_line}\n"
        "2. Is all text on the image legible (good contrast, not cut off, not overlapping)?\n"
        "3. Any visual problems (cropping, distortion, blank areas, misspelt text)?\n"
        'Reply: {"product_visible": true/false, "text_legible": true/false, "has_text": true/false, '
        '"issues": ["short descriptions"]}'
    )


# ---------------------------------------------------------------------------
# Auto-pilot decisions
# ---------------------------------------------------------------------------

DECISION_SYSTEM = (
    "You are the social media planner for a premium UK wellness and food-supplement brand. You choose what to "
    "post using only the options you are given, you never invent ids, and you reply with one JSON object only."
)


def compact_json(value: Any, limit: int = 4000) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def content_plan_prompt(
    *,
    slots: list[dict],
    candidates: list[dict],
    allowed_content_types: list[str],
    templates_by_type: dict[str, list[str]],
    recent_posts: list[dict],
    performance: dict,
    max_products_per_post: int,
    allow_repeats: bool,
    product_rules: dict[str, tuple[int, int]],
) -> str:
    type_lines = []
    for content_type in allowed_content_types:
        low, high = product_rules.get(content_type, (1, 1))
        high = min(high, max_products_per_post)
        count = f"{low}" if low == high else f"{low}–{high}"
        templates = templates_by_type.get(content_type) or []
        template_text = f"templates: {', '.join(templates)}" if templates else "templates: (any — leave empty)"
        label = CONTENT_TYPE_LABELS.get(content_type, content_type)
        type_lines.append(f"- {content_type} ({label}): {count} product(s); {template_text}")
    slot_lines = [
        compact_json({"slot_id": slot.get("slot_id"), "time": slot.get("local_time"), "networks": slot.get("providers", [])}, 300)
        for slot in slots
    ]
    candidate_lines = [compact_json(candidate, 400) for candidate in candidates]
    recent_lines = [compact_json(post, 240) for post in recent_posts[:12]]
    sections = [
        "TASK: choose one post for each slot below.",
        "SLOTS\n" + "\n".join(slot_lines),
        "ALLOWED CONTENT TYPES\n" + "\n".join(type_lines),
        "CANDIDATE PRODUCTS (id, title, price, sale %, new, days since last posted, times posted, performance 0–1, "
        "collections, tags)\n" + ("\n".join(candidate_lines) if candidate_lines else "(none)"),
    ]
    if recent_lines:
        sections.append("RECENT POSTS (newest first)\n" + "\n".join(recent_lines))
    if performance:
        sections.append("PERFORMANCE SUMMARY\n" + compact_json(performance, 1500))
    sections.append(
        "RULES\n"
        "- Use only product ids from the candidate list and only the allowed content types and templates.\n"
        "- sale_promo only for products on sale; new_arrival only for products marked new.\n"
        + ("- Do not use the same product twice in this plan.\n" if not allow_repeats else "")
        + "- Vary content types and products across the day; favour products not posted recently, products on "
        "sale or new, and what performs well.\n"
        "- angle: one sentence describing the creative angle (compliant: no health or disease claims).\n"
        "- reason: max 20 words on why this choice.\n"
        'Reply: {"slots": [{"slot_id": "…", "product_ids": ["…"], "content_type": "…", "template_key": "…", '
        '"angle": "…", "reason": "…"}], "summary": "one sentence"}'
    )
    return "\n\n".join(sections)


def promo_texts_prompt(*, theme: str, products: list[dict], discount_text: str, coupon_code: str, count: int, brand: BrandKit) -> str:
    titles = [str(product.get("title") or "") for product in products[:6] if product.get("title")]
    lines = [
        _brand_lines(brand),
        f"TASK: write {count} different short promotional lines for on-image text in a rotating promotion.",
        f"Theme: {theme or 'general offer'}",
    ]
    if titles:
        lines.append(f"Products: {', '.join(titles)}")
    if discount_text:
        lines.append(f"Discount (quote exactly, do not change the number): {discount_text}")
    if coupon_code:
        lines.append(f"Coupon code (quote exactly when used): {coupon_code}")
    lines.append(
        "RULES\n- Each line at most 40 characters including spaces. No emojis, no hashtags, no URLs.\n"
        "- British English, premium and calm; no fake urgency, no health claims, no invented numbers.\n"
        "- Vary the wording: some lead with the offer, some with the ritual or product.\n"
        f'Reply: {{"texts": ["…", …]}} with exactly {count} items.'
    )
    return "\n\n".join(lines)


def brand_content_prompt(*, kind: str, topic_hint: str, recent: list[str], brand: BrandKit, language: str) -> str:
    sections = [
        _brand_lines(brand),
        f"TASK: create one '{kind.replace('_', ' ')}' brand post.\n{BRAND_CONTENT_GUIDANCE[kind]}",
        f"LANGUAGE: {language_instruction(language)}",
    ]
    if topic_hint:
        sections.append(f"Topic hint: {topic_hint[:300]}")
    if recent:
        sections.append("RECENTLY POSTED (do not repeat or closely paraphrase):\n" + "\n".join(f"- {item[:160]}" for item in recent[:15]))
    options_rule = {
        "poll": '"options": 2–4 short answers (max 25 characters each), "answer": ""',
        "quiz": '"options": 2–4 short answers (max 25 characters each), "answer": exactly one of the options',
    }.get(kind, '"options": [], "answer": ""')
    sections.append(
        "FORMAT\n"
        '{"headline": "on-image text, max 60 characters", "body": "supporting on-image text, max 220 characters", '
        f"{options_rule}, "
        '"caption": "social caption, 150–400 characters, no hashtags or URLs"}'
    )
    return "\n\n".join(sections)


def insights_prompt(*, rollups: dict, content_types: list[str], hours: list[int]) -> str:
    return "\n\n".join([
        "TASK: analyse these social media performance roll-ups and advise the auto-pilot.",
        "ROLL-UPS\n" + compact_json(rollups, 6000),
        "RULES\n"
        "- Every number you mention must appear in the roll-ups (you may round it or show a rate as a percentage).\n"
        f"- content_type_weights: only these content types: {', '.join(content_types) or '(none)'}; "
        "multipliers between 0.2 and 3.0 where 1.0 means 'keep as is'.\n"
        f"- best_hours: only hours that appear in the roll-ups: {', '.join(str(hour) for hour in hours) or '(none)'}.\n"
        "- 2–5 concrete recommendations, each one sentence.\n"
        'Reply: {"summary": "2–3 sentences", "recommendations": ["…"], '
        '"content_type_weights": {"content_type": 1.0}, "best_hours": [9, 18]}'
    ])


def join_lines(values: Iterable[str]) -> str:
    return "\n".join(f"- {value}" for value in values if value)
