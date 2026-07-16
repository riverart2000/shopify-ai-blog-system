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

The deploy script:
1. Pushes local changes to GitHub `main` branch.
2. SSHes into the server.
3. Pulls `main` branch on the server.
4. Selectively runs `pip install` (if requirements.txt changed) and `npm install & build` (if frontend changed).
5. Restarts all services via `service.sh`.
6. Runs health checks on `:4000` (FastAPI) and `:3001` (React).

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
