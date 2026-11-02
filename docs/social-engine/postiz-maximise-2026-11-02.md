# Making full use of the Postiz Team plan — 2 November 2026

What was switched on, where it lives, and how the monthly allowances are split.

## Allowances and who spends them

| Postiz Team allowance | Monthly | Spent by | Budgeted |
|---|---|---|---|
| AI images | 100 | RSS auto-post "generate picture" (~1/day) + engine `ai_image_ad` posts + AI backgrounds on promotions | engine cap `postiz.ai_image_monthly_budget = 60`, leaving ~30 for RSS and ~10 spare |
| AI videos | 10 | engine `ai_video` posts (2 a week, Mon + Thu) | engine cap `postiz.ai_video_monthly_budget = 10` |
| Channels | 10 | all 10 used (YouTube added 2 Nov) | — |
| Video clipping | 120 min | not yet: needs founder recordings | — |
| Webhooks | 10 | engine reconcile webhook | — |

Both caps are enforced locally in `publishers/postiz_tools.py` before a credit is spent, by counting the ledgered MCP calls for the last 30 days. The MCP HTTP timeout was raised from 120 s to 300 s: every failed image generation so far was a `ReadTimeout`, not a Postiz error.

## Engine (shopify-ai-blog-system) — live since the 03:41 deploy

Settings (`store_settings` → `social_engine.settings`):

- `schedule.posts_per_channel_per_day` 2 → 4 (the planner still spaces posts ≥ 2 h apart inside 08:00–21:00 London).
- `captions.signature` = "New here? 10% off your first order with code WELCOME10", appended after the link line and before the hashtags on every channel (`captions.signature_providers` empty = all). It is the first thing dropped after hashtags when a channel limit is tight. Postiz's own signatures only apply inside its composer, never to API posts, which is why the engine adds it.
- `captions.custom_instructions` = hook-first brief (feeling/moment/problem first, one benefit, UK English, no spec lists, no health claims).
- The Shopify code **WELCOME10** exists (10% off, once per customer, no end date, combines with shipping discounts only). Discount node `gid://shopify/DiscountCodeNode/2292956823897`.

Campaigns (all `ai_review`, all active). SmartQ now accepts `ai_image_ad` and `ai_video`, and a new `posts_per_week` map spreads low-volume content across the week (`weekly_days(n)`: 1 → Mon, 2 → Mon+Fri, 3 → Mon+Wed+Fri).

| Campaign | Rule | Volume | Channels |
|---|---|---|---|
| Product posts (image set) | smartq `single_product` | 1/day | Pinterest, Instagram, Facebook, X, Threads, LinkedIn page, Google Business |
| Product videos (video set) | smartq `product_video` | 1/day | TikTok, Instagram, Facebook, Pinterest, YouTube |
| AI lifestyle ads (Postiz images) | smartq `ai_image_ad` | 1/day | Instagram, Facebook, Pinterest, X, Threads, LinkedIn page |
| AI hero videos (Postiz video credits) | smartq `ai_video` | 2/week | TikTok, Instagram, YouTube, Facebook |
| Blog articles (Pinterest + Instagram) | blog_smartq | 1/day | Pinterest, Instagram |
| Halloween 2026 | promotion | ended 1 Nov | — |

Joe's personal LinkedIn is deliberately left out of product campaigns; it only receives blog articles through the RSS auto-post.

## RSS lane (Postiz Auto Post)

- Source: `/Users/joebains/rss_aggregator_bioluxelab` now runs **on the M5**, not AWS (the AWS `rss-aggregator` unit had been failed since 22 Oct and was never moved at migration). The M5 Caddy already proxied `/publar*` and `/apps/rss*` to `127.0.0.1:18090`.
- Public feed: `https://revenuemindproai.com:8443/publar/rss` (also `/apps/rss` for the full merged feed).
- Drip reset to zero on 2 Nov: `start_time 2026-11-02T08:30:00Z`, exactly 24 h between releases, newest article first, so one article appears at 08:30 UK each day. Articles published after the start time bypass the queue and appear as soon as they are live. Links use `bioluxelab.com` (new `RSS_AGG_PUBLIC_DOMAIN`), not the myshopify domain.
- Postiz Auto Post "Bio Luxe Lab journal (drip feed)": autogenerate content, generate picture, post when found, channels X, Facebook, LinkedIn (Joe), LinkedIn page, Threads, Google Business. Pinterest and Instagram get articles from the engine's Blog SmartQ instead (own image template, UTM links, health-claim guard), so no article is posted twice on one channel.
- Launch agent: `com.joebains.shopify-blog.rss` — the plist is in the aggregator folder; install with
  `cp /Users/joebains/rss_aggregator_bioluxelab/com.joebains.shopify-blog.rss.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.joebains.shopify-blog.rss.plist`.
  Until then it runs as a plain background process started from `run_m5.sh`; health at `http://127.0.0.1:18090/health`.

## Postiz UI configuration (platform.postiz.com → Settings)

- **Signatures**: "New here? 10% off your first order with code WELCOME10 at bioluxelab.com", auto-add on (composer only).
- **Plugs**: auto-plug at 3 likes on X, LinkedIn page and Threads (follow-up with the tracked shop link + WELCOME10); auto-repost at 5 likes on X and LinkedIn page. Thresholds are low on purpose; raise them as reach grows.
- **Webhooks**: "Bio Luxe social engine (reconcile)" → the frontend relay `https://revenuemindproai.com:8443/webhooks/postiz/store_1/<token>` (token from `POST /api/social-engine/settings/webhook-token`), all channels. The Send Test call was received by the engine (`social_webhook_events`).
- **Sets**: not created. The set composer refuses to save until every selected channel's required settings are filled (Pinterest board, TikTok post type, YouTube "made for kids"), which is a manual five-minute job in the composer and only helps posts written by hand in Postiz; the engine already applies its own channel sets per campaign.
- **Time slots**: left as they are; the RSS auto-post posts when the item appears (08:30) and the engine schedules its own times.

## Still owner-only

- Record 2–3 long phone videos a month for the 120 clipping minutes (acupressure mat, massage gun after a run, why the wide-toe shoes).
- Install the RSS launch agent (one command above) so the feed survives a reboot.
- Watch the first AI image/video posts in the approval queue; `ai_review` holds anything the vision check scores low.

## Self-correction after a rejected review (added 2 November, afternoon)

Until now any draft the Gemma reviewer rejected went straight to the approval queue. Two of the five queued posts were false rejections (the reviewer saw a cut-down description that stopped before "six colour choices", and it judged the words in a product's web address), and the other three were fixable by a rewrite. The engine now does that itself:

- **Grounding.** `prompts.products_block` adds a `Variants (N options; only these exist): …` line from the catalogue variants, and the reviewer gets the same product detail as the writer (1100 characters of description, previously 700). The review prompt says a fact anywhere in the product data counts as supported.
- **Links.** The reviewer sees captions with every URL replaced by `[link]`; the content guard strips URLs before scanning for claims, so a handle like `…-stress-relief-…` no longer holds a post.
- **Fix loop** (`pipeline/selfcorrect.py`, `guards.max_review_rewrites`, default 2). When the guard holds or the reviewer rejects: the captions are rewritten with the reviewer's issues and suggestions as feedback (`CaptionRequest.feedback`); when the vision check flagged the image, the next product photo is used (`image_offset + 1`). Each fix is a full generate → guard → review round, recorded in the post timeline as "Review found issues; … (fix n of 2)". The post only reaches the queue when the fixes are used up or nothing the engine can change would help (a claim printed on the product's only photo). `generation.review_fixes` and a warning on the post show how many fixes were made.
- `post.generate` job timeout 1200 s → 2400 s to cover three rounds on the local model.

### Blockers removed later the same day

- **Signature treated as an invented discount.** The reviewer flagged "code WELCOME10" (the line the engine itself appends) as unsupported on every rewrite, so the fix loop could never succeed. The review prompt now carries a STORE FACTS section with the signature and the brand CTA (`review._store_facts`).
- **Supplier photos with printed claims** ("Slim Legs", "Slender Leg Lines", "Arms-Slimming"). Trying the next photo did not help when every photo has such text. New photo audit (`ai/photos.py`, table `social_photo_audits`, migration 3): each product photo is shown once to the vision model and the verdict is kept by URL. Generation renders only clean photos (`clean_products`), the AI video reference images use the same list, a product with no clean photo fails generation with a clear message and is skipped by `eligible_products`, and the Products page shows "Photos blocked" / "n of m photos skipped" plus a "Photos need changing" count. A periodic job `catalog.photo_audit` (every 30 min, 5 products a run, llm lane) audits the catalogue ahead of planning. If the vision model is down nothing is stored and unaudited photos count as clean, so posting never stops because the checker is unavailable.
