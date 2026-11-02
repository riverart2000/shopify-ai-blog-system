# Listings lane — look established everywhere a buyer checks (2 November 2026)

The Social Engine now has a **Listings** section (`/app/social/listings`). Its job is reputation: when a client searches "Bio Luxe Lab" or "Joe Bains coach", they should find the same name, phone and story on Google, a review site, the UK directories, and the places the workshops and coaching are sold.

## How it works

1. **One profile, copied everywhere.** `settings.listings.profile` holds the business name, tagline, three description lengths, founder bio, contact details, address (optional), categories, keywords, services and social links. Name, phone and address must match the existing Google Business Profile word for word: that is how directories and search engines match listings. "Write the descriptions for me" asks Gemma to draft the copy from the brand kit and the catalogue; every line is passed through the claim guard (no health, weight-loss or body-shaping promises) and shown with warnings for editing.
2. **A curated catalogue** (`services/social_engine/listings/catalogue.py`): 20 directories, each with trust and reach scores, cost, what it needs (account, email code, phone call, postcard, feed, address), the submit URL, description limits, and a one-line "why". Highest trust first: Google Business Profile, Trustpilot, LinkedIn, Bing, Apple, Companies House, Yell, Eventbrite, Superprof, Apprentus, then the free UK directories. No model adds entries.
3. **Fill in Chrome.** The `listings.fill` job drives the owner's signed-in Chrome through the OpenClaw relay (`npm run openclaw -- browser --browser-profile chrome`). It reuses an open tab on the directory's domain or opens the submit URL, snapshots the interactive elements, maps form labels to profile fields (label rules first; Gemma only for leftovers, validated against the refs it was shown), and fills the text fields. It **never** ticks checkboxes, chooses options, types passwords, card or identity details, accepts terms, or presses submit. Those are listed as "left for you". The tab stays open; the owner finishes there.
4. **Status and upkeep.** `social_listings` (migration 4) tracks todo → filled → submitted → verifying → live, plus broken and skipped, with the live link and a note. A daily `listings.check` job fetches each live link (older than `check_interval_days`) and confirms the page still exists and shows the business name and phone; a removed listing becomes "needs attention" in Activity.
5. **What a client finds.** `listings.presence` searches the business name (and the founder) on Google in the owner's Chrome, parses the first page of result links, groups them (store, catalogue directory, social, other) and scores how established the brand looks out of 100: store ranks for its name 25, Google Business Profile 20, Trustpilot 20, LinkedIn 10, two or more social profiles 10, a UK directory 10, Companies House 5. The page lists what to do next.

## Owner's part

- Copy the Google Business Profile details into the profile (or press "Write the descriptions for me", then edit).
- For each directory: sign in or create the account, get to the form, press **Fill in Chrome**, check the fields, press the site's submit, mark submitted, do the verification, paste the live link.
- Directories that need a public address (Yelp, Nextdoor) stay blocked until there is one.
- Trustpilot: invite every customer after delivery; reply to every review.
- Eventbrite: list the online workshop and the cohort with real dates.

## Files

- Backend: `services/social_engine/listings/` (`catalogue.py`, `relay.py`, `profile.py`, `mapping.py`, `fill.py`, `checks.py`), `db/social_engine/listings.py`, `routes/social_engine/listings.py`, jobs in `pipeline/handlers.py` (`listings.fill`, `listings.presence` on the new `relay` lane; `listings.check` periodic daily), settings `ListingProfile` / `ListingsSettings`.
- Frontend: `app/routes/app.social.listings.tsx`, nav entry in `app.social.tsx`, types in `lib/social-engine.types.ts`.
- Tests: `test_social_engine_listings.py` (catalogue, profile seeding and limits, label mapping, model refinement guardrails, fill through a fake relay, no-form handling, status stamps, live checks, snapshot parsing, presence classification and score, suggested copy claim check).

## API

`GET /api/social-engine/listings`, `POST /listings/profile` `{profile}`, `POST /listings/profile/suggest` `{overwrite}`, `POST /listings/{id}/fill` `{tab}`, `POST /listings/{id}/status` `{status, listing_url, notes}`, `POST /listings/{id}/check`, `POST /listings/presence`, `GET /listings/relay`.

## Settings

`listings.relay_dir` (default `~/openclaw`), `relay_profile` (`chrome`), `relay_timeout_seconds` (90), `check_interval_days` (7), `presence_interval_days` (7).
