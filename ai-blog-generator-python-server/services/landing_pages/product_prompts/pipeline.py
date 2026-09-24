"""Pipeline orchestration: URL list -> per-product JSON + images."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, List, Optional

from .blog import BlogScraper
from .campaign import load_campaign
from .config import Settings
from .fetchers import get_fetcher
from .images import ImageDownloader
from .models import BlogContent, ProductOutput
from .concepts import load_concepts
from .prompting import get_generator
from .utils import build_session, get_logger, slugify

log = get_logger("pipeline")


def read_url_list(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"Product list not found: {path}")
    urls: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            urls.append(stripped)
    if not urls:
        raise ValueError(f"No product URLs found in {path}")
    return urls


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        fetcher_name: str = "web",
        generator_name: str = "grok",
        max_images: int = 3,
        progress_callback: Optional[Callable[[str, int, str], None]] = None,
    ) -> None:
        self.settings = settings
        self.session = build_session(settings.user_agent, settings.max_retries)
        self.fetcher = get_fetcher(fetcher_name, settings, self.session)
        self.generator = get_generator(generator_name, settings, self.session)
        self.blog_scraper = BlogScraper(settings, self.session)
        self.downloader = ImageDownloader(settings, self.session)
        self.concepts = load_concepts(settings.concepts_list)
        self.campaign = load_campaign(settings.campaign_file)
        self.max_images = max_images
        self.progress_callback = progress_callback
        log.info(
            "Pipeline ready: fetcher=%s generator=%s concepts=%d offer=%s",
            self.fetcher.name,
            self.generator.name,
            len(self.concepts),
            self.campaign.badge_text() or "none",
        )

    # ------------------------------------------------------------------
    def _progress(self, stage: str, progress: int, message: str) -> None:
        log.info("%s (%d%%): %s", stage, progress, message)
        if self.progress_callback:
            self.progress_callback(stage, progress, message)

    def run(self, urls: List[str]) -> List[Path]:
        outputs: List[Path] = []
        for url in urls:
            try:
                outputs.append(self.process_one(url))
            except Exception as exc:  # noqa: BLE001 - one bad URL shouldn't stop the batch
                log.error("Failed to process %s: %s", url, exc)
        return outputs

    def process_one(self, url: str) -> Path:
        log.info("Processing %s", url)
        self._progress("fetching_product", 10, "Fetching current product details from Shopify.")
        product = self.fetcher.fetch(url)
        handle = slugify(product.handle)
        output_dir = self.settings.output_dir
        self._progress("product_loaded", 25, f"Loaded product: {product.title}.")

        # 1. Resolve the blog referenced in the description (if any).
        blog = BlogContent()
        blog_url = self.blog_scraper.find_blog_url(product)
        if blog_url:
            log.info("Found blog URL: %s", blog_url)
            self._progress("fetching_linked_blog", 32, "Fetching the linked product blog.")
            blog = self.blog_scraper.scrape(blog_url)
        else:
            self._progress("fetching_linked_blog", 32, "No linked blog was found; continuing with product evidence.")

        # 2. Choose image sources: prefer blog images, else product images.
        #    Everything lands directly in output/ as <handle>_1.jpg, <handle>_2.jpg, ...
        image_sources = blog.image_urls or product.image_urls
        self._progress("downloading_evidence", 40, "Downloading product reference images.")
        assets = self.downloader.download_many(
            image_sources, output_dir, prefix=handle, limit=self.max_images
        )
        main_image = next(
            (a.local_path for a in assets if a.role == "main"),
            assets[0].local_path if assets else None,
        )

        # 3. Profile the ideal client and generate every concept. Backends may
        #    do this in a single API call (Grok) or per-concept (template).
        self._progress(
            "waiting_for_grok", 55,
            f"Sent one request to {self.settings.grok_model}; waiting for its response. No retries are enabled.",
        )
        persona, concept_outputs, plan = self.generator.generate_bundle(
            product, blog, self.concepts, self.campaign
        )
        self._progress("validating_grok_response", 88, "Validating persona, concepts, and landing-page plan.")
        log.info(
            "Ideal client: %s, %s %s (%s)",
            persona.name or "?",
            persona.age or "?",
            persona.sex or "?",
            persona.race or "?",
        )

        # 4. Serialise the per-product JSON alongside its images in output/.
        result = ProductOutput(
            product=product,
            blog=blog,
            assets=assets,
            concepts=concept_outputs,
            generator=self.generator.name,
            persona=persona,
            campaign=self.campaign,
            landing_page_plan=plan,
            generation_diagnostics=self.generator.generation_diagnostics(),
            main_image=main_image,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        self._progress("saving_result", 95, "Saving the validated landing-page content.")
        out_path = output_dir / f"{handle}.json"
        out_path.write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info(
            "Wrote %s (%d concepts, %d images)",
            out_path,
            len(concept_outputs),
            len(assets),
        )
        return out_path
