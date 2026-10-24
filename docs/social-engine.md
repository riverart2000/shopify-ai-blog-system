# Social Engine — Outfy-style auto-posting via Postiz

The Social Engine turns the Shopify catalogue into on-brand social posts and
publishes them through **Postiz Cloud (Team plan)**. It replaces the old
Publer-based "Social Posts" page. Local **Gemma 4 26B** (oMLX) writes captions and
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
| `services/social_engine/publishers/` | Publisher contract (`base.py`), Postiz client/publisher, dry-run publisher, webhook parsing. |
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

## Adding features

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
