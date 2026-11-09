import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useLoaderData } from "react-router";

import {
  ACTIVE_TARGET_STATUSES, AgentTime, JobActions, JobStatusBadge, LoadError, TARGET_STATUS, elapsed, formatEventWhen, formatMs,
  isoToEpoch, shortArgs, stepOutcome, targetKindLabel, taskTypeLabel, toolLabel,
} from "../components/agent/parts";
import { ActionBanner, Badge, Card, EmptyState, Hidden, JsonBlock, StatusBadge, useBusy, usePolling } from "../components/social/ui";
import { statusInfo, type Tone } from "../components/social/vocab";
import {
  TaskAgentError, errorMessage, formString, handleJobIntent, runAgentIntent, taFetch,
  type JobDetail, type JobResponse, type Promotion, type PromotionTarget, type ResearchResult, type Step,
  type TargetActionResponse,
} from "../lib/task-agent.server";
import { seg } from "../lib/task-agent.shared";
import { authenticate } from "../shopify.server";

export const handle = { title: "Task" };

export const loader = async ({ request, params }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  const jobId = params.jobId || "";
  try {
    const data = await taFetch<JobResponse>(`/jobs/${seg(jobId)}`);
    return { jobId, job: data.job, steps: data.steps, error: "" };
  } catch (error) {
    return { jobId, job: null as JobDetail | null, steps: [] as Step[], error: errorMessage(error) };
  }
};

/** Done / Skip / Retry on one place of a promotion → POST /targets/{id}/done|skip|retry. */
function targetVerb(intent: string): "done" | "skip" | "retry" | "" {
  if (intent === "target_done") return "done";
  if (intent === "target_skip") return "skip";
  if (intent === "target_retry") return "retry";
  return "";
}

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  const verb = targetVerb(intent);
  if (verb) {
    const targetId = formString(form, "target_id");
    return runAgentIntent(intent, async () => {
      if (!/^\d+$/.test(targetId)) throw new TaskAgentError("No place was given.", { type: "validation" });
      const done = await taFetch<TargetActionResponse>(`/targets/${seg(targetId)}/${verb}`, { method: "POST" });
      return { message: done.message };
    }, targetId);
  }
  return handleJobIntent(intent, form);
};

const CONFIDENCE: Record<string, { label: string; tone: Tone; help: string }> = {
  high: { label: "High confidence", tone: "success", help: "Backed by the pages it opened" },
  medium: { label: "Medium confidence", tone: "warning", help: "Partly backed — check the gaps" },
  low: { label: "Low confidence", tone: "critical", help: "Treat this as a starting point only" },
};

function isResearchResult(value: unknown): value is ResearchResult {
  if (!value || typeof value !== "object") return false;
  const result = value as Partial<ResearchResult>;
  return typeof result.answer === "string" && Array.isArray(result.sources);
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function ResearchResultView({ result }: { result: ResearchResult }) {
  const confidence = CONFIDENCE[result.confidence] || CONFIDENCE.low;
  return (
    <div className="sa-stack">
      <div className="sa-row">
        <Badge tone={confidence.tone} title={confidence.help}>{confidence.label}</Badge>
        <span className="sa-small sa-muted">{result.sources.length} source{result.sources.length === 1 ? "" : "s"}</span>
      </div>
      <div className="sa-prewrap">{result.answer}</div>
      {result.key_points?.length ? (
        <div className="sa-stack-sm">
          <strong>Key points</strong>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {result.key_points.map((point, index) => <li key={index}>{point}</li>)}
          </ul>
        </div>
      ) : null}
      {result.sources.length ? (
        <div className="sa-stack-sm">
          <strong>Sources</strong>
          <ol style={{ margin: 0, paddingLeft: 18 }} className="sa-stack-sm">
            {result.sources.map((source, index) => (
              <li key={`${source.url}-${index}`} className="sa-break">
                <a href={source.url} target="_blank" rel="noopener noreferrer">{source.title || hostOf(source.url)}</a>
                <span className="sa-small sa-muted"> · {hostOf(source.url)}</span>
                {source.used_for ? <div className="sa-small sa-muted">Used for: {source.used_for}</div> : null}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
      {result.gaps?.length ? (
        <Card accent="warning">
          <div className="sa-stack-sm">
            <strong>What it couldn&apos;t confirm</strong>
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {result.gaps.map((gap, index) => <li key={index}>{gap}</li>)}
            </ul>
          </div>
        </Card>
      ) : null}
    </div>
  );
}

/** Only http(s) links from the agent's findings become clickable (never javascript: and the like). */
function safeHref(url: string | null | undefined): string | null {
  const text = (url || "").trim();
  return /^https?:\/\//i.test(text) ? text : null;
}

function EventSection({ promotion }: { promotion: Promotion }) {
  const event = promotion.event;
  const reused = promotion.reused_from ? (
    <p className="sa-small sa-muted">
      Reused the places that worked for <Link to={`/app/agent/tasks/${seg(promotion.reused_from)}`}>task {promotion.reused_from}</Link>, and looked for new ones.
    </p>
  ) : null;
  if (!event) {
    return (
      <s-section heading="The event">
        <p className="sa-muted">The agent hasn&apos;t read the event pages yet.</p>
        {reused}
      </s-section>
    );
  }
  const when = formatEventWhen(event.starts_at, event.ends_at);
  const place = [event.venue, event.town].map((part) => (part || "").trim()).filter(Boolean).join(", ");
  const onlineHref = safeHref(event.online_url);
  const ticketHref = safeHref(event.ticket_url);
  const imageSrc = safeHref(event.image_url);
  return (
    <s-section heading="The event">
      <div className="sa-row-top">
        {imageSrc ? <img className="sa-thumb" src={imageSrc} alt="" loading="lazy" style={{ width: 96, height: 96 }} /> : null}
        <div className="sa-stack-sm sa-grow" style={{ minWidth: 0 }}>
          <strong className="sa-break">{event.title || "Untitled event"}</strong>
          <dl className="sa-kv">
            {when ? <><dt>When</dt><dd>{when}</dd></> : null}
            {place || event.online ? (
              <>
                <dt>Where</dt>
                <dd>
                  {place || "Online"}
                  {place && event.online ? " (and online)" : null}
                  {event.online && onlineHref ? <> · <a href={onlineHref} target="_blank" rel="noopener noreferrer">Online link</a></> : null}
                </dd>
              </>
            ) : null}
            {event.price ? <><dt>Price</dt><dd>{event.price}</dd></> : null}
            {ticketHref ? <><dt>Tickets</dt><dd className="sa-break"><a href={ticketHref} target="_blank" rel="noopener noreferrer">{hostOf(ticketHref)}</a></dd></> : null}
          </dl>
          {reused}
        </div>
      </div>
    </s-section>
  );
}

/** One Done / Skip / Retry button for a place (posts to this page's action). */
function TargetButton({ target, intent, label, busyLabel, primary = false, confirm }: {
  target: PromotionTarget; intent: string; label: string; busyLabel: string; primary?: boolean; confirm?: string;
}) {
  const { busy, isSubmitting } = useBusy();
  const id = String(target.id);
  return (
    <Form
      method="post"
      className="sa-inline-form"
      preventScrollReset
      onSubmit={confirm ? (event) => { if (!window.confirm(confirm)) event.preventDefault(); } : undefined}
    >
      <Hidden values={{ intent, target_id: id }} />
      <button type="submit" className={`sa-btn sa-btn-sm${primary ? " sa-btn-primary" : ""}`} disabled={busy}>
        {isSubmitting(intent, ["target_id", id]) ? busyLabel : label}
      </button>
    </Form>
  );
}

function skipConfirm(target: PromotionTarget): string {
  return `Skip ${target.name || "this place"}? The agent won't post there.`;
}

function NeedsYouCard({ target }: { target: PromotionTarget }) {
  return (
    <Card accent="attention">
      <div className="sa-spread">
        <div className="sa-stack-sm sa-grow" style={{ minWidth: 0 }}>
          <div className="sa-row">
            <StatusBadge map={TARGET_STATUS} status="needs_you" />
            <strong className="sa-break">{target.name}</strong>
            <span className="sa-small sa-muted">{targetKindLabel(target.kind)}</span>
          </div>
          <div className="sa-prewrap">{target.need?.reason || "The site needs you to do something before the agent can post."}</div>
          <div className="sa-small sa-muted">
            It&apos;s open in the browser on the Mac. Do it there, then press Done. If you leave it, it&apos;s skipped after 24 hours.
          </div>
        </div>
        <div className="sa-row">
          <TargetButton target={target} intent="target_done" label="Done" busyLabel="Saving…" primary />
          <TargetButton target={target} intent="target_skip" label="Skip" busyLabel="Skipping…" confirm={skipConfirm(target)} />
        </div>
      </div>
    </Card>
  );
}

function TargetRow({ target }: { target: PromotionTarget }) {
  const href = safeHref(target.url);
  const resultHref = safeHref(target.result_url);
  const scheduled = isoToEpoch(target.scheduled_at);
  const later = target.status === "queued" && scheduled !== null && scheduled * 1000 > Date.now();
  return (
    <tr>
      <td className="sa-break">
        <div className="sa-small sa-muted">
          {targetKindLabel(target.kind)}{target.source === "reused" ? " · used last time" : ""}
        </div>
        {href ? <a href={href} target="_blank" rel="noopener noreferrer">{target.name || hostOf(href)}</a> : <strong>{target.name || "—"}</strong>}
        {target.why ? <div className="sa-small sa-muted">{target.why}</div> : null}
      </td>
      <td className="sa-break sa-small">{target.account || <span className="sa-muted">—</span>}</td>
      <td className="sa-nowrap">
        <StatusBadge map={TARGET_STATUS} status={target.status} />
        {later ? <div className="sa-small sa-muted">Scheduled for <AgentTime iso={target.scheduled_at} /></div> : null}
      </td>
      <td className="sa-break sa-small">
        {resultHref ? <a href={resultHref} target="_blank" rel="noopener noreferrer">View</a> : null}
        {target.note ? <div className="sa-muted">{target.note}</div> : null}
        {!resultHref && !target.note ? <span className="sa-muted">—</span> : null}
      </td>
      <td className="sa-nowrap">
        {target.job_id ? <Link to={`/app/agent/tasks/${seg(target.job_id)}`}>#{target.job_id}</Link> : <span className="sa-muted">—</span>}
      </td>
      <td>
        {target.status === "failed" || target.status === "skipped" ? (
          <TargetButton target={target} intent="target_retry" label="Retry" busyLabel="Queuing…" />
        ) : target.status === "planned" || target.status === "queued" ? (
          <TargetButton target={target} intent="target_skip" label="Skip" busyLabel="Skipping…" confirm={skipConfirm(target)} />
        ) : null}
      </td>
    </tr>
  );
}

const TARGET_STATUS_ORDER = ["needs_you", "working", "queued", "planned", "posted", "already_listed", "failed", "skipped"];

function PlacesSection({ promotion, jobActive }: { promotion: Promotion; jobActive: boolean }) {
  const targets = promotion.targets || [];
  const counts: Record<string, number | undefined> = {};
  if (promotion.counts && Object.keys(promotion.counts).length) Object.assign(counts, promotion.counts);
  else for (const target of targets) counts[target.status] = (counts[target.status] || 0) + 1;
  const order = [...TARGET_STATUS_ORDER, ...Object.keys(counts).filter((status) => !TARGET_STATUS_ORDER.includes(status))];
  const needsYou = targets.filter((target) => target.status === "needs_you");

  return (
    <s-section heading={`Where it's being promoted (${targets.length})`}>
      {targets.length ? (
        <div className="sa-stack">
          <div className="sa-row">
            {order.filter((status) => counts[status]).map((status) => {
              const info = statusInfo(TARGET_STATUS, status);
              return <Badge key={status} tone={info.tone} title={info.help}>{counts[status]} {info.label.toLowerCase()}</Badge>;
            })}
          </div>
          <p className="sa-small sa-muted">
            Each place is posted to automatically. Groups and forums are spread out: at most 5 a day for this event, at least 20 minutes apart.
          </p>
          {needsYou.map((target) => <NeedsYouCard key={target.id} target={target} />)}
          <div className="sa-table-wrap">
            <table className="sa-table">
              <thead><tr><th>Place</th><th>Posting as</th><th>Status</th><th>Result</th><th>Task</th><th /></tr></thead>
              <tbody>{targets.map((target) => <TargetRow key={target.id} target={target} />)}</tbody>
            </table>
          </div>
        </div>
      ) : (
        <p className="sa-muted">
          {jobActive ? "Still looking for places to promote it. This page updates by itself." : "No places to promote it were found."}
        </p>
      )}
    </s-section>
  );
}

function StepRow({ step }: { step: Step }) {
  const outcome = stepOutcome(step.result_summary || "");
  const summary = (step.result_summary || "").replace(/^(REJECTED|FAILED|DENIED):\s*/, "");
  return (
    <tr>
      <td className="sa-muted">{step.n}</td>
      <td className="sa-nowrap">
        {toolLabel(step.tool)}
        {outcome === "redone" ? <div><Badge tone="warning" title="The agent's answer was sent back and it had another go">Redone</Badge></div> : null}
        {outcome === "failed" ? <div><Badge tone="critical">Failed</Badge></div> : null}
        {outcome === "denied" ? <div><Badge tone="critical" title="The agent isn't allowed to use this tool for this task">Not allowed</Badge></div> : null}
      </td>
      <td className="sa-break">{shortArgs(step.args)}</td>
      <td className="sa-break sa-small">{summary.length > 280 ? `${summary.slice(0, 279)}…` : summary}</td>
      <td className="sa-nowrap sa-small">{formatMs(step.duration_ms)}</td>
    </tr>
  );
}

export default function AgentTaskDetail() {
  const { job, steps, error } = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>();
  const jobActive = Boolean(job && (job.status === "queued" || job.status === "running"));
  const targets = job?.promotion?.targets || [];
  // A promotion's places keep changing after its own job has finished (paced posts, sites that need you).
  const targetsActive = targets.some((target) => ACTIVE_TARGET_STATUSES.includes(target.status));
  const posting = targets.some((target) => target.status === "working");
  usePolling(jobActive || targetsActive, jobActive || posting ? 5_000 : 15_000);

  if (!job) {
    return (
      <>
        <LoadError error={error} />
        <s-section>
          <EmptyState title="Task not found" action={<Link to="/app/agent/tasks" className="sa-btn">Back to tasks</Link>}>
            It may have been removed, or the Task Agent couldn&apos;t be reached.
          </EmptyState>
        </s-section>
      </>
    );
  }

  const question = typeof job.payload.question === "string" ? job.payload.question : "";
  const context = typeof job.payload.context === "string" ? job.payload.context : "";
  const redone = steps.filter((step) => stepOutcome(step.result_summary || "") !== "ok").length;

  return (
    <>
      <p className="sa-small">
        <Link to="/app/agent/tasks">← All tasks</Link>
        {job.parent_id ? <> · <Link to={`/app/agent/tasks/${seg(job.parent_id)}`}>Part of task {job.parent_id}</Link></> : null}
      </p>
      <ActionBanner result={result} />
      <s-section>
        <div className="sa-spread">
          <div className="sa-stack-sm sa-grow">
            <div className="sa-row">
              <JobStatusBadge job={job} />
              <Badge tone="neutral">{taskTypeLabel(job.type)}</Badge>
              {job.priority ? <Badge tone="info">Priority {job.priority}</Badge> : null}
            </div>
            <h2 style={{ margin: 0 }} className="sa-break">{job.title}</h2>
          </div>
          <div className="sa-row">
            {job.template_key && ["queued", "failed", "cancelled"].includes(job.status) ? (
              <Link
                to={`/app/agent/new?edit=${seg(job.id)}`}
                className="sa-btn"
                title={job.status === "queued"
                  ? "Change this task's details before it starts; it keeps its number"
                  : "Change this task's details and put it back in the queue; it keeps its number"}
              >
                Edit
              </Link>
            ) : null}
            {job.template_key ? (
              <Link
                to={`/app/agent/new?template=${encodeURIComponent(job.template_key)}&clone=${seg(job.id)}`}
                className="sa-btn"
                title="Start a new task with the same template and details, so you can change what's different"
              >
                Clone
              </Link>
            ) : null}
            <JobActions job={job} size="md" />
          </div>
        </div>
        <dl className="sa-kv" style={{ marginTop: 10 }}>
          <dt>Task number</dt><dd>#{job.id}</dd>
          <dt>Added</dt><dd><AgentTime iso={job.created_at} full /> {job.created_by === "admin" ? <span className="sa-muted">(from this page)</span> : null}</dd>
          {job.status === "queued" && job.run_at ? <><dt>Starts</dt><dd><AgentTime iso={job.run_at} full /></dd></> : null}
          <dt>Started</dt><dd><AgentTime iso={job.started_at} full /></dd>
          <dt>Finished</dt><dd><AgentTime iso={job.finished_at} full /></dd>
          {job.started_at ? <><dt>Took</dt><dd>{elapsed(job.started_at, job.finished_at)}{job.status === "running" ? " so far" : ""}</dd></> : null}
          <dt>Attempts</dt><dd>{job.attempts}</dd>
          {job.template_key ? <><dt>Template</dt><dd>{job.template_key}</dd></> : null}
          {job.event_type ? <><dt>Event type</dt><dd>{job.event_type.name || job.event_type.key}</dd></> : null}
        </dl>
        {job.error ? (
          <div style={{ marginTop: 10 }}>
            <Card accent={job.status === "failed" ? "critical" : "warning"}>
              <div className="sa-stack-sm">
                <strong>{job.status === "failed" ? "Why it failed" : "Last problem"}</strong>
                <div className="sa-prewrap sa-small">{job.error}</div>
              </div>
            </Card>
          </div>
        ) : null}
      </s-section>

      {job.promotion ? (
        <>
          <EventSection promotion={job.promotion} />
          <PlacesSection promotion={job.promotion} jobActive={jobActive} />
        </>
      ) : null}

      {question ? (
        <s-section heading="What it was asked">
          <div className="sa-prewrap">{question}</div>
          {context ? <details style={{ marginTop: 8 }}><summary className="sa-small">Background it was given</summary><div className="sa-prewrap sa-small">{context}</div></details> : null}
        </s-section>
      ) : null}

      <s-section heading="Result">
        {job.result && isResearchResult(job.result) ? (
          <ResearchResultView result={job.result} />
        ) : job.result ? (
          <JsonBlock value={job.result} label="Show result" />
        ) : (
          <p className="sa-muted">
            {job.status === "queued" ? "Not started yet — it's waiting its turn."
              : job.status === "running" ? "Still working on it. This page updates by itself."
                : job.status === "waiting_approval" ? "Waiting for your approval before it carries on."
                  : "No result."}
          </p>
        )}
      </s-section>

      <s-section heading={`Steps (${steps.length})`}>
        {steps.length ? (
          <>
            {redone ? <p className="sa-small sa-muted">{redone} step{redone === 1 ? " was" : "s were"} redone — usually the agent fixing the format of its answer or opening a page it wanted to quote.</p> : null}
            <div className="sa-table-wrap">
              <table className="sa-table">
                <thead><tr><th>#</th><th>What it did</th><th>Details</th><th>What happened</th><th>Time</th></tr></thead>
                <tbody>{steps.map((step) => <StepRow key={step.n} step={step} />)}</tbody>
              </table>
            </div>
          </>
        ) : (
          <p className="sa-muted">No steps yet.</p>
        )}
        <JsonBlock value={job.payload} label="Show the task's raw input" />
      </s-section>
    </>
  );
}
