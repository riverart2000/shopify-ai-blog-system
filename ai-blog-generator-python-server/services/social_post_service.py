"""services/social_post_service.py — Generate short sales-focused social post variants."""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import replace
from typing import Any
from urllib.parse import urlparse

import db
import providers

from . import llm_service

logger = logging.getLogger("ai_blog_server")

# Text-first providers for this page. Video-first channels (for example TikTok/YouTube)
# are intentionally excluded and will be handled in a dedicated video workflow later.
SUPPORTED_TEXT_PROVIDERS = ["instagram", "facebook", "x", "linkedin", "pinterest"]
_SOCIAL_IMAGE_RATIO = "9:16"
_SOCIAL_DISCOUNT_BASE_URL = (
    os.environ.get("SOCIAL_DISCOUNT_BASE_URL")
    or "https://bioluxelab.com/discount/LAUNCH20?redirect="
).strip()
_DEFAULT_OFFER_TYPE = "direct_offer"
_SOCIAL_OFFER_TYPES: dict[str, dict[str, str]] = {
    "direct_offer": {
        "label": "Direct Offers",
        "instructions": (
            "Lead with a strong discount-led hook and immediate buy-now CTA with minimal preamble."
        ),
    },
    "ingredient_spotlight": {
        "label": "Ingredient Spotlights",
        "instructions": (
            "Highlight key ingredient(s), what they do, and why they matter, then bridge into the offer."
        ),
    },
    "science_post": {
        "label": "Science Posts",
        "instructions": (
            "Use evidence-led, mechanism-focused explanation in plain language, then drive conversion with offer CTA."
        ),
    },
    "problem_solution": {
        "label": "Problem → Solution",
        "instructions": (
            "Open with a common problem and clearly position the product as the practical solution before the offer block."
        ),
    },
    "benefits_post": {
        "label": "Benefits Posts",
        "instructions": (
            "Lead with concise, high-impact benefit statements and outcome language, then convert with the offer."
        ),
    },
    "lifestyle_post": {
        "label": "Lifestyle Posts",
        "instructions": (
            "Frame the product inside daily routines, habits, and aspirational wellness lifestyle context, then include offer CTA."
        ),
    },
    "myth_busting": {
        "label": "Myth Busting",
        "instructions": (
            "Use a myth-vs-fact framing with clear, trustworthy clarification, then transition to the offer and CTA."
        ),
    },
    "educational_carousel": {
        "label": "Educational Carousels",
        "instructions": (
            "Use a carousel-style teaching tone (step-by-step or point-by-point education), then end with offer CTA."
        ),
    },
    "blog_promotion": {
        "label": "Blog Promotion",
        "instructions": (
            "Prioritize promoting the read-more content first, then invite shopping with the launch discount offer."
        ),
    },
    "motivational": {
        "label": "Motivational",
        "instructions": (
            "Use uplifting motivational language tied to self-improvement and consistency, then include practical offer CTA."
        ),
    },
}

_SOCIAL_PROMPT_ENDING = (
    "Return ONLY a single valid JSON object with exactly these fields:\n"
    '  "title": string — short campaign name (max 8 words)\n'
    '  "summary": string — one sentence post objective\n'
    '  "keywords": array of strings — 4 to 8 SEO/product terms\n'
    '  "hashtags": array of strings — 4 to 8 hashtags with # prefix\n'
    '  "content": string — use this exact provider block structure and keep line breaks:\n'
    "instagram:\n<multiline post text>\n\n"
    "facebook:\n<multiline post text>\n\n"
    "x:\n<multiline post text>\n\n"
    "linkedin:\n<multiline post text>\n\n"
    "pinterest:\n<multiline post text>\n"
    "Each provider block must contain readable spacing (short paragraphs / line breaks).\n"
    "No markdown. No extra fields. Raw JSON only."
)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _social_image_target_count() -> int:
    raw = _clean_text(os.environ.get("SOCIAL_IMAGE_COUNT", "2")) or "2"
    try:
        return max(1, min(int(raw), 4))
    except ValueError:
        return 2


def _normalise_offer_type(value: str) -> str:
    candidate = _clean_text(value).lower()
    if candidate in _SOCIAL_OFFER_TYPES:
        return candidate
    return _DEFAULT_OFFER_TYPE


def _offer_type_label(value: str) -> str:
    normalized = _normalise_offer_type(value)
    return _SOCIAL_OFFER_TYPES[normalized]["label"]


def _offer_type_instructions(value: str) -> str:
    normalized = _normalise_offer_type(value)
    return _SOCIAL_OFFER_TYPES[normalized]["instructions"]


def _offer_type_fallback_hook(value: str, product_title: str) -> str:
    normalized = _normalise_offer_type(value)
    hooks: dict[str, str] = {
        "direct_offer": f"Limited launch window on {product_title} — claim 20% off today.",
        "ingredient_spotlight": f"What makes {product_title} effective? Let us spotlight the key ingredients.",
        "science_post": f"Why is {product_title} getting attention in science-led wellness circles?",
        "problem_solution": f"Struggling with consistency in your wellness routine? {product_title} helps close that gap.",
        "benefits_post": f"{product_title} is designed to support energy, resilience, and daily wellness momentum.",
        "lifestyle_post": f"Make {product_title} part of a smarter daily longevity lifestyle.",
        "myth_busting": f"Myth: premium wellness products are hype. Fact: {product_title} is built on purpose-led formulation.",
        "educational_carousel": f"Quick educational breakdown: how {product_title} fits into a modern wellness protocol.",
        "blog_promotion": f"Read the full breakdown on {product_title} and discover the key takeaways before you buy.",
        "motivational": f"Small consistent actions matter — {product_title} can support your long-term wellness goals.",
    }
    return hooks.get(normalized, hooks[_DEFAULT_OFFER_TYPE])


def _cap_text(provider: str, text: str) -> str:
    # Keep platform limits while preserving line-break structure for offer blocks.
    hard_limits = {
        "x": 280,
        "instagram": 1800,
        "facebook": 1800,
        "linkedin": 2200,
        "pinterest": 900,
    }
    limit = hard_limits.get(provider, 420)
    trimmed = _clean_text(text)
    trimmed = re.sub(r"[\t ]+", " ", trimmed)
    trimmed = re.sub(r"\n{3,}", "\n\n", trimmed)
    if len(trimmed) <= limit:
        return trimmed
    return trimmed[: max(0, limit - 1)].rstrip() + "..."


def _extract_provider_lines(content: str) -> dict[str, str]:
    blocks: dict[str, list[str]] = {}
    current_provider = ""

    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        header = re.match(
            r"^(instagram|facebook|x|linkedin|pinterest)\s*:\s*(.*)$",
            stripped,
            re.IGNORECASE,
        )
        if header:
            provider = header.group(1).lower().strip()
            current_provider = provider
            blocks.setdefault(provider, [])
            inline_text = header.group(2).strip()
            if inline_text:
                blocks[provider].append(inline_text)
            continue

        if not current_provider:
            continue

        if stripped:
            blocks[current_provider].append(stripped)
            continue

        # Preserve intentional paragraph spacing inside a provider block.
        provider_lines = blocks[current_provider]
        if provider_lines and provider_lines[-1] != "":
            provider_lines.append("")

    out: dict[str, str] = {}
    for provider, parts in blocks.items():
        text = "\n".join(parts).strip()
        if not text:
            continue
        out[provider] = _cap_text(provider, text)
    return out


def _derive_product_redirect_path(product_url: str, product_handle: str) -> str:
    parsed = urlparse(product_url)
    match = re.search(r"/products/([^/?#]+)", parsed.path or "", flags=re.IGNORECASE)
    if match:
        return f"/products/{match.group(1)}"

    handle = _clean_text(product_handle)
    if handle:
        safe_handle = handle.split("?")[0].split("#")[0].strip("/")
        if safe_handle:
            if safe_handle.startswith("products/"):
                safe_handle = safe_handle.split("/", 1)[1]
            return f"/products/{safe_handle}"

    return "/products/nmn"


def _build_discount_url(product_url: str, product_handle: str) -> str:
    return f"{_SOCIAL_DISCOUNT_BASE_URL}{_derive_product_redirect_path(product_url, product_handle)}"


def _build_social_text_prompt(
    *,
    store_name: str,
    product_title: str,
    product_url: str,
    discount_url: str,
    brief_text: str,
    offer_type: str,
) -> str:
    normalized_offer_type = _normalise_offer_type(offer_type)
    offer_instructions = _offer_type_instructions(normalized_offer_type)

    return (
        f"You are a direct-response social media copywriter for ecommerce.\n"
        f"Store: {store_name}\n"
        f"Goal: Write high quality short sales and marketing style social posts that create intrigue "
        f"and drive clicks to the store.\n"
        f"Offer style to apply: {_offer_type_label(normalized_offer_type)}\n"
        f"Product title: {product_title}\n"
        f"Product URL: {product_url or '(not provided)'}\n"
        f"Campaign URL (must use exactly): {discount_url}\n"
        f"Brief from merchant: {brief_text or '(not provided)'}\n\n"
        "Requirements:\n"
        f"- Offer framework: {offer_instructions}\n"
        "- Tone: confident, benefit-first, no fake urgency.\n"
        "- Mention a clear action to visit the product/store and claim the launch offer.\n"
        "- Include this exact campaign URL in EACH platform post: "
        f"{discount_url}\n"
        "- Platform length guidance:\n"
        "  - X: concise, 1 short paragraph + CTA (around 180-260 chars where possible).\n"
        "  - Instagram: slightly longer, 2-4 short paragraphs with strong storytelling + CTA.\n"
        "  - Facebook: slightly longer, 2-4 short paragraphs with benefits + offer + CTA.\n"
        "  - LinkedIn: the most detailed, 3-5 short paragraphs with clear value framing + CTA.\n"
        "  - Pinterest: medium length, 2-3 short paragraphs focused on click-through intent.\n"
        "- Include the offer block line: 🎉 Launch Offer: Save 20%\n"
        "- Include the offer block line: 🚚 Free UK Delivery\n"
        "- Include a 'Read more:' line and use Product URL where sensible.\n"
        "- Include a 'Shop with 20% OFF:' line and use the campaign URL.\n"
        "- Mention that the offer is a 20% discount.\n"
        "- Keep each post concise and natural for that network.\n"
        "- Avoid repetitive openings across platforms."
    )


def _combine_social_text_prompt(prompt: str) -> str:
    return f"{prompt}\n\n{_SOCIAL_PROMPT_ENDING}"


def _derive_image_prompt_context(
    *,
    product_title: str,
    brief_text: str,
    offer_type: str,
) -> tuple[str, str, str]:
    normalized_offer_type = _normalise_offer_type(offer_type)
    fallback_hook = _offer_type_fallback_hook(normalized_offer_type, product_title)
    campaign_name = f"{product_title} {_offer_type_label(normalized_offer_type)}".strip()
    campaign_summary = _clean_text(brief_text) or fallback_hook
    sample_post_text = fallback_hook
    return campaign_name[:120], campaign_summary[:260], sample_post_text[:260]


def _ensure_discount_url(provider: str, text: str, discount_url: str) -> str:
    content = _clean_text(text)
    if not content:
        return _cap_text(provider, discount_url)
    return _cap_text(provider, content)


def _fallback_post_text(
    *,
    provider: str,
    product_title: str,
    product_url: str,
    discount_url: str,
    brief_text: str,
    hashtags: list[str],
    offer_type: str,
) -> str:
    hashtag_suffix = " ".join(tag for tag in hashtags[:4] if tag)
    teaser = brief_text.strip() or _offer_type_fallback_hook(offer_type, product_title)
    lines: list[str] = [
        teaser,
        f"{product_title} is built for modern longevity-focused routines.",
        "🎉 Launch Offer: Save 20%",
        "🚚 Free UK Delivery",
    ]
    if _clean_text(product_url):
        lines.extend(["Read more:", product_url])
    lines.extend(["Shop with 20% OFF:", discount_url])
    if hashtag_suffix:
        lines.append(hashtag_suffix)

    return _cap_text(provider, "\n".join(lines))


def _format_offer_layout(text: str, product_url: str, discount_url: str) -> str:
    result = _clean_text(text)

    # Force readable spacing around offer and CTA sections.
    result = re.sub(r"\s*(🎉\s*Launch Offer:\s*Save\s*20%)", r"\n\n\1", result, flags=re.IGNORECASE)
    result = re.sub(r"\s*(🚚\s*Free UK Delivery)", r"\n\1", result, flags=re.IGNORECASE)
    result = re.sub(r"\s*(Read more:)", r"\n\n\1", result, flags=re.IGNORECASE)
    result = re.sub(r"\s*(Shop with 20% OFF:)", r"\n\n\1", result, flags=re.IGNORECASE)

    if _clean_text(product_url):
        result = re.sub(
            rf"(Read more:)\s*{re.escape(product_url)}",
            rf"\1\n{product_url}",
            result,
            flags=re.IGNORECASE,
        )

    result = re.sub(
        rf"(Shop with 20% OFF:)\s*{re.escape(discount_url)}",
        rf"\1\n{discount_url}",
        result,
        flags=re.IGNORECASE,
    )

    # Keep hashtags as a separate visual block.
    result = re.sub(r"\s+(#\w)", r"\n\n\1", result, count=1)

    result = re.sub(r"[ \t]+\n", "\n", result)
    result = re.sub(r"\n[ \t]+", "\n", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return _clean_text(result)


def _ensure_offer_structure(
    *,
    provider: str,
    text: str,
    product_title: str,
    product_url: str,
    discount_url: str,
    hashtags: list[str],
    offer_type: str,
) -> str:
    content = _ensure_discount_url(provider, text, discount_url)
    if not content:
        return _fallback_post_text(
            provider=provider,
            product_title=product_title,
            product_url=product_url,
            discount_url=discount_url,
            brief_text="",
            hashtags=hashtags,
            offer_type=offer_type,
        )

    result = content
    lowered = result.lower()

    if "launch offer" not in lowered:
        result += "\n\n🎉 Launch Offer: Save 20%"
    if "free uk delivery" not in lowered:
        result += "\n🚚 Free UK Delivery"

    if _clean_text(product_url):
        if "read more:" not in lowered:
            result += f"\n\nRead more:\n{product_url}"
        elif product_url not in result:
            result += f"\n{product_url}"

    if "shop with 20% off" not in lowered:
        result += f"\n\nShop with 20% OFF:\n{discount_url}"
    elif discount_url not in result:
        result += f"\n{discount_url}"

    hashtag_suffix = " ".join(tag for tag in hashtags[:6] if tag)
    if hashtag_suffix and "#" not in result:
        result += f"\n\n{hashtag_suffix}"

    result = _format_offer_layout(result, product_url, discount_url)
    return _cap_text(provider, result)


def _build_social_image_prompt(
    *,
    store_name: str,
    product_title: str,
    product_url: str,
    product_image_url: str,
    offer_type: str,
    offer_type_label: str,
    offer_instructions: str,
    campaign_name: str,
    campaign_summary: str,
    sample_post_text: str,
    brief_text: str,
    discount_url: str,
    variant_index: int,
) -> str:
    visual_angles = [
        "hero product photography with premium lifestyle art direction",
        "hands-on realistic use-case shot with product as the focal point",
        "clean lab-style ecommerce product scene with true-to-life textures",
        "performance marketing ad composition with realistic depth and lighting",
    ]
    direction = visual_angles[variant_index % len(visual_angles)]
    normalized_offer_type = _normalise_offer_type(offer_type)
    brief_summary = _clean_text(campaign_summary)[:260]
    campaign_title = _clean_text(campaign_name)[:120]
    offer_description = _clean_text(brief_text)[:320]
    sample_line = re.sub(r"\s+", " ", _clean_text(sample_post_text))[:260]
    product_ref_line = (
        f"Reference product image URL (keep the same product identity/packaging/shape/colors): {product_image_url}. "
        if _clean_text(product_image_url)
        else ""
    )

    return (
        "Create one high-quality photorealistic ecommerce social ad image. "
        f"Brand/store: {store_name}. "
        f"Product: {product_title}. "
        f"Product URL context: {product_url or '(not provided)'}. "
        f"Offer type: {offer_type_label or _offer_type_label(normalized_offer_type)} ({normalized_offer_type}). "
        f"Offer strategy: {offer_instructions or _offer_type_instructions(normalized_offer_type)}. "
        f"Campaign name: {campaign_title or f'{product_title} launch offer'}. "
        f"Campaign summary: {brief_summary or 'Launch campaign with conversion-focused urgency and trust.'}. "
        f"Offer description from merchant: {offer_description or 'Show premium wellness value and clear purchase intent.'}. "
        f"Reference post copy tone: {sample_line or 'Benefit-led, concise, and sales-oriented.'}. "
        f"Visual direction: {direction}. "
        f"{product_ref_line}"
        "The image must look like real commercial product photography, not an illustration or CGI render. "
        "Use realistic materials, shadows, reflections, and camera depth-of-field. "
        "Keep the product dominant and clearly visible in the frame. "
        "Convey launch-offer urgency (20% discount) through scene mood and composition only. "
        "Format: strict vertical 9:16 composition, stories/reels ready, premium mobile-first framing. "
        f"Campaign destination URL for CTA context: {discount_url}. "
        "Negative constraints: no watermarks, no unrelated logos, no UI mockups, no collage grid, no readable text overlays, no cartoon style."
    )


def _with_social_image_overrides(model: providers.ModelRecord) -> providers.ModelRecord:
    if model.provider != "openai":
        return model

    extra = dict(model.extra)
    if not _clean_text(extra.get("size")):
        # OpenAI images support explicit size; this maps to a vertical 9:16 output.
        extra["size"] = "1024x1792"
    extra["image_count"] = 1
    return replace(model, extra_json=json.dumps(extra))


def _model_supports_reference_image(model: providers.ModelRecord) -> bool:
    provider_name = _clean_text(model.provider).lower()
    model_name = _clean_text(model.model_name).lower()

    if provider_name == "openai":
        return "gpt-image" in model_name

    if provider_name == "grok":
        return True

    if provider_name == "replicate":
        extra = model.extra
        explicit_field = _clean_text(extra.get("reference_image_field"))
        if explicit_field:
            return True
        input_obj = extra.get("input") if isinstance(extra.get("input"), dict) else {}
        input_keys = {str(key).strip().lower() for key in input_obj.keys()}
        reference_like_keys = {
            "image",
            "input_image",
            "init_image",
            "reference_image",
            "conditioning_image",
        }
        return any(key in input_keys for key in reference_like_keys)

    return False


def _reference_model_rank(model: providers.ModelRecord) -> int:
    provider_name = _clean_text(model.provider).lower()
    model_name = _clean_text(model.model_name).lower()

    # Strong preference: OpenAI GPT Image reference conditioning.
    if provider_name == "openai" and "gpt-image" in model_name:
        return 0

    # Next best: other known image-conditioning providers.
    if provider_name == "grok":
        return 1
    if provider_name == "replicate":
        return 2

    return 9


async def _generate_social_marketing_image_once(
    store_id: str,
    prompt: str,
    product_image_url: str,
) -> tuple[str | None, dict[str, Any]]:
    rows = await db.get_active_image_models(store_id)
    if not rows:
        return None, {
            "selected_provider": "",
            "selected_model": "",
            "selected_model_name": "",
            "selected_reference_requested": bool(_clean_text(product_image_url)),
            "selected_reference_attached": False,
            "selected_reference_fallback": False,
            "attempts": [],
        }

    debug_data: dict[str, Any] = {
        "selected_provider": "",
        "selected_model": "",
        "selected_model_name": "",
        "selected_reference_requested": bool(_clean_text(product_image_url)),
        "selected_reference_attached": False,
        "selected_reference_fallback": False,
        "attempts": [],
    }

    reference_image = _clean_text(product_image_url) or None
    if reference_image:
        # Prefer models that can actually consume reference images, with GPT Image first.
        rows = sorted(
            rows,
            key=lambda row: (
                0 if _model_supports_reference_image(providers.ModelRecord.from_dict(row)) else 1,
                _reference_model_rank(providers.ModelRecord.from_dict(row)),
                int(row.get("priority") or 0),
                str(row.get("name") or ""),
            ),
        )

    skip_providers: set[str] = set()
    for row in rows:
        model = providers.ModelRecord.from_dict(row)
        if model.provider in skip_providers:
            continue

        attempt_data: dict[str, Any] = {
            "provider": model.provider,
            "model": model.name,
            "model_name": model.model_name,
            "status": "failed",
            "error": "",
            "reference_requested": bool(reference_image),
            "reference_attempted": False,
            "reference_attached": False,
            "reference_fallback_to_prompt_only": False,
        }

        try:
            effective_model = _with_social_image_overrides(model)
            provider = providers.get_image_provider(effective_model)
            supports_reference = bool(reference_image) and _model_supports_reference_image(effective_model)
            reference_payload = reference_image if supports_reference else None
            urls: list[str] = []
            reference_attempted = False
            reference_attached = False
            used_reference_fallback = False
            try:
                if reference_payload:
                    reference_attempted = True
                    reference_attached = True
                urls = await provider.generate_images(
                    prompt,
                    1,
                    reference_image=reference_payload,
                )
            except providers.ProviderError as ref_exc:
                if not reference_payload:
                    raise

                # Some image models reject conditioning fields; retry prompt-only.
                reference_attempted = True
                reference_attached = False
                used_reference_fallback = True
                logger.info(
                    "Social image provider %s rejected reference image; retrying without reference: %s",
                    model.name,
                    ref_exc,
                )
                urls = await provider.generate_images(prompt, 1)

            attempt_data["reference_attempted"] = reference_attempted
            attempt_data["reference_attached"] = reference_attached
            attempt_data["reference_fallback_to_prompt_only"] = used_reference_fallback

            if urls:
                attempt_data["status"] = "success"
                debug_data["selected_provider"] = model.provider
                debug_data["selected_model"] = model.name
                debug_data["selected_model_name"] = model.model_name
                debug_data["selected_reference_attached"] = reference_attached
                debug_data["selected_reference_fallback"] = used_reference_fallback
                debug_data["attempts"].append(attempt_data)
                return urls[0], debug_data

            attempt_data["status"] = "no_url"
            debug_data["attempts"].append(attempt_data)
        except providers.ProviderError as exc:
            err_msg = str(exc)
            attempt_data["status"] = "failed"
            attempt_data["error"] = err_msg
            debug_data["attempts"].append(attempt_data)
            logger.warning("Social image provider %s failed: %s", model.name, err_msg)
            await db.log_model_error(store_id, model.id, model.provider, "image_error", err_msg)
            if not exc.retryable:
                skip_providers.add(model.provider)
        except Exception as exc:
            err_msg = f"Unexpected error: {exc}"
            attempt_data["status"] = "failed"
            attempt_data["error"] = err_msg
            debug_data["attempts"].append(attempt_data)
            logger.exception("Unexpected social image provider error %s", model.name)
            await db.log_model_error(store_id, model.id, model.provider, "unexpected_error", err_msg)

    return None, debug_data


async def generate_social_marketing_images(
    *,
    store_id: str,
    store_name: str,
    product_title: str,
    product_url: str,
    product_image_url: str,
    offer_type: str,
    offer_type_label: str,
    offer_instructions: str,
    campaign_name: str,
    campaign_summary: str,
    sample_post_text: str,
    brief_text: str,
    discount_url: str,
    image_count: int,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    target_count = max(1, min(image_count, 4))
    image_urls: list[str] = []
    image_prompts: list[str] = []
    image_provider_runs: list[dict[str, Any]] = []
    seen: set[str] = set()

    for index in range(target_count):
        prompt = _build_social_image_prompt(
            store_name=store_name,
            product_title=product_title,
            product_url=product_url,
            product_image_url=product_image_url,
            offer_type=offer_type,
            offer_type_label=offer_type_label,
            offer_instructions=offer_instructions,
            campaign_name=campaign_name,
            campaign_summary=campaign_summary,
            sample_post_text=sample_post_text,
            brief_text=brief_text,
            discount_url=discount_url,
            variant_index=index,
        )
        image_prompts.append(prompt)
        image_url, run_debug = await _generate_social_marketing_image_once(
            store_id,
            prompt,
            product_image_url,
        )
        image_provider_runs.append({
            "index": index + 1,
            "prompt": prompt,
            "url": image_url or "",
            "selected_provider": str(run_debug.get("selected_provider") or ""),
            "selected_model": str(run_debug.get("selected_model") or ""),
            "selected_model_name": str(run_debug.get("selected_model_name") or ""),
            "selected_reference_requested": bool(run_debug.get("selected_reference_requested")),
            "selected_reference_attached": bool(run_debug.get("selected_reference_attached")),
            "selected_reference_fallback": bool(run_debug.get("selected_reference_fallback")),
            "attempts": run_debug.get("attempts", []),
        })
        if not image_url or image_url in seen:
            continue
        seen.add(image_url)
        image_urls.append(image_url)

    return image_urls, image_prompts, image_provider_runs


def _build_social_image_prompts(
    *,
    store_name: str,
    product_title: str,
    product_url: str,
    product_image_url: str,
    offer_type: str,
    offer_type_label: str,
    offer_instructions: str,
    campaign_name: str,
    campaign_summary: str,
    sample_post_text: str,
    brief_text: str,
    discount_url: str,
    image_count: int,
) -> list[str]:
    target_count = max(1, min(image_count, 4))
    prompts: list[str] = []
    for index in range(target_count):
        prompts.append(
            _build_social_image_prompt(
                store_name=store_name,
                product_title=product_title,
                product_url=product_url,
                product_image_url=product_image_url,
                offer_type=offer_type,
                offer_type_label=offer_type_label,
                offer_instructions=offer_instructions,
                campaign_name=campaign_name,
                campaign_summary=campaign_summary,
                sample_post_text=sample_post_text,
                brief_text=brief_text,
                discount_url=discount_url,
                variant_index=index,
            )
        )
    return prompts


def _serialize_provider_candidates(rows: list[dict]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(row.get("id") or ""),
            "name": str(row.get("name") or ""),
            "provider": str(row.get("provider") or ""),
            "model_type": str(row.get("model_type") or ""),
            "model_name": str(row.get("model_name") or ""),
            "priority": int(row.get("priority") or 0),
        }
        for row in rows
    ]


async def preview_social_generation_prompts(
    *,
    store_id: str,
    store_name: str,
    product_title: str,
    product_handle: str = "",
    product_url: str,
    product_image_url: str = "",
    brief_text: str,
    offer_type: str = _DEFAULT_OFFER_TYPE,
    model_id: str | None = None,
) -> dict[str, Any]:
    title = _clean_text(product_title)
    handle = _clean_text(product_handle)
    url = _clean_text(product_url)
    brief = _clean_text(brief_text)
    if not title:
        raise ValueError("product_title is required")

    normalized_offer_type = _normalise_offer_type(offer_type)
    offer_label = _offer_type_label(normalized_offer_type)
    offer_instructions = _offer_type_instructions(normalized_offer_type)
    discount_url = _build_discount_url(url, handle)

    text_prompt = _build_social_text_prompt(
        store_name=store_name,
        product_title=title,
        product_url=url,
        discount_url=discount_url,
        brief_text=brief,
        offer_type=normalized_offer_type,
    )
    combined_text_prompt = _combine_social_text_prompt(text_prompt)

    campaign_name, campaign_summary, sample_post_text = _derive_image_prompt_context(
        product_title=title,
        brief_text=brief,
        offer_type=normalized_offer_type,
    )
    image_prompts = _build_social_image_prompts(
        store_name=store_name,
        product_title=title,
        product_url=url,
        product_image_url=product_image_url,
        offer_type=normalized_offer_type,
        offer_type_label=offer_label,
        offer_instructions=offer_instructions,
        campaign_name=campaign_name,
        campaign_summary=campaign_summary,
        sample_post_text=sample_post_text,
        brief_text=brief,
        discount_url=discount_url,
        image_count=_social_image_target_count(),
    )

    text_models = await db.get_active_text_models(store_id)
    image_models = await db.get_active_image_models(store_id)
    if model_id:
        text_models = [row for row in text_models if str(row.get("id") or "") == model_id]

    image_reference_conditioning_possible = any(
        _model_supports_reference_image(providers.ModelRecord.from_dict(row))
        for row in image_models
    )

    return {
        "offer_type": normalized_offer_type,
        "offer_type_label": offer_label,
        "discount_url": discount_url,
        "image_ratio": _SOCIAL_IMAGE_RATIO,
        "image_reference_url": _clean_text(product_image_url),
        "image_reference_attached": False,
        "image_reference_available": bool(_clean_text(product_image_url)),
        "image_reference_conditioning_possible": image_reference_conditioning_possible,
        "product_image_included_in_results": bool(_clean_text(product_image_url)),
        "product_image_fallback_active": bool(_clean_text(product_image_url) and not image_reference_conditioning_possible),
        "text_generation_prompt": text_prompt,
        "text_generation_prompt_contract": _SOCIAL_PROMPT_ENDING,
        "text_generation_prompt_combined": combined_text_prompt,
        "image_generation_prompts": image_prompts,
        "text_provider_candidates": _serialize_provider_candidates(text_models),
        "image_provider_candidates": _serialize_provider_candidates(image_models),
    }


def _normalise_hashtags(raw: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in raw:
        tag = _clean_text(value)
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + tag.replace(" ", "")
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
        if len(result) >= 8:
            break
    return result


async def generate_social_post_variants(
    *,
    store_id: str,
    store_name: str,
    product_title: str,
    product_handle: str = "",
    product_url: str,
    product_image_url: str = "",
    brief_text: str,
    offer_type: str = _DEFAULT_OFFER_TYPE,
    model_id: str | None = None,
) -> dict[str, Any]:
    title = _clean_text(product_title)
    handle = _clean_text(product_handle)
    url = _clean_text(product_url)
    brief = _clean_text(brief_text)
    discount_url = _build_discount_url(url, handle)
    normalized_offer_type = _normalise_offer_type(offer_type)
    offer_instructions = _offer_type_instructions(normalized_offer_type)

    if not title:
        raise ValueError("product_title is required")

    prompt = _build_social_text_prompt(
        store_name=store_name,
        product_title=title,
        product_url=url,
        discount_url=discount_url,
        brief_text=brief,
        offer_type=normalized_offer_type,
    )

    generated = await llm_service.generate_text(
        store_id,
        prompt,
        model_id=model_id,
        prompt_ending_override=_SOCIAL_PROMPT_ENDING,
    )

    campaign_name = _clean_text(generated.get("title")) or f"{title} spotlight"
    summary = _clean_text(generated.get("summary"))
    keywords = [str(item).strip() for item in generated.get("keywords", []) if str(item).strip()][:8]
    hashtags = _normalise_hashtags(generated.get("hashtags", []))

    content = _clean_text(generated.get("content"))
    provider_texts = _extract_provider_lines(content)

    for provider in SUPPORTED_TEXT_PROVIDERS:
        if provider not in provider_texts:
            provider_texts[provider] = _fallback_post_text(
                provider=provider,
                product_title=title,
                product_url=url,
                discount_url=discount_url,
                brief_text=brief,
                hashtags=hashtags,
                offer_type=normalized_offer_type,
            )
        provider_texts[provider] = _ensure_offer_structure(
            provider=provider,
            text=provider_texts[provider],
            product_title=title,
            product_url=url,
            discount_url=discount_url,
            hashtags=hashtags,
            offer_type=normalized_offer_type,
        )

    if not summary:
        summary = provider_texts.get("instagram") or provider_texts.get("facebook") or "Social post draft"

    image_urls: list[str] = []
    image_generation_prompts: list[str] = []
    image_provider_runs: list[dict[str, Any]] = []
    target_image_count = _social_image_target_count()
    try:
        campaign_hint_name, campaign_hint_summary, sample_post_text = _derive_image_prompt_context(
            product_title=title,
            brief_text=brief,
            offer_type=normalized_offer_type,
        )
        image_urls, image_generation_prompts, image_provider_runs = await generate_social_marketing_images(
            store_id=store_id,
            store_name=store_name,
            product_title=title,
            product_url=url,
            product_image_url=product_image_url,
            offer_type=normalized_offer_type,
            offer_type_label=_offer_type_label(normalized_offer_type),
            offer_instructions=offer_instructions,
            campaign_name=campaign_hint_name,
            campaign_summary=campaign_hint_summary,
            sample_post_text=sample_post_text,
            brief_text=brief,
            discount_url=discount_url,
            image_count=target_image_count,
        )
    except Exception:
        logger.exception("Social marketing image generation failed for store=%s", store_id)

    product_reference_url = _clean_text(product_image_url)
    product_image_included_in_results = False
    if product_reference_url:
        if product_reference_url not in image_urls:
            image_urls.append(product_reference_url)
            image_provider_runs.append(
                {
                    "index": len(image_provider_runs) + 1,
                    "prompt": "(Direct product main image from Shopify Admin API)",
                    "url": product_reference_url,
                    "selected_provider": "shopify_admin_product_image",
                    "selected_model": "shopify_product_main_image",
                    "selected_model_name": "shopify_product_main_image",
                    "selected_reference_requested": True,
                    "selected_reference_attached": True,
                    "selected_reference_fallback": False,
                    "attempts": [],
                }
            )
        product_image_included_in_results = product_reference_url in image_urls

    image_reference_used_in_ai_generation = any(
        bool(run.get("selected_reference_attached"))
        for run in image_provider_runs
        if str(run.get("selected_provider") or "") != "shopify_admin_product_image"
    )

    combined_text_prompt = _combine_social_text_prompt(prompt)
    text_provider_candidates = _serialize_provider_candidates(await db.get_active_text_models(store_id))
    image_provider_candidates = _serialize_provider_candidates(await db.get_active_image_models(store_id))

    return {
        "campaign_name": campaign_name,
        "summary": summary,
        "offer_type": normalized_offer_type,
        "offer_type_label": _offer_type_label(normalized_offer_type),
        "keywords": keywords,
        "hashtags": hashtags,
        "discount_url": discount_url,
        "image_urls": image_urls,
        "image_ratio": _SOCIAL_IMAGE_RATIO,
        "image_reference_url": product_reference_url,
        "image_reference_attached": image_reference_used_in_ai_generation,
        "image_reference_available": bool(product_reference_url),
        "image_reference_used_in_ai_generation": image_reference_used_in_ai_generation,
        "product_image_included_in_results": product_image_included_in_results,
        "product_image_fallback_active": bool(product_reference_url and not image_reference_used_in_ai_generation),
        "text_generation_prompt": prompt,
        "text_generation_prompt_contract": _SOCIAL_PROMPT_ENDING,
        "text_generation_prompt_combined": combined_text_prompt,
        "image_generation_prompts": image_generation_prompts,
        "text_provider_candidates": text_provider_candidates,
        "image_provider_candidates": image_provider_candidates,
        "image_provider_runs": image_provider_runs,
        "provider_texts": provider_texts,
        "generated_by": generated.get("_model_name", ""),
        "generated_provider": generated.get("_model_provider", ""),
    }
