import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useLoaderData, useSearchParams } from "react-router";

import { ACTIVE_STATUSES, AgentTime, JobActions, JobStatusBadge, elapsed, taskTypeLabel, LoadError } from "../components/agent/parts";
import { ActionBanner, EmptyState, Tabs, usePolling } from "../components/social/ui";
import {
  errorMessage, handleJobIntent, taFetch, type JobStatus, type JobSummary, type JobsResponse,
} from "../lib/task-agent.server";
import { seg } from "../lib/task-agent.shared";
import { authenticate } from "../shopify.server";

export const handle = { title: "Tasks" };

const TABS: Array<{ key: string; label: string; statuses: JobStatus[] }> = [
  { key: "active", label: "Active", statuses: ACTIVE_STATUSES },
  { key: "done", label: "Done", statuses: ["done"] },
  { key: "failed", label: "Failed", statuses: ["failed"] },
  { key: "cancelled", label: "Cancelled", statuses: ["cancelled"] },
  { key: "all", label: "All", statuses: [] },
];
const PAGE_SIZE = 20;

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  const url = new URL(request.url);
  const tab = TABS.find((item) => item.key === url.searchParams.get("tab")) || TABS[TABS.length - 1];
  const page = Math.max(1, Number(url.searchParams.get("page") || 1) || 1);
  const search = url.searchParams.get("q") || "";
  try {
    const data = await taFetch<JobsResponse>("/jobs", {
      query: { status: tab.statuses.join(","), q: search, page, page_size: PAGE_SIZE },
    });
    return { tab: tab.key, page, search, error: "", jobs: data.jobs, total: data.total, counts: data.counts };
  } catch (error) {
    return {
      tab: tab.key, page, search, error: errorMessage(error),
      jobs: [] as JobSummary[], total: 0, counts: {} as JobsResponse["counts"],
    };
  }
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  return handleJobIntent(String(form.get("intent") || ""), form);
};

export default function AgentTasks() {
  const data = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>();
  const [params] = useSearchParams();
  usePolling(data.jobs.some((job) => ACTIVE_STATUSES.includes(job.status)), 8_000);

  const tabLink = (key: string) => {
    const next = new URLSearchParams(params);
    next.set("tab", key);
    next.delete("page");
    return `?${next.toString()}`;
  };
  const pageLink = (page: number) => {
    const next = new URLSearchParams(params);
    next.set("page", String(page));
    return `?${next.toString()}`;
  };
  const countFor = (statuses: JobStatus[]) => statuses.length
    ? statuses.reduce((sum, status) => sum + (data.counts[status] || 0), 0)
    : Object.values(data.counts).reduce((sum, value) => sum + (value || 0), 0);
  const pages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
  const currentTab = TABS.find((tab) => tab.key === data.tab);

  return (
    <>
      <ActionBanner result={result} />
      <LoadError error={data.error} />
      <s-section>
        <Tabs items={TABS.map((tab) => ({ to: tabLink(tab.key), label: tab.label, count: countFor(tab.statuses), active: tab.key === data.tab }))} />
        <div className="sa-spread">
          <Form method="get" className="sa-row" preventScrollReset>
            <input type="hidden" name="tab" value={data.tab} />
            <input type="search" name="q" defaultValue={data.search} placeholder="Search tasks" aria-label="Search tasks" style={{ width: 240 }} />
            <button type="submit" className="sa-btn">Search</button>
          </Form>
          <Link to="/app/agent/new" className="sa-btn sa-btn-primary">New task</Link>
        </div>
      </s-section>

      <s-section>
        {data.jobs.length ? (
          <div className="sa-table-wrap">
            <table className="sa-table">
              <thead>
                <tr><th>#</th><th>Task</th><th>Type</th><th>Status</th><th>Added</th><th>Took</th><th className="num">Steps</th><th /></tr>
              </thead>
              <tbody>
                {data.jobs.map((job) => (
                  <tr key={job.id}>
                    <td className="sa-muted">{job.id}</td>
                    <td className="sa-break">
                      <Link to={`/app/agent/tasks/${seg(job.id)}`}>{job.title}</Link>
                      {job.error && job.status === "failed" ? <div className="sa-small sa-error-text sa-ellipsis" style={{ maxWidth: 420 }} title={job.error}>{job.error}</div> : null}
                    </td>
                    <td className="sa-nowrap">{taskTypeLabel(job.type)}</td>
                    <td><JobStatusBadge job={job} /></td>
                    <td className="sa-nowrap"><AgentTime iso={job.created_at} relative /></td>
                    <td className="sa-nowrap">{job.started_at ? elapsed(job.started_at, job.finished_at) : "—"}</td>
                    <td className="num">{job.step_count}</td>
                    <td><JobActions job={job} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title={data.search ? "No tasks match your search" : currentTab?.key === "active" ? "Nothing in progress" : "No tasks here"}
            action={<Link to="/app/agent/new" className="sa-btn sa-btn-primary">New task</Link>}
          >
            {data.search ? "Try different words, or clear the search." : "Tasks you create appear here while they run and after they finish."}
          </EmptyState>
        )}
        {pages > 1 ? (
          <div className="sa-row" style={{ marginTop: 10 }}>
            {data.page > 1 ? <Link to={pageLink(data.page - 1)} className="sa-btn sa-btn-sm" preventScrollReset>← Newer</Link> : null}
            <span className="sa-small sa-muted">Page {data.page} of {pages} · {data.total} tasks</span>
            {data.page < pages ? <Link to={pageLink(data.page + 1)} className="sa-btn sa-btn-sm" preventScrollReset>Older →</Link> : null}
          </div>
        ) : null}
      </s-section>
    </>
  );
}
