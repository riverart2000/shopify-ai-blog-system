import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Link, useActionData, useLoaderData } from "react-router";

import {
  AgentTime, JobActions, JobStatusBadge, LoadError, elapsed, formatMs, shortArgs, stepOutcome, taskTypeLabel, toolLabel,
} from "../components/agent/parts";
import { ActionBanner, Badge, Card, EmptyState, JsonBlock, usePolling } from "../components/social/ui";
import type { Tone } from "../components/social/vocab";
import {
  errorMessage, handleJobIntent, taFetch, type JobDetail, type JobResponse, type ResearchResult, type Step,
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

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  return handleJobIntent(String(form.get("intent") || ""), form);
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
  usePolling(Boolean(job && (job.status === "queued" || job.status === "running")), 5_000);

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
      <p className="sa-small"><Link to="/app/agent/tasks">← All tasks</Link></p>
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
          <JobActions job={job} size="md" />
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
