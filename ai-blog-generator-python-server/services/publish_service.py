"""services/publish_service.py — Full blog generation + publish pipeline.
Used by scheduler.py and by routes/generate.py (publish step).
"""
from __future__ import annotations

import logging
import re as _re
from dataclasses import dataclass
from typing import Optional

import db
import shopify_client
from config import StoreConfig
from utils import text_to_html

from . import image_service, llm_service, logo_service, title_service
from .quality_service import QualityGateError, review_draft

logger = logging.getLogger("ai_blog_server")


@dataclass
class PipelineResult:
    article_id: Optional[str]
    article_url: Optional[str]
    title: str
    summary: str
    keywords: list[str]
    hashtags: list[str]
    image_count: int


async def run(
    store_id: str,
    prompt_text: str,
    blog_handle: str,
    author: str,
    prompt_id: str = "",
    system_prompt: str = "",
    product_url: str = "",
    product_title: str = "",
    scheduled_job_id: str = "",
) -> PipelineResult:
    """Full pipeline: generate text → generate images → publish → log.

    If product_url is provided the blog will be written specifically about that
    product, the product's image will be used (no AI images), and a shop CTA
    is appended to the content.
    """
    store_row = await db.get_store(store_id)
    if not store_row:
        raise ValueError(f"Store not found: {store_id}")

    store = StoreConfig(
        id=store_row["id"],
        name=store_row["name"],
        myshopify_domain=store_row["myshopify_domain"],
        custom_domain=store_row.get("custom_domain", ""),
        client_id=store_row["client_id"],
        client_secret=store_row["client_secret"],
        default_blog_handle=store_row.get("default_blog_handle", "news"),
        default_author=store_row.get("default_author", "Store Team"),
    )

    # --- Enrich prompt with product details if a product was selected ---
    resolved_product_url = product_url.strip()
    resolved_product_title = product_title.strip()
    if resolved_product_url:
        product_handle = resolved_product_url.rstrip("/").split("/")[-1]
        details = await shopify_client.fetch_product_details(store, product_handle)
        if details:
            desc = _re.sub(r"<[^>]+>", " ", details["description"]).strip()
            desc = " ".join(desc.split())[:600]
            resolved_product_title = resolved_product_title or details["title"]
            product_info = (
                f"Product name: {details['title']}\n"
                f"Product URL: {resolved_product_url}"
            )
            if desc:
                product_info += f"\nProduct description: {desc}"
            if details["tags"]:
                product_info += f"\nProduct tags/categories: {details['tags']}"
            prompt_text = (
                f"{prompt_text}\n\nWrite this blog post specifically about the following "
                f"product from {store.name}:\n{product_info}"
            )
        else:
            prompt_text = (
                f"{prompt_text}\n\nWrite this blog post specifically about the following "
                f"product: {resolved_product_url}"
            )

    # --- Pop a pre-generated blog title (non-product posts only) ---
    title_row = None
    if not resolved_product_url:
        title_row = await title_service.pop_blog_title(store_id)
        if title_row:
            title_inject = (
                f"\n\nIMPORTANT — You MUST use exactly this title for the blog post: {title_row['title']}"
            )
            if title_row.get("keyword"):
                title_inject += f"\nFocus keyword: {title_row['keyword']}"
            if title_row.get("meta_description"):
                title_inject += (
                    f"\nUse this exact text as the article summary/meta description: "
                    f"{title_row['meta_description']}"
                )
            prompt_text = f"{prompt_text}{title_inject}"
            logger.info("Using pooled blog title %r for store %s", title_row["title"], store_id)

    # --- Text generation (raises AllModelsFailedError on total failure) ---
    blog_data = await llm_service.generate_text(store_id, prompt_text, system_prompt)
    title = blog_data["title"]
    summary = blog_data["summary"]
    content = blog_data["content"]
    keywords = blog_data.get("keywords", [])
    hashtags = blog_data.get("hashtags", [])

    # --- Image generation ---
    if resolved_product_url:
        # Use the product's own image; add logo badge only (no title bar)
        logo_b64 = await db.get_store_setting(store_id, "logo_data", "")
        product_handle = resolved_product_url.rstrip("/").split("/")[-1]
        data_uri = await shopify_client.fetch_product_image_data_uri(store, product_handle)
        if data_uri:
            stamped = await logo_service.stamp_infographic(data_uri, logo_b64)
            image_urls = [stamped]
        else:
            image_urls = []
    else:
        image_urls = await image_service.generate_images(store_id, title, summary, prompt_text)

    quality_report = await review_draft(
        store_id=store_id,
        title=title,
        summary=summary,
        content=content,
        keywords=keywords,
        prompt_text=prompt_text,
        product_url=resolved_product_url,
        product_title=resolved_product_title,
        image_count=len(image_urls),
    )
    if quality_report.publish_blocked:
        await db.log_generation(
            store_id=store_id,
            store_name=store.name,
            blog_handle=blog_handle or store.default_blog_handle,
            prompt_id=prompt_id,
            prompt_text=prompt_text,
            title=title,
            summary=summary,
            content_text=content,
            keywords=keywords,
            hashtags=hashtags,
            image_count=len(image_urls),
            article_id=None,
            article_url=None,
            status="blocked_quality",
            scheduled_job_id=scheduled_job_id or None,
        )
        logger.warning(
            "Pipeline blocked by quality checks | store=%s title=%r score=%s duplicate=%r",
            store_id,
            title,
            quality_report.score,
            quality_report.duplicate_title,
        )
        raise QualityGateError(quality_report)

    # --- Build article HTML ---
    content_html = text_to_html(content)
    if resolved_product_url:
        cta_label = f"Shop {resolved_product_title}" if resolved_product_title else "Shop this product"
        content_html += (
            f'\n<p><a href="{resolved_product_url}" target="_blank" rel="noopener">'
            f"{cta_label}</a></p>"
        )

    # --- Publish to Shopify ---
    result = await shopify_client.publish_article(
        store=store,
        blog_handle=blog_handle or store.default_blog_handle,
        title=title,
        content_html=content_html,
        summary=summary,
        keywords=keywords,
        hashtags=hashtags,
        author=author or store.default_author,
        image_url_list=image_urls,
        product_url=resolved_product_url,
        product_title=resolved_product_title,
    )

    # --- Log to DB ---
    await db.log_generation(
        store_id=store_id,
        store_name=store.name,
        blog_handle=result.blog_handle,
        prompt_id=prompt_id,
        prompt_text=prompt_text,
        title=title,
        summary=summary,
        content_text=content,
        keywords=keywords,
        hashtags=hashtags,
        image_count=len(image_urls),
        article_id=str(result.article_id),
        article_url=result.article_url,
        status="published",
        scheduled_job_id=scheduled_job_id or None,
    )

    # --- Mark title pool entry as published ---
    if title_row:
        await db.mark_title_published(title_row["id"])

    logger.info(
        "Pipeline complete | store=%s title=%r article_id=%s",
        store_id, title, result.article_id,
    )

    return PipelineResult(
        article_id=str(result.article_id),
        article_url=result.article_url,
        title=title,
        summary=summary,
        keywords=keywords,
        hashtags=hashtags,
        image_count=len(image_urls),
    )
