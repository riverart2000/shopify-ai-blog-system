import { useEffect, useState } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useFetcher, useLoaderData, useSearchParams } from "react-router";

import { useSocial } from "../components/social/context";
import {
  ActionBanner, Badge, Card, EmptyState, Hidden, JsonBlock, LoadError, StatusBadge, Tabs, TimeText, usePolling, useBusy,
} from "../components/social/ui";
import { ACTIVITY_LEVEL, JOB_STATUS, humanize } from "../components/social/vocab";
import {
  errorMessage, formString, runIntent, safeId, seFetch, seg,
  type ActivityItem, type ApiCall, type ApiCallStat, type Decision, type JobDetailResponse, type JobsResponse,
  type WebhookEvent,
} from "../lib/social-engine.server";
import { authenticate } from "../shopify.server";

export const handle = { title: "Activity & debugging" };

const VIEWS = [
  { key: "activity", label: "Activity log" },
  { key: "jobs", label: "Background jobs" },
  { key: "api", label: "API calls" },
  { key: "webhooks", label: "Webhook deliveries" },
  { key: "decisions", label: "AI decisions" },
] as const;
type ViewKey = (typeof VIEWS)[number]["key"];

type LoaderData = {
  view: ViewKey;
  error: string;
  activity?: ActivityItem[];
  jobs?: JobsResponse;
  job?: JobDetailResponse | null;
  calls?: { calls: ApiCall[]; stats: ApiCallStat[] };
  webhooks?: WebhookEvent[];
  decisions?: Decision[];
};

export const loader = async ({ request }: LoaderFunctionArgs): Promise<LoaderData> => {
  await authenticate.admin(request);
  const params = new URL(request.url).searchParams;
  const requested = params.get("view") as ViewKey | null;
  const view: ViewKey = VIEWS.some((item) => item.key === requested) ? (requested as ViewKey) : "activity";
  try {
    switch (view) {
      case "activity": {
        const result = await seFetch<{ items: ActivityItem[] }>("/activity", {
          query: { level: params.get("level") || "", limit: 100, before_id: params.get("before_id") || "", entity_type: params.get("entity_type") || "" },
        });
        return { view, activity: result.items, error: "" };
      }
      case "jobs": {
        const jobId = params.get("job");
        const [jobs, job] = await Promise.all([
          seFetch<JobsResponse>("/jobs", { query: { status: params.get("status") || "", type: params.get("type") || "", limit: 100 } }),
          jobId ? seFetch<JobDetailResponse>(`/jobs/${seg(safeId(jobId))}`).catch(() => null) : Promise.resolve(null),
        ]);
        return { view, jobs, job, error: "" };
      }
      case "api": {
        const calls = await seFetch<{ calls: ApiCall[]; stats: ApiCallStat[] }>("/api-calls", {
          query: { service: params.get("service") || "", errors: params.get("errors") === "1", hours: Number(params.get("hours") || 24) || 24, limit: 150 },
        });
        return { view, calls, error: "" };
      }
      case "webhooks": {
        const result = await seFetch<{ events: WebhookEvent[] }>("/webhook-events", { query: { source: params.get("source") || "", limit: 100 } });
        return { view, webhooks: result.events, error: "" };
      }
      case "decisions": {
        const result = await seFetch<{ decisions: Decision[] }>("/decisions", { query: { decision_type: params.get("decision_type") || "", limit: 50 } });
        return { view, decisions: result.decisions, error: "" };
      }
    }
  } catch (error) {
    return { view, error: errorMessage(error) };
  }
  return { view, error: "" };
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  return runIntent(intent, async () => {
    const jobId = safeId(formString(form, "job_id"));
    if (intent === "job_retry") {
      await seFetch(`/jobs/${seg(jobId)}/retry`, { body: {} });
      return { message: "Job queued to run again." };
    }
    if (intent === "job_cancel") {
      await seFetch(`/jobs/${seg(jobId)}/cancel`, { body: {} });
      return { message: "Job cancelled." };
    }
    throw new Error(`Unknown action "${intent}".`);
  });
};

function entityLink(item: ActivityItem): string {
  if (item.entity_type === "post" && item.entity_id) return `/app/social/posts/${item.entity_id}`;
  if (item.entity_type === "campaign" && item.entity_id) return `/app/social/campaigns?edit=${item.entity_id}`;
  if (item.entity_type === "job" && item.entity_id) return `?view=jobs&job=${item.entity_id}`;
  return "";
}

function ActivityList({ initial, level, tz }: { initial: ActivityItem[]; level: string; tz: string }) {
  const fetcher = useFetcher<LoaderData>();
  const [older, setOlder] = useState<ActivityItem[]>([]);
  const [done, setDone] = useState(false);
  useEffect(() => {
    const items = fetcher.data?.activity;
    if (!items) return;
    setOlder((current) => [...current, ...items.filter((item) => !current.some((existing) => existing.id === item.id))]);
    if (items.length < 100) setDone(true);
  }, [fetcher.data]);
  const items = [...initial, ...older];
  const last = items[items.length - 1];
  if (!items.length) return <EmptyState title="No activity yet">Everything Social Autopilot does — planning, generating, publishing, errors — is recorded here.</EmptyState>;
  return (
    <div className="sa-stack-sm">
      {items.map((item) => {
        const link = entityLink(item);
        return (
          <div key={item.id} className="sa-row-top" style={{ borderTop: "1px solid var(--sa-border)", paddingTop: 6 }}>
            <span className="sa-small sa-muted sa-nowrap" style={{ minWidth: 130 }}><TimeText epoch={item.created_at} tz={tz} full /></span>
            <StatusBadge map={ACTIVITY_LEVEL} status={item.level} />
            {item.entity_type ? <Badge tone="neutral">{humanize(item.entity_type)}{item.stage ? ` · ${humanize(item.stage)}` : ""}</Badge> : null}
            <div className="sa-grow sa-break">
              {link ? <Link to={link}>{item.message}</Link> : item.message}
              <JsonBlock value={item.data} label="Details" />
            </div>
          </div>
        );
      })}
      {!done && initial.length >= 100 && last ? (
        <div>
          <button
            type="button" className="sa-btn" disabled={fetcher.state !== "idle"}
            onClick={() => fetcher.load(`/app/social/activity?view=activity&level=${encodeURIComponent(level)}&before_id=${last.id}`)}
          >
            {fetcher.state !== "idle" ? "Loading…" : "Load older activity"}
          </button>
        </div>
      ) : null}
      {fetcher.data?.error ? <span className="sa-error-text">{fetcher.data.error}</span> : null}
    </div>
  );
}

export default function ActivityPage() {
  const data = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>();
  const { tz } = useSocial();
  const { busy, isSubmitting } = useBusy();
  const [params] = useSearchParams();
  const level = params.get("level") || "";
  const activeJobs = (data.jobs?.counts.queued || 0) + (data.jobs?.counts.running || 0);
  usePolling(data.view === "jobs" && activeJobs > 0, 10_000);

  const viewLink = (key: string) => `?view=${key}`;
  const filterLink = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value) next.set(key, value); else next.delete(key);
    }
    next.delete("before_id");
    return `?${next.toString()}`;
  };

  return (
    <>
      <ActionBanner result={result} />
      <LoadError error={data.error} />
      <s-section>
        <Tabs items={VIEWS.map((view) => ({ to: viewLink(view.key), label: view.label, active: view.key === data.view }))} />

        {data.view === "activity" && data.activity ? (
          <div className="sa-stack">
            <div className="sa-row">
              <span className="sa-small sa-muted">Show:</span>
              {[["", "Everything"], ["warning", "Warnings & errors"], ["error", "Errors only"]].map(([value, label]) => (
                <Link key={label} to={filterLink({ level: value })} className={`sa-btn sa-btn-sm ${level === value ? "sa-btn-primary" : ""}`} preventScrollReset>{label}</Link>
              ))}
            </div>
            <ActivityList key={level} initial={data.activity} level={level} tz={tz} />
          </div>
        ) : null}

        {data.view === "jobs" && data.jobs ? (
          <div className="sa-stack">
            {data.job ? (
              <Card accent={data.job.job.status === "failed" ? "critical" : "info"}>
                <div className="sa-stack-sm">
                  <div className="sa-spread">
                    <div className="sa-row"><strong>Job {data.job.job.type}</strong><StatusBadge map={JOB_STATUS} status={data.job.job.status} /></div>
                    <Link to={filterLink({ job: "" })} className="sa-btn sa-btn-sm">Close</Link>
                  </div>
                  <dl className="sa-kv">
                    <dt>ID</dt><dd><code>{data.job.job.id}</code></dd>
                    <dt>Attempts</dt><dd>{data.job.job.attempts} of {data.job.job.max_attempts}</dd>
                    <dt>Run at</dt><dd><TimeText epoch={data.job.job.run_at} tz={tz} full /></dd>
                    <dt>Started / finished</dt><dd><TimeText epoch={data.job.job.started_at} tz={tz} full /> → <TimeText epoch={data.job.job.finished_at} tz={tz} full /></dd>
                    {data.job.job.correlation_id ? <><dt>Related to</dt><dd><code>{data.job.job.correlation_id}</code>{data.job.job.type.startsWith("post.") ? <> · <Link to={`/app/social/posts/${data.job.job.correlation_id}`}>open post</Link></> : null}</dd></> : null}
                    {data.job.job.last_error ? <><dt>Error</dt><dd className="sa-error-text">{data.job.job.last_error_type ? `${data.job.job.last_error_type}: ` : ""}{data.job.job.last_error}</dd></> : null}
                  </dl>
                  <JsonBlock value={data.job.job.payload} label="Payload" />
                  <JsonBlock value={data.job.job.result} label="Result" />
                  {data.job.timeline.length ? (
                    <details><summary className="sa-small">Timeline ({data.job.timeline.length})</summary>
                      <ul style={{ margin: 0, paddingLeft: 18 }}>{data.job.timeline.map((item) => <li key={item.id}><TimeText epoch={item.created_at} tz={tz} full /> — {item.message}</li>)}</ul>
                    </details>
                  ) : null}
                  {data.job.api_calls.length ? (
                    <details><summary className="sa-small">API calls ({data.job.api_calls.length})</summary>
                      <ApiCallList calls={data.job.api_calls} tz={tz} />
                    </details>
                  ) : null}
                </div>
              </Card>
            ) : null}
            <div className="sa-row">
              {Object.entries(data.jobs.counts).map(([status, count]) => (
                <Link key={status} to={filterLink({ status: params.get("status") === status ? "" : status, job: "" })} className="sa-btn sa-btn-sm" preventScrollReset>
                  <StatusBadge map={JOB_STATUS} status={status} /> {count}
                </Link>
              ))}
              <Form method="get" className="sa-row" preventScrollReset>
                <input type="hidden" name="view" value="jobs" />
                <select name="status" defaultValue={params.get("status") || ""} aria-label="Job status" style={{ width: "auto" }}>
                  <option value="">Any status</option>
                  {Object.keys(JOB_STATUS).map((status) => <option key={status} value={status}>{JOB_STATUS[status].label}</option>)}
                </select>
                <select name="type" defaultValue={params.get("type") || ""} aria-label="Job type" style={{ width: "auto" }}>
                  <option value="">Any type</option>
                  {Object.keys(data.jobs.types).sort().map((type) => <option key={type} value={type}>{type}</option>)}
                </select>
                <button type="submit" className="sa-btn sa-btn-sm">Filter</button>
              </Form>
            </div>
            {data.jobs.jobs.length ? (
              <div className="sa-table-wrap">
                <table className="sa-table">
                  <thead><tr><th>Job</th><th>Status</th><th className="num">Attempts</th><th>Created</th><th>Run at</th><th>Error</th><th /></tr></thead>
                  <tbody>
                    {data.jobs.jobs.map((job) => (
                      <tr key={job.id}>
                        <td>
                          <Link to={filterLink({ job: job.id })} preventScrollReset><code>{job.type}</code></Link>
                          {data.jobs?.types[job.type]?.description ? <div className="sa-small sa-muted">{data.jobs.types[job.type].description}</div> : null}
                        </td>
                        <td><StatusBadge map={JOB_STATUS} status={job.status} /></td>
                        <td className="num">{job.attempts}/{job.max_attempts}</td>
                        <td className="sa-nowrap"><TimeText epoch={job.created_at} tz={tz} /></td>
                        <td className="sa-nowrap"><TimeText epoch={job.run_at} tz={tz} /></td>
                        <td className="sa-break sa-small">{job.last_error ? `${job.last_error_type ? `${job.last_error_type}: ` : ""}${job.last_error}` : "—"}</td>
                        <td>
                          {["failed", "cancelled"].includes(job.status) ? (
                            <Form method="post" preventScrollReset>
                              <Hidden values={{ intent: "job_retry", job_id: job.id }} />
                              <button type="submit" className="sa-btn sa-btn-sm" disabled={busy}>{isSubmitting("job_retry", ["job_id", job.id]) ? "…" : "Retry"}</button>
                            </Form>
                          ) : job.status === "queued" ? (
                            <Form method="post" preventScrollReset>
                              <Hidden values={{ intent: "job_cancel", job_id: job.id }} />
                              <button type="submit" className="sa-btn sa-btn-sm sa-btn-critical" disabled={busy}>Cancel</button>
                            </Form>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <EmptyState title="No jobs match">Background jobs (syncs, generating posts, publishing, analytics) appear here.</EmptyState>}
            {data.jobs.periodic.length ? (
              <details>
                <summary className="sa-small">Scheduled maintenance tasks ({data.jobs.periodic.length})</summary>
                <div className="sa-table-wrap">
                  <table className="sa-table">
                    <thead><tr><th>Task</th><th>Last queued</th></tr></thead>
                    <tbody>{data.jobs.periodic.map((item) => (
                      <tr key={`${item.task}-${item.store_id}`}>
                        <td><code>{item.task}</code></td>
                        <td>{typeof item.last_enqueued_at === "number" ? <TimeText epoch={item.last_enqueued_at} tz={tz} relative /> : String(item.last_enqueued_at ?? "—")}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              </details>
            ) : null}
          </div>
        ) : null}

        {data.view === "api" && data.calls ? (
          <div className="sa-stack">
            <Form method="get" className="sa-row" preventScrollReset>
              <input type="hidden" name="view" value="api" />
              <select name="service" defaultValue={params.get("service") || ""} aria-label="Service" style={{ width: "auto" }}>
                <option value="">All services</option>
                {Array.from(new Set(data.calls.stats.map((stat) => stat.service))).map((service) => <option key={service} value={service}>{service}</option>)}
              </select>
              <select name="hours" defaultValue={params.get("hours") || "24"} aria-label="Period" style={{ width: "auto" }}>
                {[["1", "Last hour"], ["24", "Last 24 hours"], ["168", "Last 7 days"], ["720", "Last 30 days"]].map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
              <label className="sa-check"><input type="checkbox" name="errors" value="1" defaultChecked={params.get("errors") === "1"} /><span>Errors only</span></label>
              <button type="submit" className="sa-btn sa-btn-sm">Apply</button>
            </Form>
            {data.calls.stats.length ? (
              <div className="sa-table-wrap">
                <table className="sa-table">
                  <thead><tr><th>Service</th><th>Operation</th><th className="num">Calls</th><th className="num">Failed</th><th className="num">Avg time</th><th>Last call</th></tr></thead>
                  <tbody>
                    {data.calls.stats.map((stat) => (
                      <tr key={`${stat.service}-${stat.operation}`}>
                        <td>{stat.service}</td><td><code>{stat.operation || "—"}</code></td>
                        <td className="num">{stat.calls}</td>
                        <td className="num">{stat.failed_calls ? <Badge tone="critical">{stat.failed_calls}</Badge> : 0}</td>
                        <td className="num">{stat.avg_ms} ms</td>
                        <td className="sa-nowrap"><TimeText epoch={stat.last_at} tz={tz} relative /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
            {data.calls.calls.length ? <ApiCallList calls={data.calls.calls} tz={tz} /> : <EmptyState title="No API calls in this period">Calls to Postiz, Gemma and Shopify are logged here with timings and errors.</EmptyState>}
          </div>
        ) : null}

        {data.view === "webhooks" && data.webhooks ? (
          <div className="sa-stack">
            <div className="sa-row">
              {[["", "All"], ["postiz", "Postiz"], ["shopify", "Shopify"]].map(([value, label]) => (
                <Link key={label} to={filterLink({ source: value })} className={`sa-btn sa-btn-sm ${(params.get("source") || "") === value ? "sa-btn-primary" : ""}`} preventScrollReset>{label}</Link>
              ))}
            </div>
            {data.webhooks.length ? (
              <div className="sa-table-wrap">
                <table className="sa-table">
                  <thead><tr><th>Received</th><th>Source</th><th>Topic</th><th>Status</th><th>Processed</th><th>Error</th></tr></thead>
                  <tbody>
                    {data.webhooks.map((event) => (
                      <tr key={event.id}>
                        <td className="sa-nowrap"><TimeText epoch={event.received_at} tz={tz} full /></td>
                        <td>{humanize(event.source)}</td>
                        <td><code>{event.topic}</code></td>
                        <td><Badge tone={event.status === "processed" || event.status === "done" ? "success" : event.status === "failed" ? "critical" : "info"}>{humanize(event.status)}</Badge></td>
                        <td className="sa-nowrap"><TimeText epoch={event.processed_at} tz={tz} /></td>
                        <td className="sa-break sa-small">{event.error || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <EmptyState title="No webhook deliveries yet">Shopify product changes and Postiz “post published” events appear here as they arrive.</EmptyState>}
          </div>
        ) : null}

        {data.view === "decisions" && data.decisions ? (
          <div className="sa-stack-sm">
            {data.decisions.length ? data.decisions.map((decision) => (
              <Card key={decision.id} accent={decision.valid ? "success" : "critical"}>
                <div className="sa-row">
                  <strong>{humanize(decision.decision_type)}</strong>
                  <Badge tone={decision.valid ? "success" : "critical"}>{decision.valid ? "Valid" : "Invalid"}</Badge>
                  {decision.used_fallback ? <Badge tone="warning">Fallback used</Badge> : null}
                  <span className="sa-small sa-muted">{decision.model} · {decision.duration_ms} ms · <TimeText epoch={decision.created_at} tz={tz} full /></span>
                  {decision.subject_id ? <span className="sa-small sa-muted">Subject <code>{decision.subject_id}</code></span> : null}
                </div>
                {decision.error ? <div className="sa-error-text sa-break">{decision.error}</div> : null}
                <div className="sa-grid-2" style={{ marginTop: 6 }}>
                  <JsonBlock value={decision.input} label="Input" />
                  <JsonBlock value={decision.output} label="Output" />
                </div>
              </Card>
            )) : <EmptyState title="No AI decisions yet">When Gemma chooses products, writes promo lines or reviews posts, each decision is recorded here.</EmptyState>}
          </div>
        ) : null}
      </s-section>
    </>
  );
}

function ApiCallList({ calls, tz }: { calls: ApiCall[]; tz: string }) {
  return (
    <div className="sa-stack-sm">
      {calls.map((call) => (
        <details key={call.id}>
          <summary>
            <span className="sa-row" style={{ display: "inline-flex" }}>
              <Badge tone={call.ok ? "success" : "critical"}>{call.status_code ?? "no response"}</Badge>
              <span>{call.service} · {call.operation || `${call.method} ${call.url_path}`}</span>
              <span className="sa-muted sa-small">{call.duration_ms} ms{call.attempt > 1 ? ` · attempt ${call.attempt}` : ""} · <TimeText epoch={call.created_at} tz={tz} /></span>
            </span>
          </summary>
          <div className="sa-stack-sm">
            <div className="sa-small"><code>{call.method} {call.url_path}</code>{call.correlation_id ? <> · correlation <code>{call.correlation_id}</code></> : null}</div>
            {call.error ? <div className="sa-error-text sa-break">{call.error}</div> : null}
            {call.request_excerpt ? <div><span className="sa-small sa-muted">Request</span><pre>{call.request_excerpt}</pre></div> : null}
            {call.response_excerpt ? <div><span className="sa-small sa-muted">Response</span><pre>{call.response_excerpt}</pre></div> : null}
          </div>
        </details>
      ))}
    </div>
  );
}
