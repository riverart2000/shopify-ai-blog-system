# Task Agent API

Private HTTP API served by the worker process (`task-agent worker`) on `http://127.0.0.1:4100`.
The Shopify admin app calls it server-side only; the browser never sees the key.

- Auth: header `x-api-key: <TASK_AGENT_API_KEY>` (same value in `task-agent/.env` and the app's server `.env`).
  Wrong key → 401. No key configured on the Task Agent → 503.
- Errors: `{"detail": "message"}` (400/404/409), or FastAPI's 422 list `{"detail": [{"loc": [...], "msg": "..."}]}`.
- Times are ISO 8601 UTC strings, e.g. `2026-10-30T23:28:00+00:00`.
- Job statuses: `queued`, `running`, `waiting_approval`, `done`, `failed`, `cancelled`.

## Objects

**JobSummary**
```json
{"id": 3, "type": "research", "title": "…", "status": "done", "priority": 0, "attempts": 1,
 "run_at": "…", "created_at": "…", "started_at": "…|null", "finished_at": "…|null",
 "error": "…|null", "template_key": "research|null", "created_by": "admin|cli",
 "cancel_requested": false, "step_count": 9}
```
**JobDetail** = JobSummary + `"payload": {…}, "result": {…}|null, "tools_allowed": ["web_search", …]`

Research `result`: `{"answer": "…", "key_points": ["…"], "sources": [{"url": "…", "title": "…", "used_for": "…"}],
"confidence": "high|medium|low", "gaps": ["…"]}`. Other task types: render the JSON.

**Step**: `{"n": 1, "tool": "web_search", "args": "<JSON string>", "result_summary": "…", "tokens": 123|null,
"duration_ms": 1400|null, "created_at": "…"}`. A `result_summary` starting `REJECTED:` / `FAILED:` / `DENIED:` is
a step the agent had to redo.

**Template**
```json
{"key": "find-event-listings", "name": "Find places to list an event", "description": "…",
 "task_type": "research", "prompt": "Find {how_many=5} free … {event_name} … {notes?}",
 "defaults": {"min_sources": 3, "context": "…"}, "builtin": true, "enabled": true,
 "fields": [{"name": "how_many", "label": "How many", "required": false, "default": "5"}],
 "updated_at": "…|null"}
```
Placeholders in `prompt`: `{name}` required, `{name?}` optional, `{name=default}` optional with a default.
`fields` is derived from the placeholders (label = humanised name). Built-in templates can't be edited or
deleted, only duplicated.

**TaskType**: `{"type": "research", "summary": "…", "prompt_field": "question"}`

**Account** (a site login)
```json
{"site": "eventbrite", "label": "Eventbrite", "login_url": "https://…", "username": "…",
 "has_password": true, "notes": "", "has_profile": true, "last_login_ok_at": "…|null",
 "login_window_open": false}
```
**Email**
```json
{"username": "", "from_name": "", "imap_host": "", "imap_port": 993, "smtp_host": "", "smtp_port": 587,
 "has_password": false, "last_test_at": "…|null", "last_test_ok": true|false|null, "last_test_message": ""}
```
Passwords are write-only: they go to the macOS Keychain and are never returned.

## Endpoints

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/status` | | `{"worker": {"state": "running\|idle\|paused\|offline", "last_seen_at": "…\|null", "current_job": {"id", "title"}\|null}, "model", "thinking", "counts": {status: n}, "pending_approvals": n, "recent_jobs": [JobSummary]}` |
| GET | `/api/jobs` | `?status=a,b&q=&page=1&page_size=20` | `{"jobs": [JobSummary], "total", "page", "page_size", "counts": {status: n}}` |
| GET | `/api/jobs/{id}` | | `{"job": JobDetail, "steps": [Step]}` |
| POST | `/api/jobs` | `{"template_key", "values": {name: value}, "title"?: str, "priority"?: int}` | `{"job": JobSummary, "message"}` |
| POST | `/api/jobs/{id}/cancel` | | `{"job", "message"}` (a running job stops at its next step) |
| POST | `/api/jobs/{id}/retry` | | `{"job", "message"}` (failed/cancelled → queued, starts over) |
| GET | `/api/templates` | | `{"templates": [Template], "task_types": [TaskType]}` |
| POST | `/api/templates/preview` | `{"template_key", "values"}` | `{"title", "prompt", "missing": [field names]}` |
| POST | `/api/templates` | `{"key"?: (update this user template), "name", "description", "task_type", "prompt", "defaults"?: {}, "enabled"?: bool}` | `{"template", "message"}` |
| POST | `/api/templates/{key}/delete` | | `{"message"}` |
| GET | `/api/approvals` | `?status=pending\|approved\|rejected\|all` | `{"approvals": [{"id", "job_id", "job_title", "action", "preview", "status", "created_at", "decided_at", "decided_by"}], "counts": {…}}` |
| POST | `/api/approvals/{id}/decide` | `{"decision": "approve\|reject"}` | `{"approval", "message"}` |
| GET | `/api/settings` | | `{"settings": {"llm_model", "llm_thinking", "max_steps", "max_minutes", "max_attempts", "paused"}, "limits": {"max_steps": [min, max], "max_minutes": […], "max_attempts": […]}, "models": [ids], "recommended_model"}` |
| POST | `/api/settings` | any subset of `settings` | `{"settings", "message"}` |
| GET | `/api/accounts` | | `{"accounts": [Account], "email": Email}` |
| POST | `/api/accounts` | `{"site"?: (update), "label", "login_url", "username", "password"?: (blank keeps), "notes"?}` | `{"account", "message"}` |
| POST | `/api/accounts/{site}/delete` | | `{"message"}` (also removes its password and saved browser login) |
| POST | `/api/accounts/{site}/login-window` | | `{"account", "message"}`: opens a visible browser on the Mac with that site's profile; log in by hand (2FA works); closing the window saves the login. 409 if one is already open. |
| POST | `/api/email` | `{"username", "from_name", "imap_host", "imap_port", "smtp_host", "smtp_port", "password"?: (blank keeps)}` | `{"email", "message"}` |
| POST | `/api/email/test` | | `{"ok", "message", "email"}` (logs in to IMAP and SMTP; sends nothing) |

## Telegram

**TelegramStatus**: `{"configured", "bot_username", "owner_username", "linked", "linked_username", "linked_at",
"summary_hour", "social_connected", "last_error"}`. The token is write-only (Keychain `telegram:bot_token`).

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/telegram` | | `{"telegram": TelegramStatus}` |
| POST | `/api/telegram` | `{"token"?: str, "owner_username"?: str}` | `{"telegram", "message"}`. The token is checked with Telegram before saving; a new username unlinks the old account. |
| POST | `/api/telegram/test` | | `{"telegram", "message"}`: sends a test message (needs /start first) |
| POST | `/api/telegram/unlink` | | `{"telegram", "message"}`: the next /start from the username links again |
| POST | `/api/telegram/disconnect` | | `{"telegram", "message"}`: removes the token |

## Event promotion, cloning and the browser

Agents work in Joe's everyday Chrome through the OpenClaw extension (`BROWSER=chrome`, the default), or in a
separate agent profile (`BROWSER=own`, `profiles/shared`).

**JobSummary** also has `"parent_id": int|null`. A promotion's per-place jobs have `parent_id` = the promotion's
job id; `GET /api/jobs` hides them unless `?include_children=1`.

**JobDetail** also has `"template_values": {name: value}|null` (what the task was created with, for Clone; a
title Joe typed is in `"_title"`) and
`"promotion": Promotion|null` (only for `promote_event` jobs).

**Promotion**
```json
{"event": EventDetails|null, "reused_from": 12|null,
 "counts": {"queued": 4, "posted": 3, "needs_you": 1},
 "targets": [Target]}
```
**EventDetails** (all strings except `online`): `{"title", "starts_at" ("2026-11-15T19:00", UK time), "ends_at",
"venue", "address", "town", "online": bool, "online_url", "price", "ticket_url", "organiser", "description",
"category", "image_url"}`.

**Target** (one place to promote on)
```json
{"id": 5, "kind": "listing|group|forum|page|own_social", "name": "AllEvents", "url": "https://…", "why": "…",
 "account": "Facebook: joe.bains", "source": "found|reused|own",
 "status": "planned|queued|working|posted|already_listed|needs_you|skipped|failed",
 "job_id": 14|null, "scheduled_at": "…|null", "result_url": "…|null", "note": "…|null",
 "need": {"site": "meetup", "url": "https://…", "reason": "Enter the code Meetup emailed you"}|null,
 "updated_at": "…"}
```
`needs_you`: the page is open in the shared browser on the Mac (and a Telegram card was sent). After Joe finishes
there, `done` puts the place back in the queue straight away; it otherwise waits up to 24 hours, then is skipped.

**Template** `fields[]` items also have `"multiline": bool` (show a textarea: lists of URLs, accounts, notes) and,
for list fields, `"options": [{"value", "label"}]` (show a drop-down). Only `promote_event` templates get options for
`event_type` (a research template's free-text "event_type", e.g. "talk", stays free text).

`page` targets are the event type's own Facebook/LinkedIn Page (`source` "own"). `own_social` targets are from
before event types (Social Autopilot is only for Bio Luxe Lab now) and are no longer created.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/jobs` | also `"clone_of"?: int` | Cloning a promotion reuses the places that worked last time (and still looks for new ones). |
| POST | `/api/targets/{id}/done` | | `{"target", "message"}`: Joe finished what the site needed (needs_you → queued now) |
| POST | `/api/targets/{id}/skip` | | `{"target", "message"}`: don't post there (its job is cancelled) |
| POST | `/api/targets/{id}/retry` | | `{"target", "message"}`: failed/skipped → queued again |
| GET | `/api/browser` | | `{"browser": BrowserStatus}` |
| POST | `/api/browser/open` | `{"url"?: str}` | `{"browser", "message"}`: shows the shared browser on the Mac (default: a blank Google page) so Joe can log in to anything |

**BrowserStatus**: `{"running": bool, "visible": bool, "open_tabs": int, "login_tabs": ["meetup", …],
"profile_saved": bool, "busy_job_id": int|null}`. `GET /api/accounts` also returns `"browser": BrowserStatus`.
`POST /api/accounts/{site}/login-window` now opens that site's login page as a tab in the shared browser.


## Event types

**EventType**
```json
{"key": "comedy", "name": "Comedy", "email": "info@…", "imap_host": "imap.gmail.com", "imap_port": 993,
 "imap_username": "", "has_imap_password": true, "imap_password_from": "own|email_account|",
 "facebook_page": "…", "linkedin_page": "…",
 "tag_handles": "…", "audience": "…", "enabled": true, "logins": [Account],
 "last_test_at": "…|null", "last_test_ok": true|null, "last_test_message": "…", "updated_at": "…"}
```
**Account** also has `"event_type"` (a type key, or `""` = shared by every type) and `"event_type_name"`.
**JobDetail** also has `"event_type": {"key", "name"}|null`. **BrowserStatus** also has `"mode": "chrome"` and
`"offline": str|null` (why Joe's Chrome can't be used right now).

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/event-types` | | `{"event_types": [EventType], "shared_logins": [Account]}` |
| POST | `/api/event-types` | `{"key"?, "name"?, "email"?, "imap_host"?, "imap_port"?, "imap_username"?, "imap_password"? ("" keeps it), "facebook_page"?, "linkedin_page"?, "tag_handles"?, "audience"?, "enabled"?}` | `{"event_type", "message"}` (no key = new) |
| POST | `/api/event-types/{key}/delete` | | `{"message"}`; also removes that type's own logins and passwords |
| POST | `/api/event-types/{key}/test-email` | | `{"ok", "message", "event_type"}` (logs in to IMAP, reads nothing) |
| POST | `/api/accounts` | also `"event_type"?: str` | a new login for a type gets a site key like `eventbrite-comedy` |
