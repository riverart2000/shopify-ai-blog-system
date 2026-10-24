# Automation Engine — Outfy-style social auto-poster for bioluxelab.com

Handoff brief for Claude (or anyone) building this on the M5. Read fully before writing code.

## 1. Goal
Replicate the core of Outfy (https://www.outfy.com) for our own Shopify store **bioluxelab.com**:
pull products from Shopify → generate on-brand creatives (images / collages / short videos + captions) →
schedule → publish to social channels → report performance **per product**.

Single-tenant, internal tool. No Shopify OAuth, billing, or multi-store support needed.

## 2. Key decisions (already made — do not relitigate)
| Area | Decision |
|---|---|
| Posting / scheduling / analytics | **Postiz Cloud, Team plan** (https://postiz.com). We do NOT rebuild scheduling, channel OAuth, posting, analytics, plugs, RSS, etc. Use its public API to the max to justify the subscription. |
| App | **Next.js (App Router, TypeScript)** — dashboard, templates, campaign rules, approval queue, analytics |
| DB | **SQLite** via Drizzle ORM + better-sqlite3 (single file, backed up) |
| Jobs | `jobs` table + in-process worker (poll loop / node-cron). No Redis/BullMQ. |
| Creatives | Satori / `@vercel/og` (React → PNG) for static; **Remotion** for short video. Rendered locally = free & unlimited. Postiz AI image/video credits only for occasional hero assets. |
| LLM | `LLMProvider` interface. Default: **Gemma 4 26B (8-bit) on the M5** via OpenAI-compatible endpoint (reachable over Tailscale). Fallback: **Claude API** (cloud) for higher-stakes copy. |
| Publisher | `Publisher` interface with one implementation: `PostizPublisher`. Keep it swappable. |
| Reads / triggers | Postiz webhooks → our app. Optional: local n8n for anything Postiz can't read (comments, mentions). |
| Rust / Python | Not used in v1. |
| Hosting | Runs on the M5 alongside the existing project (integrate/share infra where sensible — see §9 open questions). |

## 3. What Outfy does (feature parity target)
1. **Product sync** – products, variants, images, prices, collections, inventory, discounts.
2. **Content types** – single product post, multi-product collage, sale/discount promo, new-arrival launch, restock, "product of the day", collection round-up, countdown, short video/reel, Pinterest pins.
3. **Templates** – branded, editable (logo, colours, fonts, badge text like "NEW" / "-20%").
4. **Auto-pilot campaigns** – rules like "new product → 3-post launch series over 7 days", "price drop → sale post within 1h", "daily product-of-the-day at 18:00".
5. **Smart scheduling** – per-channel cadence limits, best-time slots.
6. **Approval queue** – everything lands here first; one-click approve/edit/reject; optional full auto.
7. **Multi-channel** – IG (feed + reel), FB, TikTok, Pinterest, YouTube Shorts, Threads, LinkedIn, Bluesky, X.
8. **Analytics** – impressions/engagement/clicks per post, rolled up **per product and per template** (our differentiator over plain Postiz).
9. **UTM links** – every post links back with `utm_source=<channel>&utm_campaign=<campaign>&utm_content=<post_id>`.

## 4. Architecture
```
Shopify (Admin GraphQL + webhooks)
        │  sync
        ▼
┌──────────────────────────── Next.js app (M5) ─────────────────────────────┐
│ SQLite ── products / campaigns / templates / posts / jobs / analytics      │
│ Worker ── sync → plan (rules engine) → generate (LLM + render) → publish   │
│ UI ───── dashboard · products · templates · campaigns · queue · analytics  │
│ API ──── /api/webhooks/shopify  /api/webhooks/postiz  /api/jobs/*          │
└───────────────┬───────────────────────────┬───────────────────────────────┘
                │ OpenAI-compatible          │ Postiz public API
                ▼                            ▼
      Gemma 4 26B (M5, Tailscale)      Postiz Cloud (Team) → socials
      Claude API (fallback)            webhooks + analytics back to us
```

### Core interfaces
```ts
interface LLMProvider { complete(req: {system?: string; prompt: string; json?: boolean; temperature?: number}): Promise<string> }
interface Renderer    { render(template: Template, ctx: RenderCtx, format: Format): Promise<{path: string; mime: string}> }
interface Publisher   {
  listChannels(): Promise<Channel[]>
  uploadMedia(file: string): Promise<{id: string; path: string}>
  schedule(post: PlannedPost): Promise<{externalId: string}>
  findSlot(channelId: string): Promise<Date>
  delete(externalId: string): Promise<void>
  analytics(externalId: string): Promise<PostMetrics>
}
```

### Data model (Drizzle, SQLite)
- `products`, `variants`, `product_images`, `collections` (+ `synced_at`, raw JSON column)
- `templates` (type, format, brand tokens, layout JSON)
- `campaigns` (rule type, trigger config, channel set, cadence, status)
- `posts` (product_ids, campaign_id, template_id, channel, caption, media paths, scheduled_at, status: draft|pending_approval|approved|scheduled|published|failed, postiz_post_id, utm)
- `jobs` (type, payload JSON, run_at, attempts, status, error)
- `post_metrics` (post_id, impressions, likes, comments, shares, clicks, fetched_at)
- `settings` (brand kit, channel cadences, auto-approve flags)

## 5. Postiz API — what to use
Base: `https://api.postiz.com/public/v1` (confirm in docs). Auth: raw API key in `Authorization` header (no "Bearer").
OpenAPI spec: https://docs.postiz.com/public-api/openapi.json — generate a typed client from it.

Endpoints to wire (see https://docs.postiz.com/public-api/):
- `GET /integrations` (channels), `find-slot`, `is-connected`, `settings` (per-provider required fields)
- `POST /upload` / upload-from-url, `media-status`
- `POST /posts` (type `now` | `schedule`, multiple posts per call), `posts/list`, `delete`, `change-status`
- `GET /analytics/platform`, `GET /analytics/post`
- Video: `/video/generate` (uses Postiz AI credits — gate behind a setting)
- Per-provider settings pages under `/public-api/providers/*` (TikTok privacy level, YouTube title, Pinterest board, etc.)
- Rate limit: **100 create-post requests/hour** (cloud). Batch scheduling accordingly.

Also use in the Postiz UI (no code): plugs (auto-repost/boost), sets & signatures, RSS auto-post, AI agent, MCP server (phase 2: let Gemma drive Postiz via MCP for ad-hoc tasks).

## 6. Shopify
- Create a **custom app** in the Shopify admin → Admin API access token (scopes: `read_products`, `read_inventory`, `read_discounts`, `read_price_rules`, `read_collections`? (verify names)).
- Use Admin **GraphQL** (`2025-xx` stable version). Full sync nightly + webhooks: `products/create`, `products/update`, `products/delete`, `inventory_levels/update`, `discounts/create`.
- Webhook endpoint must be publicly reachable: use a Cloudflare Tunnel or Tailscale Funnel to the M5. Verify HMAC.

## 7. LLM usage
- `GEMMA_BASE_URL` (OpenAI-compatible, e.g. `http://<tailscale-ip>:<port>/v1`), `GEMMA_MODEL` — **ask Joe for the exact server (Ollama / LM Studio / vLLM) and model name**.
- Tasks: captions per channel (tone presets), hashtags, hooks for reels, alt text, A/B variants, product summaries, campaign naming. Always request JSON output and validate with zod.
- `ANTHROPIC_API_KEY` for fallback (`LLM_FALLBACK=claude`). Route by task importance or on Gemma failure/timeout.

## 8. Build plan (phases — complete and run each before moving on)
**Phase 1 – Skeleton**
- `pnpm create next-app` (TS, App Router, Tailwind), Drizzle + SQLite, env validation (zod), `.env.example`
- Shopify client + full product sync + webhook route; products page in UI
- Postiz client (generated from OpenAPI) + `/api/health` that lists connected channels

**Phase 2 – Creatives**
- Brand kit settings (logo, colours, fonts)
- 3 Satori templates: product card 1:1, Pinterest 2:3, story/reel cover 9:16
- 1 Remotion template: 10–15s product reel (image pan/zoom + price badge + CTA)
- Caption generator via `LLMProvider` with per-channel variants

**Phase 3 – Pipeline**
- Jobs worker; rules engine for: new product, price drop, product-of-the-day, collection round-up
- Approval queue UI (approve / edit caption / regenerate / reject / auto-approve toggle)
- Publish via Postiz (`upload` → `posts`), store `postiz_post_id`, handle per-provider settings

**Phase 4 – Feedback loop**
- Postiz webhook receiver; nightly analytics pull → `post_metrics`
- Dashboard: performance per product / template / channel; "best performing" feeds rule weights

**Phase 5 – Nice-to-haves**
- Postiz MCP + Gemma agent; n8n reads (comments/mentions); Postiz AI video for hero launches

## 9. Open questions for Joe (ask before Phase 1 if unanswered)
1. Gemma endpoint URL, server type, model name.
2. Which channels to connect first (decides which templates/formats to build first).
3. Existing project on the M5: name/path, how it's run (pm2? docker? launchd?), shared DB or separate, port conventions — so this integrates cleanly instead of duplicating infra.
4. Public ingress for webhooks: Cloudflare Tunnel vs Tailscale Funnel vs existing reverse proxy.
5. Brand kit assets location (logo, fonts, colour hex codes).

## 10. Conventions
- TypeScript strict, pnpm, ESLint/Prettier. Small commits per phase.
- Secrets only in `.env` (never committed); `.env.example` kept current.
- Every external call wrapped with retries + logged to `jobs.error`.
- Keep Joe's preference in mind: **short responses, confirm before large chunks of work, no scope creep.**
