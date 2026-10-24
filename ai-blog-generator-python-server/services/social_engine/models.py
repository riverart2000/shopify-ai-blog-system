"""Shared vocabulary: content types, media formats and per-network profiles.

`PROVIDER_PROFILES` is the single source of truth for what each social network
accepts. Captions, creatives, scheduling and publishing all read from it, so
supporting a new network starts with adding one profile here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ContentType(StrEnum):
    SINGLE_PRODUCT = "single_product"
    NEW_ARRIVAL = "new_arrival"
    SALE_PROMO = "sale_promo"
    BACK_IN_STOCK = "back_in_stock"
    PRODUCT_OF_THE_DAY = "product_of_the_day"
    COLLAGE = "collage"
    COLLECTION_ROUNDUP = "collection_roundup"
    COUNTDOWN = "countdown"
    CAROUSEL = "carousel"
    HOLIDAY = "holiday"
    QUOTE = "quote"
    PRODUCT_VIDEO = "product_video"
    AI_IMAGE_AD = "ai_image_ad"
    AI_VIDEO = "ai_video"
    BLOG_ARTICLE = "blog_article"


CONTENT_TYPE_LABELS: dict[str, str] = {
    ContentType.SINGLE_PRODUCT: "Product post",
    ContentType.NEW_ARRIVAL: "New arrival",
    ContentType.SALE_PROMO: "Sale / promotion",
    ContentType.BACK_IN_STOCK: "Back in stock",
    ContentType.PRODUCT_OF_THE_DAY: "Product of the day",
    ContentType.COLLAGE: "Collage",
    ContentType.COLLECTION_ROUNDUP: "Collection round-up",
    ContentType.COUNTDOWN: "Countdown",
    ContentType.CAROUSEL: "Carousel",
    ContentType.HOLIDAY: "Holiday / occasion",
    ContentType.QUOTE: "Quote",
    ContentType.PRODUCT_VIDEO: "Product video (reel)",
    ContentType.AI_IMAGE_AD: "AI image ad",
    ContentType.AI_VIDEO: "AI video (Postiz credits)",
    ContentType.BLOG_ARTICLE: "Blog article share",
}

VIDEO_CONTENT_TYPES = frozenset({ContentType.PRODUCT_VIDEO, ContentType.AI_VIDEO})
MULTI_PRODUCT_CONTENT_TYPES = frozenset({
    ContentType.COLLAGE, ContentType.COLLECTION_ROUNDUP, ContentType.CAROUSEL,
})


class MediaFormat(StrEnum):
    SQUARE = "square"
    PORTRAIT = "portrait"
    STORY = "story"
    PIN = "pin"
    LANDSCAPE = "landscape"


FORMAT_SIZES: dict[str, tuple[int, int]] = {
    MediaFormat.SQUARE: (1080, 1080),
    MediaFormat.PORTRAIT: (1080, 1350),
    MediaFormat.STORY: (1080, 1920),
    MediaFormat.PIN: (1000, 1500),
    MediaFormat.LANDSCAPE: (1200, 675),
}


class PostStatus(StrEnum):
    PLANNED = "planned"
    GENERATING = "generating"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    PUBLISHING = "publishing"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    PARTIALLY_PUBLISHED = "partially_published"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


EDITABLE_POST_STATUSES = frozenset({
    PostStatus.PLANNED, PostStatus.PENDING_APPROVAL, PostStatus.APPROVED, PostStatus.FAILED,
})


class TargetStatus(StrEnum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    UNCERTAIN = "uncertain"
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalMode(StrEnum):
    MANUAL = "manual"          # a person approves every post
    AI_REVIEW = "ai_review"    # Gemma reviews; flagged posts wait for a person
    AUTO = "auto"              # approved on generation (still blocked by content guards)


class PublishMode(StrEnum):
    SCHEDULE = "schedule"      # schedule in Postiz at the planned time
    NOW = "now"                # publish immediately
    DRAFT = "draft"            # create as a Postiz draft for manual finishing


@dataclass(frozen=True)
class ProviderProfile:
    key: str
    label: str
    max_length: int
    media: str = "optional"                 # required | optional | none
    supports_video: bool = True
    video_only: bool = False
    image_formats: tuple[str, ...] = (MediaFormat.SQUARE,)
    video_format: str = MediaFormat.STORY
    max_images: int = 4
    link_policy: str = "inline"             # inline | bio | settings_link | first_comment | none
    hashtags_max: int = 5
    hashtags_recommended: int = 3
    supports_comments: bool = True          # extra parts become comments/thread replies
    comments_text_only: bool = False
    title_max: int = 0                      # >0 when the network needs a title setting
    has_post_analytics: bool = False
    default_times: tuple[str, ...] = ("09:00", "18:00")
    caption_style: str = ""                 # short guidance passed to the caption writer
    extra: dict = field(default_factory=dict)


_PROFILES: tuple[ProviderProfile, ...] = (
    ProviderProfile(
        "instagram", "Instagram", 2200, media="required",
        image_formats=(MediaFormat.PORTRAIT, MediaFormat.SQUARE), max_images=10,
        link_policy="bio", hashtags_max=30, hashtags_recommended=8, comments_text_only=True,
        has_post_analytics=True, default_times=("11:00", "19:00"),
        caption_style="Hook in the first line, short paragraphs, emojis welcome, 'link in bio' CTA.",
    ),
    ProviderProfile(
        "instagram-standalone", "Instagram", 2200, media="required",
        image_formats=(MediaFormat.PORTRAIT, MediaFormat.SQUARE), max_images=10,
        link_policy="bio", hashtags_max=30, hashtags_recommended=8, comments_text_only=True,
        has_post_analytics=True, default_times=("11:00", "19:00"),
        caption_style="Hook in the first line, short paragraphs, emojis welcome, 'link in bio' CTA.",
    ),
    ProviderProfile(
        "facebook", "Facebook", 63206, image_formats=(MediaFormat.SQUARE, MediaFormat.PORTRAIT),
        max_images=10, hashtags_max=3, hashtags_recommended=2, has_post_analytics=True,
        default_times=("09:00", "13:00"),
        caption_style="Conversational, 1-3 short paragraphs, include the link.",
    ),
    ProviderProfile(
        "x", "X", 280, image_formats=(MediaFormat.LANDSCAPE, MediaFormat.SQUARE), max_images=4,
        hashtags_max=2, hashtags_recommended=1, has_post_analytics=True,
        default_times=("08:00", "12:00", "17:00"),
        caption_style="One punchy sentence plus link; at most two hashtags; links count as 23 characters.",
    ),
    ProviderProfile(
        "linkedin", "LinkedIn", 3000, image_formats=(MediaFormat.SQUARE, MediaFormat.LANDSCAPE),
        max_images=9, hashtags_max=5, hashtags_recommended=3, comments_text_only=True,
        default_times=("08:00", "12:00"),
        caption_style="Professional, insight-led, evidence-based; no hype; link at the end.",
    ),
    ProviderProfile(
        "linkedin-page", "LinkedIn Page", 3000, image_formats=(MediaFormat.SQUARE, MediaFormat.LANDSCAPE),
        max_images=9, hashtags_max=5, hashtags_recommended=3, comments_text_only=True,
        has_post_analytics=True, default_times=("08:00", "12:00"),
        caption_style="Professional, insight-led, evidence-based; no hype; link at the end.",
    ),
    ProviderProfile(
        "threads", "Threads", 500, image_formats=(MediaFormat.PORTRAIT, MediaFormat.SQUARE),
        max_images=10, hashtags_max=1, hashtags_recommended=1, has_post_analytics=True,
        default_times=("12:00", "20:00"),
        caption_style="Casual and conversational, a question to spark replies, one topic tag at most.",
    ),
    ProviderProfile(
        "bluesky", "Bluesky", 300, image_formats=(MediaFormat.LANDSCAPE, MediaFormat.SQUARE),
        max_images=4, hashtags_max=2, hashtags_recommended=1, default_times=("09:00", "18:00"),
        caption_style="Brief and friendly, plain language, link included.",
    ),
    ProviderProfile(
        "mastodon", "Mastodon", 500, image_formats=(MediaFormat.LANDSCAPE, MediaFormat.SQUARE),
        max_images=4, hashtags_max=3, hashtags_recommended=2, default_times=("10:00", "18:00"),
        caption_style="Informative, community-minded, CamelCase hashtags.",
    ),
    ProviderProfile(
        "pinterest", "Pinterest", 500, media="required", image_formats=(MediaFormat.PIN,),
        video_format=MediaFormat.STORY, max_images=5, link_policy="settings_link",
        hashtags_max=5, hashtags_recommended=3, supports_comments=False, title_max=100,
        has_post_analytics=True, default_times=("20:00", "21:30"),
        caption_style="Keyword-rich, descriptive, benefit-led pin description; no 'link in bio'.",
    ),
    ProviderProfile(
        "tiktok", "TikTok", 2200, media="required", video_only=False,
        image_formats=(MediaFormat.STORY,), video_format=MediaFormat.STORY, max_images=35,
        link_policy="bio", hashtags_max=5, hashtags_recommended=4, supports_comments=False,
        title_max=90, has_post_analytics=True, default_times=("12:00", "19:00", "21:00"),
        caption_style="Short, playful hook, trend-aware, 3-5 hashtags, 'link in bio'.",
    ),
    ProviderProfile(
        "tiktok-business", "TikTok Business", 2200, media="required", video_only=False,
        image_formats=(MediaFormat.STORY,), video_format=MediaFormat.STORY, max_images=35,
        link_policy="bio", hashtags_max=5, hashtags_recommended=4, supports_comments=False,
        title_max=90, has_post_analytics=True, default_times=("12:00", "19:00", "21:00"),
        caption_style="Short, playful hook, trend-aware, 3-5 hashtags, 'link in bio'.",
    ),
    ProviderProfile(
        "youtube", "YouTube Shorts", 5000, media="required", video_only=True,
        image_formats=(), video_format=MediaFormat.STORY, max_images=0, hashtags_max=5,
        hashtags_recommended=3, supports_comments=False, title_max=100, has_post_analytics=True,
        default_times=("15:00", "19:00"),
        caption_style="Description with keywords, link to the product, #Shorts tag.",
    ),
    ProviderProfile(
        "gmb", "Google Business", 1500, supports_video=False,
        image_formats=(MediaFormat.LANDSCAPE, MediaFormat.SQUARE),
        max_images=1, hashtags_max=0, hashtags_recommended=0, supports_comments=False,
        has_post_analytics=True, default_times=("09:00",),
        caption_style="Local-business update, plain language, no hashtags, clear call to action.",
    ),
    ProviderProfile(
        "telegram", "Telegram", 4096, image_formats=(MediaFormat.SQUARE,), max_images=10,
        hashtags_max=3, hashtags_recommended=2, default_times=("10:00", "18:00"),
        caption_style="Newsletter-style update with the link.",
    ),
    ProviderProfile(
        "reddit", "Reddit", 40000, image_formats=(MediaFormat.SQUARE,), max_images=1,
        hashtags_max=0, hashtags_recommended=0, title_max=300, default_times=("14:00",),
        caption_style="Genuinely helpful, non-promotional, no hashtags.",
    ),
)

PROVIDER_PROFILES: dict[str, ProviderProfile] = {profile.key: profile for profile in _PROFILES}

GENERIC_PROFILE = ProviderProfile("generic", "Other", 500, hashtags_max=3, hashtags_recommended=2)


def profile_for(provider: str) -> ProviderProfile:
    return PROVIDER_PROFILES.get((provider or "").strip().lower(), GENERIC_PROFILE)


def formats_for_provider(provider: str, *, video: bool) -> list[str]:
    profile = profile_for(provider)
    if video:
        return [profile.video_format] if profile.supports_video else []
    return list(profile.image_formats)
