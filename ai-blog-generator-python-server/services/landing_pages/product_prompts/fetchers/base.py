"""Abstract base class for product fetchers."""

from __future__ import annotations

import abc
import re
from typing import Optional
from urllib.parse import urlparse

import requests

from ..config import Settings
from ..models import Product


class ProductFetcher(abc.ABC):
    """Fetch a normalised :class:`Product` from a source.

    Concrete implementations decide *how* the data is retrieved (public web
    JSON, Shopify Admin GraphQL, Storefront API, ...). The pipeline only
    depends on this interface, so new sources can be added without touching
    orchestration code.
    """

    name: str = "base"

    def __init__(self, settings: Settings, session: requests.Session) -> None:
        self.settings = settings
        self.session = session

    @abc.abstractmethod
    def fetch(self, url: str) -> Product:
        """Return a populated :class:`Product` for the given product URL."""
        raise NotImplementedError

    @staticmethod
    def handle_from_url(url: str) -> str:
        """Extract the Shopify product handle from a product URL."""
        path = urlparse(url).path
        match = re.search(r"/products/([^/?#]+)", path)
        if match:
            return match.group(1)
        # Fallback: last path segment.
        segment = path.rstrip("/").rsplit("/", 1)[-1]
        return segment or "product"

    @staticmethod
    def base_url(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
