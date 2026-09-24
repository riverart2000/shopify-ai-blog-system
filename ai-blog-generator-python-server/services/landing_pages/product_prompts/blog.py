"""Extract the blog URL from a product description and scrape that article.

A product description may embed a link to a blog article about the product,
plus imagery. This module:

1. Finds candidate blog URLs in the description HTML/text.
2. Fetches the chosen article and extracts its title, readable text and images.

If no blog URL is present, the caller simply falls back to product images.
"""

from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup

from .config import Settings
from .models import BlogContent, Product
from .utils import get_logger, html_to_text

log = get_logger("blog")

# Shopify blog articles live under /blogs/<blog>/<article>. We also accept
# generic links whose path or text hints at a blog/article.
_BLOG_PATH_RE = re.compile(r"/blogs?/", re.IGNORECASE)
_BLOG_HINT_RE = re.compile(r"blog|article|read more|learn more", re.IGNORECASE)


class BlogScraper:
    def __init__(self, settings: Settings, session) -> None:
        self.settings = settings
        self.session = session

    # ------------------------------------------------------------------
    def find_blog_url(self, product: Product) -> Optional[str]:
        """Return the most likely blog URL referenced by the product."""
        base = f"{urlparse(product.url).scheme}://{urlparse(product.url).netloc}"
        if not product.description_html:
            return None

        soup = BeautifulSoup(product.description_html, "html.parser")
        candidates: List[str] = []
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            text = anchor.get_text(" ", strip=True)
            absolute = urljoin(base, href)
            if _BLOG_PATH_RE.search(urlparse(absolute).path):
                candidates.insert(0, absolute)  # strong match first
            elif _BLOG_HINT_RE.search(text) and absolute.startswith("http"):
                candidates.append(absolute)

        # Also catch bare URLs written in the text.
        for match in re.findall(r"https?://[^\s\"'<>]+", product.description_text or ""):
            if _BLOG_PATH_RE.search(urlparse(match).path):
                candidates.insert(0, match)

        # De-duplicate preserving priority order.
        seen = set()
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                return candidate
        return None

    # ------------------------------------------------------------------
    def scrape(self, blog_url: str) -> BlogContent:
        """Fetch a blog article and extract title, text and image URLs."""
        if self.settings.myshopify_domain and self.settings.shopify_access_token:
            return self._scrape_shopify_admin(blog_url)
        try:
            resp = self.session.get(blog_url, timeout=self.settings.request_timeout)
            resp.raise_for_status()
        except OSError as exc:
            raise RuntimeError(
                f"Linked blog evidence could not be fetched from {blog_url}: "
                f"{type(exc).__name__}: {exc}. No fallback was used."
            ) from exc

        soup = BeautifulSoup(resp.text, "html.parser")
        title = self._title(soup)
        container = self._article_container(soup)
        text = html_to_text(str(container)) if container else ""
        images = self._images(container or soup, blog_url)
        return BlogContent(url=blog_url, title=title, text=text, image_urls=images)

    def _scrape_shopify_admin(self, blog_url: str) -> BlogContent:
        """Load a linked article through authenticated Shopify Admin REST."""
        match = re.search(r"/blogs/([^/?#]+)/([^/?#]+)", urlparse(blog_url).path)
        if not match:
            raise RuntimeError(
                f"Linked blog URL has no Shopify blog/article handles: {blog_url}"
            )
        blog_handle, article_handle = match.groups()
        base = (
            f"https://{self.settings.myshopify_domain}/admin/api/"
            f"{self.settings.shopify_api_version}"
        )
        headers = {
            "X-Shopify-Access-Token": self.settings.shopify_access_token,
            "Accept": "application/json",
        }
        blogs_response = self.session.get(
            f"{base}/blogs.json?handle={quote(blog_handle)}&limit=1"
            "&fields=id,handle,title",
            headers=headers,
            timeout=self.settings.request_timeout,
        )
        blogs_response.raise_for_status()
        blogs = blogs_response.json().get("blogs") or []
        if not blogs:
            raise RuntimeError(
                f"Shopify Admin API found no blog with handle '{blog_handle}' "
                f"for linked URL {blog_url}."
            )
        blog_id = blogs[0].get("id")
        article_response = self.session.get(
            f"{base}/blogs/{blog_id}/articles.json?"
            f"handle={quote(article_handle)}&limit=1"
            "&fields=id,title,handle,body_html,image",
            headers=headers,
            timeout=self.settings.request_timeout,
        )
        article_response.raise_for_status()
        articles = article_response.json().get("articles") or []
        if not articles:
            raise RuntimeError(
                f"Shopify Admin API found no article '{article_handle}' in blog "
                f"'{blog_handle}' for linked URL {blog_url}."
            )
        article = articles[0]
        body_html = str(article.get("body_html") or "")
        soup = BeautifulSoup(body_html, "html.parser")
        images = self._images(soup, blog_url)
        featured = article.get("image") or {}
        featured_url = featured.get("src") or featured.get("url")
        if featured_url and featured_url not in images:
            images.insert(0, featured_url)
        return BlogContent(
            url=blog_url,
            title=str(article.get("title") or ""),
            text=html_to_text(body_html),
            image_urls=images,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _title(soup: BeautifulSoup) -> Optional[str]:
        og = soup.find("meta", attrs={"property": "og:title"})
        if og and og.get("content"):
            return og["content"].strip()
        if soup.find("h1"):
            return soup.find("h1").get_text(" ", strip=True)
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        return None

    @staticmethod
    def _article_container(soup: BeautifulSoup):
        for selector in ("article", "main", ".article__content", ".rte", ".post-content"):
            found = soup.select_one(selector)
            if found:
                return found
        return None

    @staticmethod
    def _images(scope, base_url: str) -> List[str]:
        images: List[str] = []
        # OpenGraph image first (usually the hero image).
        root = scope if hasattr(scope, "find_all") else None
        if root is not None:
            for img in root.find_all("img"):
                src = (
                    img.get("src")
                    or img.get("data-src")
                    or img.get("data-original")
                    or ""
                ).strip()
                if src and not src.startswith("data:"):
                    images.append(urljoin(base_url, src))
        # De-duplicate, keep order.
        seen = set()
        result = []
        for src in images:
            if src not in seen:
                seen.add(src)
                result.append(src)
        return result
