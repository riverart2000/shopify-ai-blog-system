/** Shared pieces for the Task Agent pages (browser-safe). */
import { Form } from "react-router";

import { DEFAULT_TIMEZONE } from "../../lib/social-time";
import type { JobStatus, JobSummary, WorkerState } from "../../lib/task-agent.types";
import { Badge, Banner, Hidden, StatusBadge, TimeText, useBusy } from "../social/ui";
import type { Tone } from "../social/vocab";

/** The store's time zone (the Mac and the shop are both in the UK). */
export const AGENT_TZ = DEFAULT_TIMEZONE;

type StatusInfo = { label: string; tone: Tone; help?: string };

export const AGENT_JOB_STATUS: Record<string, StatusInfo> = {
  queued: { label: "Queued", tone: "info", help: "Waiting for its turn (one task runs at a time)" },
  running: { label: "Running", tone: "attention", help: "The agent is working on it now" },
  waiting_approval: { label: "Waiting for you", tone: "warning", help: "Something needs your approval before it carries on" },
  done: { label: "Done", tone: "success" },
  failed: { label: "Failed", tone: "critical", help: "Open it to see what went wrong" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

export const WORKER_STATE: Record<WorkerState, StatusInfo> = {
  running: { label: "Agent working", tone: "attention", help: "The agent is running a task right now" },
  idle: { label: "Agent ready", tone: "success", help: "Running and waiting for tasks" },
  paused: { label: "Agent paused", tone: "warning", help: "It won't start new tasks until you resume it in Settings" },
  offline: { label: "Agent offline", tone: "critical", help: "The Task Agent isn't running on the Mac" },
};

export const APPROVAL_STATUS: Record<string, StatusInfo> = {
  pending: { label: "Waiting for you", tone: "attention" },
  approved: { label: "Approved", tone: "success" },
  rejected: { label: "Rejected", tone: "neutral" },
};

export const TASK_TYPE_LABELS: Record<string, string> = {
  research: "Research",
  promote_event: "Promote an event",
  promote_post: "Post to one place",
};

/** One place a promotion posts to (`Target.status`). */
export const TARGET_STATUS: Record<string, StatusInfo> = {
  planned: { label: "Planned", tone: "neutral", help: "Found, not queued yet" },
  queued: { label: "Queued", tone: "info", help: "Waiting for its turn" },
  working: { label: "Posting now", tone: "attention", help: "The agent is on this site right now" },
  posted: { label: "Posted", tone: "success" },
  already_listed: { label: "Already listed", tone: "success", help: "The event was already on this site" },
  needs_you: { label: "Needs you", tone: "attention", help: "The site needs you to do something in the browser on the Mac" },
  skipped: { label: "Skipped", tone: "neutral" },
  failed: { label: "Failed", tone: "critical", help: "Open its task to see what went wrong" },
};

/** Statuses of a place that can still change by themselves (keep the page refreshing). */
export const ACTIVE_TARGET_STATUSES = ["planned", "queued", "working", "needs_you"];

export const TARGET_KIND_LABELS: Record<string, string> = {
  listing: "Listing site",
  group: "Facebook group",
  forum: "Forum",
  own_social: "Your channels",
};

export function targetKindLabel(kind: string): string {
  return TARGET_KIND_LABELS[kind] || kind.replace(/[_-]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

const UK_DAY = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });

type EventMoment = { dayKey: string; day: string; time: string };

/** "2026-11-15T19:00" (UK local, as the API sends it) → "Sun 15 Nov" + "19:00". Times with an offset are shown in UK time. */
function eventMoment(value: string | null | undefined): EventMoment | null {
  const text = (value || "").trim();
  if (!text) return null;
  let parts: number[] | null = null;
  let hasTime = false;
  if (/T\d{2}:\d{2}.*(?:Z|[+-]\d{2}:?\d{2})$/i.test(text)) {
    const ms = Date.parse(text);
    if (!Number.isFinite(ms)) return null;
    const p = new Intl.DateTimeFormat("en-US", {
      timeZone: AGENT_TZ, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    }).formatToParts(new Date(ms));
    const get = (type: string) => Number(p.find((part) => part.type === type)?.value || 0);
    parts = [get("year"), get("month"), get("day"), get("hour") % 24, get("minute")];
    hasTime = true;
  } else {
    const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(text);
    if (!match) return null;
    parts = [Number(match[1]), Number(match[2]), Number(match[3]), Number(match[4] || 0), Number(match[5] || 0)];
    hasTime = match[4] !== undefined;
  }
  const [year, month, day, hour, minute] = parts;
  const date = new Date(Date.UTC(year, month - 1, day));
  if (Number.isNaN(date.getTime())) return null;
  const sameYear = year === new Date().getUTCFullYear();
  const pad = (n: number) => String(n).padStart(2, "0");
  return {
    dayKey: `${year}-${pad(month)}-${pad(day)}`,
    day: sameYear ? UK_DAY.format(date) : `${UK_DAY.format(date)} ${year}`,
    time: hasTime ? `${pad(hour)}:${pad(minute)}` : "",
  };
}

/** When an event is on, UK-readable: "Sun 15 Nov, 19:00–22:00" or "Sat 14 Nov, 19:00 to Sun 15 Nov, 02:00". */
export function formatEventWhen(startsAt: string | null | undefined, endsAt?: string | null): string {
  const start = eventMoment(startsAt);
  if (!start) return (startsAt || "").trim();
  const startText = start.time ? `${start.day}, ${start.time}` : start.day;
  const end = eventMoment(endsAt);
  if (!end) return startText;
  if (end.dayKey === start.dayKey) return end.time && end.time !== start.time ? `${startText}–${end.time}` : startText;
  return `${startText} to ${end.time ? `${end.day}, ${end.time}` : end.day}`;
}

export function LoadError({ error }: { error?: string }) {
  if (!error) return null;
  return <Banner tone="critical" title="Couldn't load this from the Task Agent">{error}</Banner>;
}

export const ACTIVE_STATUSES: JobStatus[] = ["queued", "running", "waiting_approval"];

export function taskTypeLabel(type: string): string {
  return TASK_TYPE_LABELS[type] || type.replace(/[_-]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/** ISO string from the API → epoch seconds (for TimeText). */
export function isoToEpoch(value: string | null | undefined): number | null {
  if (!value) return null;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}

export function AgentTime({ iso, relative = false, full = false }: { iso: string | null | undefined; relative?: boolean; full?: boolean }) {
  return <TimeText epoch={isoToEpoch(iso)} tz={AGENT_TZ} relative={relative} full={full} />;
}

/** "2m 25s" from two ISO times (or start → now while running). */
export function elapsed(start: string | null | undefined, end: string | null | undefined): string {
  const from = isoToEpoch(start);
  if (!from) return "";
  const to = isoToEpoch(end) ?? Math.floor(Date.now() / 1000);
  return formatSeconds(Math.max(0, to - from));
}

export function formatSeconds(total: number): string {
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes < 60) return seconds ? `${minutes}m ${seconds}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`;
}

export function JobStatusBadge({ job }: { job: Pick<JobSummary, "status" | "cancel_requested"> }) {
  if (job.status === "running" && job.cancel_requested) return <Badge tone="warning" title="It will stop at its next step">Stopping…</Badge>;
  return <StatusBadge map={AGENT_JOB_STATUS} status={job.status} />;
}

export function canCancel(job: Pick<JobSummary, "status" | "cancel_requested">): boolean {
  return ACTIVE_STATUSES.includes(job.status) && !job.cancel_requested;
}

export function canRetry(job: Pick<JobSummary, "status">): boolean {
  return job.status === "failed" || job.status === "cancelled";
}

/** Cancel / Retry buttons for one job (posts intent + job_id to the current route's action). */
export function JobActions({ job, size = "sm" }: { job: JobSummary; size?: "sm" | "md" }) {
  const { busy, isSubmitting } = useBusy();
  const id = String(job.id);
  const cls = size === "sm" ? "sa-btn sa-btn-sm" : "sa-btn";
  if (!canCancel(job) && !canRetry(job)) return null;
  return (
    <div className="sa-row">
      {canCancel(job) ? (
        <Form
          method="post"
          className="sa-inline-form"
          preventScrollReset
          onSubmit={(event) => {
            if (!window.confirm(`Cancel “${job.title}”?${job.status === "running" ? " It will stop at its next step." : ""}`)) event.preventDefault();
          }}
        >
          <Hidden values={{ intent: "cancel", job_id: id }} />
          <button type="submit" className={`${cls} sa-btn-critical`} disabled={busy}>
            {isSubmitting("cancel", ["job_id", id]) ? "Cancelling…" : "Cancel"}
          </button>
        </Form>
      ) : null}
      {canRetry(job) ? (
        <Form method="post" className="sa-inline-form" preventScrollReset>
          <Hidden values={{ intent: "retry", job_id: id }} />
          <button type="submit" className={cls} disabled={busy}>
            {isSubmitting("retry", ["job_id", id]) ? "Queuing again…" : "Try again"}
          </button>
        </Form>
      ) : null}
    </div>
  );
}

/** Short, readable version of a tool call's JSON args. */
export function shortArgs(args: string, max = 140): string {
  let text = args || "";
  try {
    const parsed = JSON.parse(args) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const entries = Object.entries(parsed as Record<string, unknown>);
      if (entries.length === 1 && typeof entries[0][1] === "string") text = entries[0][1] as string;
      else text = entries.map(([key, value]) => `${key}: ${typeof value === "string" ? value : JSON.stringify(value)}`).join(" · ");
    }
  } catch {
    // keep the raw text
  }
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

export const TOOL_LABELS: Record<string, string> = {
  web_search: "Searched the web",
  fetch_page: "Opened a page",
  final_result: "Wrote the answer",
};

export function toolLabel(tool: string): string {
  return TOOL_LABELS[tool] || tool.replace(/_/g, " ");
}

/** A step the agent had to redo (its answer was sent back, or the tool failed). */
export function stepOutcome(summary: string): "redone" | "failed" | "denied" | "ok" {
  if (summary.startsWith("REJECTED:")) return "redone";
  if (summary.startsWith("FAILED:")) return "failed";
  if (summary.startsWith("DENIED:")) return "denied";
  return "ok";
}
