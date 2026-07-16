"""Pluggable product fetchers."""

from .base import ProductFetcher
from .web import WebProductFetcher
from .shopify import ShopifyAdminFetcher

__all__ = ["ProductFetcher", "WebProductFetcher", "ShopifyAdminFetcher", "get_fetcher"]


def get_fetcher(name: str, settings, session):
    """Factory: return a fetcher instance by name (``web`` or ``shopify``)."""
    name = (name or "web").lower()
    if name in ("web", "json", "storefront-json"):
        return WebProductFetcher(settings, session)
    if name in ("shopify", "admin", "admin-api"):
        return ShopifyAdminFetcher(settings, session)
    raise ValueError(f"Unknown fetcher '{name}'. Use 'web' or 'shopify'.")
