"""Centralised configuration loaded from the environment / .env file.

Nothing else in the package reads ``os.environ`` directly; everything goes
through :class:`Settings` so configuration stays discoverable and testable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:  # optional dependency, but present in requirements
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - dotenv is optional at runtime
    load_dotenv = None  # type: ignore


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

def _get(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value

@dataclass
class Settings:
    # --- Grok / xAI (used by the optional LLM prompt backend) ---
    grok_api_key: Optional[str] = None
    grok_base_url: str = "https://api.x.ai/v1"
    grok_model: str = "grok-4.5"
    grok_image_model: str = "grok-imagine-image"
    grok_image_quality_model: str = "grok-imagine-image-quality"

    # --- Shopify Admin API (used by the shopify fetcher) ---
    myshopify_domain: Optional[str] = None
    shopify_api_version: str = "2026-01"
    shopify_access_token: Optional[str] = None
    shopify_client_id: Optional[str] = None
    shopify_client_secret: Optional[str] = None

    # --- Shopify Storefront API (optional alternative) ---
    storefront_domain: Optional[str] = None
    storefront_access_token: Optional[str] = None

    # --- Pipeline behaviour ---
    request_timeout: int = 30
    grok_timeout: int = 300
    max_retries: int = 3
    user_agent: str = (
        "Mozilla/5.0 (compatible; ProductPromptBot/1.0; +https://bioluxelab.com)"
    )

    # --- Paths ---
    project_root: Path = field(default=PROJECT_ROOT)
    product_list: Path = field(default=PROJECT_ROOT / "services" / "landing_pages" / "product.list")
    concepts_list: Path = field(default=PROJECT_ROOT / "services" / "landing_pages" / "creative_concepts.list")
    campaign_file: Path = field(default=PROJECT_ROOT / "services" / "landing_pages" / "campaign.txt")
    output_dir: Path = field(default=PROJECT_ROOT / "data" / "landing_pages_output")
    image_resolution: int = 1024

    @classmethod
    def load(cls, env_file: Optional[Path] = None) -> "Settings":
        """Build settings from the environment, loading ``.env`` first."""
        if load_dotenv is not None:
            dotenv_path = env_file or (PROJECT_ROOT / ".env")
            if dotenv_path.exists():
                load_dotenv(dotenv_path)

        return cls(
            grok_api_key=_get("GROK_API_KEY"),
            grok_base_url=_get("GROK_BASE_URL", "https://api.x.ai/v1"),
            grok_model=_get("GROK_MODEL", "grok-4.5"),
            grok_image_model=_get("GROK_IMAGE_MODEL", "grok-imagine-image"),
            grok_image_quality_model=_get(
                "GROK_IMAGE_QUALITY_MODEL", "grok-imagine-image-quality"
            ),
            myshopify_domain=_get("MYSHOPIFY_DOMAIN"),
            shopify_api_version=_get("SHOPIFY_API_VERSION", "2026-01"),
            shopify_access_token=_get("SHOPIFY_ACCESS_TOKEN"),
            shopify_client_id=_get("SHOPIFY_CLIENT_ID"),
            shopify_client_secret=_get("SHOPIFY_CLIENT_SECRET"),
            storefront_domain=_get("SHOPIFY_STOREFRONT_DOMAIN"),
            storefront_access_token=_get("SHOPIFY_STOREFRONT_ACCESS_TOKEN"),
            request_timeout=int(_get("REQUEST_TIMEOUT", "30")),
            grok_timeout=int(_get("GROK_TIMEOUT", "300")),
            max_retries=int(_get("MAX_RETRIES", "3")),
        )
