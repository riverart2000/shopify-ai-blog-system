# Plan: Richer Shopify blogs — multi-image, internal links, Pinterest

## Goal
Three combined enhancements to the Python backend publish pipeline (authoritative; covers manual + scheduled):

- **A.** Each blog gets 3-4 images of DIFFERENT types (hero photo, infographic, step-by-step visual card, checklist/tips card, + vertical Pinterest pin).
- **B.** Each blog gets 3-4 internal links to other store blog posts AND products — relevance-ranked, shown BOTH inline (woven by LLM from a validated candidate list) AND as a "Related reading" block at the end.
- **C.** Pinterest sharing optimisation: vertical pin image, `data-pin-*` attrs, AI pin description, Save-to-Pinterest button.
- **D.** Every blog ends with a clearly rendered footer of 3-5 long-tail keywords + 3-5 hashtags (built from the real search-intent phrases the keyword service already discovers).

### User decisions (confirmed)
- Image types: hero photo, infographic, step-by-step visual card, checklist/tips card, vertical Pinterest pin (all five).
- Links: BOTH inline + related block; mix of blogs + products; relevance-ranked by keyword/title overlap.
- Keep Pinterest work in this plan.
- Blog-link candidate source: **A** (local `generations` primary, live `fetch_store_articles` fallback).

## Platform facts (confirmed)
- System has direct Shopify MCP/Admin API access (full) — already wrapped by `shopify_client.fetch_store_articles()` and `fetch_products()`.
- SQLite DB (`db/`) — `generations` table stores every published blog (`title`, `summary`, `keywords`, `hashtags`, `article_url`, `content_text`, status). Primary source for blog→blog link candidates; fall back to live `fetch_store_articles` when sparse/new store.
- Production access via `prod.sh` → `ssh -i ./revenuemindproai/revenuemindproai.priv ubuntu@18.134.80.37`. **SAFETY:** prod publishes write live articles to the real merchant Shopify store — any end-to-end prod test must target a draft/test blog handle or be explicitly approved first. No destructive ops without approval.

## Key files / current state
- `services/image_service.py` — `generate_images()` returns `[photo, infographic]` (2 imgs). `_build_photo_prompt`/`_build_infographic_prompt`. NEED: 5 typed sources — hero photo, infographic, step-by-step visual card, checklist/tips card, vertical pin.
- `services/logo_service.py` — `stamp_photo`/`stamp_infographic` composite via Pillow → JPEG data URI, soft-fail. NEED `stamp_pin()` (1000x1500 2:3) + a reusable visual-card stamper for step/checklist layouts.
- `shopify_client.py`:
  - `_build_article_html()` (line ~589) + `publish_article()` (line ~664) — image embedding + tags. NEED: spread N images across sections, add `data-pin-*` attrs, render related-links block, accept `pin_description` + `internal_links`.
  - `fetch_store_articles(store, limit_per_blog)` (line ~225) and `fetch_products(store, limit)` (line ~351) — candidate sources for internal linking. EXCLUDE the post being written.
- `llm.py` `_JSON_CONTRACT` (line ~49) + `_validate_blog_dict` (line ~104) + `_build_user_prompt` (line ~67) — add `pin_description`; feed candidate link list and ask LLM to weave 3-4 inline links using ONLY provided URLs.
- `services/llm_service.py` (line ~15) `generate_text` + `services/publish_service.py` `run()` (line ~167) — build candidate link list, pass to LLM, validate returned links, pass images+links+pin_desc to publish.
- `routes/generate.py`, `routes/api.py` — manual/preview/publish paths mirror publish_service; thread same params.
- `routes/setup.py` `save_sharing_config` (line ~430) + setup context (line ~110) — add `share_pinterest` + `pinterest_board_url`.
- `templates/setup.html` Social Sharing card (line ~516); `templates/result.html` share-section (line ~267) + script — Pin it button + preview.

## Steps

### Phase A — Multiple typed images (3-4 per blog)
- **A1.** `image_service.py`: add `_build_step_card_prompt`, `_build_checklist_card_prompt`, and `_build_pin_prompt`. Refactor to a typed generator returning a list of `{url, type, label}` — types: `hero_photo`, `infographic`, `step_card`, `checklist_card`, `pin`. Each generated independently; soft-fail per type so partial sets still publish (target 3-4 usable, pin separate).
- **A2.** `logo_service.py`: add `stamp_pin()` (2:3 crop + title bar + logo) and a reusable visual-card stamper for numbered-step and checklist layouts on a branded background. Keep soft-fail.
- **A3.** `shopify_client._build_article_html`: distribute body images (hero first/featured, others interleaved between `</p>` blocks; cap and label). Skip the pin from inline body if reserved for pinning. Add `alt` text per image.
- **A4.** Thread the typed image list through `publish_service.run`, `routes/generate.py`, `routes/api.py` (replace the 2-item list). Update `quality_service` image_count expectations (3-4).

### Phase B — Internal links (3-4 to blogs + products)
- **B1.** New `services/internal_links.py`: candidate pool from TWO sources —
  - Blog candidates: `db.get_recent_generations(store_id, limit=...)` primary; fall back to live `shopify_client.fetch_store_articles` when sparse/new store. Score by keyword/title token overlap vs current post.
  - Product candidates: `shopify_client.fetch_products(store)` (live; optional TTL cache). Score by title/keyword overlap.
  - Exclude the current post (match on title/article_url). Return top 3-4 as `{title, url, kind}` mix preferring ≥1 product + ≥1 blog.
- **B2.** Inline links: pass candidate list into `llm._build_user_prompt`; instruct LLM to weave 2-3 links using ONLY provided URLs/anchors. After generation, VALIDATE every `<a href>` against the candidate allow-list; strip/replace any hallucinated URL. (**Security:** never let the model invent URLs.)
- **B3.** Related block: append a deterministic "Related reading" `<ul>` of the 3-4 candidates at article end (always present even if inline weaving fails).
- **B4.** Thread candidate list through publish paths (`publish_service.run`, `routes/generate.py`, `routes/api.py`).

### Phase C — Pinterest sharing
- **C1.** `logo_service.stamp_pin()` (from A2) builds the pin image.
- **C2.** `_build_article_html`: add `data-pin-description` to content imgs, `data-pin-nopin="true"` on logo/decorative imgs, render pin image with `data-pin-media`.
- **C3.** `publish_article`: optional `pin_description` param (fallback = summary + hashtags).
- **C4.** `llm.py`: add optional `"pin_description"` field to contract + validation fallback; surface in `llm_service`.
- **C5.** `routes/setup.py` + `templates/setup.html`: `share_pinterest` checkbox + `pinterest_board_url`.
- **C6.** `templates/result.html`: Pin it button (`pinterest.com/pin/create/button/?url=&media=&description=`) + optional Follow link.

### Phase D — Long-tail keyword + hashtag footer
Every published blog ends with a clearly rendered block of **3-5 long-tail keywords** and **3-5 hashtags**.
- **D1.** `llm.py` `_JSON_CONTRACT`: add `"long_tail_keywords": array of 3-5 specific multi-word search phrases` (distinct from the existing broad `keywords`). Keep `hashtags` but tighten guidance to 3-5 niche/long-tail tags. Update `_validate_blog_dict` (optional, with fallback that derives long-tail phrases from `keywords`/title when missing).
- **D2.** `services/keyword_service.py` already produces long-tail phrases (Reddit/Quora/Google autocomplete via `_normalise_title`, 3-15 words). Feed the top 3-5 pooled long-tail phrases into the prompt/footer so the footer uses real search-intent phrases, not just model output.
- **D3.** `shopify_client._build_article_html`: replace/extend the existing visible tags section with a labelled footer — e.g. a "Keep exploring" / "Topics" block listing the 3-5 long-tail keyword phrases, and a hashtag row beneath. Keep the hidden SEO keyword div for crawlers.
- **D4.** Thread `long_tail_keywords` through `llm_service` → `publish_service.run` / `routes/generate.py` / `routes/api.py` → `publish_article` → `_build_article_html`.
- **D5.** Verify the footer renders on both manual and scheduled publish, and degrades gracefully (falls back to broad keywords) when no long-tail phrases are available.

### Phase E — Title sanitisation (bug fix: "H2"/"H3" leaking into titles)
**Root cause:** the blog title is taken verbatim from the model's JSON `title` field with no cleaning. When the model emits `"H2: ..."`, `"## ..."`, `"### ..."`, `"**Title**"`, or wraps it in quotes, that leaks into the published Shopify article title. No sanitiser exists anywhere in the title path.
- **E1.** Add a shared `clean_title()` helper (in `utils.py`) that strips: leading markdown heading markers (`#`, `##`, `###`), literal `H1`/`H2`/`H3`/`H4` prefixes (with or without a trailing `:`/`.`/`-`/space), `Title:`/`Heading:` prefixes, surrounding quotes, markdown bold/italic asterisks/underscores, and collapses whitespace. Conservative — only strips known formatting noise, never real words.
- **E2.** Apply `clean_title()` at every point a title is set from model output:
  - `providers/deepseek.py` `_validate`/after parse (shared by `openai_provider.py` + `local.py`).
  - `llm.py` legacy `generate_text` / `_validate_blog_dict`.
  - `services/title_service.py` `_parse_title_array` (title-pool entries).
- **E3.** Defensive clean at publish boundary too: `shopify_client.publish_article` cleans `title` before building the article payload, so any path that bypasses the generators is still covered.
- **E4.** Existing already-published articles: add a `backfill_article_titles.py` command (mirrors `backfill_article_images.py` — `--store-id`, `--dry-run`, `--limit`, `--article-id`, `--verbose`) that fetches articles, detects titles with leading heading-marker noise, and `PUT`-updates the cleaned title. Dry-run first; never bulk-update prod without `--dry-run` review + approval.
- **E5.** Tests: unit-test `clean_title()` across cases (`"H2: ..."`, `"## ..."`, `"### Title"`, `'"Quoted"'`, `"**Bold**"`, clean titles unchanged); assert generators + publish output are clean.

## Verification
1. `cd ai-blog-generator-python-server && /usr/local/bin/python3 -m pytest test_features.py -q --tb=short` — existing image/publish tests pass (update count-based assertions 2→3-4).
2. Manual generate+publish: article body has 3-4 distinct images (hero/infographic/step/checklist) with alt text; a 2:3 pin image present.
3. Article has 3-4 internal links — ≥1 product + ≥1 blog; every `<a href>` resolves to a real store URL from the candidate list (no hallucinated links); "Related reading" block at the end.
4. Result page shows Pin it button with prefilled url/media/description.
5. Soft-fail paths: missing Pillow → stampers return originals; image provider failure → blog still publishes with fewer images; no candidate links → related block omitted gracefully.
6. Scheduled publish (`publish_service.run`) produces same images + links + pin.
7. Every blog ends with a visible footer of 3-5 long-tail keyword phrases + 3-5 hashtags; hidden SEO keyword div retained; graceful fallback to broad keywords when no long-tail phrases exist.
8. Titles are clean: no `H2`/`H3`/`##`/quote/bold noise in generated, pooled, or published titles; `clean_title()` unit tests pass; `backfill_article_titles.py --dry-run` reports existing offenders.
9. Production (via `prod.sh` SSH): deploy, then generate ONE post to a draft/test blog handle and inspect rendered article + Pin button. Do NOT mass-publish to live blog handles without approval.

## Decisions
- Scope = Python backend (authoritative; manual + scheduled). React app OUT unless requested.
- Image types: hero photo, infographic, step-by-step visual card, checklist/tips card, vertical pin (pin reserved for pinning, may also appear at end).
- Links: inline (LLM-woven from validated allow-list) + deterministic "Related reading" block; mix of blogs + products; relevance-ranked by keyword/title overlap; current post excluded.
- Security: all inline `<a>` URLs validated against the fetched candidate allow-list — model cannot introduce arbitrary URLs.
- Rich Pin `og:*` head meta still OUT (theme `<head>`); article keeps `global.description_tag`.
- Pin description falls back to summary + hashtags when LLM omits it.
- Blog-link candidate source: LOCKED = A (local `generations` primary + live fetch fallback). Products always live via `fetch_products`.

## Further considerations
1. Step/checklist cards: A) Pillow-composited numbered/checklist cards on solid or brand background (reliable) — recommended; B) AI-generated cards (prettier, less predictable).
2. Min links guarantee: if store has <3-4 eligible posts/products, fall back to fewer links rather than padding with irrelevant ones.

---

# Multi-platform sharing — analysis & recommendations

These blogs will be re-shared across dozens of platforms (Pinterest, LinkedIn, Facebook, Substack, X/Twitter, Reddit, Instagram, Medium, Quora, newsletters, etc.). Each platform pulls and ranks content differently. Below is what each one needs, where the current system already helps, and concrete recommendations.

## The single highest-leverage fix: Open Graph + Twitter Card meta tags
Almost every platform (Facebook, LinkedIn, Pinterest, X, Slack, WhatsApp, Discord, iMessage) builds its share preview from `<head>` meta tags — `og:title`, `og:description`, `og:image`, `og:type`, `article:published_time`, `twitter:card`, `twitter:image`. Right now the system only sets `global.description_tag`. The share preview image/title/description are therefore controlled by the **theme**, not per-article, so every share can look generic or wrong.
- **Limitation:** these tags live in the theme `<head>` and cannot be injected from article `body_html`.
- **Recommendation (high priority):** add a small **theme app embed / snippet** (in the existing `ai-blog-generator-app` theme extension, or a `theme.liquid` snippet) that, on `article` templates, emits per-article `og:*` + `twitter:card` tags from the article's fields (title, excerpt, featured image). This is what unlocks correct previews on *every* platform at once and makes Pinterest Rich Pins validate.
- Pair it with a 1200×630 landscape `og:image` (the hero) AND keep the 1000×1500 pin image for Pinterest.

## Per-platform notes

### Pinterest
- Needs: tall 2:3 (1000×1500) image, keyword-rich pin description (≤500 chars), `og:image`, Rich Pin meta (`og:type=article`).
- Covered by Phase A (pin image), Phase C (pin description + button), and the OG embed above.
- Extra: a board-organised, keyword-front-loaded description performs best; first ~50 chars matter most.

### LinkedIn
- Pulls `og:title`/`og:description`/`og:image`; 1200×627 image ideal. No hashtags in the preview, but 3-5 professional hashtags in the post body help reach.
- Recommendation: generate a short **LinkedIn-framed teaser** (problem → insight → CTA, professional tone) — the React app already has an `externalShareConfig` scaffold to hold this.

### Facebook
- Same OG tags; 1200×630. Engagement favours a conversational 1-2 sentence hook + link.
- Recommendation: a `facebook.postText` field (already scaffolded in `externalShareConfig`).

### X / Twitter
- `twitter:card=summary_large_image`, `twitter:image`, `twitter:title`. 280-char limit → 1-2 long-tail hashtags only.
- Already partly supported on the result page (tweet intent + `via=` handle). Add the twitter card meta via the OG embed.

### Substack / Email newsletters
- Substack imports by URL and reads OG tags; renders a subtitle + teaser. Plain, scannable HTML with real `<h2>`/`<p>`/`<ul>` (already produced by `text_to_html`) travels well.
- Recommendation: a `substack.subtitle` + `teaser` (scaffolded) and keep paragraphs short. Avoid hidden 1px SEO divs in the email body — strip those for the newsletter variant.

### Reddit / Quora
- No link previews to speak of; success depends on a genuinely useful title + first paragraph. Over-optimised/hashtag-stuffed titles get downvoted.
- The keyword service already mines Reddit/Quora intent — lean on that for natural titles. The Phase E title cleanup directly helps here.

### Instagram
- No clickable links in captions; relies on the image + a caption with 5-15 hashtags and "link in bio."
- Recommendation: the vertical pin/quote-card images double as IG-ready visuals; generate an `instagram.caption` (scaffolded) with a denser hashtag set than the blog footer.

### Medium / Dev.to / canonical syndication
- Reads OG tags; **set `rel=canonical` back to the Shopify article** when syndicating to avoid duplicate-content SEO penalties.

## Cross-cutting recommendations (priority order)
1. **Per-article OG + Twitter Card meta** via theme embed — biggest multiplier; fixes previews everywhere + Rich Pins. *(new work, recommended next)*
2. **Two image aspect ratios per post**: 1200×630 landscape (OG/LinkedIn/FB/X) + 1000×1500 vertical (Pinterest/IG). Phase A already adds the vertical; ensure a clean landscape hero exists for OG.
3. **Platform-specific caption variants** (LinkedIn/FB/X/Substack/IG) generated at publish and shown on the result page for copy-paste — the React app's `externalShareConfig` already defines the shape; wire the Python backend to populate it.
4. **Clean, hook-first titles & first paragraph** (Phase E + the "don't repeat title in content" rule already in the contract) — matters most on Reddit/Quora/email subject lines.
5. **Long-tail keyword + hashtag footer** (Phase D) — helps on-page SEO and gives sharers ready hashtags, but keep platform-tuned hashtag counts (IG dense, X sparse, LinkedIn 3-5).
6. **Canonical URL discipline** when syndicating to Medium/Dev.to.
7. **UTM tagging** on the shared URLs per platform (`?utm_source=pinterest`…) so analytics attribute traffic correctly — cheap to add to the result-page share links and pin/OG URLs.

## Suggested follow-up phases (not yet scoped into A-E)
- **Phase F — OG/Twitter meta theme embed** (unlocks correct previews + Rich Pins everywhere). *High priority.*
- **Phase G — per-platform caption generator** populating `externalShareConfig` + result-page copy blocks.
- **Phase H — UTM-tagged share links** on the result page and Pin/OG URLs.
