import os
import json
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

class PublishLandingPageRequest(BaseModel):
    handle: str
    published: bool = False
    concept_filter: Optional[List[str]] = None

def get_settings():
    settings = Settings.load()
    return settings

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
                # We can return summary data
                products.append({
                    "handle": json_file.stem,
                    "title": data.get("product", {}).get("title", ""),
                    "url": data.get("product", {}).get("url", ""),
                    "concepts_generated": len(data.get("concepts", [])),
                    "images": len(data.get("assets", [])),
                })
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
            overwrite=True
        )
        output_dir = Path("social")
        output_dir.mkdir(parents=True, exist_ok=True)
        produced = []
        
        json_path = settings.output_dir / f"{safe_handle}.json"
        if json_path.exists():
            data = json.loads(json_path.read_text(encoding="utf-8"))
            produced.extend(publisher._process_product(json_path, data, output_dir))
        else:
            raise FileNotFoundError(f"No JSON found for handle: {safe_handle}")

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

@router.get("/images/{filename}")
async def get_image(filename: str):
    """Serve images from the output directory."""
    settings = get_settings()
    img_path = settings.output_dir / filename
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    
    return FileResponse(img_path)

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
            
        publisher._publish_product_page(safe_handle, data, Path("social"), req.published)
    
    try:
        await run_in_threadpool(run_publish)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
