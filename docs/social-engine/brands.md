# Brands: more than one business on Social Autopilot

Social Autopilot runs any number of brands. Each brand has its own settings, voice, channels, campaigns,
posts and image look. Bio Luxe Lab is a Shopify brand (posts come from the catalogue). RevenueMindProAI is
a brand with no catalogue (posts are brand content: tips, checklists, myths, service spotlights). Joe Bains
(`brands/joebains.json`) is the entertainer brand: one post a day in Joe's own voice (a funny thought, a
"try this", a quiz question, a myth, a lesson from his story, a "Book Joe" service post), each service post
linking to that service's own page.

## A brand is one file

`ai-blog-generator-python-server/brands/<name>.json` holds everything about a brand:

| Part | What it sets |
|---|---|
| `store` | id, name, website host |
| `logo_file` | PNG printed on every image |
| `postiz_group` | the brand's channel group in Postiz, by name. Required, see "Keeping brands apart" |
| `settings` | any Social Autopilot setting: brand colours and fonts, captions, schedule, `voice`, `catalog`, `ai`, `guards` |
| `campaigns` | campaigns to create, matched by name when the file is applied again |

Apply it through the running engine (creates the brand, or updates what differs; `name` is the file name
without `.json`):

```
curl -X POST -H "x-api-key: $SOCIAL_API_KEY" -H 'content-type: application/json' \
  127.0.0.1:4000/api/social-engine/brands/apply -d '{"name": "revenuemind"}'
```

To add a brand: copy `brands/revenuemind.json` or `brands/joebains.json`, change the store, logo, Postiz
group, voice, kinds, topics and campaign, and apply the file. No code changes. `logo_file` may be empty: the
brand's name is then printed where the logo would be.

The brand's Postiz group must exist, but it may still be empty. A brand applied before its channels are
connected is switched on and waits: nothing is written, no AI image is spent and nothing is posted. The
hourly channel sync finds channels as soon as they are connected in (or moved into) the brand's group, and
the campaign plans its first post at its next check, a few hours at most. To start at once: Social
Autopilot, choose the brand, Channels, *Sync channels*, then run the campaign.

## What makes a brand different (all in `settings`)

* `catalog.source`: `shopify` or `none`. With `none` every Shopify job (catalogue sync, photo audit,
  automatic promotions, shop-the-feed page) is skipped and the app hides the product pages.
* `voice`: who the AI writes as. `sector`, `compliance_rules`, `voice_rules`, `image_check`, `image_style`.
  A part left empty keeps the built-in UK wellness and food-supplement text, which is what Bio Luxe Lab uses.
* `voice.content_kinds`: the brand's own kinds of brand-content post. Each has a label, a brief (`guidance`),
  an image template, a tag printed on the image (`eyebrow`), how many on-image points to write (`points`)
  and whether each post is about one of the brand's services (`about_offering`).
* `voice.offerings`: the services, each with the only facts the writer may use about it.
* `brand.decoration` (`botanical`, `dots`, `none`) and `brand.heading_weight`: the look of text cards.
* `guards.health_claim_checks`: the food-supplement claim checks. Off for a brand with no health product,
  where "cure" and "fix" are figures of speech.
* `ai.writer`: `postiz` or `local`, see below.

Brand content campaign (`rule_type: brand_content`): `kinds` in posting order, `topics` rotated one per post,
`link_url` for every post (RevenueMind: the book-a-call page), `ai_background` for a Postiz AI scene behind
the card. Image templates for these: `info_points`, `info_points_dark`, `info_statement`.

## Who writes: Postiz AI or the local model

`ai.writer = "postiz"`: Postiz's own AI agent (the one behind "Generate with AI" in the Postiz composer,
reached through the `ask_postiz` tool of Postiz's MCP server) writes captions, brand content and promotion
lines. It gets the engine's whole brief: brand voice, compliance rules, facts and format. Then exactly as
before: the deterministic guard checks the text, the local model reviews it, a rejected draft is rewritten.
If Postiz does not answer, the local model writes. Reviews and planning are always the local model, so no
post is written and checked by the same model.

The agent can also schedule posts. It is told not to, and a reply in which it used any tool is discarded
with an error that says to check the Postiz calendar. Only the live system calls it.

Postiz AI images: `voice.image_style` plus the post's subject is the prompt. Budgets
(`postiz.ai_image_monthly_budget`, `ai_video_monthly_budget`) are counted per brand, so each brand's budget
is its share of the account's monthly allowance. Keep the sum within the plan (Pro: 300 images, 30 videos).

## Keeping brands apart

* In Postiz every channel belongs to a group (customer). A brand only syncs the channels of its own group.
  `brand-apply` refuses to switch a brand on until that group exists; without the group filter a brand
  would see every other brand's channels. A brand posts to its own group's channels and no others.
* In the app, the **Brand** switch at the top of Social Autopilot chooses which brand every page shows.
  The choice is one setting for the whole app, not per browser tab.
* Everything else that calls the engine (Task Agent, Telegram, scripts) keeps talking to the default store,
  Bio Luxe Lab, unless it passes `store_id`.
* API: `GET /api/social-engine/brands`, `POST /api/social-engine/brands/active {"store_id": "..."}`.
* Postiz sends its "post published" webhook to one address, Bio Luxe Lab's. Other brands' posts get their
  status from the reconcile job, which runs every ten minutes.
