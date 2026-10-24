# Outfy feature inventory (parity reference)

Researched 24 Oct 2026 from Outfy's help centre (`outfy.com/help/docs/...`), guides,
features/pricing pages and the Shopify App Store listing. "(unverified)" marks items
not confirmed by a primary source. This file is the checklist the Social Engine is
built against; Postiz handles channel connections and publishing.

## Content types
- **Product post** — product image(s) + caption. Multi-image caps: FB 5, X 4, Pinterest 4,
  IG 10, Threads 10. "Rotate images": each repost uses the product's next image.
- **Collages** — 1 product (several images) or up to 4 products. Ratios 4:5, 1:1, 9:16.
  Elements: title, logo (default off), Shop Now button, store URL, phone, price on/off.
- **Classic videos** — template slideshow from 1–4 products, 9:16 or 4:5 (1080×1350),
  image count + shuffle, colour/pattern backgrounds, stickers ("Buy Now", "Big Sale",
  "Best Offer", "Deal of the Day"), logo in 4 corners with opacity, background music.
- **Shorts / Reels / Stories** — 9:16 templates (Promotional: sale headline; Regular:
  opening/closing text + optional coupon; Video Ad: tagline + attributes). Brand-kit
  colours, ending CTA (default "Shop Now"), title/price/logo toggles. 6–21 s.
- **Long videos** — square or 9:16, 8–33 s.
- **Review posts** — positive reviews (Judge.me/Fera) on templates with stars + author.
- **Brand content** — Quote, Fact, Poll, Quiz, Brand Awareness, Brand Value (1–3/day).
- **Blog SmartQ** — blog posts newest first, rotated once all shared.
- **AI Studio** — AI video (UGC influencer / 3-part product video, 9:16), AI images
  (background enhance, product shoot scenes), paid in credits.
- GIFs (GIPHY), custom uploads, Canva import.

## Brand Kit
Business name, description and "what you sell" (feed AI captions), one logo, up to 5
colours (primary, text, CTA + 2 complementary), default language, timezone, currency
symbol (display only), "Include taxes" %.

## Automation — "SmartQ"
- Many named automations; ON/OFF; last successful run shown.
- Products: collections multi-select; order "Latest first", "Latest first, then random",
  "Random"; per-product "Exclude from automation"; only published, in-stock products.
- Volume: 0–3 posts per content type (product, collage, classic, short, long) per day
  per network, per automation.
- Timing: weekdays, time window, timezone; random time inside the window; generated one
  day ahead so price/stock are current.
- Platform daily caps (overflow moves to the next day): IG 24, X 3, FB 96, Pinterest 24,
  TikTok 10, YouTube 100, Threads 100.
- Repeats: "Allow reposting" off → never repeat; on → repeat only after every eligible
  product has posted once.
- Per-network options: FB page / Reels vs Stories / link vs image; IG Reels vs Stories;
  TikTok visibility, auto music, disclosure (Your Brand / Branded Content); YouTube
  category, privacy, Shorts; Pinterest board, section.

## Promotions — "PromoQ"
Themes (occasions such as Black Friday/Christmas, offers such as Flash/Clearance, New
Arrivals), scope (store-wide, category, specific products), coupon code, several promo
texts rotated at random, start date, duration ≤ 15 days, posts per network per day,
weekdays, time window, AI or custom caption, logo toggle.

## Captions & hashtags
- AI captions: language (17 supported), tone (friendly, professional, persuasive, casual,
  funny; luxury), price on/off, hashtags 1–10, use product tags, emojis (off / 2–4 / 5–8),
  CTA, product URL policy (all / all except Instagram / none / custom URL), product
  category hint, custom instructions ≤ 1000 chars; editable before publishing.
- Hashtag clouds: named sets, AI-generated, mapped to collections; up to 5 random tags
  added (not on Pinterest/Threads).
- Non-AI caption templates per network with placeholders PRODUCT_TITLE, PRODUCT_DESC,
  PRODUCT_PRICE, PRODUCT_TAGS, PRODUCT_URL, STORE_URL, TAG_CLOUD.

## Scheduling & management
Share page (post now or pick a time), scheduled queue with edit/delete/reschedule,
visual planner (calendar), multi-store, notifications on automation failure and
disconnected networks.

## Where Outfy is weak (we go further)
- No approval workflow → our approval queue + Gemma review + health-claim guard.
- Analytics are basic → Postiz per-post metrics, UTM links, roll-ups per product /
  template / channel / content type, best-time learning.
