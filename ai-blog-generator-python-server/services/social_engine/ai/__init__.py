"""Social Engine AI layer: Gemma-first LLM router, captions, guards, review and
auto-pilot decisions.

    llm.py        providers (oMLX/Gemma, Models-page fallback), router, doctor check
    prompts.py    system prompts, compliance rules, prompt builders
    captions.py   AI + template captions with deterministic per-network rules
    guard.py      health-claim / length / link / hashtag checks (no model)
    review.py     AI post review (+ optional vision check)
    decisions.py  content plans, promo texts, brand content, performance insights
    text.py       JSON extraction, emoji/hashtag/length helpers
"""
from .base import (
    CaptionChannel, CaptionRequest, CaptionResult, ChannelCaption, GuardIssue, GuardResult, LLMProvider,
    LLMRequest, LLMResponse, ReviewResult,
)
from .captions import effective_caption_settings, generate_captions, template_captions
from .decisions import (
    BrandContent, ContentPlan, Insights, PlannedSlot, RejectedChoice, choose_content_plan, generate_brand_content,
    generate_promo_texts, performance_insights, pre_rank_candidates,
)
from .guard import check_captions
from .llm import (
    LLMRouter, OpenAICompatibleProvider, RegistryModelProvider, StructuredMeta, check_llm, get_router,
)
from .review import review_post

__all__ = [
    "BrandContent", "CaptionChannel", "CaptionRequest", "CaptionResult", "ChannelCaption", "ContentPlan",
    "GuardIssue", "GuardResult", "Insights", "LLMProvider", "LLMRequest", "LLMResponse", "LLMRouter",
    "OpenAICompatibleProvider", "PlannedSlot", "RegistryModelProvider", "RejectedChoice", "ReviewResult",
    "StructuredMeta", "check_captions", "check_llm", "choose_content_plan", "effective_caption_settings",
    "generate_brand_content", "generate_captions", "generate_promo_texts", "get_router", "performance_insights",
    "pre_rank_candidates", "review_post", "template_captions",
]
