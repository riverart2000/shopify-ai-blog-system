# Task Agent

Background agents that do web, email and promotion work, **one job at a time**, on the local
Gemma 4 31B model served by oMLX (the same one the Social Engine uses,
with thinking turned on for agent work). Spec: `../TASK-AGENT.md`.

## Status
- **Done:** job queue (SQLite), worker, audit log, crash-safe resume, cancel/pause, `research` task,
  task templates, the **Task Agent** page in the Shopify admin app (tasks, templates, approvals,
  settings, email account, site logins), Telegram, and **event promotion** in one shared browser.
- Next: C email (approval queue), D triggers from other systems (planned for n8n, which was removed on 5 Nov 2026).

## Admin page
Shopify admin → **Task Agent**. The app talks to the worker's private API on `127.0.0.1:4100`
(contract: `API.md`) with `TASK_AGENT_API_KEY`, which must be the same in `task-agent/.env` and the
live release `.env`. Passwords typed on the page go to the macOS Keychain (service `task-agent`).

## The browser: Joe's own Chrome
Agents work in **Joe's everyday Google Chrome**, where he's already logged in everywhere, through the OpenClaw
extension (`task_agent/chrome.py`: each action is one `openclaw browser --browser-profile chrome --json …`
command, about 4 seconds; typed text goes in on stdin, never on the command line). Agents open and close only
their own tabs. If Chrome or the extension isn't connected, the task hands itself to Joe (Telegram card) instead
of failing. Setting `BROWSER=own` switches back to a separate agent profile (`profiles/shared/`, never committed).
Code, not the model, types passwords (from the Keychain); the model can't type into password or payment boxes.

## Event types
Joe runs several kinds of events (seeded: Comedy, Bloomsbury speakers, Biohacking, Juggling; edit them on the
Settings page). Each type has its own email (with IMAP, so the agent can read sign-up codes), its own Eventbrite
and Meetup logins (logins tagged with the type) and its own Facebook Page and LinkedIn Page. Facebook and
LinkedIn themselves are one shared personal login (logins with no type). A promotion task picks a type, and the
agent then sees only that type's logins plus the shared ones. One Chrome can only be logged in to one Eventbrite
or Meetup account at a time, so the agent checks whose account it's on and switches with the type's saved login.
`check_inbox` returns only codes and the site's own verify/login links from the last 30 minutes, never whole
emails. The IMAP password is in the Keychain (`event-type:<key>:imap`); a type whose inbox is the same mailbox
as Settings → Email account uses that account's saved password instead, so it's stored once.
IMAP and SMTP check certificates against the Mac's own trust store (`tls.py`): Python's built-in list on macOS
is missing newer roots such as Let's Encrypt's ISRG Root X2.

Web search uses **Brave Search** when `BRAVE_API_KEY` is in `.env` (or `brave:api_key` in the Keychain), with
DuckDuckGo and Bing as backups.

## Promote an event (template `promote-event`)
1. `promote_event` reads the event pages (their schema.org data first; code keeps the event's picture from them,
   since the model often leaves it out of its plan), then plans up to 15 places:
   listing sites (only for our own events), Facebook groups (starting with the ones Joe is in) and
   forums. Every place must have come up in a search or page, or the plan is sent back.
2. Each place becomes a `promote_post` job (`parent_id` = the promotion; hidden in the task list).
   Listing sites go first. Groups and forums are paced: at most 5 a day per event, 20 minutes apart
   (after any group post already planned), 09:00–21:00, never in the last 2 hours before the event.
3. The event type's own Facebook Page and LinkedIn Page become places too (posted as the Page, in Chrome).
   Social Autopilot is only for Bio Luxe Lab, so promotions never post through it.
4. When a site needs Joe (captcha, code, verification email), the job hands its tab to him on the Mac,
   sends a Telegram card (Done / Skip) and waits up to a day; Done restarts it straight away.
5. A posted result must be an address the browser really opened. Telegram gets the plan and one
   final report. **Edit** on a task that hasn't started (queued, failed or cancelled) changes its
   details and queues it again under the same number. **Clone** on any task copies its values into
   a new one; cloning a promotion reuses the places that worked and still looks for new ones.

## Commands
```bash
cd task-agent
cp .env.example .env                      # defaults work as-is for oMLX on this Mac
uv run task-agent research "question"     # queue a research job
uv run task-agent run-once                # run ready jobs now
uv run task-agent worker                  # run forever (one job at a time)
uv run task-agent jobs                    # list jobs
uv run task-agent show 1                  # steps + result for job 1
uv run task-agent picture 5 <address|file> # give promotion 5 its event picture (unfinished posts get it)
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

## Telegram
Approve, ask and instruct from your phone. Set the bot token and your username on the **Settings** page,
then send `/start` to the bot. Only that account can use it, locked to its permanent Telegram id on the first
message. The bot runs inside the worker and long-polls Telegram, so no public address is needed.
- **Sends:** posts and emails waiting for approval (with Approve/Reject buttons), failures, results of tasks
  queued from Telegram, and a summary every morning (`TELEGRAM_SUMMARY_HOUR`, default 08:00).
- **Answers:** questions from live Social Autopilot and Task Agent data, using the shared local model.
- **Changes:** pause/resume campaigns, approve/reject posts, auto promotions on/off, queue/cancel/retry tasks.
  The model only *proposes*; each change runs (by code, not the model) after you press Confirm.
- Needs `SOCIAL_API_KEY` in `.env` (the live server's `AI_BLOG_BACKEND_API_KEY`).

## Model
**The model is set in one file: `~/.config/local-ai/model.json`** (server, text model, vision model, context window,
answer cap), read by OpenClaw, the Task Agent and Social Autopilot alike, because two large models can't fit in
memory together. No code names a model: `.env` and the admin page can't override the file, and if it's missing or
incomplete the Task Agent stops with a message naming the file and the problem (`task_agent/local_model.py`;
Social Autopilot instead switches to its fallback model and its AI check says why). Without a `vision_model`,
images go to the text model.

To change models:
1. In oMLX, give the new model the same model settings as the current one (speed-up drafter, context window), pin it,
   and unpin and unload the old one.
2. Edit the file. Social Autopilot picks it up at once.
3. Restart the Task Agent and run `node scripts/sync-local-model.mjs` in ~/openclaw.

One model does text and images, with oMLX's speed-up drafter, so oMLX never swaps models (a swap while one was busy
made a campaign run fail on 30 Oct). It's a Gemma 4 31B: chosen on 31 Oct as the most careful on research (no
invented details), and switched from 8-bit to 6-bit on 5 Nov (6.5 GB less memory, 10–25% faster, the same answers in
a side-by-side test). Use the chat (`-it`) version: the plain one is the base model, which doesn't follow
instructions. The Task Agent turns thinking on per request; the Social Engine keeps it off.

## How a job runs
1. A job is queued in `data/task_agent.db` (a repeated `--key` never creates a second job).
2. The worker claims it and builds that task type's agent. The agent sees **only that task's tools**.
3. Before every model call, the conversation so far is saved. If the worker or oMLX dies mid-job,
   the job goes back in the queue and **resumes** without redoing finished steps.
4. Every tool call is logged to `agent_steps` and `logs/<job_id>/steps.jsonl`; the result goes to
   `logs/<job_id>/result.json`.
5. Limits: 60 model calls / 20 minutes per job (a task type can set its own: posting one event in one place is
   150 / 60 min). Model-server or network errors retry with backoff (3 attempts); anything else fails with the
   error recorded.
6. A failed page action tells the agent OpenClaw's own error and shows the page as it is now (fresh refs). The same
   action failing 3 times gets a "do something different" warning; 8 failed page actions in a row stop the job
   ("the last 8 page actions all failed"), so a stuck job doesn't use its whole budget. After a restart the job's
   tab is gone: the agent is told to open the page again, and refs from before the restart are checked first.
7. What the agent sees of a page (`tools/browser.py`):
   - Anything that reacts to a click has a ref, not only links and buttons (menu rows and calendar days are often
     plain elements).
   - An open dialog is shown first. On a page too long for OpenClaw to read in full (Facebook: 40,000 characters),
     only the dialog on top is read and shown, because sites draw dialogs last and it would be cut off.
   - A click on something that's covered says what covers it (a dialog, an open menu's full-page layer, or a
     sticky bar), so the agent deals with that instead of clicking again.

## Guardrails in place
- Web pages and search results are wrapped as `<untrusted_data>`; the rules (sent on every request,
  never trimmable) tell the model it's data, not instructions.
- Fetches to this Mac, the local network or Tailscale are blocked (so a page can't steer the agent
  at oMLX's admin API or the live server).
- Research results may only cite pages the agent actually opened, or they're sent back.
- The agent never pays; payment steps go to Joe. Outgoing email will wait in an approvals queue (Phase C).
- Browser agents never see passwords (code types them from the Keychain), can't type into card fields,
  and only keep the latest two page views in memory (older ones are trimmed to their address).
