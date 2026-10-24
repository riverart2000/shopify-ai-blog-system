"""AI contracts: the LLM provider interface and the caption request/result.

Gemma (local, via oMLX's OpenAI-compatible API) is the primary model for
captions and all automation decisions. A fallback model from the existing
Models page can be configured per store; every attempt is logged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, Field

from ..http import CallContext


class LLMRequest(BaseModel):
    prompt: str
    system: str = ""
    json_mode: bool = True
    temperature: float | None = None
    max_tokens: int | None = None
    images: list[str] = Field(default_factory=list)   # local paths or data: URIs (vision models only)


class LLMResponse(BaseModel):
    text: str
    model: str
    provider: str
    duration_ms: int = 0
    usage: dict[str, Any] = Field(default_factory=dict)


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(self, request: LLMRequest, ctx: CallContext) -> LLMResponse: ...


# ---------------------------------------------------------------------------
# Captions
# ---------------------------------------------------------------------------

@dataclass
class CaptionChannel:
    channel_id: str
    provider: str
    max_length: int | None = None   # from Postiz integration settings when known
    rules: str = ""
    link_url: str = ""              # tracked (UTM) link for this channel, "" for none
    kind: str = "post"              # post | story | reel | short | pin


@dataclass
class CaptionRequest:
    store_id: str
    content_type: str
    products: list[dict]                       # social_products rows (decoded)
    channels: list[CaptionChannel]
    angle: str = ""                            # creative angle chosen by the planner
    brief: str = ""                            # free-text instructions for this post
    texts: dict[str, str] = field(default_factory=dict)   # promo_text, coupon_code, occasion, quote...
    caption_overrides: dict[str, Any] = field(default_factory=dict)  # campaign-level CaptionSettings fields
    correlation_id: str = ""


@dataclass
class ChannelCaption:
    channel_id: str
    provider: str
    caption: str
    first_comment: str = ""
    title: str = ""                 # YouTube / Pinterest / TikTok title
    hashtags: list[str] = field(default_factory=list)


@dataclass
class CaptionResult:
    base_caption: str
    hashtags: list[str]
    alt_text: str
    channel_captions: dict[str, ChannelCaption]   # keyed by channel_id
    headline: str = ""              # short on-image headline (<= 40 chars)
    subheadline: str = ""           # on-image supporting line (<= 70 chars)
    cta: str = ""                   # on-image call to action (<= 18 chars)
    hook: str = ""                  # first-second hook for reels
    model: str = ""
    used_fallback: bool = False
    source: str = "ai"              # ai | template
    warnings: list[str] = field(default_factory=list)


@dataclass
class GuardIssue:
    code: str
    message: str
    severity: str = "warning"       # info | warning | block
    channel_id: str = ""


@dataclass
class GuardResult:
    hold: bool                      # True → needs a person even in auto mode
    issues: list[GuardIssue] = field(default_factory=list)


@dataclass
class ReviewResult:
    score: int                      # 0-10
    approve: bool
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    model: str = ""
