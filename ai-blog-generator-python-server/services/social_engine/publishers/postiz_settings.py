"""Provider-specific `settings` objects for Postiz create-post requests.

Every post item sent to Postiz carries a `settings` object whose shape depends
on the network: `__type` plus the fields of `XSettings`, `InstagramSettings`,
`TikTokSettings`, `PinterestSettings`, ... in docs/social-engine/postiz/openapi.json.

`build_settings` turns our target settings (channel defaults merged with the
per-post overrides, using Postiz field names) into that object. It fills safe
defaults, truncates titles to each network's limit, drops keys the network
does not accept, and raises a precise error when something is missing:

* `ConfigurationError` → a person must fix a channel setting (a Pinterest
  board, a Discord channel, a Hashnode publication...).
* `ValidationError`    → this post's content cannot satisfy the network
  (no YouTube title, Instagram story with several images...).

Pass `settings["channel_name"]` (or `channel_label=`) for friendlier messages;
it is never sent to Postiz.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from ..errors import ConfigurationError, ValidationError
from ..models import PROVIDER_PROFILES

EMPTY_SETTINGS_PROVIDERS = frozenset({"threads", "mastodon", "bluesky", "telegram", "nostr", "vk", "kick"})
INTERNAL_KEYS = frozenset({"__type", "channel_name"})

TITLE_LIMITS: dict[str, int] = {"tiktok": 90, "youtube": 100, "pinterest": 100, "reddit": 300}
YOUTUBE_TAGS_MAX_CHARS = 500

X_REPLY_OPTIONS = ("everyone", "following", "mentionedUsers", "subscribers", "verified")
TIKTOK_PRIVACY = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY")
TIKTOK_METHODS = ("DIRECT_POST", "UPLOAD")
YOUTUBE_VISIBILITY = ("public", "private", "unlisted")
REDDIT_TYPES = ("self", "link", "image", "video")
GMB_TOPICS = ("STANDARD", "EVENT", "OFFER")
GMB_CTAS = ("NONE", "BOOK", "ORDER", "SHOP", "LEARN_MORE", "SIGN_UP", "GET_OFFER", "CALL")
TWITCH_MESSAGE_TYPES = ("message", "announcement")
TWITCH_COLORS = ("primary", "blue", "green", "orange", "purple")

_NETWORK_NAMES: dict[str, str] = {
    "youtube": "YouTube", "gmb": "Google Business", "lemmy": "Lemmy", "discord": "Discord",
    "slack": "Slack", "twitch": "Twitch", "dribbble": "Dribbble", "medium": "Medium",
    "devto": "Dev.to", "hashnode": "Hashnode", "wordpress": "WordPress", "listmonk": "Listmonk",
    "warpcast": "Warpcast", "moltbook": "Moltbook", "skool": "Skool", "whop": "Whop",
    "kick": "Kick", "nostr": "Nostr", "vk": "VK",
}

_URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.IGNORECASE)
_X_COMMUNITY_RE = re.compile(r"^https://(?:x|twitter)\.com/i/communities/\d+/?$", re.IGNORECASE)
_TRUE = frozenset({"true", "yes", "1", "on"})
_FALSE = frozenset({"false", "no", "0", "off", ""})


def network_name(provider: str) -> str:
    key = (provider or "").strip().lower()
    if key in _NETWORK_NAMES:
        return _NETWORK_NAMES[key]
    profile = PROVIDER_PROFILES.get(key)
    return profile.label if profile else (key.replace("-", " ").title() or "Unknown network")


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def truncate(text: Any, limit: int) -> str:
    """Collapse whitespace and cut to `limit` characters on a word boundary, adding "…"."""
    value = clean_text(text)
    if limit <= 0 or len(value) <= limit:
        return value
    cut = value[: limit - 1]
    space = cut.rfind(" ")
    if space >= int(limit * 0.6):
        cut = cut[:space]
    return cut.rstrip(" ,;:-–—|/") + "…"


@dataclass
class _Input:
    provider: str
    raw: dict[str, Any]
    title: str
    link_url: str
    image_count: int
    video_count: int
    channel: str

    @property
    def network(self) -> str:
        return network_name(self.provider)

    @property
    def has_media(self) -> bool:
        return self.image_count + self.video_count > 0

    def get(self, key: str, default: Any = None) -> Any:
        value = self.raw.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            return default
        return value

    def details(self, **extra: Any) -> dict[str, Any]:
        return {"provider": self.provider, "channel": self.channel, **extra}

    def missing(self, what: str, field: str = "") -> ConfigurationError:
        return ConfigurationError(
            f"{self.network} channel {self.channel} has no {what} selected — set one in Channels",
            details=self.details(field=field or what),
        )

    def bad_setting(self, field: str, message: str) -> ConfigurationError:
        return ConfigurationError(
            f"{self.network} channel {self.channel}: {field} {message}", details=self.details(field=field),
        )

    def bad_content(self, message: str, field: str = "") -> ValidationError:
        return ValidationError(f"{self.network} channel {self.channel}: {message}", details=self.details(field=field))

    # -- typed accessors -----------------------------------------------------

    def boolean(self, field: str, default: bool) -> bool:
        value = self.raw.get(field)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise self.bad_setting(field, f"must be true or false, got {value!r}")

    def choice(self, field: str, options: Iterable[str], default: str | None = None, value: Any = None) -> str | None:
        value = self.get(field) if value is None else value
        if value is None:
            return default
        options = tuple(options)
        text = str(value).strip()
        for option in options:
            if option.lower() == text.lower():
                return option
        raise self.bad_setting(field, f"must be one of {', '.join(options)}; got {text!r}")

    def yes_no(self, field: str, default: str) -> str:
        value = self.raw.get(field)
        if isinstance(value, bool):
            return "yes" if value else "no"
        return self.choice(field, ("yes", "no"), default) or default

    def url(self, field: str, value: Any, *, content: bool = True) -> str:
        text = str(value or "").strip()
        if _URL_RE.match(text):
            return text
        message = f"{field} must be a full http(s) URL, got {text!r}"
        if content:
            raise self.bad_content(message, field)
        raise self.bad_setting(field, message.removeprefix(f"{field} "))

    def ident(self, field: str) -> str:
        """An id that may be stored as a string or as a {id|value, label} option."""
        value = self.get(field)
        if isinstance(value, Mapping):
            value = value.get("id") or value.get("value")
        return str(value or "").strip()

    def title_text(self, limit: int = 0) -> str:
        source = self.get("title") or self.title
        return truncate(source, limit) if limit else clean_text(source)


def _tags(value: Any) -> list[dict[str, str]]:
    """Accept ["a", "b"], "a, b" or [{"value", "label"}] → Postiz Tag objects (deduped)."""
    if value is None:
        return []
    items: list[Any] = value.split(",") if isinstance(value, str) else list(value) if isinstance(value, (list, tuple)) else []
    tags: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, Mapping):
            tag_value = clean_text(item.get("value") or item.get("label"))
            label = clean_text(item.get("label") or tag_value)
        else:
            tag_value = label = clean_text(item).lstrip("#")
        if tag_value and tag_value.lower() not in seen:
            seen.add(tag_value.lower())
            tags.append({"value": tag_value, "label": label or tag_value})
    return tags


def _media_ref(value: Any) -> dict[str, str] | None:
    if isinstance(value, Mapping) and value.get("id") and value.get("path"):
        return {"id": str(value["id"]), "path": str(value["path"])}
    return None


def _community_items(value: Any) -> list[dict[str, Any]]:
    """Reddit/Lemmy/Warpcast lists: "name", ["a", "b"], [{"value": {...}}] or [{...}]."""
    if value is None or value == "":
        return []
    raw_items = [value] if isinstance(value, (str, Mapping)) else list(value) if isinstance(value, (list, tuple)) else []
    items: list[dict[str, Any]] = []
    for item in raw_items:
        if isinstance(item, str) and item.strip():
            items.append({"subreddit": item.strip()})
        elif isinstance(item, Mapping):
            inner = item.get("value")
            items.append(dict(inner) if isinstance(inner, Mapping) else dict(item))
    return items


# ---------------------------------------------------------------------------
# Builders (one per Postiz settings schema)
# ---------------------------------------------------------------------------

def _x(inp: _Input) -> dict[str, Any]:
    out: dict[str, Any] = {
        "__type": "x",
        "who_can_reply_post": inp.choice("who_can_reply_post", X_REPLY_OPTIONS, "everyone"),
    }
    if inp.boolean("made_with_ai", False):
        out["made_with_ai"] = True
    community = clean_text(inp.get("community"))
    if community:
        if not _X_COMMUNITY_RE.match(community):
            raise inp.bad_setting("community", f"must look like https://x.com/i/communities/123, got {community!r}")
        out["community"] = community
    return out


def _instagram(inp: _Input) -> dict[str, Any]:
    requested = inp.get("post_type")
    if requested is None and inp.boolean("story", False):
        requested = "story"
    post_type = str(requested or "post").strip().lower()
    if post_type in {"reel", "reels", "feed", "carousel"}:
        post_type = "post"   # Postiz publishes a video "post" as a Reel
    post_type = inp.choice("post_type", ("post", "story"), value=post_type) or "post"
    media_count = inp.image_count + inp.video_count
    if post_type == "story" and media_count > 1:
        raise inp.bad_content(f"an Instagram story takes exactly one image or video, got {media_count}", "post_type")
    trial = inp.boolean("is_trial_reel", False) and post_type == "post" and inp.video_count > 0
    collaborators: list[dict[str, str]] = []
    if post_type == "post":
        for item in inp.get("collaborators") or []:
            name = item.get("label") if isinstance(item, Mapping) else item
            name = clean_text(name).lstrip("@")
            if name and all(c["label"].lower() != name.lower() for c in collaborators):
                collaborators.append({"label": name})
        if len(collaborators) > 3:
            raise inp.bad_setting("collaborators", f"allows at most 3 collaborators, got {len(collaborators)}")
    out: dict[str, Any] = {
        "__type": inp.provider, "post_type": post_type, "is_trial_reel": trial, "collaborators": collaborators,
    }
    if trial:
        out["graduation_strategy"] = inp.choice("graduation_strategy", ("MANUAL", "SS_PERFORMANCE"), "MANUAL")
    return out


def _facebook(inp: _Input) -> dict[str, Any]:
    out: dict[str, Any] = {"__type": "facebook"}
    post_type = inp.choice("post_type", ("post", "story"), None)
    if post_type:
        out["post_type"] = post_type
        if post_type == "story":
            if inp.image_count + inp.video_count < 1:
                raise inp.bad_content("a Facebook story needs at least one image or video", "post_type")
            return out
    link = inp.get("url") or inp.link_url
    if link:
        out["url"] = inp.url("url", link)
    return out


def _linkedin(inp: _Input) -> dict[str, Any]:
    carousel = inp.boolean("post_as_images_carousel", inp.image_count > 1)
    if inp.image_count < 2 or inp.video_count:
        carousel = False   # a carousel needs several images and no video
    out: dict[str, Any] = {"__type": inp.provider, "post_as_images_carousel": carousel}
    if carousel:
        name = clean_text(inp.get("carousel_name") or inp.title)
        if name:
            out["carousel_name"] = name
    return out


def _tiktok(inp: _Input) -> dict[str, Any]:
    privacy = inp.choice("privacy_level", TIKTOK_PRIVACY, "PUBLIC_TO_EVERYONE")
    brand_content = inp.boolean("brand_content_toggle", False)
    if brand_content and privacy == "SELF_ONLY":
        raise inp.bad_setting("privacy_level", "cannot be SELF_ONLY for branded content — choose another visibility")
    out: dict[str, Any] = {
        "__type": inp.provider,
        "privacy_level": privacy,
        "duet": inp.boolean("duet", False),
        "stitch": inp.boolean("stitch", False),
        "comment": inp.boolean("comment", True),
        "autoAddMusic": inp.yes_no("autoAddMusic", "no"),
        "brand_content_toggle": brand_content,
        # We promote our own products: disclose it honestly as "Your brand".
        "brand_organic_toggle": inp.boolean("brand_organic_toggle", True),
        "content_posting_method": inp.choice("content_posting_method", TIKTOK_METHODS, "DIRECT_POST"),
        "video_made_with_ai": inp.boolean("video_made_with_ai", False),
    }
    title = inp.title_text(TITLE_LIMITS["tiktok"])
    if title:
        out["title"] = title
    music = inp.get("music")
    if isinstance(music, Mapping) and music.get("id") and out["content_posting_method"] == "DIRECT_POST":
        track = {"id": str(music["id"])}
        for key in ("title", "artist", "image"):
            if music.get(key):
                track[key] = str(music[key])
        for key in ("audio_volume", "video_volume"):
            if music.get(key) is not None:
                track[key] = max(0, min(100, int(music[key])))
        out["music"] = track
    location = inp.get("location")
    if isinstance(location, Mapping) and location.get("id"):
        out["location"] = dict(location)
    return out


def _youtube(inp: _Input) -> dict[str, Any]:
    title = truncate(re.sub(r"[<>]", "", clean_text(inp.get("title") or inp.title)), TITLE_LIMITS["youtube"])
    if len(title) < 2:
        raise inp.bad_content("a video title of 2–100 characters is required", "title")
    tags: list[dict[str, str]] = []
    used = 0
    for tag in _tags(inp.get("tags")):
        used += len(tag["value"]) + 1
        if used > YOUTUBE_TAGS_MAX_CHARS:
            break
        tags.append(tag)
    out: dict[str, Any] = {
        "__type": "youtube",
        "title": title,
        "type": inp.choice("type", YOUTUBE_VISIBILITY, "public"),
        "selfDeclaredMadeForKids": inp.yes_no("selfDeclaredMadeForKids", "no"),
        "tags": tags,
    }
    thumbnail = _media_ref(inp.get("thumbnail"))
    if thumbnail:
        out["thumbnail"] = thumbnail
    return out


def _pinterest(inp: _Input) -> dict[str, Any]:
    board = inp.ident("board")
    if not board:
        raise inp.missing("board")
    out: dict[str, Any] = {"__type": "pinterest", "board": board}
    title = inp.title_text(TITLE_LIMITS["pinterest"])
    if title:
        out["title"] = title
    link = inp.get("link") or inp.link_url
    if link:
        out["link"] = inp.url("link", link)
    color = clean_text(inp.get("dominant_color"))
    if color:
        out["dominant_color"] = color
    return out


def _empty(inp: _Input) -> dict[str, Any]:
    return {"__type": inp.provider}


def _reddit(inp: _Input) -> dict[str, Any]:
    items = _community_items(inp.get("subreddit") or inp.get("subreddits"))
    if not items:
        raise inp.missing("subreddit", "subreddit")
    if inp.video_count:
        default_type = "video"
    elif inp.image_count:
        default_type = "image"
    else:
        default_type = "link" if inp.link_url else "self"
    entries: list[dict[str, Any]] = []
    for item in items:
        name = clean_text(item.get("subreddit")).removeprefix("/").removeprefix("r/")
        if len(name) < 2:
            raise inp.bad_setting("subreddit", f"name {name!r} is not valid")
        title = truncate(item.get("title") or inp.get("title") or inp.title, TITLE_LIMITS["reddit"])
        if len(title) < 2:
            raise inp.bad_content(f"a post title is required for r/{name}", "title")
        post_type = inp.choice("type", REDDIT_TYPES, value=item.get("type") or inp.get("type") or default_type)
        url = clean_text(item.get("url")) or (inp.link_url if post_type == "link" else "")
        if post_type == "link":
            if not url:
                raise inp.bad_content(f"a link post to r/{name} needs a URL", "url")
            url = inp.url("url", url)
        flair_required = bool(item.get("is_flair_required"))
        flair = item.get("flair") if isinstance(item.get("flair"), Mapping) and item["flair"].get("id") else None
        if flair_required and not flair:
            raise inp.bad_setting("flair", f"is required by r/{name} — choose one in Channels")
        entries.append({"value": {
            "subreddit": name, "title": title, "type": post_type, "url": url,
            "is_flair_required": flair_required,
            "flair": {"id": str(flair["id"]), "name": str(flair.get("name") or "")} if flair else None,
        }})
    return {"__type": "reddit", "subreddit": entries}


def _lemmy(inp: _Input) -> dict[str, Any]:
    items = _community_items(inp.get("subreddit") or inp.get("communities"))
    if not items:
        raise inp.missing("community", "subreddit")
    entries: list[dict[str, Any]] = []
    for item in items:
        name = clean_text(item.get("subreddit") or item.get("name"))
        community_id = clean_text(item.get("id"))
        if len(name) < 2 or not community_id:
            raise inp.bad_setting("subreddit", "needs a community name and id — pick the community again in Channels")
        title = clean_text(item.get("title") or inp.get("title") or inp.title)
        if len(title) < 2:
            raise inp.bad_content(f"a post title is required for {name}", "title")
        value: dict[str, Any] = {"subreddit": name, "id": community_id, "title": title}
        url = clean_text(item.get("url")) or inp.link_url
        if url:
            value["url"] = inp.url("url", url)
        entries.append({"value": value})
    return {"__type": "lemmy", "subreddit": entries}


def _chat_channel(inp: _Input) -> dict[str, Any]:
    channel = inp.ident("channel")
    if not channel:
        raise inp.missing("channel", "channel")
    return {"__type": inp.provider, "channel": channel}


def _twitch(inp: _Input) -> dict[str, Any]:
    out: dict[str, Any] = {"__type": "twitch"}
    message_type = inp.choice("messageType", TWITCH_MESSAGE_TYPES)
    if message_type:
        out["messageType"] = message_type
        if message_type == "announcement":
            out["announcementColor"] = inp.choice("announcementColor", TWITCH_COLORS, "primary")
    return out


def _dribbble(inp: _Input) -> dict[str, Any]:
    title = inp.title_text()
    if not title:
        raise inp.bad_content("a shot title is required", "title")
    out: dict[str, Any] = {"__type": "dribbble", "title": title}
    team = inp.get("team")
    if team:
        out["team"] = inp.url("team", team, content=False)
    return out


def _article_common(inp: _Input, out: dict[str, Any], *, max_tags: int | None) -> None:
    canonical = inp.get("canonical") or inp.link_url
    if canonical:
        out["canonical"] = inp.url("canonical", canonical)
    image = _media_ref(inp.get("main_image"))
    if image:
        out["main_image"] = image
    tags = _tags(inp.get("tags"))
    if tags:
        out["tags"] = tags[:max_tags] if max_tags else tags


def _medium(inp: _Input) -> dict[str, Any]:
    title = inp.title_text()
    if len(title) < 2:
        raise inp.bad_content("an article title of at least 2 characters is required", "title")
    subtitle = clean_text(inp.get("subtitle"))
    if len(subtitle) < 2:
        raise inp.bad_content("a subtitle of at least 2 characters is required", "subtitle")
    out: dict[str, Any] = {"__type": "medium", "title": title, "subtitle": subtitle}
    publication = inp.ident("publication")
    if publication:
        out["publication"] = publication
    _article_common(inp, out, max_tags=4)
    out.pop("main_image", None)   # not part of MediumSettings
    return out


def _devto(inp: _Input) -> dict[str, Any]:
    title = inp.title_text()
    if len(title) < 2:
        raise inp.bad_content("an article title of at least 2 characters is required", "title")
    out: dict[str, Any] = {"__type": "devto", "title": title}
    organization = inp.ident("organization")
    if organization:
        out["organization"] = organization
    _article_common(inp, out, max_tags=4)
    return out


def _hashnode(inp: _Input) -> dict[str, Any]:
    title = inp.title_text()
    if len(title) < 6:
        raise inp.bad_content("an article title of at least 6 characters is required", "title")
    publication = inp.ident("publication")
    if not publication:
        raise inp.missing("publication")
    out: dict[str, Any] = {"__type": "hashnode", "title": title, "publication": publication}
    subtitle = clean_text(inp.get("subtitle"))
    if len(subtitle) >= 2:
        out["subtitle"] = subtitle
    _article_common(inp, out, max_tags=None)
    if not out.get("tags"):
        raise ConfigurationError(
            f"Hashnode channel {inp.channel} needs at least one tag — set default tags in Channels",
            details=inp.details(field="tags"),
        )
    return out


def _wordpress(inp: _Input) -> dict[str, Any]:
    title = inp.title_text()
    if len(title) < 2:
        raise inp.bad_content("a post title of at least 2 characters is required", "title")
    out: dict[str, Any] = {"__type": "wordpress", "title": title, "type": clean_text(inp.get("type")) or "post"}
    image = _media_ref(inp.get("main_image"))
    if image:
        out["main_image"] = image
    return out


def _listmonk(inp: _Input) -> dict[str, Any]:
    subject = clean_text(inp.get("subject") or inp.title)
    if not subject:
        raise inp.bad_content("an email subject is required", "subject")
    mailing_list = inp.ident("list")
    if not mailing_list:
        raise inp.missing("mailing list", "list")
    out: dict[str, Any] = {
        "__type": "listmonk", "subject": subject, "preview": clean_text(inp.get("preview")), "list": mailing_list,
    }
    template = inp.ident("template")
    if template:
        out["template"] = template
    return out


def _gmb(inp: _Input) -> dict[str, Any]:
    topic = inp.choice("topicType", GMB_TOPICS, "STANDARD")
    out: dict[str, Any] = {"__type": "gmb", "topicType": topic}
    cta = inp.choice("callToActionType", GMB_CTAS, "LEARN_MORE" if inp.link_url else None)
    if cta:
        out["callToActionType"] = cta
        if cta not in {"NONE", "CALL"}:
            cta_url = inp.get("callToActionUrl") or inp.link_url
            if not cta_url:
                raise inp.bad_content(f"the {cta} button needs a link", "callToActionUrl")
            out["callToActionUrl"] = inp.url("callToActionUrl", cta_url)
    if topic == "EVENT":
        event_title = clean_text(inp.get("eventTitle") or inp.title)
        if not event_title:
            raise inp.bad_content("an event post needs an event title", "eventTitle")
        out["eventTitle"] = event_title
        for key in ("eventStartDate", "eventEndDate", "eventStartTime", "eventEndTime"):
            if inp.get(key):
                out[key] = clean_text(inp.get(key))
    if topic == "OFFER":
        for key in ("offerCouponCode", "offerTerms"):
            if inp.get(key):
                out[key] = clean_text(inp.get(key))
        if inp.get("offerRedeemUrl"):
            out["offerRedeemUrl"] = inp.url("offerRedeemUrl", inp.get("offerRedeemUrl"))
    return out


def _warpcast(inp: _Input) -> dict[str, Any]:
    out: dict[str, Any] = {"__type": "warpcast"}
    ids = [clean_text(item.get("id") or item.get("subreddit")) for item in _community_items(inp.get("subreddit") or inp.get("channels"))]
    ids = [channel_id for channel_id in ids if channel_id]
    if ids:
        out["subreddit"] = [{"value": {"id": channel_id}} for channel_id in ids]
    return out


def _moltbook(inp: _Input) -> dict[str, Any]:
    submolt = inp.ident("submolt")
    if not submolt:
        raise inp.missing("submolt")
    return {"__type": "moltbook", "submolt": submolt}


def _skool(inp: _Input) -> dict[str, Any]:
    group, label = inp.ident("group"), inp.ident("label")
    if not group:
        raise inp.missing("group")
    if not label:
        raise inp.missing("label")
    title = inp.title_text()
    if not title:
        raise inp.bad_content("a post title is required", "title")
    return {"__type": "skool", "group": group, "label": label, "title": title}


def _whop(inp: _Input) -> dict[str, Any]:
    company, experience = inp.ident("company"), inp.ident("experience")
    if not company:
        raise inp.missing("company")
    if not experience:
        raise inp.missing("experience (forum)", "experience")
    out: dict[str, Any] = {"__type": "whop", "company": company, "experience": experience}
    title = inp.title_text()
    if title:
        out["title"] = title
    return out


_BUILDERS: dict[str, Callable[[_Input], dict[str, Any]]] = {
    "x": _x,
    "instagram": _instagram,
    "instagram-standalone": _instagram,
    "facebook": _facebook,
    "linkedin": _linkedin,
    "linkedin-page": _linkedin,
    "tiktok": _tiktok,
    "tiktok-business": _tiktok,
    "youtube": _youtube,
    "pinterest": _pinterest,
    "reddit": _reddit,
    "lemmy": _lemmy,
    "discord": _chat_channel,
    "slack": _chat_channel,
    "twitch": _twitch,
    "dribbble": _dribbble,
    "medium": _medium,
    "devto": _devto,
    "hashnode": _hashnode,
    "wordpress": _wordpress,
    "listmonk": _listmonk,
    "gmb": _gmb,
    "warpcast": _warpcast,
    "moltbook": _moltbook,
    "skool": _skool,
    "whop": _whop,
    **{provider: _empty for provider in EMPTY_SETTINGS_PROVIDERS},
}

SUPPORTED_PROVIDERS = frozenset(_BUILDERS)


def build_settings(
    provider: str,
    settings: Mapping[str, Any] | None = None,
    *,
    title: str = "",
    link_url: str = "",
    image_count: int = 0,
    video_count: int = 0,
    channel_label: str = "",
) -> dict[str, Any]:
    """Return the validated Postiz `settings` object (including `__type`) for one post item."""
    key = (provider or "").strip().lower()
    if not key:
        raise ValidationError("Cannot build Postiz settings: the target has no provider.")
    raw = {str(k): v for k, v in dict(settings or {}).items()}
    label = clean_text(channel_label or raw.get("channel_name")) or "(unnamed)"
    builder = _BUILDERS.get(key)
    if builder is None:
        # A network this module does not know yet: pass the configured keys through.
        return {"__type": key, **{k: v for k, v in raw.items() if k not in INTERNAL_KEYS and v is not None}}
    return builder(_Input(
        provider=key, raw=raw, title=clean_text(title), link_url=clean_text(link_url),
        image_count=max(0, int(image_count)), video_count=max(0, int(video_count)), channel=label,
    ))


__all__ = [
    "EMPTY_SETTINGS_PROVIDERS", "SUPPORTED_PROVIDERS", "TITLE_LIMITS", "build_settings",
    "network_name", "truncate",
]
