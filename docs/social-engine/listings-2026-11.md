# Listings lane — look established everywhere a buyer checks (2 November 2026)

The Social Engine now has a **Listings** section (`/app/social/listings`). Its job is reputation: when a client searches "Bio Luxe Lab" or "Joe Bains coach", they should find the same name, phone and story on Google, a review site, the UK directories, and the places the workshops and coaching are sold.

## How it works

1. **One profile, copied everywhere.** `settings.listings.profile` holds the business name, tagline, three description lengths, founder bio, contact details, address (optional), categories, keywords, services and social links. Name, phone and address must match the existing Google Business Profile word for word: that is how directories and search engines match listings. "Write the descriptions for me" asks Gemma to draft the copy from the brand kit and the catalogue; every line is passed through the claim guard (no health, weight-loss or body-shaping promises) and shown with warnings for editing.
2. **A curated catalogue** (`services/social_engine/listings/catalogue.py`): 20 directories, each with trust and reach scores, cost, what it needs (account, email code, phone call, postcard, feed, address), the submit URL, description limits, and a one-line "why". Highest trust first: Google Business Profile, Trustpilot, LinkedIn, Bing, Apple, Companies House, Yell, Eventbrite, Superprof, Apprentus, then the free UK directories. No model adds entries.
3. **Fill in Chrome.** The `listings.fill` job drives the owner's signed-in Chrome through the OpenClaw relay (`npm run openclaw -- browser --browser-profile chrome`). It reuses an open tab on the directory's domain or opens the submit URL, snapshots the interactive elements, maps form labels to profile fields (label rules first; Gemma only for leftovers, validated against the refs it was shown), and fills the text fields. It **never** ticks checkboxes, chooses options, types passwords, card or identity details, accepts terms, or presses submit. Those are listed as "left for you". The tab stays open; the owner finishes there.
4. **Status and upkeep.** `social_listings` (migration 4) tracks todo → filled → submitted → verifying → live, plus broken and skipped, with the live link and a note. A daily `listings.check` job fetches each live link (older than `check_interval_days`) and confirms the page still exists and shows the business name and phone; a removed listing becomes "needs attention" in Activity.
5. **What a client finds.** `listings.presence` searches the business name (and the founder) on Google in the owner's Chrome, parses the first page of result links, groups them (store, catalogue directory, social, other) and scores how established the brand looks out of 100: store ranks for its name 25, Google Business Profile 20, Trustpilot 20, LinkedIn 10, two or more social profiles 10, a UK directory 10, Companies House 5. The page lists what to do next.

## The worker (added 2 November, afternoon)

The owner asked for an employee, not a form-filler: the engine now works each directory end to end and writes everything down.

- **`listings.work`** (`services/social_engine/listings/worker.py`, relay lane, one at a time): snapshot the page → ask the model (Gemma first, Grok as the configured fallback) for ONE action → check it → do it through the relay → log it in `social_listing_steps`. Actions: click, type, fill, select, press, navigate, wait, check_email, done, submitted, need_owner. Runs until the listing is live (status `live` with the URL), pending the site's review (`submitted`), or something only the owner can do (`needs_owner`, with the reason, the page, the Chrome tab and a screenshot).
- **Accounts.** When a site needs one the worker registers with `listings.account_email` (default: the profile email) and a generated 20-character password. The model only ever sees `{{password}}`; the relay types the real one; the step log masks it. Credentials go in `social_listing_accounts`, Fernet-encrypted with a key in `SOCIAL_ENGINE_VAULT_KEY` or `<media root>/listings/vault.key` (0600). The Listings page can reveal a password and edit the account.
- **Verification mail.** `check_email` opens the Gmail search for the directory's mail in the owner's Chrome, reads the code or the confirmation link (regex first, model second), closes the tab, and feeds it back as a FACT for the next step.
- **Hard stops, by design:** card, bank or billing fields and anything that wants payment → `needs_owner`; CAPTCHAs → `needs_owner` (never attempted); phone, SMS and postcard verification → `needs_owner`; identity documents → refused. The same action three times in a row, four impossible refs, the step cap (`max_steps_per_run`, 45) or 25 minutes → `needs_owner` with "press Continue".
- **Interrupted runs.** A backend restart (a deploy) kills the run in flight. `recover_interrupted` (called when the page loads and before the autopilot picks the next directory) notices a 'working' listing whose job lease belongs to a dead process and hands it back as `needs_owner` ("press Continue"). Avoid deploying while the relay lane is busy.
- **Failed actions.** A relay action that fails (a styled dropdown that is not a `<select>`, a detached element) is logged as a step with `error: …` and surfaced to the model as `last_error`; four in a row → `needs_owner`.
- **Verification mail** is read over IMAP from the business mailbox first (`LISTINGS_MAIL_*` in the live `.env`, logins from `docs/emails.md`, which is gitignored), Gmail-in-Chrome second.
- **Address.** With no address in the profile the worker refuses every address, postcode or address-suggestion field (`ADDRESS`/`POSTCODE` guards) and hands back. Once an address is in the profile it is used everywhere, so the profile address must be the one the owner wants public (Chrome autofill can suggest a home address on a form; the worker copies only the profile).
- **Continue.** After the owner does their part in Chrome, "Continue" starts a new run on the open tab with the stored account ("sign in, don't register").
- **Autopilot.** `listings_work` runs every `work_interval_hours` (12) and takes the next unworked directory, highest trust first, while `listings.autopilot` is on. "Work through all" queues every workable directory at once; the relay lane does them one after another.
- **Sole traders.** `profile.business_type` (default `sole_trader`) skips Companies House, drops it from the established score (the rest is scaled to 100) and keeps the worker away from company-number fields.
- **Step log.** Every directory card shows "What the worker did": each step with the element it used, what it typed or chose, its one-line reason and the result (ok, refused, error). Runs are grouped; the account used is shown with a "Show password" button.

## Owner's part

- Copy the Google Business Profile details into the profile (or press "Write the descriptions for me", then edit).
- Press **Let the worker do it** (or **Work through all**) and watch Chrome if you like. When a card says **Needs you**, do that one thing (the CAPTCHA, the phone call, the postcard code), then press **Continue**. "Just prefill the open form" is still there for sites you would rather finish yourself.
- Directories that need a public address (Yelp, Nextdoor) stay blocked until there is one; the address in the profile is published as-is, so check it first.
- Trustpilot: invite every customer after delivery; reply to every review.
- Eventbrite: list the online workshop and the cohort with real dates.

## Files

- Backend: `services/social_engine/listings/` (`catalogue.py`, `relay.py`, `profile.py`, `mapping.py`, `fill.py`, `checks.py`), `db/social_engine/listings.py`, `routes/social_engine/listings.py`, jobs in `pipeline/handlers.py` (`listings.fill`, `listings.presence` on the new `relay` lane; `listings.check` periodic daily), settings `ListingProfile` / `ListingsSettings`.
- Frontend: `app/routes/app.social.listings.tsx`, nav entry in `app.social.tsx`, types in `lib/social-engine.types.ts`.
- Tests: `test_social_engine_listings.py` (catalogue, profile seeding and limits, label mapping, model refinement guardrails, fill through a fake relay, no-form handling, status stamps, live checks, snapshot parsing, presence classification and score, suggested copy claim check).

## API

`GET /api/social-engine/listings`, `POST /listings/profile` `{profile}`, `POST /listings/profile/suggest` `{overwrite}`, `POST /listings/{id}/work` `{tab}`, `POST /listings/work_all`, `GET /listings/{id}/steps?run_id=`, `GET /listings/{id}/account?reveal=1`, `POST /listings/{id}/account`, `POST /listings/{id}/fill` `{tab}`, `POST /listings/{id}/status` `{status, listing_url, notes}`, `POST /listings/{id}/check`, `POST /listings/presence`, `GET /listings/relay`.

## Settings

`listings.relay_dir` (default `~/openclaw`), `relay_profile` (`chrome`), `relay_timeout_seconds` (90), `check_interval_days` (7), `presence_interval_days` (7), `autopilot` (true), `work_interval_hours` (12), `account_email` (profile email), `max_steps_per_run` (45).
