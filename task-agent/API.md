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
