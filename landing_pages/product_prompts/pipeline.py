"""Pipeline orchestration: URL list -> per-product JSON + images."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

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
        generator_name: str = "template",
        max_images: int = 3,
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
        log.info(
            "Pipeline ready: fetcher=%s generator=%s concepts=%d offer=%s",
            self.fetcher.name,
            self.generator.name,
            len(self.concepts),
            self.campaign.badge_text() or "none",
        )

    # ------------------------------------------------------------------
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
        product = self.fetcher.fetch(url)
        handle = slugify(product.handle)
        output_dir = self.settings.output_dir

        # 1. Resolve the blog referenced in the description (if any).
        blog = BlogContent()
        blog_url = self.blog_scraper.find_blog_url(product)
        if blog_url:
            log.info("Found blog URL: %s", blog_url)
            blog = self.blog_scraper.scrape(blog_url)

        # 2. Choose image sources: prefer blog images, else product images.
        #    Everything lands directly in output/ as <handle>_1.jpg, <handle>_2.jpg, ...
        image_sources = blog.image_urls or product.image_urls
        assets = self.downloader.download_many(
            image_sources, output_dir, prefix=handle, limit=self.max_images
        )
        main_image = next(
            (a.local_path for a in assets if a.role == "main"),
            assets[0].local_path if assets else None,
        )

        # 3. Profile the ideal client and generate every concept. Backends may
        #    do this in a single API call (Grok) or per-concept (template).
        persona, concept_outputs, plan = self.generator.generate_bundle(
            product, blog, self.concepts, self.campaign
        )
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
            main_image=main_image,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
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
