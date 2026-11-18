"""Change a refused post's text so the network accepts it.

Two repairs, both for one channel version (the others are not touched):

* `shorten_caption` — no model involved: the wording before the link is cut at
  a sentence or word end; the link, the signature and (when there is room) the
  hashtags stay as they were.
* `rewrite_refused` — the model rewrites the caption and title from the
  diagnosis ("what to change"). Its answer is only used when it fits the
  network's limits, still carries every link, differs from what was refused
  and passes the same wording checks as a new caption.

Media is never changed here.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, field_validator

from ..context import StoreContext
from ..models import ProviderProfile
from . import prompts
from .base import LLMRequest
from .guard import claim_issues
from .llm import get_router
from .text import URL_RE, find_terms, trim_to_limit, truncate_words

_PARAGRAPHS = re.compile(r"\n\s*\n")
_MIN_BODY = 40      # the same floor the caption writer keeps for the wording (captions.assemble)


def _hashtags_only(paragraph: str) -> bool:
    words = paragraph.split()
    return bool(words) and all(word.startswith("#") for word in words)


def shorter_goal(caption: str, limit: int, measure: Callable[[str], int], step_percent: int) -> int:
    """The length to shorten to. Over the limit by our own count: the limit. Already inside it, yet refused as
    too long: the network counts differently from us, so go `step_percent` under what was sent."""
    current = measure((caption or "").strip())
    if current > limit:
        return limit
    return max(1, current * (100 - step_percent) // 100)


def shorten_caption(caption: str, goal: int, measure: Callable[[str], int]) -> str:
    """`caption` cut to `goal`, keeping everything from the link onwards when the wording before it can give way."""
    value = (caption or "").strip()
    if measure(value) <= goal:
        return value
    parts = [part.strip() for part in _PARAGRAPHS.split(value) if part.strip()]
    first_kept = next((i for i, part in enumerate(parts) if URL_RE.search(part) or _hashtags_only(part)), len(parts))
    body, kept = "\n\n".join(parts[:first_kept]), parts[first_kept:]

    def _tail() -> str:
        return "".join(f"\n\n{part}" for part in kept)

    if body:
        need = min(measure(body), max(_MIN_BODY, goal // 3))
        while goal - measure(_tail()) < need:
            hashtag_blocks = [i for i, part in enumerate(kept) if _hashtags_only(part)]
            if not hashtag_blocks:
                break
            kept.pop(hashtag_blocks[-1])        # hashtags give way before the wording does
        room = goal - measure(_tail())
        trimmed = trim_to_limit(body, room, measure) if room > 0 else ""
        if trimmed:
            result = (trimmed + _tail()).strip()
            if measure(result) <= goal:
                return result
    return trim_to_limit(value, goal, measure)


class RepairDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    caption: str
    title: str = ""

    @field_validator("caption", "title", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return str(value or "").strip()


def repair_problems(
    draft: RepairDraft, *, caption: str, title: str, channel_id: str, limit: int, profile: ProviderProfile,
    measure: Callable[[str], int], banned_terms: list[str], health: bool,
) -> list[str]:
    """Why a rewritten version must not go out ([] = it may)."""
    problems: list[str] = []
    if not draft.caption:
        return ["the caption is empty"]
    if draft.caption == (caption or "").strip() and (draft.title or title) == title:
        problems.append("nothing was changed")
    length = measure(draft.caption)
    if length > limit:
        problems.append(f"the caption is {length} characters; the limit is {limit}")
    if profile.title_max > 0 and len(draft.title) > profile.title_max:
        problems.append(f"the title is {len(draft.title)} characters; the limit is {profile.title_max}")
    for url in dict.fromkeys(URL_RE.findall(caption or "")):
        if url not in draft.caption:
            problems.append(f"the link {url} is missing or was changed")
    wording = URL_RE.sub(" ", "\n".join(part for part in (draft.caption, draft.title) if part))
    problems.extend(issue.message for issue in claim_issues(wording, channel_id, "the caption", health=health)
                    if issue.severity == "block")
    problems.extend(f"banned term '{term}'" for term in find_terms(wording, banned_terms))
    return problems


async def rewrite_refused(
    sctx: StoreContext, *, target: dict, limit: int, profile: ProviderProfile, measure: Callable[[str], int],
    error: str, instruction: str,
) -> RepairDraft:
    """The model's corrected caption and title for one refused channel version.

    Raises ValidationError when the model produced nothing that may go out."""
    caption, title = str(target.get("caption") or ""), str(target.get("title") or "")
    settings = sctx.settings

    def _checked(draft: RepairDraft) -> RepairDraft:
        if profile.title_max <= 0:
            draft.title = title                       # this network has no title to change
        elif not draft.title:
            draft.title = truncate_words(title, profile.title_max)
        problems = repair_problems(
            draft, caption=caption, title=title, channel_id=str(target.get("channel_id") or ""), limit=limit,
            profile=profile, measure=measure, banned_terms=settings.captions.banned_terms,
            health=settings.guards.health_claim_checks,
        )
        if problems:
            raise ValueError("; ".join(problems[:6]))
        return draft

    draft, _ = await get_router(sctx).structured(
        "repair.refused",
        LLMRequest(
            system=prompts.post_repair_system(),
            prompt=prompts.post_repair_prompt(
                network=profile.label if profile.key != "generic" else str(target.get("provider") or ""),
                error=error, instruction=instruction, caption=caption, title=title,
                length_now=measure(caption.strip()), limit=limit, title_max=profile.title_max,
                style=profile.caption_style,
            ),
            json_mode=True, temperature=settings.ai.decision_temperature, max_tokens=1500,
        ),
        RepairDraft,
        ctx=sctx.call_context(),
        decision_type="post_repair",
        subject_id=str(target.get("id") or "")[:80],
        postprocess=_checked,
    )
    return _checked(draft)
