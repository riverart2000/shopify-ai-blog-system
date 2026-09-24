# Production Server Environment (revenuemindproai.com)

## Overview
This server (Ubuntu) hosts the **Shopify AI Blog System** and its associated services.

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
