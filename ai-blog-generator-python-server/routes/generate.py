"""
routes/generate.py — Blog generation and main UI.

  GET  /            — index page (session-scoped store)
  POST /generate    — generate text + images, show preview/edit page
  POST /publish     — publish the (possibly edited) preview to Shopify
"""
from __future__ import annotations

import json
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import db
import shopify_client
import state
from config import StoreConfig
from security import limiter
from utils import text_to_html
from services import image_service, llm_service, logo_service
from services import internal_links
from services import social_captions
from services.quality_service import review_draft
from services import title_service
from providers import AllModelsFailedError

router = APIRouter()
logger = logging.getLogger("ai_blog_server")

_LLM_REVIEW_SYSTEM = (
        "You are a senior ecommerce SEO editor. Review drafts for usefulness, search intent, "
        "commerce fit, readability, claim safety, and brand trust. Be concise and actionable."
)
_LLM_REVIEW_PROMPT_ENDING = """Return ONLY a single valid JSON object with exactly these fields:
    "title": string - concise editorial verdict with a score, for example "Editorial Review - 82/100"
    "summary": string - 1-2 sentence overall assessment
    "keywords": array of strings - 3-6 short issue categories
    "hashtags": array of strings - empty array
    "content": string - actionable review notes in plain text. Use ## headings and - bullets. Include Strengths, Issues, and Recommended Edits. Do not rewrite the full article.

No markdown fences. No explanation. Raw JSON only."""


def _get_session_store_id(request: Request) -> str:
    return request.session.get("store_id", "")


async def _render_index(
    request: Request,
    store: dict,
    error: Optional[str] = None,
) -> HTMLResponse:
    store_id = store["id"]
    prompts = await db.get_prompts(store_id)
    default_prompt_id = await db.get_store_setting(store_id, "default_prompt_id", "")
    text_models = await db.get_active_text_models(store_id)
    return state.templates.TemplateResponse(
        request,
        "index.html",
        {
            "store": store,
            "prompts": prompts,
            "default_prompt_id": default_prompt_id,
            "text_models": text_models,
            "error": error,
        },
    )


def _default_image_labels(image_types: list[str], image_count: int) -> list[str]:
    labels: list[str] = []
    for i in range(image_count):
        img_type = image_types[i] if i < len(image_types) else ""
        if img_type == "product":
            labels.append("Product Image")
        elif img_type == "photo":
            labels.append("Photo")
        elif img_type == "infographic":
            labels.append("Infographic")
        else:
            labels.append(f"Image {i + 1}")
    return labels


def _decode_json_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


async def _render_preview(
    request: Request,
    *,
    store: dict,
    store_id: str,
    prompt_id: str,
    prompt_text: str,
    blog_handle: str,
    author: str,
    title: str,
    summary: str,
    content: str,
    keywords: list[str],
    hashtags: list[str],
    image_url_list: list[str],
    image_types: list[str],
    image_labels: Optional[list[str]] = None,
    image_display_urls: Optional[list[str]] = None,
    generated_by: str = "",
    product_url: str = "",
    product_title: str = "",
    selected_image_index: int = 0,
    preview_error: Optional[str] = None,
    quality_report: Optional[dict] = None,
    title_pool_id: int = 0,
    long_tail_keywords: Optional[list[str]] = None,
    pin_description: str = "",
) -> HTMLResponse:
    if long_tail_keywords is None:
        long_tail_keywords = []
    if image_labels is None:
        image_labels = _default_image_labels(image_types, len(image_url_list))

    if image_display_urls is None:
        logo_b64 = await db.get_store_setting(store_id, "logo_data", "")
        if product_url.strip():
            image_display_urls = []
            for url in image_url_list:
                image_display_urls.append(await logo_service.stamp_infographic(url, logo_b64))
        else:
            image_display_urls = []
            for i, url in enumerate(image_url_list):
                img_type = image_types[i] if i < len(image_types) else "photo"
                if img_type in ("photo", "hero_photo"):
                    image_display_urls.append(await logo_service.stamp_photo(url, title, logo_b64))
                else:
                    image_display_urls.append(await logo_service.stamp_infographic(url, logo_b64))

    if quality_report is None:
        quality_report = (await review_draft(
            store_id=store_id,
            title=title,
            summary=summary,
            content=content,
            keywords=keywords,
            prompt_text=prompt_text,
            product_url=product_url,
            product_title=product_title,
            image_count=len(image_url_list),
        )).as_dict()

    text_models = await db.get_active_text_models(store_id)

    return state.templates.TemplateResponse(
        request,
        "preview.html",
        {
            "store": store,
            "store_id": store_id,
            "prompt_id": prompt_id,
            "prompt_text": prompt_text,
            "blog_handle": blog_handle,
            "author": author,
            "title": title,
            "summary": summary,
            "content": content,
            "keywords": json.dumps(keywords),
            "hashtags": json.dumps(hashtags),
            "long_tail_keywords": json.dumps(long_tail_keywords),
            "pin_description": pin_description,
            "image_urls": json.dumps(image_url_list),
            "image_display_urls": image_display_urls,
            "image_types": json.dumps(image_types),
            "image_labels": image_labels,
            "generated_by": generated_by,
            "product_url": product_url,
            "product_title": product_title,
            "selected_image_index": selected_image_index,
            "preview_error": preview_error,
            "quality_report": quality_report,
            "text_models": text_models,
            "title_pool_id": title_pool_id,
        },
    )


@router.post("/quality/review")
async def quality_review(
    request: Request,
    title: Annotated[str, Form()] = "",
    summary: Annotated[str, Form()] = "",
    content: Annotated[str, Form()] = "",
    prompt_text: Annotated[str, Form()] = "",
    keywords_json: Annotated[str, Form()] = "[]",
    image_urls_json: Annotated[str, Form()] = "[]",
    product_url: Annotated[str, Form()] = "",
    product_title: Annotated[str, Form()] = "",
):
    store_id = _get_session_store_id(request)
    if not store_id or store_id == "__admin__":
        return JSONResponse({"ok": False, "error": "No store session"}, status_code=401)

    keywords = _decode_json_list(keywords_json)
    image_count = len(_decode_json_list(image_urls_json))
    report = await review_draft(
        store_id=store_id,
        title=title,
        summary=summary,
        content=content,
        keywords=keywords,
        prompt_text=prompt_text,
        product_url=product_url,
        product_title=product_title,
        image_count=image_count,
    )
    return JSONResponse({"ok": True, "quality_report": report.as_dict()})


@router.post("/quality/llm-review")
async def llm_quality_review(
    request: Request,
    title: Annotated[str, Form()] = "",
    summary: Annotated[str, Form()] = "",
    content: Annotated[str, Form()] = "",
    prompt_text: Annotated[str, Form()] = "",
    keywords_json: Annotated[str, Form()] = "[]",
    product_url: Annotated[str, Form()] = "",
    product_title: Annotated[str, Form()] = "",
    model_id: Annotated[str, Form()] = "",
):
    store_id = _get_session_store_id(request)
    if not store_id or store_id == "__admin__":
        return JSONResponse({"ok": False, "error": "No store session"}, status_code=401)

    keywords = _decode_json_list(keywords_json)
    review_prompt = f"""
Review this generated ecommerce blog draft before publishing.

Original prompt:
{prompt_text.strip() or "(not provided)"}

Target SEO keywords:
{', '.join(keywords) if keywords else "(not provided)"}

Related product title:
{product_title.strip() or "(not provided)"}

Related product URL:
{product_url.strip() or "(not provided)"}

Draft title:
{title.strip()}

Draft summary:
{summary.strip()}

Draft content:
{content.strip()}

Review for prompt relevance, search intent, SEO completeness, product fit, readability, trust and claim safety, AI artifacts, and concrete edit recommendations.
""".strip()

    try:
        review = await llm_service.generate_text(
            store_id,
            review_prompt,
            system_prompt=_LLM_REVIEW_SYSTEM,
            model_id=model_id.strip() or None,
            prompt_ending_override=_LLM_REVIEW_PROMPT_ENDING,
        )
    except AllModelsFailedError as exc:
        return JSONResponse({"ok": False, "error": f"LLM review failed: {exc}"}, status_code=502)
    except Exception as exc:
        logger.exception("Unexpected error during LLM quality review")
        return JSONResponse({"ok": False, "error": f"Unexpected error: {exc}"}, status_code=500)

    return JSONResponse({
        "ok": True,
        "review": {
            "title": review.get("title", "Editorial Review"),
            "summary": review.get("summary", ""),
            "content": review.get("content", ""),
            "keywords": review.get("keywords", []),
            "model": review.get("_model_name", ""),
            "provider": review.get("_model_provider", ""),
        },
    })


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    store_id = _get_session_store_id(request)
    if store_id == "__admin__":
        return RedirectResponse("/setup", status_code=303)
    store = await db.get_store(store_id)
    if not store:
        return RedirectResponse("/logout", status_code=303)
    return await _render_index(request, store)


@router.post("/generate", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def generate(
    request: Request,
    prompt_id: Annotated[str, Form()],
    custom_prompt: Annotated[str, Form()] = "",
    blog_handle: Annotated[str, Form()] = "",
    author_name: Annotated[str, Form()] = "",
    model_id: Annotated[str, Form()] = "",
    product_url: Annotated[str, Form()] = "",
):
    store_id = _get_session_store_id(request)
    store = await db.get_store(store_id)
    if not store:
        return RedirectResponse("/logout", status_code=303)

    # Resolve prompt text
    if prompt_id == "custom":
        prompt_text = custom_prompt.strip()
        if not prompt_text:
            return await _render_index(request, store, error="Please enter a custom prompt.")
    else:
        prompts = await db.get_prompts(store_id)
        prompt_cfg = next((p for p in prompts if p["id"] == prompt_id), None)
        if not prompt_cfg:
            return await _render_index(request, store, error=f"Unknown prompt: {prompt_id}")
        extra = custom_prompt.strip()
        prompt_text = f"{prompt_cfg['text']}\n\n{extra}" if extra else prompt_cfg["text"]

    resolved_blog_handle = blog_handle.strip() or store["default_blog_handle"]
    resolved_author = author_name.strip() or store["default_author"]
    resolved_product_url = product_url.strip()
    product_title = ""

    # If a product was selected, build StoreConfig once and fetch product details
    # so the LLM gets accurate context (title, description, tags) rather than just a URL.
    if resolved_product_url:
        product_handle_pre = resolved_product_url.rstrip("/").split("/")[-1]
        store_cfg = StoreConfig(
            id=store["id"], name=store["name"],
            myshopify_domain=store["myshopify_domain"],
            custom_domain=store.get("custom_domain", ""),
            client_id=store["client_id"], client_secret=store["client_secret"],
            default_blog_handle=store.get("default_blog_handle", "news"),
            default_author=store.get("default_author", "Store Team"),
        )
        product_details = await shopify_client.fetch_product_details(store_cfg, product_handle_pre)
        if product_details:
            import re as _re_html
            desc = _re_html.sub(r"<[^>]+>", " ", product_details["description"]).strip()
            # Cap description to avoid bloating the prompt
            desc = " ".join(desc.split())[:600]
            product_title = product_details["title"]
            product_info = (
                f"Product name: {product_title}\n"
                f"Product URL: {resolved_product_url}"
            )
            if desc:
                product_info += f"\nProduct description: {desc}"
            if product_details["tags"]:
                product_info += f"\nProduct tags/categories: {product_details['tags']}"
            prompt_text = (
                f"{prompt_text}\n\nWrite this blog post specifically about the following "
                f"product from {store['name']}:\n{product_info}"
            )
        else:
            # Fallback: just include the URL
            prompt_text = (
                f"{prompt_text}\n\nWrite this blog post specifically about the following "
                f"product: {resolved_product_url}"
            )

    logger.info("Generating blog | store=%s prompt_id=%s", store_id, prompt_id)

    # --- Title pool injection (non-product blogs only) ---
    title_pool_id = 0
    if not resolved_product_url:
        title_row = await title_service.pop_blog_title(store_id)
        if title_row:
            title_pool_id = title_row["id"]
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
        else:
            # No title pool entry — try the dedicated keyword pool for a focus keyword
            kw_row = await db.pop_keyword(store_id)
            if kw_row:
                kw = kw_row["keyword"]
                kw_block = f"\n\nFocus keyword for this article: {kw}"
                kw_content = kw_row.get("content", "").strip()
                if kw_content:
                    kw_block += (
                        f"\n\nWhat people are currently discussing about this topic "
                        f"(use as context, do not quote directly):\n{kw_content[:600]}"
                    )
                prompt_text = f"{prompt_text}{kw_block}"
                logger.info("Using pooled keyword %r for store %s", kw, store_id)

    try:
        blog_data = await llm_service.generate_text(store_id, prompt_text, model_id=model_id or None)
    except AllModelsFailedError as exc:
        logger.error("Text generation failed: %s", exc)
        return await _render_index(request, store, error=f"Blog generation failed: {exc}")
    except Exception as exc:
        logger.exception("Unexpected error during text generation")
        return await _render_index(request, store, error=f"Unexpected error: {exc}")

    title = blog_data["title"]
    summary = blog_data["summary"]
    content = blog_data["content"]

    # Strip leading title heading from content if the LLM included it.
    # Handles Markdown (# / ## Title), HTML (<h1>...</h1>), and bold (**Title**).
    import re as _re
    content = _re.sub(
        r"^\s*(?:<h[12][^>]*>.*?</h[12]>|#{1,2}\s+[^\n]+|\*\*[^\n]+\*\*)\s*\n?",
        "",
        content,
        count=1,
        flags=_re.IGNORECASE | _re.DOTALL,
    ).lstrip()
    keywords = blog_data.get("keywords", [])
    hashtags = blog_data.get("hashtags", [])
    long_tail_keywords = blog_data.get("long_tail_keywords", [])
    pin_description = blog_data.get("pin_description", "")
    generated_by = blog_data.get("_model_name", "")

    logo_b64 = await db.get_store_setting(store_id, "logo_data", "")

    if resolved_product_url:
        # Use the product's main image — no AI generation, no title overlay
        product_handle = resolved_product_url.rstrip("/").split("/")[-1]
        logger.info("Product selected | url=%s handle=%s", resolved_product_url, product_handle)
        # Use the CDN URL (small) in the form field so the hidden input stays manageable.
        # The data URI path used previously caused multi-hundred-KB hidden inputs, which
        # could break JSON parsing in JS and hit form-size limits on publish.
        product_image_cdn = await shopify_client.fetch_product_image_url(store_cfg, product_handle)
        if product_image_cdn:
            logger.info("Product image CDN URL: %s", product_image_cdn[:80])
            image_url_list = [product_image_cdn]  # CDN URL — small, safe in form
            image_types = ["product"]
            image_labels = ["Product Image"]
            # Stamp with logo badge for preview; _fetch_bytes handles public HTTPS URLs
            display_urls = [await logo_service.stamp_infographic(product_image_cdn, logo_b64)]
        else:
            logger.warning("No product image found for handle=%s — no image will be used", product_handle)
            image_url_list = []
            image_types = []
            image_labels = []
            display_urls = []
    else:
        image_url_list, image_types, image_labels = await image_service.generate_typed_images(
            store_id, title, summary, prompt_text
        )
        # Composite images for preview display (title bar + logo). Raw URLs stay
        # in the form so we can re-composite at publish time for Shopify upload.
        display_urls = []
        for i, url in enumerate(image_url_list):
            if image_types[i] in ("photo", "hero_photo"):
                display_urls.append(await logo_service.stamp_photo(url, title, logo_b64))
            else:
                display_urls.append(await logo_service.stamp_infographic(url, logo_b64))

    return await _render_preview(
        request,
        store=store,
        store_id=store_id,
        prompt_id=prompt_id,
        prompt_text=prompt_text,
        blog_handle=resolved_blog_handle,
        author=resolved_author,
        title=title,
        summary=summary,
        content=content,
        keywords=keywords,
        hashtags=hashtags,
        image_url_list=image_url_list,
        image_types=image_types,
        image_labels=image_labels,
        image_display_urls=display_urls,
        generated_by=generated_by,
        product_url=resolved_product_url,
        product_title=product_title,
        title_pool_id=title_pool_id,
        long_tail_keywords=long_tail_keywords,
        pin_description=pin_description,
    )


@router.post("/publish", response_class=HTMLResponse)
async def publish(
    request: Request,
    prompt_id: Annotated[str, Form()],
    prompt_text: Annotated[str, Form()],
    blog_handle: Annotated[str, Form()],
    author: Annotated[str, Form()],
    title: Annotated[str, Form()],
    summary: Annotated[str, Form()],
    content: Annotated[str, Form()],
    keywords_json: Annotated[str, Form()] = "[]",
    hashtags_json: Annotated[str, Form()] = "[]",
    long_tail_keywords_json: Annotated[str, Form()] = "[]",
    pin_description: Annotated[str, Form()] = "",
    image_urls_json: Annotated[str, Form()] = "[]",
    image_types_json: Annotated[str, Form()] = "[]",
    selected_image_index: Annotated[int, Form()] = 0,
    product_url: Annotated[str, Form()] = "",
    product_title: Annotated[str, Form()] = "",
    title_pool_id: Annotated[int, Form()] = 0,
):
    store_id = _get_session_store_id(request)
    store_row = await db.get_store(store_id)
    if not store_row:
        return RedirectResponse("/logout", status_code=303)

    try:
        keywords: list[str] = json.loads(keywords_json)
        hashtags: list[str] = json.loads(hashtags_json)
        long_tail_keywords: list[str] = json.loads(long_tail_keywords_json)
        image_url_list: list[str] = json.loads(image_urls_json)
        image_types: list[str] = json.loads(image_types_json)
    except json.JSONDecodeError:
        keywords, hashtags, image_url_list, image_types = [], [], [], []
        long_tail_keywords = []

    quality_report = await review_draft(
        store_id=store_id,
        title=title,
        summary=summary,
        content=content,
        keywords=keywords,
        prompt_text=prompt_text,
        product_url=product_url,
        product_title=product_title,
        image_count=len(image_url_list),
    )
    if quality_report.publish_blocked:
        logger.info("Publish blocked by local quality checks | store=%s score=%s", store_id, quality_report.score)
        return await _render_preview(
            request,
            store=store_row,
            store_id=store_id,
            prompt_id=prompt_id,
            prompt_text=prompt_text,
            blog_handle=blog_handle,
            author=author,
            title=title,
            summary=summary,
            content=content,
            keywords=keywords,
            hashtags=hashtags,
            image_url_list=image_url_list,
            image_types=image_types,
            generated_by="",
            product_url=product_url,
            product_title=product_title,
            selected_image_index=selected_image_index,
            preview_error="Quality checks blocked publishing. Improve the failed items below and try again.",
            quality_report=quality_report.as_dict(),
            long_tail_keywords=long_tail_keywords,
            pin_description=pin_description,
        )

    # Re-composite images for Shopify upload (skip for product images — use raw URL as-is)
    logo_b64 = await db.get_store_setting(store_id, "logo_data", "")
    resolved_product_url = product_url.strip()

    if resolved_product_url:
        # Stamp product images with logo before uploading (CDN URLs are passed
        # through the form — stamp here so the published article gets the logo badge)
        composited = []
        for url in image_url_list:
            composited.append(await logo_service.stamp_infographic(url, logo_b64))
    else:
        composited = []
        for i, url in enumerate(image_url_list):
            img_type = image_types[i] if i < len(image_types) else "photo"
            if img_type in ("photo", "hero_photo"):
                composited.append(await logo_service.stamp_photo(url, title, logo_b64))
            else:
                composited.append(await logo_service.stamp_infographic(url, logo_b64))

    # Reorder so the user-selected image is first (used as Shopify featured image)
    if composited and 0 < selected_image_index < len(composited):
        composited = (
            [composited[selected_image_index]]
            + composited[:selected_image_index]
            + composited[selected_image_index + 1 :]
        )
    image_url_list = composited

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

    content_html = text_to_html(content)

    # If this blog was written for a specific product, append a shop link
    if resolved_product_url:
        cta_label = f"Shop {product_title.strip()}" if product_title.strip() else "Shop this product"
        content_html += (
            f'\n<p><a href="{resolved_product_url}" target="_blank" rel="noopener">'
            f"{cta_label}</a></p>"
        )

    # Internal links (other blog posts + store products)
    try:
        related_links = await internal_links.build_internal_links(
            store,
            store_id,
            title=title,
            keywords=keywords,
            long_tail_keywords=long_tail_keywords,
            current_url="",
            max_links=4,
        )
        related_block = internal_links.render_related_block(related_links)
        if related_block:
            content_html += "\n" + related_block
    except Exception as exc:  # noqa: BLE001 — best-effort, never block publish
        logger.warning("Internal link build failed for store %s: %s", store_id, exc)

    # Vertical Pinterest pin image (best-effort)
    pin_image_url = ""
    if image_url_list:
        try:
            pin_image_url = await logo_service.stamp_pin(image_url_list[0], title, logo_b64)
        except Exception as exc:  # noqa: BLE001 — pin is optional
            logger.warning("Pin image build failed for store %s: %s", store_id, exc)

    try:
        result = await shopify_client.publish_article(
            store=store,
            blog_handle=blog_handle,
            title=title,
            content_html=content_html,
            summary=summary,
            keywords=keywords,
            hashtags=hashtags,
            author=author,
            image_url_list=image_url_list,
            product_url=resolved_product_url,
            product_title=product_title.strip(),
            long_tail_keywords=long_tail_keywords,
            pin_description=pin_description,
            pin_image_url=pin_image_url,
        )
    except shopify_client.ShopifyError as exc:
        logger.error("Shopify publish failed: %s", exc)
        store_dict = store_row
        return await _render_index(
            request, store_dict, error=f"Could not publish to Shopify: {exc}"
        )

    logger.info("Published | article_id=%s url=%s", result.article_id, result.article_url)

    try:
        social_share_buttons = json.loads(
            await db.get_store_setting(store_id, "social_share_buttons", '["x","facebook","linkedin"]')
        )
    except Exception:
        social_share_buttons = ["x", "facebook", "linkedin"]
    social_x_handle = await db.get_store_setting(store_id, "social_x_handle", "")
    social_facebook_url = await db.get_store_setting(store_id, "social_facebook_url", "")
    social_linkedin_url = await db.get_store_setting(store_id, "social_linkedin_url", "")

    await db.log_generation(
        store_id=store_id,
        store_name=store_row["name"],
        blog_handle=blog_handle,
        prompt_id=prompt_id,
        prompt_text=prompt_text,
        title=title,
        summary=summary,
        keywords=keywords,
        hashtags=hashtags,
        image_count=len(image_url_list),
        article_id=str(result.article_id),
        article_url=result.article_url,
        status="published",
    )

    if title_pool_id and not resolved_product_url:
        await db.mark_title_published(title_pool_id)

    share_captions = social_captions.build_captions(
        title=title,
        summary=summary,
        keywords=keywords,
        hashtags=hashtags,
        long_tail_keywords=long_tail_keywords,
        article_url=result.article_url,
        pin_description=pin_description,
    )

    return state.templates.TemplateResponse(
        request,
        "result.html",
        {
            "store": store,
            "result": result,
            "title": title,
            "summary": summary,
            "keywords": keywords,
            "hashtags": hashtags,
            "image_count": len(image_url_list),
            "product_url": resolved_product_url,
            "product_title": product_title.strip(),
            "social_share_buttons": social_share_buttons,
            "pin_description": pin_description,
            "share_captions": share_captions,
            "social_x_handle": social_x_handle,
            "social_facebook_url": social_facebook_url,
            "social_linkedin_url": social_linkedin_url,
        },
    )
