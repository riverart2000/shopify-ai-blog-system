import os
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import db
from providers import ModelRecord

# Note: We're going to import from services.landing_pages
from services.landing_pages.product_prompts.config import Settings
from services.landing_pages.social_publisher.pipeline import SocialPublisher
from services.landing_pages.social_publisher.landing_page import LandingPagePublisher
from services.landing_pages.social_publisher.rss_feed import read_product_section
from services.landing_pages.video_service import LandingPageVideoService
from services.landing_pages.product_prompts.utils import build_session, slugify
from services.landing_page_jobs import create_job, get_job, run_job
from services.xai_billing_service import check_xai_credit

router = APIRouter(
    prefix="/api/landing-pages",
    tags=["landing_pages"],
)
logger = logging.getLogger("landing_pages")

class GeneratePromptsRequest(BaseModel):
    product_url: str
    shop: str = ""
    fetcher: str = "shopify"
    generator: str = "grok"

class GenerateSocialRequest(BaseModel):
    handle: str
    backend: str = "grok"
    quality: bool = False
    variations: int = 1
    concept_filter: Optional[List[str]] = None
    overwrite: bool = False

class PublishLandingPageRequest(BaseModel):
    handle: str
    published: bool = False
    concept_filter: Optional[List[str]] = None

class VideoScriptRequest(BaseModel):
    handle: str
    concept: str
    shop: str = ""

class GenerateVideoRequest(BaseModel):
    handle: str
    concept: str
    shop: str = ""

class UpdateVideoRequest(BaseModel):
    script: Optional[dict] = None
    posting_text: Optional[str] = None
    approved: Optional[bool] = None

def get_settings():
    settings = Settings.load()
    return settings


def _product_json_path(settings: Settings, handle: str) -> Path:
    return settings.output_dir / f"{slugify(handle)}.json"


def _read_product_json(settings: Settings, handle: str) -> tuple[Path, dict]:
    json_path = _product_json_path(settings, handle)
    if not json_path.exists():
        raise FileNotFoundError(f"No landing-page product data found for: {handle}")
    return json_path, json.loads(json_path.read_text(encoding="utf-8"))


def _write_product_json(json_path: Path, data: dict) -> None:
    temporary_path = json_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temporary_path.replace(json_path)


def _find_creative_concept(data: dict, concept_slug: str) -> dict:
    base_slug = re.sub(r"_v\d+$", "", concept_slug)
    concepts = data.get("creative_concepts") or data.get("concepts") or []
    for concept in concepts:
        if slugify(str(concept.get("concept") or "")) == base_slug:
            return concept
    raise ValueError(f"Creative concept '{concept_slug}' is not present in the product data.")


def _find_social_image(social_dir: Path, handle: str, concept: str) -> Optional[Path]:
    for extension in (".jpg", ".jpeg", ".png", ".webp"):
        candidate = social_dir / f"{handle}__{concept}{extension}"
        if candidate.exists():
            return candidate
    return None


def _apply_store_grok_model(settings: Settings, row: dict) -> bool:
    """Apply a store's existing xAI text-model record to landing pages."""
    model = ModelRecord.from_dict(row)
    api_key = model.resolved_api_key
    if not api_key:
        return False

    endpoint = (model.endpoint or "https://api.x.ai").rstrip("/")
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if endpoint.endswith(suffix):
            endpoint = endpoint[: -len(suffix)]
            break
    if endpoint == "https://api.x.ai":
        endpoint += "/v1"

    settings.grok_api_key = api_key
    settings.grok_base_url = endpoint
    configured_name = (model.model_name or "").strip()
    settings.grok_model = (
        "grok-4.3" if configured_name in ("", "grok-latest", "grok-4.3-latest")
        else configured_name
    )
    timeout = model.extra.get("timeout")
    if timeout:
        settings.grok_timeout = int(timeout)
    return True


async def _configure_store_grok(settings: Settings, shop: str) -> bool:
    """Reuse the Grok credentials already configured in the main blog system."""
    store = await db.get_store_by_domain(shop)
    if not store:
        return False
    rows = await db.get_active_text_models(store["id"])
    for row in rows:
        model_name = str(row.get("model_name") or "").lower()
        endpoint = str(row.get("endpoint") or "").lower()
        if "grok" in model_name or "api.x.ai" in endpoint:
            return _apply_store_grok_model(settings, row)
    return False


def _configure_store_shopify(settings: Settings, store: dict) -> None:
    """Use the selected store's Admin API credentials for evidence fetching."""
    settings.myshopify_domain = str(store.get("myshopify_domain") or "").strip()
    settings.shopify_client_id = str(store.get("client_id") or "").strip() or None
    settings.shopify_client_secret = str(store.get("client_secret") or "").strip() or None

def landing_pages_rss_url() -> str:
    configured = (os.environ.get("LANDING_PAGES_RSS_URL") or "").strip()
    if configured:
        return configured
    app_url = (os.environ.get("SHOPIFY_REACT_APP_URL") or "").strip().rstrip("/")
    if app_url:
        return f"{app_url}/publar/rss-landingpages"
    return "/publar/rss-landingpages"


def landing_page_product_summary(json_file: Path, data: dict) -> dict:
    product = data.get("product", {}) if isinstance(data.get("product"), dict) else {}
    concepts = data.get("creative_concepts")
    if not isinstance(concepts, list):
        concepts = data.get("concepts", [])
    assets = data.get("assets", [])
    publication = data.get("landing_page_publication", {})
    if not isinstance(publication, dict):
        publication = {}
    page = publication.get("page", {})
    if not isinstance(page, dict):
        page = {}

    # Use the original Shopify handle for registry matching. The JSON filename
    # can be truncated to 80 characters for filesystem safety.
    return {
        "handle": product.get("handle") or json_file.stem,
        "storage_handle": json_file.stem,
        "title": product.get("title", ""),
        "url": product.get("url", ""),
        "concepts_generated": len(concepts),
        "images": len(assets) if isinstance(assets, list) else 0,
        "landing_page": {
            "url": page.get("url") or "",
            "handle": page.get("handle") or "",
            "title": page.get("title") or "",
            "action": page.get("action") or "",
            "published_at": publication.get("published_at") or "",
        },
    }

@router.get("/products")
async def list_products():
    """
    List all generated products by scanning the output_dir.
    """
    settings = get_settings()
    output_dir = settings.output_dir
    products = []
    
    if output_dir.exists():
        for json_file in output_dir.glob("*.json"):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                products.append(landing_page_product_summary(json_file, data))
            except Exception as e:
                pass
    return {"products": products}

@router.get("/credits")
async def landing_page_credits(shop: str = ""):
    """Read xAI prepaid balance before any paid generation is attempted."""
    credit = await run_in_threadpool(check_xai_credit)
    return {"success": True, "shop": shop, "credit": credit}


@router.post("/generate-prompts")
async def generate_prompts(
    req: GeneratePromptsRequest, background_tasks: BackgroundTasks
):
    """Validate credit, then enqueue one persistent no-retry generation job."""
    settings = get_settings()
    generator = req.generator.lower()
    store = await db.get_store_by_domain(req.shop)
    if not store:
        return {
            "success": False,
            "accepted": False,
            "error": (
                f"Store configuration was not found for '{req.shop}'. "
                "No generation request was sent."
            ),
        }
    _configure_store_shopify(settings, store)
    if generator not in ("grok", "xai", "llm"):
        return {
            "success": False,
            "accepted": False,
            "error": (
                f"Background landing-page generation only accepts Grok, not "
                f"'{req.generator}'. No request was sent."
            ),
        }

    configured = bool(settings.grok_api_key)
    if not configured:
        configured = await _configure_store_grok(settings, req.shop)
    if not configured:
        return {
            "success": False,
            "accepted": False,
            "error": (
                "No active xAI/Grok inference model with an API key is configured "
                "for this store. No generation request was sent."
            ),
        }

    credit = await run_in_threadpool(check_xai_credit)
    if not credit["can_start"]:
        return {
            "success": False,
            "accepted": False,
            "credit": credit,
            "error": credit["message"],
        }

    job, created = await run_in_threadpool(
        lambda: create_job(
            shop=req.shop,
            store_id=store["id"],
            product_url=req.product_url,
            credit=credit,
        )
    )
    if created:
        background_tasks.add_task(
            run_job, job["id"], settings, req.fetcher, req.generator
        )
    return {
        "success": True,
        "accepted": True,
        "created": created,
        "credit": credit,
        "job": job,
    }


@router.get("/generate-prompts/jobs/{job_id}")
async def generation_job_status(job_id: str):
    """Poll persisted progress or the exact terminal failure."""
    job = await run_in_threadpool(get_job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Generation job '{job_id}' was not found.")
    return {"success": True, "job": job}

@router.post("/generate-social")
async def generate_social(req: GenerateSocialRequest):
    """Run generate_social.py logic for a product handle."""
    settings = get_settings()
    from services.landing_pages.product_prompts.utils import slugify
    safe_handle = slugify(req.handle)
    
    def run_publisher():
        publisher = SocialPublisher(
            settings,
            backend_name=req.backend,
            quality=req.quality,
            variations=req.variations,
            concept_filter=req.concept_filter,
            overwrite=req.overwrite
        )
        output_dir = settings.project_root / "social"
        output_dir.mkdir(parents=True, exist_ok=True)
        produced = []
        
        json_path = settings.output_dir / f"{safe_handle}.json"
        if json_path.exists():
            data = json.loads(json_path.read_text(encoding="utf-8"))
            produced.extend(publisher._process_product(json_path, data, output_dir))
        else:
            raise FileNotFoundError(f"No JSON found for handle: {safe_handle}")

        if not produced:
            requested = ", ".join(req.concept_filter or [])
            failures = " | ".join(publisher.failures)
            if failures:
                raise RuntimeError(
                    "Social image generation failed: "
                    f"{failures} No retry or fallback was attempted."
                )
            if requested:
                raise RuntimeError(
                    f"No creative concept matched the requested image: {requested}. "
                    "No retry or fallback was attempted."
                )
            raise RuntimeError(
                "No social images were generated because no eligible creative "
                "concept was found. No retry or fallback was attempted."
            )

        return [str(p) for p in produced], publisher.failures

    try:
        produced, warnings = await run_in_threadpool(run_publisher)
        return {
            "success": True,
            "produced": produced,
            "warnings": warnings,
        }
    except Exception as e:
        logger.exception("Social image generation failed for handle=%s", safe_handle)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/products/{handle}")
async def get_product(handle: str):
    """Get the generated JSON data for a specific product."""
    settings = get_settings()
    # slugify the handle the exact same way as the pipeline does
    from services.landing_pages.product_prompts.utils import slugify
    safe_handle = slugify(handle)
    json_path = settings.output_dir / f"{safe_handle}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="Product JSON not found")
    
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/products/{handle}")
async def update_product(handle: str, update_data: dict):
    """Update the generated JSON data for a specific product (e.g. edited text)."""
    settings = get_settings()
    # slugify the handle the exact same way as the pipeline does
    from services.landing_pages.product_prompts.utils import slugify
    safe_handle = slugify(handle)
    json_path = settings.output_dir / f"{safe_handle}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="Product JSON not found")
    
    try:
        current_data = json.loads(json_path.read_text(encoding="utf-8"))
        if "data" in update_data:
            current_data = update_data["data"]
        else:
            current_data.update(update_data)
            
        json_path.write_text(json.dumps(current_data, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"success": True, "message": "Updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/rss")
async def get_rss_feed():
    """Serve the generated RSS feed."""
    settings = get_settings()
    feed_path = settings.project_root / "social" / "feed.xml"
    if not feed_path.exists():
        raise HTTPException(status_code=404, detail="RSS feed not yet generated")
    
    return FileResponse(feed_path, media_type="application/rss+xml")

@router.get("/rss/{handle}")
async def get_product_rss_section(handle: str):
    """Return the landing-page RSS items for one product."""
    settings = get_settings()
    from services.landing_pages.product_prompts.utils import slugify
    safe_handle = slugify(handle)
    section = read_product_section(
        settings.project_root / "social" / "feed.xml", safe_handle
    )
    section["feed_url"] = landing_pages_rss_url()
    return {"success": True, "rss": section}

@router.get("/images/{filename}")
async def get_image(filename: str):
    """Serve images from the output directory."""
    settings = get_settings()
    img_path = settings.output_dir / filename
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    
    return FileResponse(img_path)

@router.get("/social/images/{filename}")
async def get_social_image(filename: str):
    """Serve images from the social directory."""
    settings = get_settings()
    img_path = settings.project_root / "social" / filename
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="Social image not found")
    
    return FileResponse(img_path)

@router.get("/social/videos/{filename}")
async def get_social_video(filename: str):
    """Serve a generated marketing video from the social directory."""
    settings = get_settings()
    if filename != Path(filename).name or not filename.lower().endswith(".mp4"):
        raise HTTPException(status_code=400, detail="Invalid video filename")
    video_path = settings.project_root / "social" / filename
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Marketing video not found")
    return FileResponse(video_path, media_type="video/mp4", filename=filename)

@router.post("/videos/script")
async def create_video_script(req: VideoScriptRequest):
    """Ask Grok to create and persist a 6–12 second video plan."""
    settings = get_settings()
    await _configure_store_grok(settings, req.shop)
    safe_handle = slugify(req.handle)
    concept_slug = slugify(req.concept)
    json_path: Optional[Path] = None
    data: dict = {}
    try:
        json_path, data = _read_product_json(settings, safe_handle)
        concept = _find_creative_concept(data, concept_slug)
        social_dir = settings.project_root / "social"
        txt_file = social_dir / f"{safe_handle}__{concept_slug}.txt"
        social_text = txt_file.read_text(encoding="utf-8") if txt_file.exists() else str(
            concept.get("social_text") or ""
        )
        service = LandingPageVideoService(
            settings, build_session(settings.user_agent, settings.max_retries)
        )
        script = await run_in_threadpool(
            service.create_script, data, concept, social_text
        )
        videos = data.setdefault("marketing_videos", {})
        record = videos.get(concept_slug) if isinstance(videos.get(concept_slug), dict) else {}
        record.update({
            "concept": str(concept.get("concept") or req.concept),
            "concept_slug": concept_slug,
            "script": script,
            "posting_text": script.get("posting_text") or social_text,
            "status": "script_ready",
            "approved": False,
            "last_error": "",
        })
        # A new script invalidates any old render and approval for this concept.
        for key in ("video_file", "video_version", "shopify_url", "shopify_file_id", "generated_at", "request_id"):
            record.pop(key, None)
        videos[concept_slug] = record
        _write_product_json(json_path, data)
        return {"success": True, "video": record}
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        logger.error(
            "UGC video script failed for product=%s concept=%s: %s",
            safe_handle,
            concept_slug,
            error_message,
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={"operation": "create_ugc_video_script"},
        )
        if json_path is not None and data:
            videos = data.setdefault("marketing_videos", {})
            record = (
                videos.get(concept_slug)
                if isinstance(videos.get(concept_slug), dict)
                else {}
            )
            record.update({
                "concept": str(record.get("concept") or req.concept),
                "concept_slug": concept_slug,
                "status": "error",
                "approved": False,
                "last_error": error_message,
            })
            videos[concept_slug] = record
            _write_product_json(json_path, data)
        raise HTTPException(status_code=500, detail=str(exc))

@router.post("/videos/generate")
async def generate_marketing_video(req: GenerateVideoRequest):
    """Render a previously reviewed Grok script and persist the MP4."""
    settings = get_settings()
    await _configure_store_grok(settings, req.shop)
    safe_handle = slugify(req.handle)
    concept_slug = slugify(req.concept)
    json_path: Optional[Path] = None
    data: dict = {}
    try:
        json_path, data = _read_product_json(settings, safe_handle)
        videos = data.get("marketing_videos") or {}
        record = videos.get(concept_slug)
        if not isinstance(record, dict) or not isinstance(record.get("script"), dict):
            raise ValueError("Create and review the video script before generating the video.")
        social_dir = settings.project_root / "social"
        image_path = _find_social_image(social_dir, safe_handle, concept_slug)
        if image_path is None:
            raise FileNotFoundError(
                f"No source social image was found for concept: {concept_slug}"
            )
        output_path = social_dir / f"{safe_handle}__{concept_slug}.mp4"
        record["status"] = "generating"
        record["last_error"] = ""
        record["approved"] = False
        _write_product_json(json_path, data)

        service = LandingPageVideoService(
            settings, build_session(settings.user_agent, settings.max_retries)
        )
        generated = await run_in_threadpool(
            service.generate_video, record["script"], image_path, output_path
        )
        record.update(generated)
        record.update({"status": "generated", "approved": False, "last_error": ""})
        record.pop("shopify_url", None)
        record.pop("shopify_file_id", None)
        _write_product_json(json_path, data)
        return {"success": True, "video": record}
    except Exception as exc:
        if json_path is not None and data:
            videos = data.get("marketing_videos") or {}
            record = videos.get(concept_slug)
            if isinstance(record, dict):
                record["status"] = "error"
                record["last_error"] = str(exc)
                _write_product_json(json_path, data)
        raise HTTPException(status_code=500, detail=str(exc))

@router.put("/videos/{handle}/{concept}")
async def update_marketing_video(handle: str, concept: str, req: UpdateVideoRequest):
    """Save script/posting-text edits or approve a generated video."""
    settings = get_settings()
    safe_handle = slugify(handle)
    concept_slug = slugify(concept)
    try:
        json_path, data = _read_product_json(settings, safe_handle)
        videos = data.get("marketing_videos") or {}
        record = videos.get(concept_slug)
        if not isinstance(record, dict):
            raise ValueError("Create the video script before editing or approving it.")
        if req.script is not None:
            duration = int(req.script.get("duration_seconds") or 0)
            if not 6 <= duration <= 12:
                raise ValueError("Video duration must remain between 6 and 12 seconds.")
            if not str(req.script.get("video_prompt") or "").strip():
                raise ValueError("The video generation prompt cannot be empty.")
            LandingPageVideoService._validate_spoken_script(
                req.script.get("spoken_script"), duration
            )
            record["script"] = req.script
            if record.get("video_file"):
                record["approved"] = False
        if req.posting_text is not None:
            record["posting_text"] = req.posting_text
        if req.approved is not None:
            if req.approved and not record.get("video_file"):
                raise ValueError("Generate the video before approving it.")
            record["approved"] = req.approved
            record["status"] = "approved" if req.approved else "generated"
        _write_product_json(json_path, data)
        return {"success": True, "video": record}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.get("/social/{handle}")
async def get_social_items(handle: str):
    """Get the generated social images and texts for a product."""
    settings = get_settings()
    from services.landing_pages.product_prompts.utils import slugify
    safe_handle = slugify(handle)
    social_dir = settings.project_root / "social"
    
    items = []
    videos = {}
    try:
        _json_path, product_data = _read_product_json(settings, safe_handle)
        raw_videos = product_data.get("marketing_videos") or {}
        if isinstance(raw_videos, dict):
            videos = raw_videos
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    if social_dir.exists():
        image_files = sorted(
            path
            for path in social_dir.glob(f"{safe_handle}__*")
            if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        )
        for img_file in image_files:
            concept_slug = img_file.stem.replace(f"{safe_handle}__", "")
            txt_file = social_dir / f"{img_file.stem}.txt"
            
            text_content = ""
            if txt_file.exists():
                text_content = txt_file.read_text(encoding="utf-8")
                
            items.append({
                "concept": concept_slug,
                "image_file": img_file.name,
                "image_version": img_file.stat().st_mtime_ns,
                "text": text_content,
                "video": videos.get(concept_slug),
            })
    return {"success": True, "items": items}

@router.put("/social/{handle}/{concept}")
async def update_social_text(handle: str, concept: str, update_data: dict):
    """Update the text for a specific social post."""
    settings = get_settings()
    from services.landing_pages.product_prompts.utils import slugify
    safe_handle = slugify(handle)
    txt_file = settings.project_root / "social" / f"{safe_handle}__{concept}.txt"
    
    if "text" in update_data:
        txt_file.write_text(update_data["text"], encoding="utf-8")
        return {"success": True}
    
    raise HTTPException(status_code=400, detail="Missing text in update payload")

@router.post("/publish")
async def publish_landing_page(req: PublishLandingPageRequest):
    """Run publish_landing_page.py logic."""
    settings = get_settings()
    from services.landing_pages.product_prompts.utils import slugify
    safe_handle = slugify(req.handle)
    
    def run_publish():
        publisher = LandingPagePublisher(settings, concept_filter=req.concept_filter)
        json_path = settings.output_dir / f"{safe_handle}.json"
        if not json_path.exists():
            raise FileNotFoundError(f"No JSON found for handle: {safe_handle}")
        
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        result = publisher._publish_product_page(
            safe_handle,
            data,
            settings.project_root / "social",
            req.published,
        )
        result["rss"]["feed_url"] = landing_pages_rss_url()
        result["published_at"] = datetime.now(timezone.utc).isoformat()
        data["landing_page_publication"] = result
        temporary_path = json_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        temporary_path.replace(json_path)
        return result
    
    try:
        result = await run_in_threadpool(run_publish)
        return {"success": True, "publication": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
