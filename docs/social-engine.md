# Social Engine — Outfy-style auto-posting via Postiz

The Social Engine turns the Shopify catalogue into on-brand social posts and
publishes them through **Postiz Cloud (Team plan)**. It replaces the old
Publer-based "Social Posts" page. The local model (oMLX; set in the shared file `~/.config/local-ai/model.json`,
read by `services/local_model.py`; no code names a model, and the settings page only shows it) writes captions and
makes the auto-pilot decisions; everything is stored in the existing SQLite
database (`data/ai_blog_server.db`).

Merchant UI: Shopify admin → **Social Autopilot** (`/app/social`).
Feature parity reference: [`social-engine/outfy-parity.md`](social-engine/outfy-parity.md).
Postiz API reference (spec + docs snapshot): [`social-engine/postiz/`](social-engine/postiz/).

## How a post flows

```
Shopify ──sync/webhooks──▶ social_products (+ change events)
                                 │
campaigns (SmartQ, PromoQ, launch, price drop, …) ──plan──▶ social_planned_posts [planned]
                                 │  job post.generate (lane llm)
          Gemma captions + Pillow/ffmpeg creatives + per-channel targets
          content guard + Gemma review ──▶ [pending_approval] or [approved]
                                 │  person approves (or auto / ai_review)
                                 │  job post.publish (lane postiz)
          upload media → one Postiz create per time slot ──▶ [scheduled]
                                 │  job posts.reconcile (+ Postiz webhook)
                                 ▼
          [published] / [failed with the platform's exact error]
                                 │  job analytics.collect
                                 ▼
          social_post_metrics → roll-ups per product / template / channel / hour
```

## Code map (`ai-blog-generator-python-server/`)

| Path | What it owns |
|---|---|
| `services/social_engine/models.py` | Content types, media formats, **per-network profiles** (limits, link policy, hashtags, best times). Add a network here first. |
| `services/social_engine/settings.py` | All per-store settings (pydantic, stored as one JSON doc). Add a field with a default to add an option. |
| `services/social_engine/catalog/` | Shopify GraphQL sync, change detection (new product, price drop, back in stock…), webhook processing, eligibility. |
| `services/social_engine/creatives/` | Brand kit, declarative layout engine, built-in templates, ffmpeg product videos, media storage. |
| `services/social_engine/ai/` | LLM router (Gemma first, optional fallback model), captions, content guard, Gemma review, Gemma decisions (content plans, promo lines, brand content, insights). |
| `services/social_engine/campaigns/` | Rule framework + rules (`rules/`), product selection, occasion calendar, engine. |
| `services/social_engine/scheduling/slots.py` | Posting windows, caps, gaps, best times. |
| `services/social_engine/publishers/` | Publisher contract (`base.py`), Postiz client/publisher, extra Postiz servers (`combined.py`), dry-run publisher, webhook parsing. |
| `services/social_engine/pipeline/` | Job handlers: generate → publish → reconcile, freshness guard, user actions, channel sync. |
| `services/social_engine/analytics/` | Metrics collection and reports. |
| `services/social_engine/jobs/` | Durable queue, worker (leases, retries, lanes), recurring tasks. |
| `services/social_engine/health.py` | The "doctor" — checks every dependency. |
| `services/social_engine/cli.py` | Operations/debugging CLI. |
| `db/social_engine/` | All SQL. `schema.py` holds versioned migrations. |
| `routes/social_engine/` | HTTP API under `/api/social-engine` (backend API key), webhooks. |

Embedded app (`ai-blog-generator-app/`): `app/routes/app.social*.tsx`,
`app/lib/social-engine.server.ts`, `app/components/social/`, webhook relays
`webhooks.postiz.$storeId.$token.tsx` and `webhooks.products.tsx`.

## Reliability rules (why it is built this way)

* **Jobs, not inline work.** API calls enqueue jobs; the worker (inside
  `scheduler.py`) runs them with leases. A crashed worker's jobs are requeued.
* **Lanes** cap concurrency across processes: `llm` = 1 (oMLX serves one
  request at a time), `postiz` = 1, `shopify` = 1, `render` = 2.
* **Retries only when safe.** Reads retry with backoff. "Create post" is never
  blindly retried: an ambiguous failure marks targets `uncertain`, then the next
  run looks the post up in Postiz before creating anything.
* **Rate budget.** Postiz limits post creation per hour; the engine counts its
  own calls in `social_api_calls` and defers work instead of hitting 429s.
* **Idempotent planning.** Every planned post has a `dedupe_key`
  (e.g. `smartq:<campaign>:<date>:<type>:<n>`), so re-running rules never duplicates.
* **Freshness guard.** Posts snapshot price/stock; if Shopify changes before
  publishing, the post is pulled back and regenerated (or skipped if sold out).
* **Exact errors everywhere.** Every external call, job attempt, AI decision and
  webhook is recorded; warnings/errors also appear in System Health.

## Debugging

1. **Shopify admin → Social Autopilot → Overview → Run diagnostics.**
2. **Post detail page** shows the whole story of one post: media, every channel
   version, timeline, jobs, API calls (request/response excerpts), AI decisions.
3. **Activity page**: activity log, jobs (retry/cancel), API call log, webhooks.
4. CLI on the server (from `ai-blog-generator-python-server/`):

```bash
../.venv/bin/python -m services.social_engine.cli doctor
../.venv/bin/python -m services.social_engine.cli post <post_id>
../.venv/bin/python -m services.social_engine.cli jobs --status failed
../.venv/bin/python -m services.social_engine.cli job-retry <job_id>
../.venv/bin/python -m services.social_engine.cli calls --service postiz --errors
../.venv/bin/python -m services.social_engine.cli evaluate --campaign <id> --dry-run
../.venv/bin/python -m services.social_engine.cli worker --once
```

Useful SQL tables: `social_jobs`, `social_activity`, `social_api_calls`,
`social_decisions`, `social_webhook_events`, `social_planned_posts`,
`social_post_targets`.

## UK and US promotions

The store sells to the UK and the US from one web address (Shopify shows US visitors dollar prices).
Every promotion has a `market` (`services/social_engine/markets.py`):

| Market | Posts | Prices | Captions |
|---|---|---|---|
| `GB` (default, as before) | the store schedule, UK time | £ | British English |
| `US` | 09:00–21:00 New York time | Shopify's own US $ price (`contextualPricing`, country US); none if missing | American English |
| `both` | 12:00–22:00 UK time (UK afternoon/evening = US morning) | none (the offer is the message) | for both |

Discount codes work for every customer anywhere, whatever the market. The occasion calendar
(`campaigns/holidays.py`) marks each occasion's countries: shared days (Black Friday, Christmas…), festivals
(Diwali, Eid, Lunar New Year, Hanukkah, Holi, St Patrick's) and wellness days get one promotion for both; UK-only
(Boxing Day, UK Mother's Day, bank holidays) and US-only (Thanksgiving, 4th of July, Memorial/Labor/Presidents'
Day, US Mother's Day, Small Business Saturday) get their own. Lunar festival dates are a table
(`FESTIVAL_DATES`, to 2030): extend it before 2031. Wellness days now get a sale as well as a greeting.

## Shoppable page (bio link)

Instagram and TikTok posts can't carry a link, so their captions say "link in bio". `services/social_engine/shoppable.py`
keeps one Shopify page (default `/pages/shop-our-feed`) showing the products most recently posted, newest first, each
opening its own product page with `utm_source=linkinbio`. Put the page's address in the Instagram and TikTok bios.

- Settings → Shoppable page (`shoppable.*`): on/off (off by default), address, title, intro line, how many products
  and how many days back.
- Job `shoppable.refresh` runs hourly; Shopify is written only when the page changed. `POST /api/social-engine/shoppable/refresh`
  rebuilds it now, `GET /api/social-engine/shoppable` says where it is and what's on it.
- Products shown: posted within the window, active, in stock, with a picture (`products.list_recently_posted`).
- Only a page made here is ever updated (its body starts with a marker comment). If another page already has the
  address the refresh stops with a message; nothing is overwritten.

## Extra Postiz servers

Social Autopilot can post through more than one Postiz: the main one (Postiz Cloud) plus extra servers, for example
the self-hosted one on this Mac (`docs/postiz-local.md`) that holds channels the main one doesn't have.

- Settings → Postiz → Extra Postiz servers (`postiz.servers`): short name, label, API address (ends in
  `/public/v1`), the name of the `.env` variable that holds its API key, hourly post budget, on/off. Nothing about a
  server is in the code; the key itself is only ever in the server `.env`.
- `publishers/combined.py` puts them behind the one publisher. Every channel and post on an extra server has the
  server's short name in front of its id (`local:cm…`), in the database too, so each call goes to the right server.
  Don't rename a server once it has posted.
- Its channels appear on the Channels page after a channel sync and get posts like any other channel.
- Pictures and videos are always uploaded to the main Postiz. An extra server on a private address can't hand files
  to the networks, but it accepts the main Postiz's public file address, so one upload serves both. The main Postiz
  therefore has to be working for an extra server's posts with media.
- One create request goes to one server (`pipeline/publish.py` groups targets by time and server). Each server has
  its own hourly budget and its own name in the API-call log (`postiz:<name>`).
- If an extra server is down, the main one carries on: the server's channels are kept (not marked removed), its
  posts are left alone by the reconciler, and the health check says which server is not working. A missing key only
  stops that server's channels.
- Postiz webhooks come from the main Postiz only; posts on an extra server are followed by the reconciler.
- AI images, AI videos, clipping and the media library are the main Postiz's.

## Adding features

* **New occasion:** add an `Occasion` (with its `countries`) and its date in `campaigns/holidays.py`, and a
  `PromotionTheme` in `campaigns/themes.py` (optional short code in `extra={"code": …}`).
* **New auto-pilot rule:** add a `CampaignRule` subclass in
  `campaigns/rules/` with a pydantic `config_model`, decorate with
  `@register_rule`, import it in `rules/__init__.py`. The UI form is generated
  from the config model's JSON schema.
* **New template:** add it to the creatives template library; seed with
  `cli seed-templates`.
* **New network:** add a `ProviderProfile` in `models.py` and its settings
  builder in `publishers/postiz_settings.py`.
* **New job type:** `@job("area.action", lane=...)` in a handler module listed
  in `jobs/__init__.py:HANDLER_MODULES`; optional `@on_final_failure`.
* **New recurring task:** append a `PeriodicTask` in `jobs/periodic.py`.
* **Schema change:** append a `Migration` in `db/social_engine/schema.py`.

## Going live (first time)

1. Postiz → Settings → Developers → Public API: copy the key into the server
   `.env` as `POSTIZ_API_KEY`, restart backend + scheduler.
2. Connect the channels in Postiz (Team plan: up to 10). Optional in Postiz:
   time slots, signatures, sets, plugs, RSS auto-post (these stay Postiz features).
3. Social Autopilot → Channels → **Sync channels**; choose a Pinterest board,
   Instagram post type, TikTok privacy, caps per network.
4. Overview → switch the engine **on** (this syncs the catalogue and installs
   templates). Start with the dry-run publisher if you want a rehearsal.
5. Templates → check the brand kit; Campaigns → create a SmartQ campaign,
   use **Preview** (dry run) before activating.
6. Optional webhooks: Channels page shows the Postiz webhook URL (Postiz →
   Settings → Webhooks). Shopify product webhooks are declared in
   `shopify.app.toml` and take effect after `shopify app deploy`.

Production releases follow `SERVER_README.md` (build locally, back up SQLite,
restart launch agents). The Social Engine schema migrates automatically on
backend/scheduler start.
