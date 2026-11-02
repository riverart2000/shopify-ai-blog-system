# Hero offers, coaching and workshops — build notes (2 November 2026)

Hormozi-style offers for Bio Luxe Lab: three kits with a value stack, a named guarantee, digital bonuses and a founder call; a coaching and workshops line; a lead magnet with email capture; upsells; and the WELCOME10 replacement. Everything is **unpublished**: a duplicate theme, draft products, a draft page. Nothing a customer can see has changed. Owner approves copy, records the videos, then publishes with the steps in section 9.

Companion files
- `docs/store/founder-video-briefs-2026-11.md`: the ten video briefs (FV-01 to FV-10).
- `docs/store/hero-offer-assets/`: the two PDFs and their HTML sources.
- `scripts/storefront/hero-offer/`: the scripts that built it, the product and collection ids, and `publish.py` (publish and rollback).

---

## 1. What was built, and where

| Thing | Where | State |
|---|---|---|
| Duplicate theme **"Hero offers 2026-11 (copy of live)"** | Online Store > Themes, id 210264916313 | Unpublished. Exact file-for-file copy of the live theme (id 209606738265, "Updated copy of BioLuxeLab Modern Botanical Pre…", verified by checksum, JSON templates compared after parsing), plus the changes in section 3. Editor: `https://admin.shopify.com/store/bioluxelab/themes/210264916313/editor` |
| 3 kit products, built as **Shopify Bundles** (the owner installed the Bundles app at 13:20; the earlier standalone kit drafts were deleted and replaced) | Products, type "Kit", tags `hero-kit`, `bundle`; Bundles app lists them | **Draft**. Ids in `scripts/storefront/hero-offer/created-products-2026-11-02.json`. Inventory follows the components (36/24, 24, 282 on 2 Nov) |
| 4 service products | Products, types Coaching / Workshop / Programme, tags `service`, `no-social`, `no-feed` | **Draft** |
| Collection **Hero Kits** (`/collections/hero-kits`) | id 721072193881 | Created; empty on the storefront until the kits go active |
| Collection **Coaching & Workshops** (`/collections/coaching-and-workshops`) | id 721072226649 | Created; Online Store only |
| Page **Meet Joe** (`/pages/meet-joe`) | id 725710733657, template `page.meet-joe` | **Unpublished**, draft bio for Joe to edit |
| 10 founder-video placeholder images | Files, `founder-video-placeholder-FV-01.png` … `FV-10.png` | Used by every video slot |
| Lead magnet PDF **The 7-Night Wind-Down Plan** (11 pages) | Files: `https://cdn.shopify.com/s/files/1/1037/0849/2121/files/bio-luxe-lab-7-night-wind-down-plan.pdf` | Ready to link from the welcome email |
| Bonus PDF **The 30-Day Routine Tracker** (3 pages, A4 landscape) | Files: `https://cdn.shopify.com/s/files/1/1037/0849/2121/files/bio-luxe-lab-30-day-routine-tracker.pdf` | Ready to link from the order email |
| Complementary-product links (Search & Discovery) | metafield `shopify--discovery--product_recommendation.complementary_products` on 17 products | Live data, but only visible on the duplicate theme's "Complete the routine" section |

Not done, by design: nothing installed by the session (the owner installed Shopify Bundles and Shopify Messaging himself during the build; no Forms, no booking app), no policy changed, no menu changed, the Social Engine signature unchanged (section 7).

Facts that shaped the build
- Plan: Shopify **Basic**. Shopify Bundles was installed by the owner during the build, so each kit is a real bundle: component products and quantities set through `productBundleCreate`, bundle variants priced below the component sum (section 2.4 covers fulfilment).
- "Messaging" in the installed apps (`shopify-messaging`, admin path `/apps/shopify-messaging`) is **Shopify Messaging**, the renamed Shopify Email: email, SMS and WhatsApp campaigns plus automations. It is the email tool for the welcome flow (section 5). Shopify Inbox (store chat) is a separate sales channel and is not involved.
- Capture uses the theme's own email-signup block (Shopify customer list, marketing consent). Shopify Messaging's automation "Welcome new subscribers" picks those subscribers up.
- Booking: no booking app; v1 uses dated variants and a Calendly link (section 2.3).
- Microsoft Channel's publication is rejected by the API ("not publishable"; the channel has been broken since August, see `docs/growth/free-product-feeds-2026-11.md`), so kits cannot be attached to it until the owner reconnects the channel.
- Judge.me metafields show imported review counts (for example 15 reviews, 4.8 stars on the mat) on a store with no sales. The kit and service pages use an honest "no reviews yet, we never show imported ones" block; see section 8 for the risk on existing pages.

---

## 2. The offers

### 2.1 Kits (prices before supplier shipping; landed margins in 2.2)

**The Evening Wind-Down Kit** (`/products/evening-wind-down-kit`, £79, compare-at £91.52, variants Matte Black mat / Dusty Pink mat)

| Component | Variant in the kit | Supplier stock (2 Nov) | Standalone £ | Supplier cost £ |
|---|---|---|---|---|
| Essential 4mm Anti-Slip Yoga & Pilates Mat | Matte Black (58782927028569) or Dusty Pink (58782927094105) | 36 / 24 | 34.56 | 5.22 |
| Grooved EPP Foam Roller | Black blue (58782925390169) | 292 | 29.73 | 6.15 |
| Monochrome Watercolour Meditation Cards, 36 | 58782935122265 | 9,981 | 27.23 | 4.14 |
| Sum / cost | | | **91.52** | **15.51** |

Bundle variants "Matte Black Roll" and "Dusty Pink Yoga Mat" (the option values are the mat's own, which Bundles requires); stock follows the mat variant (36 / 24). Substitution: the brief suggested the Fifty Mindful Affirmation Cards; supplier stock is 9, so the Meditation Cards (same job) are used.

**The Pilates-at-Home Starter Kit** (`/products/pilates-at-home-starter-kit`, £74, compare-at £89.31)

| Component | Variant | Stock | Standalone £ | Cost £ |
|---|---|---|---|---|
| Ergonomic Pilates Resistance Ring | Black (58782924636505) | 24 | 26.93 | 3.62–4.40 |
| Essential EVA Foam Yoga Block | Blue (58782925685081) | 79 | 14.41 | 2.20 |
| Resistance Band with Handles | 15lb (58782926373209) | 45 | 12.37 | 1.98 |
| Foldable EVA Travel Yoga Mat | 0.3cm Black (58782927323481) | 84 | 35.60 | 5.41 |
| Sum / cost | | | **89.31** | **13.2–14.0** |

Bundle stock follows the lowest component, the ring (24 Black); watch it. Only the ring's clean images are used; seven of its ten supplier photos carry body-shaping text and must never appear. The two-block set (stock 10) and the loop bands (stock 9) were swapped for the single block and the handled band.

**The Post-Run Recovery Kit** (`/products/post-run-recovery-kit`, £69, compare-at £75.93)

| Component | Variant | Stock | Standalone £ | Cost £ |
|---|---|---|---|---|
| Deep Tissue Massage Gun | 6-speed compact (58835959480665) | 282 | 46.20 | 7.43 |
| Grooved EPP Foam Roller | Black blue (58782925390169) | 292 | 29.73 | 6.15 |
| Sum / cost | | | **75.93** | **13.58** |

Bundle stock follows the gun (282). The massage gun's "Quick Muscle Healing" supplier image is not used. The Four-Corner Fascia Ball would be the natural third item; it has at most 8 units per colour, so add it when restocked.

Every kit's value stack also carries: the follow-along founder video (FV-03 / FV-05 / FV-07, unlisted YouTube link once recorded), the 30-Day Routine Tracker PDF, the 7-Night Wind-Down Plan PDF (Evening kit), and **a 20-minute Routine Check-in call with Joe** (the bonus nobody else can offer; one per kit order, booked via Calendly). Guarantee: **The 30-Night Promise** (section 2.5). Delivery promise: free UK delivery, dispatched in 1–3 working days, usually with you in 3–14 days, tracked, parcels may arrive separately.

### 2.2 Margin maths, headline and landed

The DSers snapshot gives supplier cost **before** supplier shipping. Shipping assumption, to be checked in DSers before publishing: AliExpress Standard / Choice shipping to the UK at **£3.50 per component parcel** on average (small items £2–3, the mat about £5). Shopify Payments 1.9% + 25p on the kit price, no VAT line (the store is below the threshold; if VAT-registered, take 1/6 off the price before anything else), no ad cost.

| Kit | Price | Supplier cost | Headline margin | Supplier shipping (assumed) | Payment fee | **Landed margin** | Landed % |
|---|---|---|---|---|---|---|---|
| Evening Wind-Down | 79.00 | 15.51 | 63.49 (80%) | 3 × 3.50 = 10.50 | 1.75 | **51.24** | 65% |
| Pilates Starter | 74.00 | 13.60 | 60.40 (82%) | 4 × 3.50 = 14.00 | 1.66 | **44.74** | 60% |
| Post-Run Recovery | 69.00 | 13.58 | 55.42 (80%) | 2 × 3.50 = 7.00 | 1.56 | **46.86** | 68% |

Sensitivity: every £1 of extra supplier shipping per parcel takes £3 / £4 / £2 off the three landed margins. The 20-minute call costs Joe's time, not cash. A refund under the promise costs the outbound shipping already paid plus the components (they come back to Norwich, not to the supplier), so the worst case per returned kit is about the landed cost, £26–30.

Path to £10k a month profit at these margins: about 200 kits a month at an average £48 landed, or 120 kits plus 40 coaching or workshop sales. Organic only until the kits have reviews; that is the plan.

### 2.3 Coaching & Workshops (collection `coaching-and-workshops`)

| Product | Handle | Price | Variants, stock | Shipping | Template |
|---|---|---|---|---|---|
| Routine Reset Session with Joe (1:1, 60 minutes) | `routine-reset-session` | £65 | one, untracked | none | `product.service-coaching` (FV-09 slot) |
| Evening Reset Workshop (online, 90 minutes) | `evening-reset-workshop` | £35 seat; £99 seat + Evening Wind-Down Kit (compare-at £114) | Date × Ticket: **Tue 17 Nov 2026 19:30** and **Tue 1 Dec 2026 19:30** (PLACEHOLDER DATES), 5 seat-only + 3 with-kit per date = 8 | kit variant only | `product.service-workshop` (FV-10 slot) |
| The 28-Day Routine Reset (4-week group programme, kit included) | `28-day-routine-reset` | £149 | Cohort 1 **starts Mon 23 Nov 2026**, Cohort 2 **starts Mon 11 Jan 2027** (PLACEHOLDER DATES), 10 each | yes (kit) | `product.service-coaching` |
| Learn to Juggle Online Workshop (90 minutes) | `learn-to-juggle-online-workshop` | £25 | **Thu 26 Nov 2026 19:00** (PLACEHOLDER), 8 seats | none | `product.service` |

Pricing reasoning (confirmed by the owner): Apprentus anchors are $83/h for coaching (≈ £62) and $66 for the meditation and mysticism sessions; £65 rounds the 1:1 to a UK-friendly figure, a £35 group seat is an easy first purchase, the £99 "seat + kit" variant is the "ticket includes a kit" play (kit £79 + seat £35 = £114, so it saves £15 and still carries about £83 of margin before shipping), and the programme at £149 with the kit included is priced well under four 1:1 sessions (£260) so the group route is the obvious choice. Juggling at £25 is a low-risk, zero-claims product that also serves as a team-workshop lead.

Seat caps: Shopify inventory is per variant, so a workshop date's 8 seats are split 5 seat-only / 3 with-kit. If one sells out while the other has seats, move inventory between the two variants in admin.

Scheduling: no booking app. The order-confirmation email and the product copy carry **`[CALENDLY LINK]`** as a visible placeholder (also stored on each service product in metafield `custom.scheduling_link`). Replace it everywhere once Joe supplies the Calendly URL: product descriptions (4), the kit descriptions (3) and FAQ rows (3 templates), the Meet Joe page, and the email drafts below. A grep for `CALENDLY` over `scripts/storefront/hero-offer/` and this file lists every spot; the Shopify copies were created from those scripts.

Channel exclusion for services, exactly how it is done
1. Tags `service`, `no-social`, `no-feed` on all four products. The Social Engine's `content.excluded_tags` already contains `no-social` (checked in the store's saved settings), so it will never post them.
2. They carry `requiresShipping = false` on seat variants and no weight, product types "Coaching", "Workshop", "Programme".
3. Sales channels: because the products are drafts, Shopify ignores `publishablePublish` until they are active. `publish.py products services` sets them active and attaches **Online Store + the UK and USA market catalogs only**, then explicitly unpublishes them from Shop, Pinterest, Google & YouTube, Facebook & Instagram and TikTok. `publish.py status` shows the result. The Google & YouTube app also honours the per-product channel setting, so nothing reaches Merchant Center.
4. If the owner ever ticks a sales channel on a service product in admin, re-run `publish.py products services`.

### 2.4 Fulfilment of a kit order

The kits are Shopify Bundles (fixed bundles, one quantity of each component). How an order flows:
- The customer buys "The Evening Wind-Down Kit". Shopify's bundle cart transform expands it at checkout: the order contains **one line item per component** (mat, roller, cards) at the bundle's allocated prices, grouped under the bundle title. Inventory is deducted from the component variants, so the kit shows sold out when any component is.
- **DSers sees the component line items**, each of which is already mapped to its AliExpress listing on the standalone product, and creates one supplier order per line in the usual way. **No new DSers mapping is needed for the three kits.** Caveat: this is how DSers documents its handling of Shopify Bundles orders; confirm with a £0 test order (Discounts > create a 100% code, place an order for a kit, check DSers > Open Orders shows three lines, then cancel) before launch. If DSers ever shows the bundle as "unmapped", the fix is DSers > My Products > the kit > Mapping > Bundle tab, mapping each component listing.
- The bundle's own variants carry the price (£79 / £74 / £69) and a compare-at of the component sum; the components' allocated prices in the order are Shopify's proportional split of the bundle price.
- Still manual: the **workshop "Seat + Evening Wind-Down Kit"** variant and the **28-Day programme** cohorts are plain variants that include a kit. For those orders, place the three component orders in DSers by hand (or add the Evening kit to the order as a £0 line via Order > Edit so DSers picks up the components). Noted in section 10.
- Returns under the promise come back to Norwich, not to the supplier.

### 2.5 The 30-Night Promise (worded from the refund policy of 1 Nov 2026)

> You get thirty nights from the day it arrives. Use it. If it has not earned a place in your routine, email info@bioluxelab.com with your order number, send it back unused in its packaging, and we refund you. Any reason. Return postage is yours if you have simply changed your mind; if anything arrives faulty, we cover it and replace it. This sits on top of your legal rights, not instead of them.

It promises nothing the policy does not already give (30 days from delivery, any reason, unused and in packaging, customer pays change-of-mind postage). "Thirty nights" is the policy's thirty days. Do not let it drift to "keep it and get a refund" in any video or email. The FV-08 video script repeats this wording.

---

## 3. Theme changes (all on the duplicate theme only)

| File | Change |
|---|---|
| `templates/index.json` | New sections after the slideshow and quiz: **founder_welcome** (media-with-text, FV-01 placeholder, "Hi, I'm Joe…", buttons to `/collections/hero-kits` and `/pages/meet-joe`), **hero_kits** (product list from collection `hero-kits`), **coaching_teaser** (links to the coaching collection and Meet Joe). New **wind_down_signup** section before the brand story: "The 7-Night Wind-Down Plan" heading, theme email-signup block, button "Send me the plan". |
| `templates/product.json` (every product) | New **complete_routine** product-recommendations section (recommendation type *complementary*, heading "Complete the routine") above "You may also like". Shows the kit on each component's page, and the session/workshop on the kit pages, driven by the Search & Discovery complementary metafield. |
| `templates/product.kit-evening.json`, `product.kit-pilates.json`, `product.kit-recovery.json` | Offer pages: product-information (gallery, price with compare-at, variant picker, buy buttons, description) → **kit_intro** (FV-02/04/06 slot + hook + "Add the kit" button) → **kit_stack** (the value stack with standalone prices) → **kit_bonus** (FV-03/05/07 slot, the routine bonus) → **kit_promise** (FV-08 slot + the promise wording) → **kit_faq** (accordion) → **kit_reviews** (Judge.me widget set to hide when empty + the store's own review block retitled "Reviews, honestly" with the no-imported-reviews text) → **kit_pairs** (complementary recommendations) → **kit_signup** (lead-magnet capture). |
| `templates/product.service-coaching.json`, `product.service-workshop.json`, `product.service.json` | product-information → **svc_intro** (FV-09 or FV-10 slot; none on the plain service template) → FAQ (booking, rescheduling, not medical advice) → honest reviews block → "Goes with" recommendations. |
| `templates/page.meet-joe.json` | Hero media-with-text with the FV-09 slot, "Meet Joe", buttons to the coaching collection and joebains.com, then the page body. |
| `templates/cart.json` | The cart page's product list now shows collection `hero-kits` under "Complete the routine: the kits" with a "See all kits" button (was "Featured picks"). The cart drawer is unchanged. |

Verified: the home page renders both new sections on the duplicate theme (preview in the owner's Chrome, screenshots taken); the Evening kit page renders on the `kit-evening` template in the theme editor with every section and block present (the editor sidebar lists the FV-02/03/08 slot texts, the value stack, the promise and the FAQ rows). Draft products cannot be viewed on the storefront preview domain, so the section-by-section look of the kit pages should be checked in the editor before publishing: `…/themes/210264916313/editor?previewPath=/products/evening-wind-down-kit`.

Video slots, where each placeholder sits (swap for a Video block with the YouTube URL once recorded, see the briefs file):

| Slot | Page / section |
|---|---|
| FV-01 | Home, founder_welcome; welcome email 2 |
| FV-02 / FV-04 / FV-06 | Kit pages, kit_intro |
| FV-03 / FV-05 / FV-07 | Kit pages, kit_bonus (unlisted link in the order email) |
| FV-08 | Kit pages, kit_promise; email 3 |
| FV-09 | Meet Joe page hero; Routine Reset Session and 28-Day programme pages; welcome email 2 alternative |
| FV-10 | Evening Reset Workshop page; email 4 |

---

## 4. Lead magnet and capture

- **The 7-Night Wind-Down Plan**: 11-page A4 PDF built from the sleep-routine, evening-yoga, breathwork and habits articles in the store blog, rewritten without the articles' health claims (no melatonin percentages, no "lowers blood pressure", no condition names). Night 1 to Night 7, a tracker page, a breathing cheat sheet, and a last page that introduces the Evening Wind-Down Kit and the first-order bonus. Source HTML in `docs/store/hero-offer-assets/wind-down-plan.html`; re-render with headless Chrome (`--print-to-pdf`) after edits.
- **The 30-Day Routine Tracker**: 3-page landscape PDF (how to use it, the 30-day grid, weekly reflections) with suggested rows for all three kits. Source `routine-tracker.html`.
- Capture: theme email-signup blocks on the home page and at the foot of every kit page. They post Shopify's customer form with marketing consent, so subscribers land in Customers with the "Email subscribers" segment. There is no double opt-in by default; Shopify Email's welcome automation (section 5) delivers the PDF.
- Shopify Forms (free) would add a pop-up and a first-name field; optional, needs installing.

---

## 5. Welcome flow (Shopify Messaging, 4 emails) and the order email

**Built in the app, left OFF.** The automation exists in Shopify Messaging, listed as "Welcome new subscribers with a discount series", status **Inactive** (rename it to "Welcome series: 7-Night Wind-Down Plan" from its ⋯ menu), created from that template: `https://admin.shopify.com/store/bioluxelab/apps/shopify-messaging/automations/flows/82691260761` (trigger: customer subscribed to email marketing; four emails). What was set through the owner's Chrome: the subject, preview text and main body text of all four emails (the copy below, with the PDF link in email 1 and the kit and workshop links in emails 3 and 4). Sender shows as "Bio Luxe Lab - info@bioluxelab.com".

What the owner must finish in the editor before turning it on (the browser relay could not reach the per-section delete control):
1. In each email, delete the template sections that remain under the new text: email 1 the black "Special offer / Discount" block and the "Bestsellers and fan favorites" product block; email 2 the social image and social-links blocks; email 3 the product block; email 4 the "Enjoy special savings" text and the discount block. Select the section, then the bin icon in its toolbar. No discount is ever selected, so the automation will refuse to activate until the discount blocks are gone, which is a useful safety catch.
2. Delays: the template sends at 0 h, +24 h, +24 h and +2 days (last one only if no order). Change to 0 h / 2 days / 2 days / 3 days in the automation's workflow view so the series runs 0 / 2 / 4 / 7 days.
3. Replace the bracketed placeholders in the body text: `[FOUNDER VIDEO GOES HERE: FV-01]` (email 2), `[FV-08]` (email 3), `[FV-10]` and `[DATE 1]`, `[DATE 2]` (email 4).
4. Email 3 shows an "Active" badge in the editor header while the series is still a draft; check it in the overview before turning on, and send a test of each email to yourself (Send test).
5. Turn on only after the kits are published, or email 3 links to a 404.

The copy, as entered:

**Email 1, immediately: "Your 7-Night Wind-Down Plan"**
> Subject: Your 7-Night Wind-Down Plan (PDF inside)
> Here it is: **[button: Download the plan]** → https://cdn.shopify.com/s/files/1/1037/0849/2121/files/bio-luxe-lab-7-night-wind-down-plan.pdf
> Seven evenings, one small ritual each. Tonight is Night 1 and it only asks you to turn the big light off and put the phone on a shelf. Do that and you are already ahead of most evenings.
> Two things to know. Everything in the plan works with what you already own. And it is general wellbeing information, not medical advice: if you have a health condition or an injury, check with a professional before you start the stretches.
> I'll send a short note in a couple of days to see how Night 3 went. Reply to this email any time; it comes to me.
> Joe

**Email 2, +2 days: "How did Night 3 go?" (founder video slot FV-01)**
> Subject: Night 3: the one most people skip
> **[FOUNDER VIDEO GOES HERE: FV-01, YouTube link or thumbnail linking to it]**
> That's me, and that's the mat I leave out so I actually use it. Night 3 is the stretches; it is also the night most people skip because the mat is in a cupboard. Leave it out. Visible kit gets used.
> If you are a night or two behind, no catching up needed; just do tonight's. The plan is yours for good.
> Joe
> P.S. Everything I use is on the site, but you don't need any of it for the plan to work.

**Email 3, +4 days: "The kit behind the plan" (kit offer, first-order bonus, FV-08)**
> Subject: The three things that made the routine stick for me
> By Night 5 you know which parts of the plan you like. Here is what made it stick for me: a mat that stays rolled out, a grooved roller by the sofa, and a deck of cards on the bedside table. I put those three together as **The Evening Wind-Down Kit**: £79 with free UK delivery (the three products are £91.52 on their own), plus my follow-along routine video, the 30-Day Routine Tracker and a 20-minute Routine Check-in call with me.
> **[FOUNDER VIDEO GOES HERE: FV-08, the 30-Night Promise]**
> The 30-Night Promise: use it for a month, and if it hasn't earned its place in your evening, send it back for a refund. Any reason.
> **Your first order comes with a bonus, no code needed:** the full 30-Day Routine Tracker pack and early access to every new routine video, added to your order email automatically.
> **[button: See the Evening Wind-Down Kit]** → /products/evening-wind-down-kit
> Joe

**Email 4, +7 days: "Do it with me, live" (workshop slot, FV-10)**
> Subject: Ninety minutes, eight people, a routine you'll keep
> **[FOUNDER VIDEO GOES HERE: FV-10, workshop promo]**
> If reading the plan was easy and doing it was harder, that is normal; doing it with other people is what fixes it. The **Evening Reset Workshop** is ninety minutes online with eight people: breathing, the twenty-minute wind-down walked through on the mat, and the meditation that works for people who gave up. Next dates: **[DATE 1]** and **[DATE 2]**, 19:30 UK.
> £35 a seat, or £99 with the Evening Wind-Down Kit delivered before we start.
> **[button: Grab a seat]** → /products/evening-reset-workshop
> Joe

**Order confirmation additions** (Settings > Notifications > Order confirmation, add a block that shows when the order contains a kit or service; or send as a separate Shopify Email automation "Order placed, product tag hero-kit / service"):
> **Your bonuses.** Follow-along video: [FOUNDER VIDEO LINK FV-03 / FV-05 / FV-07, unlisted YouTube]. The 30-Day Routine Tracker: https://cdn.shopify.com/s/files/1/1037/0849/2121/files/bio-luxe-lab-30-day-routine-tracker.pdf. The 7-Night Wind-Down Plan: https://cdn.shopify.com/s/files/1/1037/0849/2121/files/bio-luxe-lab-7-night-wind-down-plan.pdf. Book your 20-minute Routine Check-in with Joe: **[CALENDLY LINK]** (one per kit order, within 90 days).
> **Coaching and workshops.** Your Zoom/Google Meet link and the scheduling page: **[CALENDLY LINK]**. To move a date, reply to this email at least 24 hours before.

Compliance in the emails: no claims about sleep quality, stress or health outcomes; "switch off", "wind down", "a routine you'll keep" only.

---

## 6. Upsells

- **Product pages (all products)**: "Complete the routine" section using Search & Discovery's complementary products. Set on: mat → Evening kit; grooved roller → Evening and Recovery kits; meditation and affirmation cards → Evening kit; Premium EVA mat → Evening kit; ring, block, handled bands, foldable mat, two-block set → Pilates kit; massage gun and dense roller → Recovery kit; each kit → Routine Reset Session (+ the workshop on the Evening kit); the workshop → Evening kit + programme; the session → programme + Evening kit. Free, no app installed beyond what exists.
- **Cart page**: the kits collection under "Complete the routine: the kits". The theme's cart drawer has no product block, so the page is where the upsell lives; the drawer's "View cart" leads there.
- **Post-purchase upsell page**: needs a third-party app with a post-purchase extension (AfterSell, ReConvert, Zipify OCU all have free tiers or trials). Works on Basic with Shopify Payments. Not installed; owner's call.
- **Needs Shopify Plus, skipped**: checkout UI extensions (upsell blocks inside checkout itself), Shopify Scripts, checkout branding beyond the basics, B2B bundles.
- The kits are Shopify Bundles, so the cart and order show the components under the kit title and stock follows the components.

---

## 7. WELCOME10 replacement (drafts only; nothing changed)

Site copy (home signup section, kit pages, email 3):
> **Your first order comes with a bonus, no code needed.** Every first order gets the full 30-Day Routine Tracker pack and early access to Joe's new routine videos, added to your order email automatically.

Social Engine `captions.signature` (currently "New here? 10% off your first order with code WELCOME10", 200-character limit, dropped first when a channel is tight):
> New here? Your first order comes with Joe's 30-Day Routine Tracker pack, no code needed. bioluxelab.com

Shorter variant for X: "First order? The 30-Day Routine Tracker pack comes with it, no code. bioluxelab.com"

To switch over (owner): Social Autopilot > Settings > Captions > signature, paste the line; `review._store_facts` picks it up automatically so the reviewer stops expecting a code. Then Discounts > "Welcome offer — 10% off your first order (social signature)" (WELCOME10) > Deactivate. Postiz's own composer signature and the auto-plug text also mention WELCOME10; update those in Postiz (see `docs/social-engine/postiz-maximise-2026-11-02.md`). The other active codes (LAUNCH20, BIOHACK30/40/60, PIN20/30, PINTEREST30) are outside this task but contradict a no-discount position; worth a decision.

---

## 8. Compliance notes

- No health, medical, weight-loss or body-shaping claims in any kit, service, page, PDF or email copy. Each kit carries a "Good to know" block (accessories, not medical devices; check with a professional; stop if it hurts). Services carry "What this is, and is not".
- Supplier images with printed claims are excluded: Pilates ring (7 of 10), massage gun (1), grooved roller (the two with "myofascial release"/"muscle recovery" are not among the ones used), yoga block (3 with posture/injury text not used).
- Service copy: routines, habits, breathwork, meditation practice, sleep hygiene, training and recovery, measurement and accountability. No condition names, no "cure/treat/fix", no "anti-ageing", no age-performance claims, no brain percentages, no client outcome quotes. The Meet Joe bio follows the same list and ends with a note telling Joe what to keep out.
- Compare-at prices on the kits show the sum of the standalone prices. That is a genuine reference price (the components are sold at those prices on the same site), which is what UK pricing guidance expects; do not raise standalone prices to inflate it.
- Existing risk, outside this build: Judge.me imported reviews on current product pages (for example "15 reviews, 4.80" on the mat) on a store with no sales. Under the DMCC Act 2024 fake-reviews rules, reviews not from genuine purchasers presented as such are a problem. Recommend hiding the Judge.me widget or clearly labelling imported reviews as supplier-platform reviews.
- Workshop refunds: the copy promises one free reschedule with 24 hours' notice and a full refund up to 14 days after purchase if the session has not taken place (Consumer Contracts Regulations). Keep to it.

---

## 9. Publish steps and rollback

Preconditions: copy approved; Calendly link inserted everywhere `[CALENDLY LINK]` appears; real workshop and cohort dates set on the variants (edit the option values in admin); DSers Bundle mapping done for the three kits and the two shippable service variants; Shopify Email installed and the four-email automation built and tested; at least FV-01, FV-02, FV-08 recorded or the slots accepted as placeholders for launch.

Publish, in this order (each step is one command, from the repo root; `status` first):
```bash
python3 scripts/storefront/hero-offer/publish.py status
```
```bash
python3 scripts/storefront/hero-offer/publish.py products kits
```
```bash
python3 scripts/storefront/hero-offer/publish.py products services
```
```bash
python3 scripts/storefront/hero-offer/publish.py page
```
```bash
python3 scripts/storefront/hero-offer/publish.py theme
```
Then by hand: Online Store > Navigation > Main menu > add "Kits" (`/collections/hero-kits`), "Coaching & Workshops" (`/collections/coaching-and-workshops`) and "Meet Joe" (`/pages/meet-joe`); the footer "Our story" menu gets "Meet Joe" too. Menus are live immediately, which is why the script does not touch them. Check `https://bioluxelab.com/products/evening-wind-down-kit` resolves for a UK visitor (the products are attached to the UK market catalog by the script; see the publishing gotcha in `docs/store/policies-2026-11-01.md`).

Rollback, any time, in any order:
```bash
python3 scripts/storefront/hero-offer/publish.py rollback-theme
```
```bash
python3 scripts/storefront/hero-offer/publish.py rollback-products
```
```bash
python3 scripts/storefront/hero-offer/publish.py rollback-page
```
`rollback-theme` republishes theme 209606738265 (the live theme as of 2 Nov 2026); the duplicate stays for edits. `rollback-products` sets the seven products back to draft (orders already placed are unaffected). Menus are removed by hand. To remove everything: delete the seven products, the two collections, the page and the duplicate theme in admin; the Files (placeholders, kit collages, PDFs) can stay.

---

## 10. Owner's remaining actions

1. Read and edit the copy: three kit pages, four service pages, the Meet Joe bio, the four emails, the signature line. Say "approved" or send changes.
2. Give the Calendly URL; replace `[CALENDLY LINK]` (section 2.3 lists where).
3. Set real dates on the workshop (two), programme cohorts (two) and juggling (one) variants; adjust seats if needed.
4. Record the videos per `docs/store/founder-video-briefs-2026-11.md` and drop them into `~/Bio Luxe Lab Videos` with the exact file names. FV-01, FV-02 and FV-08 unblock launch; the follow-alongs (FV-03/05/07) are the bonuses and should exist before the first kit ships.
5. DSers: place a £0 test order for one kit and confirm DSers shows the three component lines (section 2.4); decide how the workshop "+Kit" and programme orders get their kit (manual in DSers, or add the kit as a £0 line on the order).
6. Finish the welcome automation in Shopify Messaging (section 5: delete the leftover template sections, set the delays, replace the placeholders, send tests) and add the order-email bonus block; turn the series on only after the kits are live.
7. Decide on the Judge.me imported reviews (section 8) and on the other active discount codes (section 7).
8. Publish (section 9), then switch the Social Engine signature and deactivate WELCOME10.
9. joebains.com: add a "Shop & coaching" link to `https://bioluxelab.com/pages/meet-joe` and point the Biohacking section's call to action at `https://bioluxelab.com/collections/coaching-and-workshops`; the site's "Video coming soon / Meet Joe on camera" slot can reuse FV-09.
10. Optional: Shopify Forms (pop-up capture), a post-purchase upsell app, reconnect the Microsoft Channel so the kits reach Bing.
