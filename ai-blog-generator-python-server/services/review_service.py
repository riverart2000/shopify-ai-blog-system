"""Validation, privacy and moderation helpers for customer reviews."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
from urllib.parse import urlparse

from PIL import Image, UnidentifiedImageError

from .image_optimizer import optimize_image


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
URL_RE = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MAX_PHOTO_BYTES = 2_000_000
MAX_PHOTO_PIXELS = 16_000_000


class ReviewValidationError(ValueError):
    pass


def clean_text(value: object, max_length: int, *, multiline: bool = False) -> str:
    text = CONTROL_RE.sub("", str(value or "")).strip()
    if not multiline:
        text = re.sub(r"\s+", " ", text)
    else:
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:max_length]


def validate_email(value: object) -> str:
    email = clean_text(value, 254).lower()
    if not EMAIL_RE.fullmatch(email):
        raise ReviewValidationError("Enter a valid email address. It will never be displayed publicly.")
    return email


def validate_facebook_review_url(value: object) -> str:
    url = clean_text(value, 1000)
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise ReviewValidationError("Enter a valid Facebook review URL.") from exc
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        hostname == "facebook.com" or hostname.endswith(".facebook.com")
    ):
        raise ReviewValidationError(
            "The original review link must be an https://facebook.com URL."
        )
    return url


def moderation_flags(title: str, body: str, name: str) -> list[str]:
    combined = f"{title}\n{body}"
    flags: list[str] = []
    if len(URL_RE.findall(combined)):
        flags.append("contains_link")
    letters = [char for char in combined if char.isalpha()]
    if len(letters) >= 30 and sum(char.isupper() for char in letters) / len(letters) > 0.7:
        flags.append("mostly_capitals")
    if re.search(r"(.)\1{7,}", combined, re.IGNORECASE):
        flags.append("repeated_characters")
    if URL_RE.search(name):
        flags.append("suspicious_name")
    return flags


def hash_ip(value: str) -> str:
    salt = os.environ.get("REVIEW_IP_SALT") or os.environ.get("SESSION_SECRET") or "review-rate-limit"
    return hashlib.sha256(f"{salt}:{value.strip()}".encode("utf-8")).hexdigest() if value.strip() else ""


def normalize_photo(data_uri: str) -> str:
    if not data_uri:
        return ""
    match = re.fullmatch(r"data:image/(jpeg|jpg|png|webp);base64,([A-Za-z0-9+/=\r\n]+)", data_uri)
    if not match:
        raise ReviewValidationError("The review photograph must be a JPEG, PNG or WebP image.")
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except Exception as exc:
        raise ReviewValidationError("The review photograph is not valid image data.") from exc
    if len(raw) > MAX_PHOTO_BYTES:
        raise ReviewValidationError("The review photograph must be smaller than 2 MB.")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > MAX_PHOTO_PIXELS:
                raise ReviewValidationError("The review photograph dimensions are too large.")
    except (UnidentifiedImageError, OSError) as exc:
        raise ReviewValidationError("The uploaded file is not a readable image.") from exc
    optimized = optimize_image(raw, max_width=1600, max_height=1600)
    return "data:image/webp;base64," + base64.b64encode(optimized).decode("ascii")
