# Free product feeds — status and owner actions (2 Nov 2026)

Store: Bio Luxe Lab (bioluxelab.com, Shopify `bioluxelab` / xfxs8g-hn.myshopify.com).
Checked on 2 Nov 2026 through the owner's signed-in Chrome and the Shopify Admin API.
Nothing was installed, uninstalled, purchased or connected. One setting was changed (see "What was changed").

## Summary

- Google free listings are live and healthy, but they are **not being shown**: under 20 impressions and 0 product clicks in the last 28 days across 739 offers. The problem is reach, not approval.
- All four Part B channels are **already installed and connected**: Microsoft Channel, Pinterest, Facebook & Instagram, Shop. Pinterest, Facebook/Instagram and Shop have live catalogues. **Microsoft's feed is empty (0 products published) and the channel has shown an error since 19 Aug 2026**; fixing it needs the owner to sign in to Microsoft Advertising.
- Three Google offers are held (2 not approved, 1 limited); all three are fixable in Shopify by the owner (one image, one unreachable variant page, one false adult-content flag to appeal).
- The 210 "Art Prints" products are genuine Bio Luxe Lab items: size variants of three yoga/chakra wall-art products. Nothing was edited.

## Part A — Google Merchant Center (account 5806332677)

### Free-listings performance (Analytics > Products, last 28 days)

| Metric | Value |
|---|---|
| Impressions (ads + organic, product pages) | under 20 |
| Clicks on products | 0 |
| Purchases | 0 |
| Overview "Total clicks" | 5 (down 28.6%); these are not product clicks |

The per-product table (Analytics > Products > "View all products") returns **"No results"**: no single offer has enough impressions to be listed, so a top-ten-by-impressions list cannot be produced. Conclusion: listings are approved but effectively **not shown**, not shown-and-ignored. Merchant Center's "Click potential: Low" on every product is consistent with that.

Where to look later: `https://merchants.google.com/mc/reporting/performance/products?a=5806332677` → "View all products".

### Held offers (Products > Needs attention)

| Offer | Status | Reason (Google) | Fix (owner, in Shopify) |
|---|---|---|---|
| Open-Ear Bone Conduction Sports Headphones, Black / "China Mainland" variant | Not approved | Product page unavailable | The variant named "China Mainland" is a supplier shipping-origin option, not a real buying choice. In Shopify > Products > that product, delete or rename the "Ships from" option so the variant page resolves, or unpublish the variant from Google. |
| Peanut Spiky Massage Ball, 15 cm, Purple | Limited | Adult-orientated content (false positive on title/images) | In Merchant Center > Products > Needs attention, open the offer and click **"Request review"**; also check the supplier photos for printed claims (engine `social_photo_audits`). |
| Foldable EVA Travel Yoga Mat, 0.4 cm, Blue | Not approved | Promotional overlay on image | Replace the feed image: in Shopify the first image of the Blue variant has printed promotional text. Use a clean product shot as the variant image. |

"Prioritised fixes" in Merchant Center reports everything resolved; these three sit under "View all issues".

### "What to do next" items

1. **Update descriptions for Art Prints (210 products).** "Art Prints" is Google's category for size variants of three Shopify products: *Yoga Pose Line Art Canvas Print Unframed* (77 variants), *Yoga and Chakra Canvas Wall Art* (32) and *Chakra Meditation Wall Tapestry* (18). They are Bio Luxe Lab wellness-decor products, so this is not a foreign catalogue. Google's only suggested addition is a "Theme" key detail; the Shopify descriptions already contain material, size and theme. Not edited. If the owner wants the suggestion applied, it is a Shopify description edit on three products, not 210.
2. **Add 26 products found in your online shop / Download 26 products.** Google crawled 26 product pages that are not in the Shopify feed. The Shopify catalogue has 52 products (50 active, 2 drafts: a portable bath tub and an electric kettle) and 377 variants; Google already holds 371 UK and 368 US offers from the "Shopify App API" sources. The 26 are most likely crawl duplicates (collection/variant URLs) plus the two drafts. **Not clicked**: auto-added products would bypass the Shopify-managed feed (no price/stock sync). Recommend ignoring, or letting it auto-add on 24 Nov and reviewing then. Page: Settings > Data sources > "Found by Google".
3. **Add checkout to your listings.** Reviewed and **not enabled**. The account-level setting (Business info > "Edit checkout settings") takes one URL template containing `{id}`, which Google fills with the feed's offer `id`. The Shopify Google channel sends ids like `shopify_GB_<productId>_<variantId>`, while a Shopify cart link needs the bare variant id (`/cart/<variantId>:1`), so no template built from Shopify's checkout URL pattern works. Enabling would need the per-product `checkout_link_template` attribute, which the Shopify channel does not expose. Left on "Don't include a URL".
4. **Build trust with your customer service.** Validated. Merchant Center had only the email. Confirmed `info@bioluxelab.com` appears 6 times on the public contact page and 9 times on the refund policy; `https://bioluxelab.com/pages/contact` returns 200. Phone and live chat are not offered by the store, so left blank.

### US region ("Missing signals")

Shop quality is rated **Great** for both the UK and the US. The US flag is for missing signals only: no return window/cost (the only return policy in Merchant Center is "Standard for United Kingdom", 30 days, free, verified) and no store rating. Delivery to the US is synced as free, 6–9 days (rated "Fair" on delivery time).

Should the US be targeted? Shopify ships to GB, US, AU, CA, NZ with free shipping and has an enabled "United States" market, and the Google channel mirrors Shopify markets automatically (368 US offers, 367 approved). Keeping it costs nothing and may bring some free-listing traffic. The clean way to remove it, if the owner only wants UK buyers, is **Shopify > Markets > United States > deactivate**, which makes the Google, Facebook and Shop channels drop the US feed together. If kept, the single improvement is to add a US return policy: Merchant Center > Delivery and returns > Return policies > "Add return policy" (owner-only, it asserts a legal policy).

### What was changed in Merchant Center

- Business info > Customer service: **Website URL set to `https://bioluxelab.com/pages/contact`** (saved, visible under "Customer service contact"). Nothing else was changed.

### Other Merchant Center facts worth knowing

- Data sources: two "Shopify App API" sources (GBP feed, 371 products; USD feed, 368 products), both updated by the Shopify Google & YouTube channel.
- Delivery policies: free UK 6–9 days, free US 6–9 days, both "Complete".
- Shop quality (US scorecard): high-resolution images only 32% (upload larger supplier photos to move this), 10.3 images per offer ("Exceptional"), eWallets 3/4.
- Google & YouTube app in Shopify: Merchant Center, Google Analytics (G-YE01T87VFV) and Google Business Profile all connected; product sync and shipping sync both On. The app shows a "Local inventory: not syncing" warning, which only matters for Business Profile local listings.

## Part B — other free channels

All channel apps were already installed; the Shopify API lists publications for Online Store, Shop, Pinterest, Google & YouTube, Inbox, Facebook & Instagram, Microsoft Channel and TikTok. Publication coverage from the API: 50 of 52 products on Online Store/Google/Shop, 49 on Pinterest/Facebook/Microsoft/TikTok (the *Handmade Black Obsidian Beaded Bracelet* is unpublished from Pinterest and Facebook; the *Premium Anti-Slip Resistance Loop Bands* from Microsoft and TikTok).

### 1. Microsoft Merchant Center (Bing Shopping) — connected but empty

| | |
|---|---|
| App | Microsoft Channel, installed 19 Aug 2026, "Active", 1 market (UK), pixel connected |
| Accounts | Google account bioluxelab@gmail.com; Microsoft Advertising "Bio Luxe Lab" CID 255022426 / AID 189338422; Microsoft Merchant Center store "Bio Luxe Lab"; requirements checklist 4/4 |
| Feed | Language English, Country of sale United Kingdom |
| Products | **0 published, 0 awaiting review, 0 rejected** in the summary; the product list shows every product as "Awaiting Review"; last sync 19 Aug 2026 15:53 "Success" |
| Error | Settings page shows "An unknown error occurred. Please refresh the page or try again"; Shopify alerts from 19 Aug: "There's an issue with Microsoft Channel" (x3) |

Reading: the one-off sync on install day ran, but nothing reached the Microsoft Merchant Center store, and the daily sync has not run since. This cannot be fixed from Shopify alone.

**Owner must:** sign in at `https://ads.microsoft.com` with the Microsoft account used on 19 Aug (my attempt redirected to the sign-in page with "session expired"), open Tools > Microsoft Merchant Center > store "Bio Luxe Lab" > Catalog management, and check whether a catalog/feed exists and its status. Then in Shopify > Microsoft Channel > Settings, click **Disconnect** next to "Connect to Microsoft services" and reconnect the same Advertising account and store so the app re-creates the feed; after that check Microsoft Channel > Products shows "Published" within 3–5 business days.

Stopped at: Microsoft Advertising sign-in page (owner credentials).

### 2. Pinterest catalogue — live

| | |
|---|---|
| App | Pinterest, installed 4 Jun 2026, "Active", pixel connected, products synced "just now" |
| Marketing | Connected |
| Catalog | Connected: **354 approved, 0 not approved** |

Nothing pending. Shipping/returns are not part of the Pinterest catalogue; contact details come from the Pinterest business account. One optional owner step: Shopify > Pinterest > Settings, confirm the product group/feed market is UK (GBP). The *Obsidian bracelet* is unpublished from Pinterest; publish it from the Shopify product page if intended.

### 3. Facebook and Instagram Shops — live, one wrong setting

| | |
|---|---|
| App | Facebook & Instagram, installed 18 Jun 2026, "Active", 2 markets (UK, US), pixel connected |
| Shops | Facebook shop "Bio Luxe Lab" Active; Instagram shop "Bioluxe Lab" Active |
| Products | **48 approved, 1 rejected** (title not readable without clicking into Meta Commerce Manager) |
| Business details | Contact email info@bioluxelab.com; **Return policy shows "Returns not accepted"** |
| Ads | "Connect pixel/dataset" prompt for the ad account (ads only, not needed for free shops) |

Shopify's own return rules are "30-day returns" (Settings > Policies), so the Meta shop is showing a worse policy than the store offers. This affects buyer trust and Meta's shop eligibility.

**Owner must:** Shopify admin > Sales channels > Facebook & Instagram > **Settings** tab > Business details > **Change** > set returns to 30 days > Save (Meta may ask to confirm in Commerce Manager). Then on the Overview tab click **"View all synced products"** > filter Rejected to see the one rejected item and its reason. A Facebook cookie dialog pops up inside the app; it was left unanswered.

### 4. Shop app — live

| | |
|---|---|
| App | Shop, installed 5 Apr 2026, 2 markets, "Active" |
| Requirements | 7 of 7 complete, **Approved** |
| Selling method | Sell directly in Shop (Shop Pay On, local currencies On) |
| Store | Live, 1 follower; **49 products selling directly, 1 prohibited product** |
| Last 30 days | 0 Shop orders, 0 referral orders |

**Owner must:** Shopify admin > Sales channels > Shop > Overview > click **"1 Prohibited products"** to see which product and why (likely the same item Google flagged as adult content, the peanut massage ball, but unverified). Shop's "Promise" page (delivery promise) is worth enabling if the dropship delivery estimates can be entered; it is optional.

### Also seen

- **TikTok** channel installed 3 weeks ago: "Needs attention", pixel disconnected, 0 functions active. Out of scope for free listings; TikTok Shop UK needs the owner's TikTok seller verification.
- **Google & YouTube**: everything connected; only "Enable Google Customer Reviews" is offered on its Overview, which needs the owner to accept Google's programme terms.

### Shipping, returns and contact carry-over

| Channel | Shipping | Returns | Contact |
|---|---|---|---|
| Google | Synced from Shopify (free, 6–9 days, UK + US) | UK 30-day free policy verified; US none | Email + contact page (added today) |
| Microsoft | Country of sale UK in feed settings; nothing live because the feed is empty | n/a until feed works | n/a |
| Pinterest | Not applicable | Not applicable | Pinterest business account |
| Facebook/Instagram | From Shopify markets (UK, US) | **Wrong: "Returns not accepted"** | info@bioluxelab.com |
| Shop | Shopify shipping profiles | Shopify 30-day return rules | Shopify store contact |

Shopify side: one default "General profile" with free shipping to GB, US, AU, CA, NZ, plus three inactive supplier profiles; policies (refund, shipping, privacy, terms, contact, legal) all published; default return rule 30 days.

## Rejected / held products, all channels

| Channel | Approved | Held | Reasons |
|---|---|---|---|
| Google (variants) | 736 | 3 (2 not approved, 1 limited) | page unavailable; promotional overlay; adult content (false positive) |
| Microsoft | 0 | 0 (feed empty) | sync broken since 19 Aug |
| Pinterest | 354 | 0 | — |
| Facebook/Instagram | 48 | 1 rejected | reason inside Meta Commerce Manager |
| Shop | 49 | 1 prohibited | reason behind the Shop "Prohibited products" link |

## Expected traffic value, one line each

- **Google free listings:** potentially the largest free source, but today it delivers nothing (under 20 impressions a month); without ads it will stay small until the site earns organic authority and more reviews, so treat it as a long-term asset, not a short-term driver.
- **Microsoft/Bing Shopping:** small UK share (roughly a tenth of Google's), but free listings there are far less competitive; once the feed syncs, expect tens of impressions a day and the occasional click, costing nothing.
- **Pinterest:** the best fit for wellness decor, yoga and self-care products; shoppable Pins can bring steady low-volume traffic if the social engine keeps pinning, and the catalogue is already clean.
- **Facebook and Instagram Shops:** low organic reach for a new page; value comes mainly from product tags in the engine's posts and from the shop looking complete, so fixing the returns setting matters more than the one rejected product.
- **Shop app:** near zero discovery for a store with 1 follower; it mostly adds Shop Pay convenience and a verified-store badge, not traffic.

## Owner checklist (exact clicks)

1. `https://ads.microsoft.com` > sign in > Tools > Microsoft Merchant Center > "Bio Luxe Lab" > check catalog; then Shopify > Microsoft Channel > Settings > Disconnect/reconnect Microsoft services.
2. Shopify > Facebook & Instagram > Settings > Business details > Change > Return policy: 30 days.
3. Shopify > Facebook & Instagram > Overview > View all synced products > Rejected (read reason, fix in Shopify).
4. Shopify > Shop > Overview > "1 Prohibited products" (read reason).
5. Shopify > Products > Open-Ear Bone Conduction Sports Headphones > remove the "China Mainland" shipping-origin option.
6. Shopify > Products > Foldable EVA Travel Yoga Mat > replace the Blue variant image that carries printed text.
7. Merchant Center > Products > Needs attention > Peanut Spiky Massage Ball > Request review.
8. Decide on the US: keep (then add a US return policy in Merchant Center > Delivery and returns > Return policies) or deactivate Shopify > Markets > United States.
9. Optional: Google & YouTube > Overview > "Enable Google Customer Reviews" > Get started (accept programme terms).

## How this was checked

Merchant Center pages were read through the OpenClaw relay into the owner's Chrome (tabs under merchants.google.com); Shopify channel app pages render inside cross-origin iframes, so they were read from screenshots. Product and publication data came from the Shopify Admin GraphQL API using the Social Engine's client credentials.
