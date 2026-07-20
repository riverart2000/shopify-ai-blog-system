# Shopify AI Blog System

This repository contains two active applications:

1. `ai-blog-generator-python-server` — the Python backend that manages store configuration, AI model routing, blog generation workflows, scheduling, review, and Shopify publishing.
2. `ai-blog-generator-app` — the embedded Shopify React app that gives merchants an in-admin publishing experience and a theme app extension for product-page blog links.

The embedded app also includes a native-storefront **Wellness Quiz & Routine Builder**. Its Shopify theme block renders and scores four questions without an iframe or an LLM request, then uses a cached catalogue prepared by the Python backend to recommend a three-step routine. Consent-aware anonymous quiz events are included in Customer Intelligence.

The authoritative backend in this repository is the Python server. The repo contains the Python backend and the embedded Shopify app only.

This file is written for coding agents. It is intended to answer three questions quickly:

1. What are the moving parts?
2. Where does each behavior live?
3. Where should an edit start for a given bug or feature?

## Repo Layout

`ai-blog-generator-python-server/`
- FastAPI application and scheduled-job worker.
- Owns store login, store settings, model registry, prompt registry, manual generation UI, scheduled publishing, quality checks, and Shopify Admin API publishing.

`ai-blog-generator-app/`
- Embedded Shopify app built with React Router.
- Owns Shopify app auth, Prisma session storage, per-shop “studio” settings, direct blog publish flow, and the theme app extension that renders “related guide” content on product pages.

`ai-blog-generator-app/README.md`
- Upstream Shopify template README. Useful for generic Shopify app tooling, but not authoritative for this repo’s architecture.

## System Overview

The repo supports two operator-facing workflows:

1. Python server workflow
- A store logs into the Python server UI.
- The store manages AI text/image providers, prompts, Shopify credentials, social settings, and scheduled jobs.
- A user generates a draft, reviews local quality checks, then publishes the article to Shopify.
- Scheduled jobs use the same publish pipeline without the browser.

2. Shopify React app workflow
- A merchant installs the embedded Shopify app in the Shopify admin.
- The app loads store context from Shopify, loads per-shop app settings from Prisma, sends a prompt to the configured Python generation backend, and publishes the result directly to Shopify.
- The app can optionally link a published post back to a Shopify product through metafields.
- A theme app extension block reads those metafields and renders a “Related product guide” card on product pages.

These two workflows share Shopify as the system of record for blog articles and product-page guide links.

## Python Server: What It Owns

### Entry Point

Start here for server assembly:

- `ai-blog-generator-python-server/main.py`

Responsibilities:

- Loads `.env` from either the server directory or workspace root.
- Loads `config.json` through `config.load_config()`.
- Initializes logging.
- Initializes SQLite via `db.init_db()`.
- Builds the FastAPI app and mounts route modules.
- Sets up session middleware, auth middleware, security headers, and rate limiting.

### Main Route Modules

`routes/auth.py`
- Store and admin login/logout.
- First login sets the password if none exists.

`routes/generate.py`
- Browser-facing generate/preview/publish flow.
- `POST /generate` builds a draft and preview page.
- `POST /publish` runs quality checks, composites images, and publishes to Shopify.

`routes/setup.py`
- Admin store management.
- Store-level AI model, prompt, Shopify, logo, sharing, and password settings.
- Also exposes the “Install / Open Shopify React App” link in Setup.

`routes/api.py`
- Utility endpoints for blogs and products.
- History page, live Shopify post scan, and delete-low-quality-post flow.
- Exposes async, non-blocking background queue tasks to generate product blogs and verify corresponding description sync tags.

`routes/app.product-blogs-api.tsx`
- Dedicated Resource Route inside the Shopify App.
- Handles App Bridge-authenticated raw JSON actions (`generate`, `status`, `ensure-description`) securely without returning HTML.

`routes/scheduler_routes.py`
- CRUD UI for scheduled publishing jobs.

`routes/intelligence.py`
- Customer Intelligence dashboard, manual analysis, GA4 read-only credentials, automation, and recommendation controls.

`routes/wellness_quiz_api.py`
- Product catalogue sync, public quiz bootstrap, anonymous event collection, and quiz reporting.

### Service Layer

`services/llm_service.py`
- Chooses active text models by store.
- Supports failover across providers.
- Returns normalized blog payloads.

`services/image_service.py`
- Chooses active image models by store.
- Generates photo and infographic assets.
- Soft-fails to `[]` so publishing can continue without images.

`services/intelligence_service.py`
- Collects aggregate Shopify funnel, acquisition, landing-page, sales, catalogue, content, and policy signals.
- Applies sample-size thresholds before creating evidence-backed recommendations.
- Uses the store's existing Grok text model to rank verified findings and write the management summary; Grok is not given customer identifiers and cannot introduce metrics.

`services/ga4_service.py`
- Optional read-only GA4 Data API connector for aggregate channel, device, and landing-page reports.
- Uses a GA4 property ID and service account saved from the Intelligence page.

### Reusable Backfill Command

Use the checked-in command when existing Shopify articles need featured images backfilled:

`cd ai-blog-generator-python-server && set -a && source ../.env && set +a && ../.venv/bin/python backfill_article_images.py --store-id store_1 --dry-run`

Common options:

- `--limit 10` to process only the first 10 missing articles.
- `--article-id 123456789` to target a specific missing article.
- `--verbose` to print each candidate or update as it runs.

The command reads active image model config from the SQLite DB and can use provider tokens from environment variables such as `REPLICATE_API_TOKEN`.

`services/logo_service.py`
- Composites logos and overlays onto generated or product images.
- Used heavily in preview/publish image handling.

`services/quality_service.py`
- Local deterministic quality gate.
- Scores drafts for title/excerpt depth, headings, CTA, filler, and internal duplication.
- Used by both manual publish and scheduler publish.

`services/publish_service.py`
- Shared scheduled-publish pipeline.
- Used by `scheduler.py`.
- Mirrors the manual publish path but without UI.

### Shopify API Integration

`shopify_client.py` is the main Shopify integration boundary for the Python server.

It owns:

- access token fetch and cache
- blog fetch
- product fetch
- article publish
- article delete
- product-image lookup
- shared product-guide metafield writes

Important implementation facts:

- Storefront product handles must be resolved through `/products.json?handle=...`; Shopify’s `/products/{id}.json` expects a numeric ID.
- Published product blogs can write shared merchant-owned metafields under `custom.ai_blog_related_guide_*`.
- The Python publisher does not own the Shopify app namespace. It writes shared `custom` metafields so the React app’s storefront block can render links regardless of which publisher created the post.

### Database Model

SQLite is initialized in:

- `db/base.py`

Core logical tables:

- `stores` — one row per managed Shopify store.
- `store_settings` — arbitrary per-store key/value settings.
- `models` — active AI providers and credentials per store.
- `prompts` — reusable prompt templates.
- `access_tokens` — cached Shopify access tokens.
- `generations` — publish history and text content used for duplicate checks.
- `generation_errors` — provider/runtime errors.
- `scheduled_jobs` — cron-like jobs, including `is_product_blog`.
- `intelligence_runs` — historical aggregate analysis snapshots.
- `intelligence_recommendations` — evidence, action, confidence, impact, effort, and lifecycle state for each finding.

Behavior-specific DB modules:

- `db/stores.py`
- `db/models.py`
- `db/prompts.py`
- `db/generations.py`
- `db/scheduled_jobs.py`
- `db/intelligence.py`

### Scheduling

`scheduler.py`
- Standalone async poller.
- Loads the same DB.
- Pulls due jobs every 60 seconds.
- Calls `services.publish_service.run()`.
- For product-blog jobs, it picks a random Shopify product before publishing.
- Checks every 15 minutes for stores whose 24-hour customer-intelligence refresh is due.

This file is the right starting point for scheduler bugs, missed runs, or cron behavior issues.

## Shopify React App: What It Owns

### Entry Points

Start here:

- `ai-blog-generator-app/app/shopify.server.ts`
- `ai-blog-generator-app/app/routes/app._index.tsx`

`app/shopify.server.ts`
- Configures the embedded Shopify app.
- Uses Prisma session storage.
- Exposes `authenticate`, `login`, webhook helpers, and session storage.

`app/routes/app._index.tsx`
- Main app-home workflow.
- Loads Shopify shop context and app settings.
- Accepts a freeform prompt.
- Optionally links the post to a selected product.
- Publishes to Shopify and returns a theme editor link.

### Server Helpers

`app/lib/blog-studio.server.ts`
- Main business logic for the embedded app.
- Loads blogs, products, scopes, and shop details from Shopify.
- Calls the configured Python generation backend.
- Normalizes the returned AI payload into a draft.
- Uploads local assets to Shopify Files.
- Publishes the article to Shopify.
- Writes product-guide metafields.

Important contract:

- The React app expects a backend endpoint at `POST /generate` that accepts `{ "prompt": string }` and returns a JSON payload with `title`, `summary`, `content`, optional `keywords`, optional `hashtags`, and optional `images`.
- The default backend base URL is `http://127.0.0.1:4000`, which matches the Python server’s default port in `ai-blog-generator-python-server/config.json`.
- If generation integration is failing, inspect this boundary first.

`app/lib/studio-settings.server.ts`
- Loads and saves per-shop app settings in Prisma.
- Covers brand context, target audience, CTA guidance, default author, preferred provider, prompt-ending presets, and social destinations.

`app/lib/studio-assets.server.ts`
- Saves generated data-URL images into `public/generated-assets/...`.
- Uploads those files to Shopify Files via GraphQL.

### Theme App Extension

Extension root:

- `ai-blog-generator-app/extensions/product-related-guide/`

Important file:

- `blocks/related-product-guide.liquid`

Behavior:

- Reads app-owned guide metafields from `$app.related_guide_*` when they exist.
- Falls back to shared merchant-owned `custom.ai_blog_related_guide_*` metafields.
- Renders a guide card on product templates.

Why the fallback exists:

- The React app can write both `$app` and `custom` metafields.
- The Python server can only safely write the shared `custom` metafields.
- The storefront block reads both so either publisher can feed the same product-page surface.

## How The Two Apps Fit Together

### Shared publishing model

Both the Python server and the Shopify React app publish blog articles to Shopify blogs.

### Shared product-guide link model

Both systems can link a blog post back to a product page.

Shared merchant-owned keys:

- `custom.ai_blog_related_guide_title`
- `custom.ai_blog_related_guide_url`
- `custom.ai_blog_related_guide_excerpt`

React-app-only app-owned keys:

- `$app.related_guide_title`
- `$app.related_guide_url`
- `$app.related_guide_excerpt`

If you are debugging “blog link not appearing on product page”, inspect these three places together:

1. `ai-blog-generator-python-server/shopify_client.py`
2. `ai-blog-generator-app/app/lib/blog-studio.server.ts`
3. `ai-blog-generator-app/extensions/product-related-guide/blocks/related-product-guide.liquid`

## High-Value Starting Points By Task

If the task is about login or setup UI:
- Python server: `routes/auth.py`, `routes/setup.py`, `templates/setup.html`
- React app: `app/shopify.server.ts`, auth routes, `shopify.app.toml`

If the task is about draft generation quality:
- Python server: `services/llm_service.py`, `services/quality_service.py`, provider modules

If the task is about image behavior:
- Python server: `services/image_service.py`, `services/logo_service.py`, `services/image_optimizer.py` (which runs real-time WebP optimization, compression, and resolution downscaling on all uploaded assets), `shopify_client.py`
- React app: `app/lib/studio-assets.server.ts`, `app/lib/blog-studio.server.ts`

If the task is about Shopify publish behavior:
- Python server: `routes/generate.py`, `services/publish_service.py`, `shopify_client.py`
- React app: `app/lib/blog-studio.server.ts`, `app/routes/app._index.tsx`

If the task is about schedule behavior:
- `scheduler.py`
- `routes/scheduler_routes.py`
- `db/scheduled_jobs.py`

If the task is about live-store cleanup or deletion:
- `routes/api.py`
- `shopify_client.py`
- `templates/history.html`

If the task is about the product-page blog block:
- `extensions/product-related-guide/blocks/related-product-guide.liquid`
- `app/lib/blog-studio.server.ts`
- `shopify_client.py`

## Commands Agents Will Use Often

Python server:

```bash
cd ai-blog-generator-python-server
/usr/local/bin/python3 -m pytest test_features.py -q --tb=short
```

Scheduler:

```bash
cd ai-blog-generator-python-server
/usr/local/bin/python3 scheduler.py
```

Shopify app typecheck:

```bash
cd ai-blog-generator-app
npm run typecheck
```

Shopify app dev:

```bash
cd ai-blog-generator-app
shopify app dev
```

## Current Gotchas

1. Product handles are not numeric product IDs. Resolve by handle first.
2. Manual publish and scheduled publish should stay aligned through the shared quality gate.
3. The Shopify app’s generation helper and the Python backend contract are a critical integration seam. Verify it before changing generation behavior.
4. The checked-in `ai-blog-generator-app/README.md` is the stock Shopify template README. Use this root `README.MD` as the repo-specific guide.
