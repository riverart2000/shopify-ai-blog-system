# Task Agent

Background agents that do web, email and promotion work, **one job at a time**, on the local
Gemma model served by oMLX (the same one the Social Engine uses,
with thinking turned on for agent work). Spec: `../TASK-AGENT.md`.

## Status
- **Phase A (done):** job queue (SQLite), worker, audit log, crash-safe resume, `research` task.
- Next: B browser (sign-ups, logins, listings), C email (approval queue) + Postiz, D n8n + approvals page + 24/7 service.

## Commands
```bash
cd task-agent
cp .env.example .env                      # defaults work as-is for oMLX on this Mac
uv run task-agent research "question"     # queue a research job
uv run task-agent run-once                # run ready jobs now
uv run task-agent worker                  # run forever (one job at a time)
uv run task-agent jobs                    # list jobs
uv run task-agent show 1                  # steps + result for job 1
uv run pytest                             # offline tests (fake model, no internet)
```

## Run as a service (24/7, restarts on crash or reboot)
```bash
cp deploy/com.joebains.task-agent.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.joebains.task-agent.plist
launchctl print gui/$(id -u)/com.joebains.task-agent | grep state   # → running
tail -f logs/worker.log
# stop:    launchctl bootout gui/$(id -u)/com.joebains.task-agent
```
A stop or restart mid-job is safe: the job goes back in the queue and resumes from its last step.

## Model
One text model for everything: `gemma-4-26b-a4b-it-8bit`, shared with the Social Engine, so oMLX
never has to swap models (a swap while one is busy is what made a campaign run fail on 30 Oct).
The Task Agent turns Gemma's thinking on per request; the Social Engine keeps it off. Images stay on
`Qwen3-VL-8B`, which fits in memory alongside Gemma. Switching model is `LLM_MODEL` in `.env`.

## How a job runs
1. A job is queued in `data/task_agent.db` (a repeated `--key` never creates a second job).
2. The worker claims it and builds that task type's agent. The agent sees **only that task's tools**.
3. Before every model call, the conversation so far is saved. If the worker or oMLX dies mid-job,
   the job goes back in the queue and **resumes** without redoing finished steps.
4. Every tool call is logged to `agent_steps` and `logs/<job_id>/steps.jsonl`; the result goes to
   `logs/<job_id>/result.json`.
5. Limits: 60 model calls / 20 minutes per job. Model-server or network errors retry with backoff
   (3 attempts); anything else fails with the error recorded.

## Guardrails in place
- Web pages and search results are wrapped as `<untrusted_data>`; the rules (sent on every request,
  never trimmable) tell the model it's data, not instructions.
- Fetches to this Mac, the local network or Tailscale are blocked (so a page can't steer the agent
  at oMLX's admin API or the live server).
- Research results may only cite pages the agent actually opened, or they're sent back.
- The agent never pays; payment steps go to Joe. Outgoing email will wait in an approvals queue (Phase C).
