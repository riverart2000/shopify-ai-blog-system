"""Pluggable image-generation backends."""

from .base import ImageBackend, GeneratedImage
from .grok import GrokImageBackend

__all__ = ["ImageBackend", "GeneratedImage", "GrokImageBackend", "get_image_backend"]


def get_image_backend(name: str, settings, session, quality: bool = False):
    """Factory: return an image backend by name (currently ``grok``)."""
    name = (name or "grok").lower()
    if name in ("grok", "xai", "imagine"):
        return GrokImageBackend(settings, session, quality=quality)
    raise ValueError(f"Unknown image backend '{name}'. Use 'grok'.")
