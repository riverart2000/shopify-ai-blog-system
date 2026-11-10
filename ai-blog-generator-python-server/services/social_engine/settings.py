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
from services.local_model import LocalModel, LocalModelError, local_model

SETTINGS_KEY = "social_engine.settings"


def _shared() -> LocalModel | None:
    """The shared local model, or None when its file is missing or broken (logged by local_model)."""
    try:
        return local_model()
    except LocalModelError:
        return None


def _shared_value(name: str) -> str:
    shared = _shared()
    return str(getattr(shared, name)) if shared else ""

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
    # Weight of the heading when its font is a variable one (e.g. "Bold" for InstrumentSans-Variable.ttf).
    heading_weight: str = Field("", max_length=20)
    use_store_logo: bool = True
    logo_position: Literal["top_left", "top_right", "bottom_left", "bottom_right", "none"] = "top_left"
    logo_opacity: float = Field(1.0, ge=0.1, le=1.0)
    currency_symbol: str = "£"
    include_taxes_pct: float = Field(0.0, ge=0, le=100)
    show_price: bool = True
    music_path: str = ""
    # What decorates text-only cards: botanical sprigs and a leaf pattern, a plain dot grid, or nothing.
    decoration: Literal["botanical", "dots", "none"] = "botanical"


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
    # Appended to every caption (after the link, before hashtags): a standing offer
    # such as "New here? 10% off your first order with code WELCOME10". Postiz's
    # own signatures only apply in its composer, not to API posts, so the engine
    # adds it. Empty providers = every channel; otherwise e.g. ["instagram", "facebook"].
    signature: str = Field("", max_length=200)
    signature_providers: list[str] = Field(default_factory=list)
    # Ends every caption, on every network that takes hashtags, so posts made by Social Autopilot can be told
    # apart from everything else on the feed. Kept exactly as typed (a marker must never change), always the
    # last hashtag, and it stays when the hashtags have to be cut for length.
    closing_hashtag: str = Field("", max_length=60)
    product_category_hint: str = ""
    banned_terms: list[str] = Field(default_factory=list)
    hashtag_clouds: list[HashtagCloud] = Field(default_factory=list)
    templates: dict[str, str] = Field(default_factory=dict)


    @field_validator("closing_hashtag", mode="before")
    @classmethod
    def _closing_hashtag(cls, value: Any) -> str:
        text = str(value or "").strip().lstrip("#＃")
        if not text:
            return ""
        if not re.fullmatch(r"\w+", text) or not any(char.isalpha() for char in text):
            raise ValueError("The closing hashtag can only have letters, numbers and underscores (e.g. #bioluxelab_uk).")
        return f"#{text}"

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


class PostizServer(_Model):
    """A second Postiz used beside the main one, for channels the main one doesn't have (for example a Postiz
    hosted on this Mac). Nothing about it is built in: the address and the name of the .env variable that holds
    its API key are saved here."""
    # Prefix of this server's channel and post ids ("local:cm..."). Don't change it once posts exist.
    name: str = Field(min_length=2, max_length=24, pattern=r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
    label: str = Field("", max_length=60)
    enabled: bool = True
    base_url: str = Field(min_length=8, max_length=300, pattern=r"^https?://\S+$")  # its public API address
    api_key_env: str = Field(min_length=3, max_length=80, pattern=r"^[A-Z][A-Z0-9_]*$")
    create_post_hourly_budget: int = Field(25, ge=1, le=100)

    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "").strip()


class PostizSettings(_Model):
    base_url: str = "https://api.postiz.com/public/v1"
    api_key_env: str = "POSTIZ_API_KEY"
    create_post_hourly_budget: int = Field(25, ge=1, le=100)
    short_links: bool = False
    tag_posts: bool = True
    upload_adjust_video: bool = True
    customer_id: str = ""
    # Postiz Team plan: 100 AI images + 10 AI videos per month. Postiz's own RSS
    # auto-posting ("generate picture") draws on the same image allowance, so the
    # engine's share is set per store (see docs/social-engine/postiz-maximise.md).
    ai_video_monthly_budget: int = Field(10, ge=0, le=100)
    ai_image_monthly_budget: int = Field(100, ge=0, le=500)  # the Team plan's 100 AI images a month
    mcp_url: str = "https://mcp.postiz.com/mcp"
    video_voice_id: str = ""                    # ElevenLabs voice for image-text-slides (empty = first offered)
    # Models-page image model (e.g. Grok) used when Postiz image credits are
    # used up or Postiz fails, so running out of credits never stops a post.
    image_fallback_model_id: str = ""
    ai_backgrounds_for_promotions: bool = True  # Postiz AI scene behind the real product on promo posts
    trending_tiktok_music: bool = True          # pick music via Postiz's TikTok musicSearch tool for videos
    webhook_token: str = ""
    # Extra Postiz servers. Pictures and videos still go through the main one (see publishers/combined.py).
    servers: list[PostizServer] = Field(default_factory=list, max_length=5)

    @field_validator("servers")
    @classmethod
    def _server_names(cls, value: list[PostizServer]) -> list[PostizServer]:
        names = [server.name for server in value]
        if len(set(names)) != len(names):
            raise ValueError("Each extra Postiz server needs its own name")
        return value

    def api_key(self) -> str:
        return os.environ.get(self.api_key_env or "POSTIZ_API_KEY", "").strip()


class AISettings(_Model):
    # The server, text model and vision model come from the shared file ~/.config/local-ai/model.json (also read by
    # OpenClaw and the Task Agent), whatever is saved here: two different models can't fit in memory together. If the
    # file is missing or broken they're empty: AI calls go to the fallback model and the AI check says why.
    base_url: str = Field(default_factory=lambda: _shared_value("server_url"))
    model: str = Field(default_factory=lambda: _shared_value("text_model"))
    api_key_env: str = "OMLX_API_KEY"
    timeout_seconds: int = Field(240, ge=10, le=1800)
    temperature: float = Field(0.7, ge=0, le=2)
    decision_temperature: float = Field(0.2, ge=0, le=2)
    max_tokens: int = Field(2048, ge=128, le=16384)
    fallback_model_id: str = ""
    # Who writes the posts (captions, brand content, promotion lines). "postiz": Postiz's own AI agent, the one
    # behind "Generate with AI" in its composer; the local model then only reviews, and writes when Postiz
    # doesn't answer. "local": the local model writes too. Reviews and planning are always the local model.
    writer: Literal["local", "postiz"] = "local"
    writer_timeout_seconds: int = Field(180, ge=20, le=900)
    ai_decisions: bool = True
    ai_review: bool = True
    # Force JSON with the server's grammar. Off: the prompt asks for JSON and replies are checked and repaired here,
    # which lets oMLX's speed-up drafter run (it can't with a grammar). Turn on only if replies keep failing to parse.
    strict_json: bool = False
    vision_model: str = Field(default_factory=lambda: _shared_value("vision_model"))
    # A Models-page model asked for a second opinion only when Gemma rejects a
    # post on its wording; if it finds nothing blocking, the post is approved.
    verify_model_id: str = ""

    @model_validator(mode="after")
    def _shared_model(self) -> "AISettings":
        shared = _shared()
        wanted = [("base_url", shared.server_url if shared else ""), ("model", shared.text_model if shared else "")]
        if self.vision_model and shared:  # empty = image review is off; otherwise it's the shared vision model
            wanted.append(("vision_model", shared.vision_model))
        for name, value in wanted:
            if getattr(self, name) != value:
                object.__setattr__(self, name, value)  # not setattr: validate_assignment would run this again
        return self

    def api_key(self) -> str:
        return os.environ.get(self.api_key_env or "", "").strip()


class GuardSettings(_Model):
    # Food-supplement claim checks (health and medicinal claims). Off for a brand that sells no health product,
    # where words like "cure" or "treat" are ordinary figures of speech.
    health_claim_checks: bool = True
    hold_health_claims: bool = True
    hold_low_review_score: int = Field(6, ge=0, le=10)
    max_caption_retries: int = Field(1, ge=0, le=3)
    # When the reviewer rejects a draft, the engine rewrites the captions from
    # its notes (and tries the next product photo when the image was the
    # problem) this many times before a person is asked.
    max_review_rewrites: int = Field(2, ge=0, le=4)


class UtmSettings(_Model):
    enabled: bool = True
    medium: str = "social"
    source_overrides: dict[str, str] = Field(default_factory=dict)


class CatalogSettings(_Model):
    """Where the things a brand posts about come from. "shopify": the store's products. "none": the brand has
    no product catalogue (a consultancy, a performer); it posts brand content only, and every job that needs
    Shopify is skipped for it."""
    source: Literal["shopify", "none"] = "shopify"


class BrandContentKind(_Model):
    """One kind of brand-content post (Campaigns → Brand content), written by the AI without a product."""
    label: str = Field(min_length=2, max_length=60)
    guidance: str = Field(min_length=10, max_length=1200)   # what to write, in the brand's own terms
    template_key: str = Field("", max_length=60)            # the image template; empty = the default card
    eyebrow: str = Field("", max_length=40)                 # small tag printed above the headline
    points: int = Field(0, ge=0, le=5)                      # on-image list items to ask for (0 = none)
    about_offering: bool = False                            # each post is about one of the brand's offerings


class Offering(_Model):
    """A service the brand sells, for posts that are not about a Shopify product."""
    name: str = Field(min_length=2, max_length=120)
    summary: str = Field(min_length=10, max_length=900)     # the only facts the writer may use about it
    url: str = Field("", max_length=300)                    # where its posts link; empty = the campaign's link


class VoiceSettings(_Model):
    """Who the AI writes as. Every field left empty keeps the built-in UK wellness and food-supplement voice."""
    sector: str = Field("", max_length=200)                 # e.g. "a UK AI and automation consultancy"
    compliance_rules: str = Field("", max_length=4000)      # rules that override everything else
    voice_rules: str = Field("", max_length=3000)
    image_check: str = Field("", max_length=200)            # what kind of brand the image reviewer checks for
    # The look of Postiz AI scenes behind this brand's cards (Campaigns → brand content → AI background).
    image_style: str = Field("", max_length=600)
    content_kinds: dict[str, BrandContentKind] = Field(default_factory=dict)
    offerings: list[Offering] = Field(default_factory=list, max_length=30)

    @field_validator("content_kinds")
    @classmethod
    def _kind_keys(cls, value: dict[str, BrandContentKind]) -> dict[str, BrandContentKind]:
        for key in value:
            if not re.fullmatch(r"[a-z][a-z0-9_]{1,39}", key):
                raise ValueError(f"Content kind key {key!r} must be lower-case letters, digits and underscores")
        return value


class ContentSettings(_Model):
    multi_image: bool = True
    rotate_images: bool = True
    allow_reposting: bool = True
    repost_after_days: int = Field(14, ge=1, le=365)
    excluded_product_ids: list[str] = Field(default_factory=list)
    excluded_tags: list[str] = Field(default_factory=lambda: ["no-social"])


# Awareness days: a 20%-off sale on these can read badly, so they're never automatic.
AWARENESS_THEMES = ("blue_monday", "mental_health_week", "world_mental_health_day", "self_care_day")


class AutoPromotionSettings(_Model):
    """Promotions → Automatic occasion promotions: a real Shopify code + a promotion per occasion."""
    enabled: bool = True
    percent: int = Field(20, ge=5, le=90)
    days_before: int = Field(1, ge=0, le=7)       # the code starts this many days before the occasion
    days_after: int = Field(1, ge=0, le=7)        # and stays valid this many days after it ends
    run_hour: int = Field(7, ge=0, le=23)         # store time; checked hourly from then on
    skip_themes: list[str] = Field(default_factory=list)  # wellness days get sales too (Joe, 4 Nov 2026)


class FounderVideoSettings(_Model):
    """Owner recordings → full video on YouTube → Postiz clipping → short clips everywhere.

    Postiz can only clip YouTube URLs, so a dropped file is first published to
    YouTube by the engine; a pasted YouTube URL skips straight to clipping."""
    enabled: bool = True
    drop_folder: str = "~/Bio Luxe Lab Videos"           # watched on the Mac that runs the engine
    full_video_providers: list[str] = Field(default_factory=lambda: ["youtube"])
    clip_providers: list[str] = Field(default_factory=lambda: [
        "tiktok-business", "instagram-standalone", "youtube", "facebook", "pinterest"])
    clips_per_video: int = Field(5, ge=1, le=20)
    fit: Literal["crop", "blur"] = "crop"
    approval_mode: Literal["manual", "ai_review", "auto"] = "manual"
    youtube_visibility: Literal["public", "unlisted"] = "public"
    clip_days_apart: int = Field(1, ge=0, le=7)           # clip N goes out N*days_apart days after the first
    max_source_minutes: int = Field(40, ge=1, le=120)     # refuse recordings that would burn the month's minutes


class ListingProfile(_Model):
    """One business profile every directory copies.

    Name, phone and address must be identical everywhere: local search engines
    cross-check listings, and a client who finds three spellings trusts none.
    """
    business_name: str = ""
    business_type: Literal["sole_trader", "limited_company", "partnership"] = "sole_trader"
    tagline: str = Field("", max_length=120)
    short_description: str = Field("", max_length=300)
    description: str = Field("", max_length=1200)
    long_description: str = Field("", max_length=4000)
    categories: list[str] = Field(default_factory=lambda: [
        "Health and wellness shop", "Wellness coaching", "Online retailer",
    ])
    founded_year: int | None = Field(None, ge=1900, le=2100)
    founder_name: str = ""
    founder_bio: str = Field("", max_length=1500)
    email: str = ""
    phone: str = ""
    website: str = ""
    address_line1: str = ""
    address_line2: str = ""
    city: str = "London"
    postcode: str = ""
    country: str = "United Kingdom"
    service_area: str = "London and online, UK-wide delivery"
    hours: str = "Online store open 24/7; coaching and workshops by appointment"
    logo_url: str = ""
    cover_image_url: str = ""
    social_links: dict[str, str] = Field(default_factory=dict)   # instagram, facebook, tiktok, youtube, linkedin, x, pinterest
    keywords: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    price_range: str = "££"
    payment_methods: list[str] = Field(default_factory=lambda: ["Card", "Apple Pay", "Google Pay", "PayPal", "Klarna"])


class ListingsSettings(_Model):
    """Directory and listing submissions driven through the owner's own Chrome."""
    enabled: bool = True
    profile: ListingProfile = Field(default_factory=ListingProfile)
    relay_dir: str = "~/openclaw"                 # OpenClaw checkout whose browser relay drives Chrome
    relay_profile: str = "chrome"
    relay_timeout_seconds: int = Field(90, ge=10, le=600)
    check_interval_days: int = Field(7, ge=1, le=90)
    presence_interval_days: int = Field(7, ge=1, le=90)
    # The worker: signs up, fills, submits and verifies on its own, one directory at a time.
    autopilot: bool = True                        # periodic: pick the next unworked directory every work_interval_hours
    work_interval_hours: int = Field(12, ge=1, le=168)
    account_email: str = ""                       # login email for new accounts (default: the profile email)
    max_steps_per_run: int = Field(45, ge=5, le=150)


class ShoppableSettings(_Model):
    """A Shopify page for the Instagram and TikTok bio link: the products most recently posted, newest first."""
    enabled: bool = False
    page_handle: str = Field("shop-our-feed", min_length=3, max_length=60, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    title: str = Field("Shop our feed", min_length=1, max_length=80)
    intro: str = Field("The products from our latest posts. Tap one to see it in the shop.", max_length=240)
    max_products: int = Field(24, ge=4, le=60)
    days: int = Field(45, ge=7, le=365)  # how far back "recently posted" goes


class EngineSettings(_Model):
    enabled: bool = False
    publisher: Literal["postiz", "dry_run"] = "postiz"
    default_approval_mode: Literal["manual", "ai_review", "auto"] = "manual"
    brand: BrandKit = Field(default_factory=BrandKit)
    captions: CaptionSettings = Field(default_factory=CaptionSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    content: ContentSettings = Field(default_factory=ContentSettings)
    catalog: CatalogSettings = Field(default_factory=CatalogSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    postiz: PostizSettings = Field(default_factory=PostizSettings)
    ai: AISettings = Field(default_factory=AISettings)
    guards: GuardSettings = Field(default_factory=GuardSettings)
    utm: UtmSettings = Field(default_factory=UtmSettings)
    auto_promotions: AutoPromotionSettings = Field(default_factory=AutoPromotionSettings)
    founder_videos: FounderVideoSettings = Field(default_factory=FounderVideoSettings)
    listings: ListingsSettings = Field(default_factory=ListingsSettings)
    shoppable: ShoppableSettings = Field(default_factory=ShoppableSettings)


def _deep_merge(base: dict, patch: dict) -> dict:
    merged = dict(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict) and key not in {"daily_caps", "templates", "source_overrides", "social_links", "content_kinds"}:
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
    for shown, server in zip(data["postiz"]["servers"], settings.postiz.servers):
        shown["api_key_configured"] = bool(server.api_key())
    data["ai"]["api_key_configured"] = bool(settings.ai.api_key())
    return data
