# Founder videos — record once, post everywhere (added 2 November 2026)

Joe records long, natural phone videos (on the acupressure mat, the massage gun after a run,
why he wears the wide-toe shoes). The engine posts each one in full to YouTube, has Postiz cut
it into short clips, and schedules the clips across the video channels. Every post waits in
the approval queue first (`founder_videos.approval_mode = manual`).

## Why YouTube is in the middle

Postiz Cloud's clipping endpoint accepts **YouTube URLs only** (`POST /clipping` answers 400
"Only YouTube videos can be clipped" for anything else). So a dropped file is first published
to YouTube by the engine as a `founder_video` post; once Postiz reports the YouTube URL back
(reconcile / webhook → `social_post_targets.release_url`), clipping starts. A YouTube link
pasted in the app skips straight to clipping.

## Ways in

| Route | Limit | Best for |
|---|---|---|
| Drop folder on the M5: `~/Bio Luxe Lab Videos` (a READ ME is written into it) | none | phone recordings AirDropped to the Mac |
| Social Autopilot → Founder videos → paste a YouTube link | — | videos already on the channel |
| Social Autopilot → Founder videos → upload | ~95 MB (Cloudflare request body limit through the tunnel) | short clips, tests |

Files are picked up by the `founder.scan` periodic job (every 5 minutes, also triggered by
"Check now" and after a URL is added) once they have been untouched for 90 seconds. The file
is moved into media storage (`data/social_engine/media/<store>/founder/<id>/original.<ext>`)
and a marker text file is left in `Processed/`. The product link is matched from the words in
the filename or note (two title words must match, one for single-word titles); otherwise the
link is the storefront.

## State machine (`social_founder_videos.status`)

`new` → `awaiting_youtube` (full post planned; waiting for its YouTube release URL, or a pasted
URL waiting for a free Postiz clipping slot) → `clipping` (Postiz job running, polled every 5
min) → `done` (clip posts planned) / `failed` (reason in `error`; "Retry" in the app resets it).

Postiz caps: one clipping at a time (429 → retried after 15 min), 20 starts a day, 120 clipping
minutes a month on the Team plan (402 → failed with a message). Recordings over
`max_source_minutes` (40) are refused up front so one video cannot burn the month.

## What the posts look like

- `founder_video`: the original file as pre-made media on the channels in
  `full_video_providers` (YouTube by default, visibility `youtube_visibility`), caption written
  in the founder's voice from the title and note.
- `founder_clip`: one post per clip on `clip_providers` (TikTok, Instagram, YouTube Shorts,
  Facebook, Pinterest), `clip_days_apart` days apart, the clip's Postiz media id reused so
  nothing is re-uploaded, Postiz's suggested clip title and summary fed into the caption brief.
  Pinterest gets the clip thumbnail as the pin cover.

Both go through the normal generate → guard → AI review → approval → publish pipeline;
`options.premade_media` is what tells `pipeline/generate.py` not to render a template video.

## Code

- `services/social_engine/founder/ingest.py` — intake, product matching, state machine
- `db/social_engine/founder.py`, schema migration v2 — `social_founder_videos`
- `routes/social_engine/founder.py` — `/api/social-engine/founder-videos[...]`
- `app/routes/app.social.founder-videos.tsx` — the page; nav entry in `app.social.tsx`
- `test_social_engine_founder.py` — matching, URL parsing, the full file → YouTube → clips flow
  with a fake Postiz client, busy/no-minutes handling, duplicates and over-long recordings
