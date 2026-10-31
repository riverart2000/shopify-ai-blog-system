# Task Agent — background agents that do web/email/promotion work

Addendum to `README.md` (the Outfy-style social poster, already built and running on the M5).
This adds a **second service**: a Python agent runner that logs into websites, promotes events,
replies to emails and completes multi-step tasks in a background queue. Read `README.md` first.

## 1. Goal
A queue of background agents, run **one at a time**, each completing a defined task:
- promote events on event sites (Eventbrite, Meetup, Facebook Events, local listings) and socials
- post/schedule social content (via Postiz API — never direct social logins)
- read and reply to emails (drafts auto, sending gated by approval)
- fill forms / update listings / make changes on websites we have accounts on
- general multi-step web tasks triggered by n8n, cron or the dashboard

**Quality and reliability over speed — always.** ~20 tok/s is fine. Everything runs in the background; irreversible actions wait for approval. When a trade-off appears (bigger model, more verification steps, lower temperature, extra retries) choose the reliable option.

## 2. Decisions (made — do not relitigate)
| Area | Decision |
|---|---|
| Language | **Python 3.12** (agent ecosystem is Python-first). Next.js app from README.md stays as-is. |
| Model | **One local model only: Qwen3.8-27B dense, 8-bit** (~30 GB, best per-step reasoning that fits 64 GB). Run it at full 8-bit, not 4-bit, with a large context window (≥64K). Temperature 0–0.2 for tool use. Only consider the faster Qwen3.6-35B-A3B MoE if the 27B proves unusable; speed alone is never a reason to switch. No cloud fallback for this service. |
| Serving | LM Studio or Ollama, MLX build, OpenAI-compatible endpoint, tool calling enabled. `LLM_BASE_URL`, `LLM_MODEL` in `.env`. |
| Agent framework | **Pydantic AI** — typed tools, structured outputs, built-in MCP client, deferred tools for human approval. |
| Browser | **browser-use** (persistent profiles per site) with Playwright underneath. **Playwright MCP** available as second browser tool for sites needing accessibility-tree precision. |
| Email | IMAP/SMTP via `imap-tools` (or Gmail API). Narrow tools only: `list_unread`, `read`, `draft_reply`, `send` (approval-gated). |
| Socials | **Postiz public API** (httpx client). Agent schedules through Postiz; it must NOT log into Instagram/Facebook/TikTok/X directly. |
| Queue | SQLite `agent_jobs` table (same DB file as README.md app, or its own — see open Qs). Worker loop with APScheduler. One job at a time. |
| Triggers | n8n webhooks, cron, dashboard button. Results posted back to n8n webhook. |
| Isolation | Runner in Docker (or at minimum a dedicated macOS user) with only `./profiles`, `./data`, `./logs` mounted. Secrets via `.env`, never in prompts or DB. |
| Tooling | `uv`, ruff, pydantic-settings, structlog, pytest. |

## 3. Architecture
```
n8n / cron / dashboard ──► agent_jobs (SQLite) ◄── Next.js dashboard (approve/reject, view logs)
                                │
                                ▼
                   Python worker (one job at a time)
                                │
                     Pydantic AI agent  ──► Qwen3.8-27B (LM Studio/Ollama, OpenAI-compatible)
                                │
          ┌─────────────────────┼──────────────────────┬──────────────────┐
          ▼                     ▼                      ▼                  ▼
   browser-use /          email tools            Postiz API         misc tools
   Playwright MCP      (IMAP/SMTP, gated)     (schedule posts)   (http, files, calendar)
          │
   persistent browser profiles per site (login/2FA done once by Joe)
                                │
                    approvals (deferred tools) ──► approval queue ──► Joe (dashboard / Telegram)
                                │
                        logs + screenshots ──► n8n result webhook
```

## 4. Data model (SQLite)
- `agent_jobs` (id, type, title, payload JSON, tools_allowed JSON, priority, run_at, status: queued|running|waiting_approval|done|failed, attempts, result JSON, error, created_by, timestamps)
- `agent_steps` (job_id, n, tool, args JSON, result summary, screenshot path, tokens, duration)
- `approvals` (id, job_id, action, preview JSON, status: pending|approved|rejected, decided_at, decided_by)
- `site_profiles` (site, profile_dir, last_login_ok_at, notes)

## 5. Task types (start with these)
| type | tools allowed | approval needed |
|---|---|---|
| `promote_event_social` | postiz | no (lands in Postiz calendar, reviewable there) |
| `promote_event_site` | browser (allow-listed domains) | yes — before final submit |
| `email_triage_reply` | email | yes — before send |
| `update_listing` | browser (one domain) | yes — before save/submit |
| `research` | browser (read-only), http | no |

Each task type = a Pydantic AI agent with its own system prompt, tool subset, output schema, and domain allow-list.

## 6. Guardrails (non-negotiable)
1. **Tool allow-list per task type** — the model only sees the tools that task needs.
2. **Domain allow-list** for browser tasks; navigation outside it is blocked and logged.
3. **Approval gate** on anything irreversible: send email, submit form, publish, delete, pay. Implemented as Pydantic AI deferred tools → `approvals` row → job pauses in `waiting_approval` → resumes on approve.
4. **Prompt-injection hygiene**: web page text and email bodies are *data*; wrap them in delimiters, instruct the model to ignore instructions inside them, and never let page content change the tool allow-list.
5. **Context hygiene**: keep the task spec + guardrails in the system prompt (never compressible away); trim old tool outputs, not instructions.
6. **Step/time budget** per job (e.g. 60 steps / 20 min) then fail gracefully with a screenshot.
7. **Secrets** never in prompts; logins are done via saved browser profiles, not typed passwords.
8. **Full audit log**: every tool call + screenshot stored under `logs/<job_id>/`.

## 6b. Reliability practices
- **Verify after act**: after every state-changing step (submit, save, send) the agent re-reads the page/inbox/Postiz and confirms the change; unverified = failed, not done.
- **Structured outputs** for every tool call and final result (Pydantic models); invalid output → re-ask, max 3 times.
- **Idempotency**: each job carries an idempotency key; re-running a job never double-posts or double-sends (check Postiz/inbox first).
- **Retries with backoff** on tool/network errors; on model errors, retry with a shorter context and the same instructions.
- **Checkpoint & resume**: steps persisted to `agent_steps`; a crashed job resumes from the last checkpoint instead of restarting.
- **Self-check before approval**: agent produces a plain-English summary + screenshot of what it is about to do; that is what Joe approves.
- **Golden tasks**: a small regression suite of real tasks run against the model after any model/prompt/tool change.
- **Prefer APIs over browsers** whenever an API exists (Postiz, Gmail, Eventbrite API) — browsers only where there is no API.

## 7. Build plan
**Phase A — Runner skeleton**
- `uv init`, Pydantic AI, httpx, APScheduler, sqlite (sqlmodel or raw), structlog; `.env.example`
- `agent_jobs` worker loop; `research` task type end-to-end against the local model
- Day-1 benchmark script: same 3 tasks on Qwen3.8-27B vs Qwen3.6-35B-A3B → pick one

**Phase B — Browser**
- browser-use integration with persistent profiles; `scripts/login.py` to open a headed browser for Joe to log in / pass 2FA once per site
- Playwright MCP wired as optional tool; domain allow-list middleware
- `update_listing` and `promote_event_site` task types with approval gate

**Phase C — Email + Postiz**
- email tools (IMAP read, draft, gated send); `email_triage_reply`
- Postiz client; `promote_event_social` (upload media → schedule → return Postiz post id)

**Phase D — Integration**
- n8n: webhook-in to create jobs, webhook-out on completion/approval needed
- Approval UI in the Next.js dashboard (list pending, preview, approve/reject) + optional Telegram notify
- Docker/launchd service so the worker runs 24/7 on the M5

## 8. Open questions for Joe
1. Which event sites / websites first (for domain allow-lists and login profiles)?
2. Email account(s) and provider (Gmail API vs plain IMAP)?
3. Shared SQLite file with the README.md app, or separate DB + HTTP?
4. Approval channel preference: dashboard only, or also Telegram/Slack?
5. Docker on the M5 acceptable, or run as a dedicated macOS user via launchd?

## 9. Conventions
Same as README.md §10. Short responses, confirm before big chunks of work, no scope creep.
