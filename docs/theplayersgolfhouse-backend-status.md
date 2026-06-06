# The Players Golf House Backend Status

Last updated: 2026-06-06

## Goal

Create a second, completely separate Python backend instance for The Players Golf House while keeping the existing BioluxeLab system stable.

The second backend should:

- run as its own backend and scheduler
- use its own SQLite database
- use its own Shopify credentials
- stay on a separate internal port
- be exposed publicly under the same `:8443` entrypoint at `/theplayersgolfhouse/`
- continue using the shared service/deploy script family instead of introducing a second service manager

## Current state

The shared Python backend is now path-aware and instance-aware.

Implemented in the primary backend:

- `ROOT_PATH` support so the backend can run behind `/theplayersgolfhouse`
- `DB_PATH` support so each instance can point at its own SQLite file
- `SESSION_COOKIE_NAME` support so multiple instances do not collide on cookies
- redirect handling that preserves the mounted path prefix
- template-side path rewriting for same-origin links, forms, and fetches in the server-rendered UI

## New secondary backend folder

Created:

- `python-server-theplayersgolfhouse/`

This folder is a backend-only copy of the updated Python server.

Not included in the secondary folder:

- `service.sh`
- standalone Caddy config
- private keys
- caches
- test file copy
- existing runtime logs/data from the original backend

Prepared in the secondary folder:

- empty `data/ai_blog_server.db`
- `logs/` directory
- `config.json` updated for port `4001`
- `.env.example` updated for `ROOT_PATH=/theplayersgolfhouse`
- `.env.example` updated for unique cookie name `aiblog_session_theplayersgolfhouse`
- `config.json` updated to use Players Golf House placeholder store metadata and separate Shopify credential env names

## Shared script changes

### `ai-blog-generator-python-server/service.sh`

Updated to:

- auto-detect `python-server-theplayersgolfhouse/`
- start and stop a secondary `main.py`
- start and stop a secondary `scheduler.py`
- create separate PID and log locations for the secondary instance
- install secondary Python dependencies during `setup`
- reserve internal port `4001` for the secondary backend
- add a Caddy route so `/theplayersgolfhouse/` proxies to `localhost:4001`

### `deploy_shopify_ai_blog_generator.sh`

Updated to:

- know about `python-server-theplayersgolfhouse/` on the production server
- run shared setup if either backend virtualenv is missing
- install secondary Python dependencies when needed
- keep using the shared `service.sh restart`
- health-check the secondary backend at `http://localhost:4001/health`

## Validation completed

The following checks passed locally:

- `bash -n ai-blog-generator-python-server/service.sh`
- `bash -n deploy_shopify_ai_blog_generator.sh`
- `python3 -m compileall ai-blog-generator-python-server python-server-theplayersgolfhouse`
- workspace error scan on the changed Python, shell, and JSON files
- focused pytest slice:

`cd ai-blog-generator-python-server && pytest test_features.py -q -k 'test_schedule_page_store or test_schedule_page_renders_blog_handle_dropdown or test_store_add_schedule_job_normalizes_auto_blog_handle'`

Result:

- `3 passed, 140 deselected`

## Not done yet

The secondary instance has not been deployed.

The secondary instance has not been fully started and exercised end to end with real credentials.

The remaining blocker is configuration, not code wiring.

## Required before deployment

Create `python-server-theplayersgolfhouse/.env` from the example and fill in at least:

- `SHOPIFY_CLIENT_ID_PLAYERSGOLFHOUSE`
- `SHOPIFY_CLIENT_SECRET_PLAYERSGOLFHOUSE`
- `SESSION_SECRET`
- any required AI provider keys such as `DEEPSEEK_API_KEY`, `GROK_API_KEY`, or `REPLICATE_API_TOKEN`

Confirm or update if needed:

- `ROOT_PATH=/theplayersgolfhouse`
- `DB_PATH=data/ai_blog_server.db`
- `SESSION_COOKIE_NAME=aiblog_session_theplayersgolfhouse`
- `MYSHOPIFY_DOMAIN=theplayersgolfhouse.myshopify.com`

## Expected production shape

Primary backend:

- internal port `4000`
- existing public app remains unchanged

Secondary backend:

- internal port `4001`
- public path `https://revenuemindproai.com:8443/theplayersgolfhouse/`
- separate scheduler
- separate database
- separate session cookie
- separate Shopify app credentials

## Resume checklist

When returning to this work, do this next:

1. Create `python-server-theplayersgolfhouse/.env` from the example.
2. Add the real Players Golf House Shopify credentials and secrets.
3. Run the shared deploy flow.
4. Verify `https://revenuemindproai.com:8443/theplayersgolfhouse/` loads correctly.
5. Verify login, setup, scheduler, and a safe draft-generation flow on the secondary instance.
6. Check the secondary health endpoint on `localhost:4001/health` after restart.

## Important safety note

Do not treat this as multi-store inside one app.

This work intentionally keeps The Players Golf House as a separate backend instance so changes there do not reintroduce the earlier multi-store complexity into the existing BioluxeLab flow.