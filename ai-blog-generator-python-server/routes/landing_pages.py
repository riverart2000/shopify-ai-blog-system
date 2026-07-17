import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

# Note: We're going to import from services.landing_pages
from services.landing_pages.product_prompts.config import Settings
from services.landing_pages.product_prompts.pipeline import Pipeline
from services.landing_pages.social_publisher.pipeline import SocialPublisher
from services.landing_pages.social_publisher.landing_page import LandingPagePublisher
from services.landing_pages.social_publisher.rss_feed import read_product_section

router = APIRouter(
    prefix="/api/landing-pages",
    tags=["landing_pages"],
)

class GeneratePromptsRequest(BaseModel):
    product_url: str
    fetcher: str = "web"
    generator: str = "template"

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

def get_settings():
    settings = Settings.load()
    return settings

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

@router.post("/generate-prompts")
async def generate_prompts(req: GeneratePromptsRequest):
    """Run generate_prompts.py logic for a single product URL."""
    settings = get_settings()
    
    def run_pipeline():
        pipeline = Pipeline(
            settings,
            fetcher_name=req.fetcher,
            generator_name=req.generator,
        )
        return pipeline.process_one(req.product_url)
    
    try:
        out_path = await run_in_threadpool(run_pipeline)
        
        # Read and return the generated json
        data = json.loads(out_path.read_text(encoding="utf-8"))
        return {
            "success": True,
            "handle": out_path.stem,
            "data": data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
            if requested:
                raise RuntimeError(
                    f"No image was regenerated for concept: {requested}"
                )
            raise RuntimeError("No social images were generated")

        return [str(p) for p in produced]

    try:
        produced = await run_in_threadpool(run_publisher)
        return {
            "success": True,
            "produced": produced
        }
    except Exception as e:
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

@router.get("/social/{handle}")
async def get_social_items(handle: str):
    """Get the generated social images and texts for a product."""
    settings = get_settings()
    from services.landing_pages.product_prompts.utils import slugify
    safe_handle = slugify(handle)
    social_dir = settings.project_root / "social"
    
    items = []
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
                "text": text_content
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
