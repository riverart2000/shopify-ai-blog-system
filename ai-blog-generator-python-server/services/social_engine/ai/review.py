"""AI review of a generated post (Gemma), with an optional vision check of the image."""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from db.social_engine import observability

from ..context import StoreContext
from ..errors import describe
from ..models import ContentType
from . import prompts
from .base import CaptionResult, LLMRequest, ReviewResult
from .guard import check_captions
from .llm import OpenAICompatibleProvider, get_router
from .text import extract_json

logger = logging.getLogger("ai_blog_server.social_engine.ai")

_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".gif"})
_NO_PRODUCT_TYPES = frozenset({ContentType.QUOTE, ContentType.BLOG_ARTICLE, ContentType.HOLIDAY})
_CAPTION_CHARS = 1500


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    raise ValueError("expected a list of strings")


class ReviewDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    score: int = Field(ge=0, le=10)
    critical_issues: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    summary: str = ""

    @field_validator("score", mode="before")
    @classmethod
    def _score(cls, value: Any) -> int:
        if isinstance(value, str):
            value = value.split("/")[0].strip()
        number = round(float(value))
        if not 0 <= number <= 10:
            raise ValueError("score must be between 0 and 10")
        return int(number)

    @field_validator("critical_issues", "issues", "suggestions", mode="before")
    @classmethod
    def _lists(cls, value: Any) -> list[str]:
        return _str_list(value)


class VisionDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    product_visible: bool | None = None
    text_legible: bool | None = None
    has_text: bool | None = None
    claims: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _keys(cls, value: Any) -> Any:
        # Small vision models sometimes write "product visible" instead of "product_visible".
        if isinstance(value, dict):
            keys = {re.sub(r"[^a-z0-9]+", "_", str(k).strip().lower()).strip("_"): v for k, v in value.items()}
            for singular in ("claim", "issue"):
                if singular in keys and f"{singular}s" not in keys:
                    keys[f"{singular}s"] = keys.pop(singular)
            return keys
        return value

    @field_validator("claims", "issues", mode="before")
    @classmethod
    def _lists(cls, value: Any) -> list[str]:
        # Models can repeat themselves; keep each item once.
        return list(dict.fromkeys(_str_list(value)))


_VISION_FLAG = re.compile(r'"(product[ _.\-]?visible|text[ _.\-]?legible|has[ _.\-]?text)"\s*:\s*(true|false)',
                          re.IGNORECASE)
_SPELLING_NOISE = re.compile(r"misspel|spelling|typo", re.IGNORECASE)
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')


_FLAG_FIELDS = {"product": "product_visible", "text": "text_legible", "has": "has_text"}


def _vision_payload(text: str) -> dict[str, Any]:
    """Parse the vision reply, salvaging the answers from a reply cut off at the token limit."""
    try:
        data = extract_json(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        return data
    # Cut off mid-reply: the parser may only find an inner list, so read the fields directly.
    salvaged: dict[str, Any] = {
        next(field for prefix, field in _FLAG_FIELDS.items() if match.group(1).lower().startswith(prefix)):
            match.group(2).lower() == "true"
        for match in _VISION_FLAG.finditer(text or "")
    }
    for key in ("claims", "issues"):
        found = re.search(rf'"{key[:-1]}s?"\s*:\s*\[(.*?)(?:\]|$)', text or "", re.IGNORECASE | re.DOTALL)
        if found:
            salvaged[key] = _QUOTED.findall(found.group(1))   # complete strings only
    if not salvaged:
        raise ValueError("The vision reply could not be read: " + " ".join((text or "").split())[:120])
    return salvaged


def _first_image(media_paths: list[str]) -> str:
    for path in media_paths:
        if path and Path(path).suffix.lower() not in _VIDEO_SUFFIXES:
            return path
    return ""


async def _vision_check(
    sctx: StoreContext, *, image: str, products: list[dict], headline: str, content_type: str, subject_id: str,
) -> tuple[list[str], bool]:
    """Return (issues, critical). Never raises: failures become a note."""
    ai = sctx.settings.ai
    if not ai.vision_model:
        return ["Vision check skipped: no vision model is configured."], False
    expects_product = bool(products) and content_type not in _NO_PRODUCT_TYPES
    provider = OpenAICompatibleProvider(
        ai.base_url, ai.vision_model, api_key=ai.api_key(), timeout=min(float(ai.timeout_seconds), 180.0),
        name="vision", max_attempts=1,
    )
    request = LLMRequest(
        system=prompts.VISION_SYSTEM,
        prompt=prompts.vision_prompt(
            product_titles=[str(product.get("title") or "") for product in products],
            headline=headline, expects_product=expects_product,
        ),
        images=[image], json_mode=True, temperature=0, max_tokens=400,
    )
    started = time.monotonic()
    ctx = sctx.call_context(subject_id)
    try:
        response = await provider.complete(request, ctx, operation="llm.review.vision")
        draft = VisionDraft.model_validate(_vision_payload(response.text))
    except Exception as exc:
        message = describe(exc)[1]
        logger.info(
            "Vision check skipped: %s", message,
            extra={"operation": "social_engine.llm.review.vision", "store_id": sctx.store_id, "correlation_id": subject_id},
        )
        return [f"Vision check skipped: {message[:200]}"], False
    issues: list[str] = []
    critical = False
    if expects_product and draft.product_visible is False:
        issues.append("Vision: the product is not clearly visible in the image.")
        critical = True
    if draft.has_text is not False and draft.text_legible is False:
        issues.append("Vision: text on the image is not legible.")
        critical = True
    if draft.claims:
        quoted = ", ".join(f'"{claim}"' for claim in draft.claims[:3])
        issues.append(f"Vision: the image shows a health or body claim ({quoted}) — use another photo or product.")
        critical = True
    # Our on-image text is drawn from exact strings, so "misspelt" reports from the
    # small vision model are noise; keep the other observations.
    observations = [item for item in draft.issues if not _SPELLING_NOISE.search(item)]
    issues.extend(f"Vision: {item}" for item in observations[:4])
    try:
        await observability.record_decision(
            store_id=sctx.store_id, decision_type="review.vision", subject_id=subject_id, model=response.model,
            valid=True, used_fallback=False, duration_ms=int((time.monotonic() - started) * 1000),
            input_data={"image": Path(image).name, "expects_product": expects_product},
            output_data=draft.model_dump(),
        )
    except Exception:
        logger.debug("Could not record vision decision", exc_info=True)
    return issues, critical


async def review_post(
    sctx: StoreContext,
    *,
    post: dict,
    captions: CaptionResult,
    products: list[dict],
    media_paths: list[str] | None = None,
) -> ReviewResult:
    """Score a generated post 0–10 and decide whether it can be auto-approved.

    approve = score ≥ guards.hold_low_review_score, no critical issue from the
    model, the content guard does not hold the post and the vision check (when
    an image is given and the vision model answers) found no critical problem.
    Raises ExternalServiceError/ValidationError when no review model answered.
    """
    settings = sctx.settings
    subject_id = str(post.get("id") or post.get("post_id") or "")
    guard = check_captions(captions, settings=settings)
    guard_notes = [f"[{issue.severity}] {issue.message}" for issue in guard.issues]

    channel_texts = []
    for item in captions.channel_captions.values():
        text = item.caption if len(item.caption) <= _CAPTION_CHARS else item.caption[:_CAPTION_CHARS] + " …"
        extra = []
        if item.title:
            extra.append(f"Title: {item.title}")
        if item.first_comment:
            extra.append(f"First comment: {item.first_comment}")
        channel_texts.append(f"[{item.provider}]\n{text}" + ("\n" + "\n".join(extra) if extra else ""))
    prompt = prompts.review_prompt(
        brand=settings.brand,
        post=post,
        products_text=prompts.products_block(products, settings.brand, include_price=True, description_chars=700),
        on_image={"headline": captions.headline, "subheadline": captions.subheadline, "cta": captions.cta,
                  "hook": captions.hook, "alt text": captions.alt_text},
        channel_texts=channel_texts or [f"[base]\n{captions.base_caption}"],
        guard_notes=guard_notes,
        language=settings.captions.language,
    )
    router = get_router(sctx)
    draft, meta = await router.structured(
        "review",
        LLMRequest(system=prompts.REVIEW_SYSTEM, prompt=prompt, json_mode=True,
                   temperature=settings.ai.decision_temperature, max_tokens=900),
        ReviewDraft,
        ctx=sctx.call_context(subject_id),
        decision_type="review",
        subject_id=subject_id,
    )
    issues = [f"Critical: {issue}" for issue in draft.critical_issues] + list(draft.issues)
    issues.extend(f"Guard: {issue.message}" for issue in guard.issues if issue.severity == "block")
    critical = bool(draft.critical_issues) or guard.hold

    image = _first_image(list(media_paths or []))
    if image:
        vision_issues, vision_critical = await _vision_check(
            sctx, image=image, products=products, headline=captions.headline,
            content_type=str(post.get("content_type") or ""), subject_id=subject_id,
        )
        issues.extend(vision_issues)
        critical = critical or vision_critical

    approve = draft.score >= settings.guards.hold_low_review_score and not critical
    suggestions = list(draft.suggestions[:5])
    if draft.summary and not approve:
        suggestions.insert(0, draft.summary)
    return ReviewResult(
        score=draft.score, approve=approve, issues=list(dict.fromkeys(issues)), suggestions=suggestions,
        model=meta.model,
    )
