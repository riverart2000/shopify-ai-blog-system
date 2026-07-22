"""Shopify landing page generator using uploaded marketing images."""

import json
import mimetypes
import re
import time
from html import escape
from pathlib import Path
from typing import List, Optional

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from services.landing_pages.product_prompts.config import Settings
from services.landing_pages.product_prompts.utils import build_session, get_logger, slugify
from services.landing_pages.product_prompts.fetchers.shopify import ShopifyAdminFetcher
from services.landing_pages.social_publisher.rss_feed import write_product_section

log = get_logger("social.landing_page")

# Basic premium CSS inserted into the Shopify page
_PAGE_CSS = """
<style>
.mkt-page {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  color: #1d1d1f;
  line-height: 1.5;
  max-width: 100%;
  overflow-x: hidden;
  margin: 0;
  padding: 0;
}
.mkt-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 60px 20px;
}
.mkt-section:nth-child(even) {
  background-color: #f5f5f7;
}
.mkt-section:nth-child(odd) {
  background-color: #ffffff;
}
.mkt-container {
  max-width: 1080px;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 40px;
}
@media (min-width: 768px) {
  .mkt-row {
    flex-direction: row;
    align-items: center;
  }
  .mkt-row.reverse {
    flex-direction: row-reverse;
  }
}
.mkt-row {
  display: flex;
  flex-direction: column;
  gap: 40px;
}
.mkt-col {
  flex: 1;
}
.mkt-image-wrapper {
  flex: 1.2;
  border-radius: 20px;
  overflow: hidden;
  box-shadow: 0 10px 30px rgba(0,0,0,0.08);
}
.mkt-image {
  width: 100%;
  height: auto;
  display: block;
}
.mkt-video-wrapper {
  width: min(100%, 430px);
  margin: 0 auto;
  border-radius: 20px;
  overflow: hidden;
  box-shadow: 0 10px 30px rgba(0,0,0,0.14);
  background: #000;
}
.mkt-video {
  width: 100%;
  max-height: 760px;
  display: block;
  background: #000;
}
.mkt-content {
  flex: 1;
  padding: 20px;
}
.mkt-title {
  font-size: 2.5rem;
  font-weight: 700;
  letter-spacing: -0.015em;
  margin-bottom: 20px;
}
.mkt-subtitle {
  font-size: 1.5rem;
  font-weight: 600;
  margin-bottom: 16px;
  color: #1d1d1f;
}
.mkt-text {
  font-size: 1.125rem;
  color: #515154;
  margin-bottom: 30px;
  white-space: pre-wrap;
}
.mkt-cta {
  display: inline-block;
  background-color: #0071e3;
  color: #ffffff;
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.022em;
  padding: 14px 28px;
  border-radius: 980px;
  text-decoration: none;
  transition: all 0.3s ease;
}
.mkt-cta:hover {
  background-color: #0077ed;
  transform: scale(1.02);
}
.mkt-hero {
  text-align: center;
  padding: 80px 20px;
  background-color: #000;
  color: #fff;
}
.mkt-hero .mkt-title {
  color: #fff;
  font-size: 3.5rem;
}
.mkt-hero .mkt-text {
  color: #a1a1a6;
  font-size: 1.25rem;
  max-width: 600px;
  margin: 0 auto 40px auto;
}
</style>
"""

class LandingPagePublisher:
    def __init__(self, settings: Settings, concept_filter: Optional[List[str]] = None) -> None:
        self.settings = settings
        self.session = build_session(settings.user_agent, settings.max_retries)
        self.fetcher = ShopifyAdminFetcher(settings, self.session)
        self.token = self.fetcher._access_token()
        self.endpoint = self.fetcher._endpoint()
        self.concept_filter = (
            {c.strip().lower() for c in concept_filter} if concept_filter else None
        )

    def run(self, input_dir: Path, social_dir: Path, published: bool) -> None:
        if not input_dir.exists():
            log.error("Input directory %s not found.", input_dir)
            return

        for json_path in sorted(input_dir.glob("*.json")):
            log.info("Processing %s for landing page", json_path.name)
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            self._publish_product_page(json_path.stem, data, social_dir, published)

    def _publish_product_page(self, handle: str, data: dict, social_dir: Path, published: bool) -> dict:
        product = data.get("product", {})
        campaign = data.get("campaign", {})
        concepts = data.get("creative_concepts", [])
        plan = data.get("landing_page_plan", {})
        funnel_stages = plan.get("funnel_stages", [])
        
        selected_concepts = []
        if funnel_stages:
            selected_concepts = [s.get("concept") for s in funnel_stages if s.get("concept")]
        elif plan.get("selected_concepts"):
            selected_concepts = plan.get("selected_concepts")
            
        # If still empty (e.g. old JSON), fallback to the first 3 concepts
        if not selected_concepts and concepts:
            selected_concepts = [c.get("concept") for c in concepts[:3]]
            log.info("No landing page plan found, falling back to first 3 concepts: %s", selected_concepts)
        else:
            log.info("Using planned funnel concepts: %s", selected_concepts)
        
        # 1. Collect every generated concept image. The landing-page plan only
        # controls the page layout; RSS should make use of every creative.
        concept_data = []
        for c in concepts:
            name = c.get("concept", "")
            if self.concept_filter and name.strip().lower() not in self.concept_filter:
                continue

            slug = slugify(name)
            image_path = self._find_generated_image(social_dir, handle, slug)

            if image_path:
                concept_data.append({
                    "concept": c,
                    "slug": slug,
                    "image_path": image_path,
                    "cdn_url": None
                })
        
        if not concept_data:
            raise RuntimeError(f"No generated images found for {handle}")

        # 2. Upload images to Shopify
        log.info("Uploading %d images to Shopify for %s...", len(concept_data), handle)
        for cd in concept_data:
            url = self._upload_image(cd["image_path"])
            if url:
                cd["cdn_url"] = url
            else:
                log.warning("Failed to upload image %s", cd["image_path"].name)

        # Filter out failed uploads
        concept_data = [cd for cd in concept_data if cd["cdn_url"]]
        if not concept_data:
            raise RuntimeError(f"All Shopify image uploads failed for {handle}")

        # The Shopify page stays focused on its planned funnel concepts. If a
        # planned image failed to upload, use up to three available creatives so
        # the page can still be published. RSS continues with the complete list.
        landing_concept_data = self._select_landing_concepts(concept_data, selected_concepts)

        # Approved videos are the only ones allowed onto the storefront or RSS.
        # Reuse the stored Shopify URL on republish so the same MP4 is not uploaded twice.
        video_data = []
        videos = data.get("marketing_videos") or {}
        if isinstance(videos, dict):
            for concept_slug, record in videos.items():
                if not isinstance(record, dict) or not record.get("approved"):
                    continue
                if self.concept_filter and concept_slug.strip().lower() not in self.concept_filter:
                    continue
                video_file = str(record.get("video_file") or "")
                video_path = social_dir / video_file
                if not video_file or not video_path.exists():
                    raise RuntimeError(
                        f"Approved video is missing for concept '{concept_slug}': {video_file or 'no filename'}"
                    )
                shopify_url = str(record.get("shopify_url") or "").strip()
                if not shopify_url:
                    file_id, shopify_url = self._upload_video(video_path)
                    record["shopify_file_id"] = file_id
                    record["shopify_url"] = shopify_url
                video_data.append({
                    "slug": concept_slug,
                    "concept": str(record.get("concept") or concept_slug),
                    "video_path": video_path,
                    "cdn_url": shopify_url,
                    "posting_text": str(record.get("posting_text") or ""),
                    "duration_seconds": int(record.get("duration_seconds") or (record.get("script") or {}).get("duration_seconds") or 0),
                    "poster_url": next((item["cdn_url"] for item in concept_data if item["slug"] == concept_slug), ""),
                })

        # 3. Build HTML
        html = self._build_html(product, campaign, landing_concept_data, video_data)

        # 4. Create Page
        page_title = f"{product.get('title')} - Special Offer"
        if campaign.get("discount"):
            page_title = f"{product.get('title')} - {campaign.get('discount')}"
            
        page_handle = f"{handle}-offer"
        
        # Build a meta description for SEO
        meta_desc = (product.get("description_text") or "").strip()
        if not meta_desc and concepts:
            meta_desc = concepts[0].get("social_text", "").strip()
        
        # Truncate to a reasonable length for SEO (~160 chars is standard, up to 320)
        if len(meta_desc) > 250:
            meta_desc = meta_desc[:247] + "..."
            
        page_id, url, page_action, actual_page_handle = self._upsert_page(
            page_title, page_handle, html, published, meta_desc
        )
        if url:
            log.info("✅ Published Landing Page for %s: %s", handle, url)
        else:
            log.info("✅ Created Draft Landing Page for %s: ID %s", handle, page_id)

        # 5. Link the Landing Page back into the Original Blog Post (if applicable)
        blog = data.get("blog", {})
        blog_url = blog.get("url")
        if blog_url and url:
            self._update_blog_article_with_link(blog_url, url)

        rss_result = write_product_section(
            self.settings.project_root / "social" / "feed.xml",
            handle=handle,
            product_title=str(product.get("title") or ""),
            landing_page_url=url,
            concepts=concept_data,
            videos=video_data,
        )
        return {
            "page": {
                "id": page_id,
                "url": url,
                "handle": actual_page_handle,
                "title": page_title,
                "action": page_action,
                "duplicate_prevented": page_action == "updated",
            },
            "rss": rss_result,
        }

    @staticmethod
    def _find_generated_image(social_dir: Path, handle: str, slug: str) -> Optional[Path]:
        for suffix in ("", "_v1"):
            for extension in (".jpg", ".jpeg", ".png", ".webp"):
                image_path = social_dir / f"{handle}__{slug}{suffix}{extension}"
                if image_path.exists():
                    return image_path
        return None

    @staticmethod
    def _select_landing_concepts(concept_data: list, selected_concepts: list) -> list:
        ordered_data = []
        for selected_name in selected_concepts:
            match = next(
                (
                    item
                    for item in concept_data
                    if item["concept"].get("concept") == selected_name
                ),
                None,
            )
            if match and match not in ordered_data:
                ordered_data.append(match)
        return ordered_data or concept_data[:3]

    def _update_blog_article_with_link(self, blog_url: str, landing_page_url: str) -> None:
        """Update a Shopify blog article's content to append a CTA to the landing page."""
        parsed = urlparse(blog_url)
        path_parts = parsed.path.strip("/").split("/")
        # Path looks like: blogs/{blog_handle}/{article_handle}
        if len(path_parts) >= 3 and path_parts[-3] == "blogs":
            article_handle = path_parts[-1]
            
            # Find the article ID by handle
            query_find = """
            query getArticles($query: String!) {
              articles(first: 1, query: $query) {
                edges {
                  node {
                    id
                    bodyHtml
                  }
                }
              }
            }
            """
            variables_find = {"query": f"handle:{article_handle}"}
            resp = self.session.post(
                self.endpoint, 
                json={"query": query_find, "variables": variables_find}, 
                headers={"X-Shopify-Access-Token": self.token}
            )
            
            edges = resp.json().get("data", {}).get("articles", {}).get("edges", [])
            if not edges:
                log.warning("Could not find article on Shopify with handle: %s", article_handle)
                return
                
            article_node = edges[0]["node"]
            article_id = article_node["id"]
            body_html = article_node.get("bodyHtml", "") or ""
            
            # Check if we already linked it to avoid duplicates
            if landing_page_url in body_html:
                log.info("Blog article %s already links to the landing page.", article_handle)
                return
                
            # Append a highly visible CTA banner to the end of the blog post
            banner_html = f"""
            <div style="margin-top: 40px; padding: 30px; background-color: #f5f5f7; border-radius: 12px; text-align: center; border: 2px solid #000;">
                <h3 style="margin-bottom: 15px; font-weight: bold; color: #1d1d1f;">Ready to experience the results yourself?</h3>
                <p style="margin-bottom: 25px; color: #515154;">We've put together a special offer just for our readers.</p>
                <a href="{landing_page_url}" style="display: inline-block; background-color: #0071e3; color: #fff; padding: 12px 24px; border-radius: 980px; text-decoration: none; font-weight: bold;">Unlock Your Special Offer</a>
            </div>
            """
            
            new_body = body_html + banner_html
            
            # Update the article
            query_update = """
            mutation articleUpdate($id: ID!, $article: ArticleUpdateInput!) {
              articleUpdate(id: $id, article: $article) {
                article { id }
                userErrors { field message }
              }
            }
            """
            variables_update = {
                "id": article_id,
                "article": {
                    "bodyHtml": new_body
                }
            }
            resp_update = self.session.post(
                self.endpoint, 
                json={"query": query_update, "variables": variables_update}, 
                headers={"X-Shopify-Access-Token": self.token}
            )
            
            update_data = resp_update.json()
            errors = update_data.get("data", {}).get("articleUpdate", {}).get("userErrors", [])
            if errors:
                log.error("Failed to update blog article %s: %s", article_handle, errors)
            else:
                log.info("✅ Added landing page CTA to blog post: %s", article_handle)

    def _build_html(self, product: dict, campaign: dict, concept_data: list, video_data: Optional[list] = None) -> str:
        blocks = [_PAGE_CSS, "<div class='mkt-page'>"]
        
        # We will use the first concept (usually Lifestyle) as the hero
        hero = concept_data[0]
        rest = concept_data[1:]
        
        product_url = product.get("url", "")
        if campaign.get("code"):
            parts = urlparse(product_url)
            params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "discount"]
            params.append(("discount", campaign.get("code")))
            product_url = urlunparse(parts._replace(query=urlencode(params)))
            
        offer_text = campaign.get("raw", "")
        if not offer_text and campaign.get("discount"):
            offer_text = f"{campaign.get('discount')} OFF"
            if campaign.get("free_shipping"):
                offer_text += " + FREE SHIPPING"

        # HERO SECTION
        blocks.append(f'''
        <div class="mkt-hero">
            <div class="mkt-container" style="align-items: center;">
                <h1 class="mkt-title">{hero["concept"].get("image_text", {}).get("headline") or product.get("title")}</h1>
                <p class="mkt-text">{hero["concept"].get("social_text", "")}</p>
                <div class="mkt-image-wrapper" style="max-width: 800px; margin: 0 auto 40px auto;">
                    <img src="{hero['cdn_url']}" class="mkt-image" alt="Hero Image">
                </div>
                <a href="{product_url}" class="mkt-cta">Claim Offer Now</a>
            </div>
        </div>
        ''')

        # ALTERNATING SECTIONS
        for i, cd in enumerate(rest):
            concept = cd["concept"]
            reverse_class = "reverse" if i % 2 == 1 else ""
            headline = concept.get("image_text", {}).get("headline") or concept.get("concept")
            text = concept.get("social_text", "")
            
            blocks.append(f'''
            <div class="mkt-section">
                <div class="mkt-container">
                    <div class="mkt-row {reverse_class}">
                        <div class="mkt-image-wrapper">
                            <img src="{cd['cdn_url']}" class="mkt-image" loading="lazy" alt="{headline}">
                        </div>
                        <div class="mkt-content">
                            <h2 class="mkt-subtitle">{headline}</h2>
                            <p class="mkt-text">{text}</p>
                            <a href="{product_url}" class="mkt-cta">Shop {product.get('title')}</a>
                        </div>
                    </div>
                </div>
            </div>
            ''')

        # APPROVED VIDEO SECTIONS
        for video in video_data or []:
            caption = escape(str(video.get("posting_text") or ""))
            concept_name = escape(str(video.get("concept") or "Marketing video"))
            video_url = escape(str(video.get("cdn_url") or ""), quote=True)
            poster = escape(str(video.get("poster_url") or ""), quote=True)
            poster_attr = f' poster="{poster}"' if poster else ""
            blocks.append(f'''
            <div class="mkt-section">
                <div class="mkt-container" style="align-items: center; text-align: center;">
                    <h2 class="mkt-subtitle">{concept_name}</h2>
                    <div class="mkt-video-wrapper">
                        <video class="mkt-video" controls playsinline preload="metadata"{poster_attr}>
                            <source src="{video_url}" type="video/mp4">
                        </video>
                    </div>
                    <p class="mkt-text" style="max-width: 760px;">{caption}</p>
                    <a href="{product_url}" class="mkt-cta">Shop {escape(str(product.get('title') or 'Now'))}</a>
                </div>
            </div>
            ''')

        # FOOTER CTA
        blocks.append(f'''
        <div class="mkt-section" style="background-color: #000; color: #fff; text-align: center;">
            <div class="mkt-container" style="align-items: center;">
                <h2 class="mkt-title" style="color: #fff;">Ready to upgrade your routine?</h2>
                <p class="mkt-text" style="color: #a1a1a6;">{offer_text}</p>
                <a href="{product_url}" class="mkt-cta">Get Started Today</a>
            </div>
        </div>
        ''')

        blocks.append("</div>")
        return "\n".join(blocks)

    def _upload_image(self, image_path: Path) -> Optional[str]:
        mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        # 1. stagedUploadsCreate
        query = """
        mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
          stagedUploadsCreate(input: $input) {
            stagedTargets { url resourceUrl parameters { name value } }
            userErrors { field message }
          }
        }
        """
        variables = {
            "input": [{
                "filename": image_path.name,
                "mimeType": mime_type,
                "httpMethod": "POST",
                "resource": "IMAGE"
            }]
        }
        resp = self.session.post(self.endpoint, json={"query": query, "variables": variables}, headers={"X-Shopify-Access-Token": self.token})
        data = resp.json()
        
        targets = data.get("data", {}).get("stagedUploadsCreate", {}).get("stagedTargets")
        if not targets:
            log.error("Failed to get staged target: %s", data)
            return None
        target = targets[0]

        # 2. POST file
        url = target["url"]
        params = {p["name"]: p["value"] for p in target["parameters"]}
        files = {"file": (image_path.name, image_path.read_bytes(), mime_type)}
        resp2 = self.session.post(url, data=params, files=files)
        resp2.raise_for_status()

        # 3. fileCreate
        query2 = """
        mutation fileCreate($files: [FileCreateInput!]!) {
          fileCreate(files: $files) {
            files { id }
            userErrors { field message }
          }
        }
        """
        variables2 = {
            "files": [{
                "alt": image_path.stem,
                "contentType": "IMAGE",
                "originalSource": target["resourceUrl"]
            }]
        }
        resp3 = self.session.post(self.endpoint, json={"query": query2, "variables": variables2}, headers={"X-Shopify-Access-Token": self.token})
        data3 = resp3.json()
        
        created_files = data3.get("data", {}).get("fileCreate", {}).get("files")
        if not created_files:
            log.error("fileCreate failed: %s", data3)
            return None
            
        file_id = created_files[0]["id"]

        # 4. Poll for READY
        query3 = """
        query getFile($id: ID!) {
          node(id: $id) {
            ... on MediaImage {
              fileStatus
              image { url }
            }
          }
        }
        """
        for _ in range(10):
            time.sleep(2)
            resp4 = self.session.post(self.endpoint, json={"query": query3, "variables": {"id": file_id}}, headers={"X-Shopify-Access-Token": self.token})
            node = resp4.json().get("data", {}).get("node")
            if not node:
                continue
            status = node.get("fileStatus")
            if status == "READY":
                return node.get("image", {}).get("url")
            elif status == "FAILED":
                log.error("Image processing failed on Shopify side.")
                return None
                
        log.error("Timeout waiting for image to be READY.")
        return None

    def _upload_video(self, video_path: Path) -> tuple[str, str]:
        """Upload an MP4 to Shopify Files and return its stable ID and CDN URL."""
        mime_type = "video/mp4"
        staged_query = """
        mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
          stagedUploadsCreate(input: $input) {
            stagedTargets { url resourceUrl parameters { name value } }
            userErrors { field message }
          }
        }
        """
        staged_variables = {
            "input": [{
                "filename": video_path.name,
                "mimeType": mime_type,
                "fileSize": str(video_path.stat().st_size),
                "httpMethod": "POST",
                "resource": "VIDEO",
            }]
        }
        response = self.session.post(
            self.endpoint,
            json={"query": staged_query, "variables": staged_variables},
            headers={"X-Shopify-Access-Token": self.token},
        )
        response.raise_for_status()
        payload = response.json()
        targets = payload.get("data", {}).get("stagedUploadsCreate", {}).get("stagedTargets")
        if not targets:
            raise RuntimeError(self._user_error_message("stagedUploadsCreate", payload))
        target = targets[0]

        parameters = {item["name"]: item["value"] for item in target["parameters"]}
        with video_path.open("rb") as video_file:
            upload = self.session.post(
                target["url"],
                data=parameters,
                files={"file": (video_path.name, video_file, mime_type)},
            )
        upload.raise_for_status()

        create_query = """
        mutation fileCreate($files: [FileCreateInput!]!) {
          fileCreate(files: $files) {
            files { id fileStatus }
            userErrors { field message }
          }
        }
        """
        create_variables = {
            "files": [{
                "alt": video_path.stem,
                "contentType": "VIDEO",
                "originalSource": target["resourceUrl"],
            }]
        }
        create_response = self.session.post(
            self.endpoint,
            json={"query": create_query, "variables": create_variables},
            headers={"X-Shopify-Access-Token": self.token},
        )
        create_response.raise_for_status()
        create_payload = create_response.json()
        files = create_payload.get("data", {}).get("fileCreate", {}).get("files")
        if not files:
            raise RuntimeError(self._user_error_message("fileCreate", create_payload))
        file_id = str(files[0]["id"])

        status_query = """
        query getVideoFile($id: ID!) {
          node(id: $id) {
            ... on Video {
              fileStatus
              sources { url mimeType format }
            }
          }
        }
        """
        for _ in range(60):
            time.sleep(3)
            status_response = self.session.post(
                self.endpoint,
                json={"query": status_query, "variables": {"id": file_id}},
                headers={"X-Shopify-Access-Token": self.token},
            )
            status_response.raise_for_status()
            node = status_response.json().get("data", {}).get("node") or {}
            if node.get("fileStatus") == "FAILED":
                raise RuntimeError(f"Shopify failed to process video {video_path.name}.")
            if node.get("fileStatus") == "READY":
                sources = node.get("sources") or []
                mp4 = next(
                    (source for source in sources if source.get("mimeType") == "video/mp4"),
                    sources[0] if sources else None,
                )
                if mp4 and mp4.get("url"):
                    return file_id, str(mp4["url"])
        raise RuntimeError(f"Shopify did not finish processing video {video_path.name} within 3 minutes.")

    @staticmethod
    def _single_line(value: str, limit: int = 250) -> str:
        normalised = re.sub(r"\s+", " ", value or "").strip()
        if len(normalised) <= limit:
            return normalised
        return normalised[: max(0, limit - 3)].rstrip() + "..."

    @staticmethod
    def _user_error_message(operation: str, payload: dict) -> str:
        errors = payload.get("data", {}).get(operation, {}).get("userErrors", [])
        messages = [str(error.get("message") or "").strip() for error in errors]
        messages = [message for message in messages if message]
        return "; ".join(messages) or f"Shopify {operation} failed"

    def _find_page(self, handle: str, title: str) -> Optional[dict]:
        query = """
        query findLandingPage($query: String!) {
          pages(first: 10, query: $query) {
            nodes { id title handle }
          }
        }
        """
        def find(search_query: str) -> list:
            response = self.session.post(
                self.endpoint,
                json={"query": query, "variables": {"query": search_query}},
                headers={"X-Shopify-Access-Token": self.token},
            )
            response.raise_for_status()
            return response.json().get("data", {}).get("pages", {}).get("nodes", [])

        nodes = find(f"handle:{handle}")
        exact_handle = next((node for node in nodes if node.get("handle") == handle), None)
        if exact_handle:
            return exact_handle

        # Older versions let Shopify generate the handle from the title. Find
        # those pages by exact title and preserve their URL when updating.
        escaped_title = title.replace('"', '\\"')
        nodes = find(f'title:"{escaped_title}"')
        return next((node for node in nodes if node.get("title") == title), None)

    def _set_page_seo_metafields(self, page_id: str, title: str, meta_desc: str) -> None:
        metafields = [
            {
                "ownerId": page_id,
                "namespace": "global",
                "key": "title_tag",
                "type": "single_line_text_field",
                "value": self._single_line(title, 255),
            }
        ]
        description = self._single_line(meta_desc, 250)
        if description:
            metafields.append(
                {
                    "ownerId": page_id,
                    "namespace": "global",
                    "key": "description_tag",
                    "type": "single_line_text_field",
                    "value": description,
                }
            )

        query = """
        mutation setLandingPageSeo($metafields: [MetafieldsSetInput!]!) {
          metafieldsSet(metafields: $metafields) {
            metafields { id namespace key }
            userErrors { field message }
          }
        }
        """
        response = self.session.post(
            self.endpoint,
            json={"query": query, "variables": {"metafields": metafields}},
            headers={"X-Shopify-Access-Token": self.token},
        )
        response.raise_for_status()
        payload = response.json()
        errors = payload.get("data", {}).get("metafieldsSet", {}).get("userErrors", [])
        if errors:
            messages = "; ".join(str(error.get("message") or "") for error in errors)
            raise RuntimeError(f"Failed to set Shopify page SEO: {messages}")

    def _upsert_page(
        self,
        title: str,
        handle: str,
        html: str,
        published: bool,
        meta_desc: str = "",
    ) -> tuple[str, str, str, str]:
        existing = self._find_page(handle, title)
        effective_handle = str(existing.get("handle") or handle) if existing else handle
        page_input = {
            "title": title,
            "handle": effective_handle,
            "body": html,
            "isPublished": published,
        }

        if existing:
            query = """
            mutation pageUpdate($id: ID!, $page: PageUpdateInput!) {
              pageUpdate(id: $id, page: $page) {
                page { id title handle }
                userErrors { field message }
              }
            }
            """
            operation = "pageUpdate"
            variables = {"id": existing["id"], "page": page_input}
            action = "updated"
        else:
            query = """
        mutation pageCreate($page: PageCreateInput!) {
          pageCreate(page: $page) {
            page { id title handle }
            userErrors { field message }
          }
        }
        """
            operation = "pageCreate"
            variables = {"page": page_input}
            action = "created"

        resp = self.session.post(self.endpoint, json={"query": query, "variables": variables}, headers={"X-Shopify-Access-Token": self.token})
        resp.raise_for_status()
        data = resp.json()

        page_node = data.get("data", {}).get(operation, {}).get("page")
        if not page_node:
            log.error("%s failed: %s", operation, data)
            raise RuntimeError(self._user_error_message(operation, data))

        page_id = page_node["id"]
        self._set_page_seo_metafields(page_id, title, meta_desc)

        # Try to guess the URL if published
        url = ""
        if published:
            domain = self.settings.myshopify_domain.replace(".myshopify.com", "")
            # Actual storefront domain is better if configured
            storefront = self.settings.storefront_domain
            if storefront:
                url = f"{storefront.rstrip('/')}/pages/{page_node['handle']}"
            else:
                url = f"https://{domain}.myshopify.com/pages/{page_node['handle']}"

        return page_id, url, action, str(page_node["handle"])
