"""Shopify landing page generator using uploaded marketing images."""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from services.landing_pages.product_prompts.config import Settings
from services.landing_pages.product_prompts.utils import build_session, get_logger, slugify
from services.landing_pages.product_prompts.fetchers.shopify import ShopifyAdminFetcher

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

    def _publish_product_page(self, handle: str, data: dict, social_dir: Path, published: bool) -> None:
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
        
        # 1. Collect images to upload
        concept_data = []
        for c in concepts:
            name = c.get("concept", "")
            if self.concept_filter and name.strip().lower() not in self.concept_filter:
                continue
                
            # If the LLM gave us a plan, only use the selected concepts.
            if selected_concepts and name not in selected_concepts:
                continue

            slug = slugify(name)
            # We look for the primary image without variation suffixes first, or _v1
            image_path = social_dir / f"{handle}__{slug}.jpg"
            if not image_path.exists():
                image_path = social_dir / f"{handle}__{slug}_v1.jpg"
            
            if image_path.exists():
                concept_data.append({
                    "concept": c,
                    "slug": slug,
                    "image_path": image_path,
                    "cdn_url": None
                })
        
        # Ensure we respect the plan's order if possible
        if selected_concepts:
            ordered_data = []
            for sc in selected_concepts:
                for cd in concept_data:
                    if cd["concept"].get("concept") == sc:
                        ordered_data.append(cd)
                        break
            if ordered_data:
                concept_data = ordered_data
                
        if not concept_data:
            log.warning("No generated images found in %s for %s", social_dir, handle)
            return

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
            log.error("All image uploads failed for %s", handle)
            return

        # 3. Build HTML
        html = self._build_html(product, campaign, concept_data)

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
            
        page_id, url = self._create_page(page_title, page_handle, html, published, meta_desc)
        if url:
            log.info("✅ Published Landing Page for %s: %s", handle, url)
        else:
            log.info("✅ Created Draft Landing Page for %s: ID %s", handle, page_id)

        # 5. Link the Landing Page back into the Original Blog Post (if applicable)
        blog = data.get("blog", {})
        blog_url = blog.get("url")
        if blog_url and url:
            self._update_blog_article_with_link(blog_url, url)

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

    def _build_html(self, product: dict, campaign: dict, concept_data: list) -> str:
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
                "mimeType": "image/jpeg",
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
        files = {"file": (image_path.name, image_path.read_bytes(), "image/jpeg")}
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

    def _create_page(self, title: str, handle: str, html: str, published: bool, meta_desc: str = "") -> tuple[str, str]:
        query = """
        mutation pageCreate($page: PageCreateInput!) {
          pageCreate(page: $page) {
            page { id title handle }
            userErrors { field message }
          }
        }
        """
        variables = {
          "page": {
            "title": title,
            "body": html,
            "isPublished": published
          }
        }
        if meta_desc:
            # Shopify uses metafields on pages for SEO title and description
            variables["page"]["metafields"] = [
                {
                    "namespace": "global",
                    "key": "title_tag",
                    "type": "single_line_text_field",
                    "value": title
                },
                {
                    "namespace": "global",
                    "key": "description_tag",
                    "type": "single_line_text_field",
                    "value": meta_desc
                }
            ]
            
        resp = self.session.post(self.endpoint, json={"query": query, "variables": variables}, headers={"X-Shopify-Access-Token": self.token})
        data = resp.json()
        
        page_node = data.get("data", {}).get("pageCreate", {}).get("page")
        if not page_node:
            log.error("pageCreate failed: %s", data)
            raise RuntimeError("Failed to create Shopify Page")
            
        page_id = page_node["id"]
        
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
                
        return page_id, url
