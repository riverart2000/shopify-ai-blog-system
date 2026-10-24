"""Typed, validated per-store settings for the Social Engine.

Settings are stored as one JSON document per store (`store_settings` key
`social_engine.settings`). Every field has a default, so adding a feature is
just adding a field here; old stored documents keep working.
"""
from __future__ import annotations

import os
import re
import secrets
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

import db

SETTINGS_KEY = "social_engine.settings"

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class BrandColors(_Model):
    primary: str = "#285746"
    text: str = "#15251c"
    background: str = "#f7f4ec"
    cta: str = "#b89245"
    accent: str = "#a8b4a6"
    secondary: str = "#1f4638"

    @field_validator("*")
    @classmethod
    def _hex(cls, value: str) -> str:
        value = str(value).strip()
        if not _HEX.match(value):
            raise ValueError(f"Colour must be #RRGGBB, got {value!r}")
        return value.lower()


class BrandKit(_Model):
    business_name: str = ""
    description: str = ""
    what_you_sell: str = ""
    website: str = ""
    slogan: str = ""
    cta_text: str = "Shop Now"
    colors: BrandColors = Field(default_factory=BrandColors)
    heading_font: str = "InstrumentSerif-Regular.ttf"
    body_font: str = "InstrumentSans-Variable.ttf"
    use_store_logo: bool = True
    logo_position: Literal["top_left", "top_right", "bottom_left", "bottom_right", "none"] = "top_left"
    logo_opacity: float = Field(1.0, ge=0.1, le=1.0)
    currency_symbol: str = "£"
    include_taxes_pct: float = Field(0.0, ge=0, le=100)
    show_price: bool = True
    music_path: str = ""


class HashtagCloud(_Model):
    name: str
    tags: list[str] = Field(default_factory=list)
    collection_ids: list[str] = Field(default_factory=list)
    is_default: bool = False


class CaptionSettings(_Model):
    source: Literal["ai", "template"] = "ai"
    language: str = "en-GB"
    tone: Literal["friendly", "professional", "persuasive", "casual", "funny", "luxury", "expert"] = "expert"
    include_price: bool = True
    hashtags_count: int = Field(5, ge=0, le=10)
    use_product_tags: bool = True
    emoji_level: Literal["off", "light", "heavy"] = "light"
    include_cta: bool = True
    link_policy: Literal["all", "all_except_instagram", "none", "custom"] = "all"
    custom_url: str = ""
    link_in_first_comment: bool = True
    custom_instructions: str = Field("", max_length=1000)
    product_category_hint: str = ""
    banned_terms: list[str] = Field(default_factory=list)
    hashtag_clouds: list[HashtagCloud] = Field(default_factory=list)
    templates: dict[str, str] = Field(default_factory=dict)


class ScheduleSettings(_Model):
    timezone: str = "Europe/London"
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    window_start: str = "08:00"
    window_end: str = "21:00"
    randomize_times: bool = True
    use_best_times: bool = True
    min_gap_minutes: int = Field(120, ge=0, le=1440)
    plan_days_ahead: int = Field(1, ge=0, le=14)
    # Outfy-style daily volume: every channel posts at most this many times a
    # day, and auto-pilot keeps every one of those slots filled.
    posts_per_channel_per_day: int = Field(2, ge=1, le=24)
    always_fill_daily_slots: bool = True
    daily_caps: dict[str, int] = Field(default_factory=lambda: {
        "instagram": 24, "instagram-standalone": 24, "x": 3, "facebook": 96, "pinterest": 24,
        "tiktok": 10, "youtube": 100, "threads": 100, "linkedin": 5, "linkedin-page": 5,
        "bluesky": 20, "gmb": 2,
    })

    @field_validator("window_start", "window_end")
    @classmethod
    def _time(cls, value: str) -> str:
        if not _HHMM.match(value):
            raise ValueError("Times must be HH:MM (24h)")
        return value

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown time zone {value!r} (use e.g. Europe/London)") from exc
        return value

    @model_validator(mode="after")
    def _window(self) -> "ScheduleSettings":
        if self.window_start == self.window_end:
            raise ValueError("The posting window must have a different start and end time")
        return self

    @field_validator("days")
    @classmethod
    def _days(cls, value: list[int]) -> list[int]:
        cleaned = sorted({int(day) for day in value if 0 <= int(day) <= 6})
        if not cleaned:
            raise ValueError("Select at least one posting day")
        return cleaned


class PostizSettings(_Model):
    base_url: str = "https://api.postiz.com/public/v1"
    api_key_env: str = "POSTIZ_API_KEY"
    create_post_hourly_budget: int = Field(25, ge=1, le=100)
    short_links: bool = False
    tag_posts: bool = True
    upload_adjust_video: bool = True
    customer_id: str = ""
    ai_video_monthly_budget: int = Field(8, ge=0, le=100)
    # Postiz Team plan: 100 AI images + 10 AI videos per month. Leave headroom for manual use in Postiz.
    ai_image_monthly_budget: int = Field(80, ge=0, le=500)
    mcp_url: str = "https://mcp.postiz.com/mcp"
    video_voice_id: str = ""                    # ElevenLabs voice for image-text-slides (empty = first offered)
    ai_backgrounds_for_promotions: bool = True  # Postiz AI scene behind the real product on promo posts
    trending_tiktok_music: bool = True          # pick music via Postiz's TikTok musicSearch tool for videos
    webhook_token: str = ""

    def api_key(self) -> str:
        return os.environ.get(self.api_key_env or "POSTIZ_API_KEY", "").strip()


class AISettings(_Model):
    base_url: str = "http://127.0.0.1:8000/v1"
    model: str = "gemma-4-26b-a4b-it-8bit"
    api_key_env: str = "OMLX_API_KEY"
    timeout_seconds: int = Field(240, ge=10, le=1800)
    temperature: float = Field(0.7, ge=0, le=2)
    decision_temperature: float = Field(0.2, ge=0, le=2)
    max_tokens: int = Field(2048, ge=128, le=16384)
    fallback_model_id: str = ""
    ai_decisions: bool = True
    ai_review: bool = True
    vision_model: str = "mlx-community--Qwen3-VL-8B-Instruct-8bit"

    def api_key(self) -> str:
        return os.environ.get(self.api_key_env or "", "").strip()


class GuardSettings(_Model):
    hold_health_claims: bool = True
    hold_low_review_score: int = Field(6, ge=0, le=10)
    max_caption_retries: int = Field(1, ge=0, le=3)


class UtmSettings(_Model):
    enabled: bool = True
    medium: str = "social"
    source_overrides: dict[str, str] = Field(default_factory=dict)


class ContentSettings(_Model):
    multi_image: bool = True
    rotate_images: bool = True
    allow_reposting: bool = True
    repost_after_days: int = Field(14, ge=1, le=365)
    excluded_product_ids: list[str] = Field(default_factory=list)
    excluded_tags: list[str] = Field(default_factory=lambda: ["no-social"])


class EngineSettings(_Model):
    enabled: bool = False
    publisher: Literal["postiz", "dry_run"] = "postiz"
    default_approval_mode: Literal["manual", "ai_review", "auto"] = "manual"
    brand: BrandKit = Field(default_factory=BrandKit)
    captions: CaptionSettings = Field(default_factory=CaptionSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    content: ContentSettings = Field(default_factory=ContentSettings)
    postiz: PostizSettings = Field(default_factory=PostizSettings)
    ai: AISettings = Field(default_factory=AISettings)
    guards: GuardSettings = Field(default_factory=GuardSettings)
    utm: UtmSettings = Field(default_factory=UtmSettings)


def _deep_merge(base: dict, patch: dict) -> dict:
    merged = dict(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict) and key not in {"daily_caps", "templates", "source_overrides"}:
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


async def _store_defaults(store_id: str) -> dict[str, Any]:
    store = await db.get_store(store_id) or {}
    domain = str(store.get("custom_domain") or store.get("myshopify_domain") or "").strip()
    return {
        "brand": {
            "business_name": str(store.get("name") or ""),
            "website": f"https://{domain}" if domain and not domain.startswith("http") else domain,
        }
    }


async def load_settings(store_id: str) -> EngineSettings:
    import json

    raw = await db.get_store_setting(store_id, SETTINGS_KEY, "")
    stored: dict = {}
    if raw:
        try:
            stored = json.loads(raw)
        except ValueError:
            stored = {}
    merged = _deep_merge(await _store_defaults(store_id), stored if isinstance(stored, dict) else {})
    try:
        return EngineSettings.model_validate(merged)
    except Exception:
        # A corrupt section must not take the engine down: fall back section by section.
        settings = EngineSettings.model_validate(await _store_defaults(store_id))
        for name in EngineSettings.model_fields:
            if name in merged:
                try:
                    setattr(settings, name, merged[name])
                except Exception:
                    continue
        return settings


async def save_settings(store_id: str, patch: dict) -> EngineSettings:
    """Deep-merge `patch` into the stored settings, validate, then persist."""
    import json

    current = (await load_settings(store_id)).model_dump()
    merged = _deep_merge(current, patch or {})
    settings = EngineSettings.model_validate(merged)
    await db.set_store_settings(store_id, {SETTINGS_KEY: json.dumps(settings.model_dump())})
    return settings


async def ensure_webhook_token(store_id: str) -> str:
    settings = await load_settings(store_id)
    if settings.postiz.webhook_token:
        return settings.postiz.webhook_token
    token = secrets.token_urlsafe(32)
    await save_settings(store_id, {"postiz": {"webhook_token": token}})
    return token


def public_settings(settings: EngineSettings) -> dict:
    """Settings safe to send to the browser (no secrets)."""
    data = settings.model_dump()
    data["postiz"]["webhook_token_set"] = bool(data["postiz"].pop("webhook_token", ""))
    data["postiz"]["api_key_configured"] = bool(settings.postiz.api_key())
    data["ai"]["api_key_configured"] = bool(settings.ai.api_key())
    return data
