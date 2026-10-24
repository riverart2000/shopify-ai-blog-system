# Production Hosting (revenuemindproai.com)

## Overview
The **Shopify AI Blog System** runs on the M5 behind Cloudflare Tunnel. AWS
continues to host the separate RevenueMindProAI website and other sites.

## M5 production — migrated 24 October 2026

The Shopify AI Blog System now runs on the M5. Its public URL remains
`https://revenuemindproai.com:8443`; Shopify OAuth callbacks, webhooks and app
proxy configuration were not changed. Cloudflare Tunnel carries traffic to the
M5's loopback-only Caddy proxy on port 8787. The ordinary port-443 website still
runs on AWS, reached through that same M5 proxy over verified HTTPS. Therefore
both public routes depend on the M5 being awake, online and logged in.

Cloudflare account: `b2967eee2560b13b7e1761c466e7a067`.
Tunnel: `m5-shopify-blog` (`a46d228b-465e-4038-adc4-115528e699a6`).
The apex DNS record is now a proxied CNAME to
`a46d228b-465e-4038-adc4-115528e699a6.cfargotunnel.com` (previously a proxied A
record pointing to `18.134.80.37`). The diagnostic hostname
`m5-origin.revenuemindproai.com` uses the same tunnel. Other DNS records are
unchanged. The tunnel has four connections to Cloudflare's London locations.

### Runtime and automatic startup

Private runtime: `/Users/joebains/shopify-ai-blog-system/.runtime/m5/`.
Production source and data: its `release/` directory, source commit `305cefc`.
Python dependencies match AWS; Node is 22.22.2. The runtime and credential files
are ignored by Git. The token is in `tunnel.token`, mode 0600, and is passed by
file path rather than command-line value. Do not print, commit or share it.

The following launch agents live in `~/Library/LaunchAgents/` and restart on
failure and on user login:

- `com.joebains.shopify-blog.frontend`: `127.0.0.1:3001`.
- `com.joebains.shopify-blog.backend`: `127.0.0.1:4000`, production mode.
- `com.joebains.shopify-blog.scheduler`: scheduled publishing and scans.
- `com.joebains.shopify-blog.proxy`: Caddy on `127.0.0.1:8787`.
- `com.joebains.shopify-blog.tunnel`: Cloudflare connector, metrics on
  `127.0.0.1:20241`.

They do not start before login after a reboot; FileVault/login must be completed
for unattended recovery. Frontend and backend startup after a reboot was
verified during migration. Logs are in `.runtime/m5/logs/` and backend application
logs are in `.runtime/m5/release/ai-blog-generator-python-server/logs/`.
`SCHEDULER_ENABLED` gates scheduler startup; `PRODUCTION_ACTIVE` prevents the old
AWS deploy script and legacy local service manager from touching live ports.

### Validation and AWS resources

Verified after the DNS switch:

- Existing app URL: HTTP 200 with `X-Shopify-Origin: m5`.
- Existing standard HTTPS website: HTTP 200.
- Backend health: HTTP 200, production mode.
- Public quiz read through frontend and backend: HTTP 200.
- Real Shopify storefront `/apps/wellness-quiz` proxy: HTTP 200 and expected JSON.
- Migrated offline Shopify session: authenticated read-only shop API returned 200.
- Invalid webhook signature: rejected with HTTP 401.
- Both SQLite databases: integrity check passed before final restore.
- Scheduler: startup logged against the final M5 database; AWS scheduler absent.

AWS frontend, backend, scheduler and this app's Caddy proxy are stopped. Ports
3001, 4000 and 8443 are no longer listening on AWS. nginx and the separate
Next.js websites on ports 3000/3002 remain active. Available memory increased
from 628 MiB immediately before cutover to 817 MiB after cleanup; used memory
fell from 1277 MiB to 1088 MiB and used swap from 249 MiB to 171 MiB.

Existing RSS routes pointing at port 18090 were already unavailable before this
migration. They remain configured but no RSS service was moved or invented.

### Data and rollback

The AWS writers were stopped before the final online SQLite backups were taken.
The final archive is `.runtime/m5/snapshots/final.tar.gz`; the earlier staging
snapshot and pre-restore local state are retained in the same private directory.
Five saved landing-page output paths were remapped to the M5 release directory.
The AWS copy is preserved, but is now stale as the M5 accepts writes.

Do not simply point DNS back at AWS or restart its scheduler. For rollback:
stop new writes and the M5 scheduler, stop app services, take fresh SQLite
backups, transfer current data/files back to AWS, reverse the saved output path
mapping, verify database integrity, and only then restore AWS services and DNS.
Never enable both schedulers. AWS has `.m5-migrated` and a service-manager guard
to prevent accidental restart; its previous manager is retained as
`ai-blog-generator-python-server/service.sh.before-m5.bak`. These guards must
only be lifted as part of a deliberate, data-aware rollback.

### Future deployment

The old `deploy_shopify_ai_blog_generator.sh` deliberately refuses AWS deployment
while M5 production is active. The development checkout is separate from the
running release, so editing source does not silently change production.

For a future release, prepare and build it locally, validate its Python and
Node dependencies, and preserve the live `.env` files, SQLite databases,
generated output and tunnel configuration. Pause scheduled/background work and
take fresh database backups before applying any migrations or swapping code.
Restart the M5 frontend/backend launch agents, run the public validation above,
and resume the M5 scheduler only after checks pass. Do not use `service.sh` or
`npm run dev` to manage these production processes. A dependency or schema
change requires its own tested deployment; do not overwrite the production
release with a development database.

To restart one service after a reviewed update, use e.g.
`launchctl kickstart -k gui/$(id -u)/com.joebains.shopify-blog.frontend`.
To stop a service for maintenance, use `launchctl bootout` with its label; to
start it again, use `launchctl bootstrap` with the installed plist path.

Migration helpers are in `scripts/m5/`. The proxy's source configuration is
`scripts/m5/Caddyfile`; its active copy is `.runtime/m5/Caddyfile`. The snapshot
helper streams secrets and must always be redirected into a private archive,
never printed to the terminal. No automatic off-machine backup schedule was
created by this migration; the retained cutover snapshot is a point-in-time copy.

## Previous AWS deployment reference

The following describes the retired AWS app deployment. The unrelated AWS
websites remain in service, and the server itself must not be shut down.

## Directory Structure
- `/home/ubuntu/shopify-ai-blog-system/` -> Main application repository (cloned from GitHub)
  - `ai-blog-generator-app/` -> React frontend (Shopify App)
  - `ai-blog-generator-python-server/` -> FastAPI backend, Caddy, Scheduler
  - `deploy_shopify_ai_blog_generator.sh` -> Main deployment script

## Services & Ports
The services are managed by `ai-blog-generator-python-server/service.sh` and are reverse-proxied by **Caddy**:

1. **Caddy (Reverse Proxy & TLS)**
   - External Ports: `80` (ACME HTTP-01), `8443` (Cloudflare HTTPS Inbound)
   - Handles TLS certificates.
   - Proxies traffic to the frontend, backend, and standalone scripts.

2. **React App (Frontend)**
   - Internal Port: `3001`
   - Run via `npm run start` (react-router-serve).

3. **FastAPI Backend (Primary)**
   - Internal Port: `4000`
   - Run via `uvicorn main:app`.
   - Handles core logic, LLM integrations, Shopify API interactions, and landing page generation.

4. **Scheduler Service**
   - Autonomous blog post scheduler (`scheduler.py`).

5. **RSS Aggregator / Publer Service**
   - Internal Port: `18090`
   - Proxied by Caddy at `/publar*` and `/apps/rss*`.

## Deployment
Deployments are completely automated from the local dev machine using the script:
`./deploy_shopify_ai_blog_generator.sh`

### Critical rule: never build the frontend on production

The production server does not have enough spare CPU/RAM for a React/Vite build.
Running any of the following on production can exhaust the server and take the
live application down:

- `npm install`
- `npm run build`
- `vite build`
- `react-router build`

The Shopify React app must always be compiled on the local development machine
(or a separate Linux build/CI machine). Only the completed
`ai-blog-generator-app/build/` directory is transferred to production. The
deployment script enforces this workflow: it builds locally, uploads the bundle
to a staging directory, swaps the bundle on the server, and then restarts the
services. Production must only pull source, apply lightweight database
migrations when needed, swap prebuilt artefacts, and restart services.

The repository root on production contains a sentinel file named
`.production-no-frontend-build`. The React app's `preinstall` and `prebuild`
guards detect it and stop `npm install` or `npm run build` with an explanatory
message before they consume production resources. The deploy script creates the
sentinel automatically. Do not delete or bypass it on the live server.

If `package.json` or `package-lock.json` adds a new runtime dependency, do not
install it on the live server. Prepare and verify a Linux-compatible runtime
bundle away from production before deploying it.

The deploy script:
1. Pushes local changes to GitHub `main` branch.
2. Installs/builds the React app locally and uploads the completed build bundle.
3. SSHes into the server and pulls `main`.
4. Selectively runs `pip install` only if Python requirements changed; it never compiles the frontend on production.
5. Atomically swaps in the prebuilt frontend bundle.
6. Restarts all services via `service.sh`.
7. Runs health checks on `:4000` (FastAPI) and `:3001` (React).

## Logs & Process Management
Logs are located at: `/home/ubuntu/shopify-ai-blog-system/ai-blog-generator-python-server/logs/`
- `main.log` (FastAPI backend)
- `frontend.log` (React app)
- `scheduler.log`
- `caddy.log` & `caddy-access.log`

Start/Stop services manually:
```bash
cd /home/ubuntu/shopify-ai-blog-system/ai-blog-generator-python-server
./service.sh stop
./service.sh start
```
