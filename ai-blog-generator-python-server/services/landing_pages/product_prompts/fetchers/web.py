"""Web fetcher: reads the public Shopify ``/products/<handle>.json`` endpoint.

This is the most robust, dependency-free path and works for any Shopify
storefront without credentials. It falls back to scraping the product page
HTML (JSON-LD / OpenGraph) if the JSON endpoint is unavailable.
"""

from __future__ import annotations

import json
from typing import List, Optional

from bs4 import BeautifulSoup

from ..models import Product
from ..utils import get_logger, html_to_text
from .base import ProductFetcher

log = get_logger("fetchers.web")


class WebProductFetcher(ProductFetcher):
    name = "web"

    def fetch(self, url: str) -> Product:
        handle = self.handle_from_url(url)
        base = self.base_url(url)
        json_url = f"{base}/products/{handle}.json"

        product = self._fetch_json(url, json_url, handle)
        if product is not None:
            return product

        log.warning("JSON endpoint failed for %s; falling back to HTML scrape", handle)
        return self._fetch_html(url, handle)

    # ------------------------------------------------------------------
    def _fetch_json(self, url: str, json_url: str, handle: str) -> Optional[Product]:
        try:
            resp = self.session.get(json_url, timeout=self.settings.request_timeout)
            resp.raise_for_status()
            data = resp.json().get("product")
        except (ValueError, OSError) as exc:  # network / json errors
            log.debug("JSON fetch error for %s: %s", handle, exc)
            return None
        if not data:
            return None

        images = [img.get("src") for img in data.get("images", []) if img.get("src")]
        variants = data.get("variants") or []
        price = None
        if variants:
            price = variants[0].get("price")
        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        body_html = data.get("body_html", "") or ""
        return Product(
            url=url,
            handle=data.get("handle", handle),
            title=data.get("title", handle),
            description_html=body_html,
            description_text=html_to_text(body_html),
            vendor=data.get("vendor"),
            product_type=data.get("product_type"),
            tags=tags,
            price=str(price) if price is not None else None,
            image_urls=images,
            source="web",
        )

    # ------------------------------------------------------------------
    def _fetch_html(self, url: str, handle: str) -> Product:
        resp = self.session.get(url, timeout=self.settings.request_timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        title = self._meta(soup, "og:title") or (
            soup.title.string.strip() if soup.title and soup.title.string else handle
        )
        description_html = ""
        # Prefer JSON-LD Product description when available.
        ld = self._json_ld_product(soup)
        if ld:
            title = ld.get("name", title)
            description_html = ld.get("description", "") or ""

        images = self._collect_images(soup, ld)
        price = None
        if ld and isinstance(ld.get("offers"), dict):
            price = ld["offers"].get("price")

        return Product(
            url=url,
            handle=handle,
            title=title,
            description_html=description_html,
            description_text=html_to_text(description_html)
            or self._meta(soup, "og:description")
            or "",
            image_urls=images,
            price=str(price) if price is not None else None,
            source="web-html",
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _meta(soup: BeautifulSoup, prop: str) -> Optional[str]:
        tag = soup.find("meta", attrs={"property": prop}) or soup.find(
            "meta", attrs={"name": prop}
        )
        if tag and tag.get("content"):
            return tag["content"].strip()
        return None

    @staticmethod
    def _json_ld_product(soup: BeautifulSoup) -> Optional[dict]:
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "")
            except (ValueError, TypeError):
                continue
            candidates = data if isinstance(data, list) else [data]
            if isinstance(data, dict) and "@graph" in data:
                candidates = data["@graph"]
            for item in candidates:
                if isinstance(item, dict) and item.get("@type") == "Product":
                    return item
        return None

    def _collect_images(self, soup: BeautifulSoup, ld: Optional[dict]) -> List[str]:
        images: List[str] = []
        if ld:
            img = ld.get("image")
            if isinstance(img, str):
                images.append(img)
            elif isinstance(img, list):
                images.extend(i for i in img if isinstance(i, str))
        og = self._meta(soup, "og:image")
        if og:
            images.append(og)
        # De-duplicate, keep order.
        seen = set()
        result = []
        for src in images:
            if src and src not in seen:
                seen.add(src)
                result.append(src)
        return result
