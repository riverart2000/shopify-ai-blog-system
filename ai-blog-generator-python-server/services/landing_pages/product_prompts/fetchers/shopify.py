"""Shopify Admin GraphQL fetcher (alternative to the public web fetcher).

Authentication order:
1. ``SHOPIFY_ACCESS_TOKEN`` if provided.
2. OAuth *client credentials* grant using ``SHOPIFY_CLIENT_ID`` /
   ``SHOPIFY_CLIENT_SECRET`` (mirrors the shopify-mcp-ext behaviour).
"""

from __future__ import annotations

from typing import List, Optional

from ..models import Product
from ..utils import get_logger, html_to_text
from .base import ProductFetcher

log = get_logger("fetchers.shopify")

_PRODUCT_QUERY = """
query ProductByHandle($handle: String!) {
  productByHandle(handle: $handle) {
    id
    handle
    title
    descriptionHtml
    vendor
    productType
    tags
    onlineStoreUrl
    featuredImage { url }
    images(first: 20) { edges { node { url } } }
    priceRangeV2 { minVariantPrice { amount currencyCode } }
  }
}
"""


class ShopifyAdminFetcher(ProductFetcher):
    name = "shopify"

    def __init__(self, settings, session) -> None:
        super().__init__(settings, session)
        self._token: Optional[str] = None

    # ------------------------------------------------------------------
    def _endpoint(self) -> str:
        domain = self.settings.myshopify_domain
        if not domain:
            raise RuntimeError("MYSHOPIFY_DOMAIN is required for the shopify fetcher.")
        version = self.settings.shopify_api_version
        return f"https://{domain}/admin/api/{version}/graphql.json"

    def _access_token(self) -> str:
        if self._token:
            return self._token
        if self.settings.shopify_access_token:
            self._token = self.settings.shopify_access_token
            return self._token
        if self.settings.shopify_client_id and self.settings.shopify_client_secret:
            self._token = self._client_credentials_token()
            return self._token
        raise RuntimeError(
            "No Shopify credentials: set SHOPIFY_ACCESS_TOKEN or "
            "SHOPIFY_CLIENT_ID/SHOPIFY_CLIENT_SECRET."
        )

    def _client_credentials_token(self) -> str:
        domain = self.settings.myshopify_domain
        url = f"https://{domain}/admin/oauth/access_token"
        payload = {
            "client_id": self.settings.shopify_client_id,
            "client_secret": self.settings.shopify_client_secret,
            "grant_type": "client_credentials",
        }
        resp = self.session.post(url, json=payload, timeout=self.settings.request_timeout)
        resp.raise_for_status()
        token = resp.json().get("access_token")
        if not token:
            raise RuntimeError("Client credentials grant did not return an access_token.")
        log.debug("Obtained Shopify access token via client credentials.")
        return token

    # ------------------------------------------------------------------
    def fetch(self, url: str) -> Product:
        handle = self.handle_from_url(url)
        token = self._access_token()
        resp = self.session.post(
            self._endpoint(),
            json={"query": _PRODUCT_QUERY, "variables": {"handle": handle}},
            headers={
                "X-Shopify-Access-Token": token,
                "Content-Type": "application/json",
            },
            timeout=self.settings.request_timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(f"Shopify GraphQL errors: {body['errors']}")
        node = (body.get("data") or {}).get("productByHandle")
        if not node:
            raise RuntimeError(f"Product not found via Admin API: {handle}")

        images: List[str] = []
        featured = node.get("featuredImage") or {}
        if featured.get("url"):
            images.append(featured["url"])
        for edge in (node.get("images") or {}).get("edges", []):
            src = (edge.get("node") or {}).get("url")
            if src and src not in images:
                images.append(src)

        price = None
        currency = None
        price_range = node.get("priceRangeV2") or {}
        min_price = price_range.get("minVariantPrice") or {}
        if min_price.get("amount"):
            price = min_price["amount"]
            currency = min_price.get("currencyCode")

        body_html = node.get("descriptionHtml", "") or ""
        return Product(
            url=node.get("onlineStoreUrl") or url,
            handle=node.get("handle", handle),
            title=node.get("title", handle),
            description_html=body_html,
            description_text=html_to_text(body_html),
            vendor=node.get("vendor"),
            product_type=node.get("productType"),
            tags=node.get("tags") or [],
            price=str(price) if price is not None else None,
            currency=currency,
            image_urls=images,
            source="shopify",
        )
