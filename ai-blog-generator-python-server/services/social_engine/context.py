"""Per-store context loaded once per job or request."""
from __future__ import annotations

from dataclasses import dataclass

import db
from config import StoreConfig

from .errors import NotFoundError
from .http import CallContext
from .settings import EngineSettings, load_settings


@dataclass
class StoreContext:
    store_id: str
    store: dict
    store_config: StoreConfig
    settings: EngineSettings
    correlation_id: str = ""

    @property
    def storefront_url(self) -> str:
        """Public storefront origin, e.g. https://bioluxelab.com (no trailing slash)."""
        website = (self.settings.brand.website or "").strip().rstrip("/")
        if website:
            return website if website.startswith("http") else f"https://{website}"
        domain = self.store_config.custom_domain or self.store_config.myshopify_domain
        return f"https://{domain}".rstrip("/")

    def call_context(self, correlation_id: str = "") -> CallContext:
        return CallContext(store_id=self.store_id, correlation_id=correlation_id or self.correlation_id)

    def product_url(self, handle: str) -> str:
        return f"{self.storefront_url}/products/{handle}" if handle else self.storefront_url


async def load_store_context(store_id: str, *, correlation_id: str = "") -> StoreContext:
    row = await db.get_store(store_id)
    if not row:
        raise NotFoundError(f"Store '{store_id}' does not exist.")
    return StoreContext(
        store_id=store_id,
        store=row,
        store_config=StoreConfig.from_row(row),
        settings=await load_settings(store_id),
        correlation_id=correlation_id,
    )
