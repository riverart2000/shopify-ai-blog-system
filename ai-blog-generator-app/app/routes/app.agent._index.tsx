import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Link, useActionData, useLoaderData } from "react-router";

import { ACTIVE_STATUSES, AgentTime, JobActions, JobStatusBadge, elapsed, taskTypeLabel } from "../components/agent/parts";
import { ActionBanner, Card, EmptyState, Kpi, usePolling } from "../components/social/ui";
import { errorMessage, handleJobIntent, taFetch, type StatusResponse } from "../lib/task-agent.server";
import { seg } from "../lib/task-agent.shared";
import { authenticate } from "../shopify.server";

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  try {
    return { status: await taFetch<StatusResponse>("/status"), error: "" };
  } catch (error) {
    return { status: null as StatusResponse | null, error: errorMessage(error) };
  }
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  return handleJobIntent(String(form.get("intent") || ""), form);
};

export default function AgentOverview() {
  const { status } = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>();
  const active = Boolean(status && (status.worker.state === "running" || ACTIVE_STATUSES.some((s) => (status.counts[s] || 0) > 0)));
  usePolling(active, 8_000);

  if (!status) {
    // The layout already shows why the agent couldn't be reached.
    return (
      <s-section>
        <EmptyState title="Nothing to show yet">
          Once the Task Agent is running on the Mac, its tasks and status appear here.
        </EmptyState>
      </s-section>
    );
  }

  const counts = status.counts;
  const activeCount = ACTIVE_STATUSES.reduce((sum, s) => sum + (counts[s] || 0), 0);
  const current = status.worker.current_job;

  return (
    <>
      <ActionBanner result={result} />
      <s-section>
        <div className="sa-grid-sm">
          <Kpi label="In progress" value={activeCount} to="/app/agent/tasks?tab=active" detail={counts.running ? "One is running now" : activeCount ? "Waiting their turn" : "Nothing waiting"} />
          <Kpi
            label="Needs your approval"
            value={status.pending_approvals}
            to="/app/agent/approvals"
            detail={status.pending_approvals ? "Review them now" : "All caught up"}
          />
          <Kpi label="Done" value={counts.done || 0} to="/app/agent/tasks?tab=done" />
          <Kpi label="Failed" value={counts.failed || 0} to="/app/agent/tasks?tab=failed" detail={counts.failed ? "Open one to see why" : undefined} />
          <Kpi label="Cancelled" value={counts.cancelled || 0} to="/app/agent/tasks?tab=cancelled" />
        </div>
      </s-section>

      <s-section heading="Right now">
        {current ? (
          <Card accent="attention">
            <div className="sa-spread">
              <div className="sa-stack-sm">
                <strong>{current.title}</strong>
                <span className="sa-small sa-muted">The agent is working on this task. Only one task runs at a time.</span>
              </div>
              <Link to={`/app/agent/tasks/${seg(current.id)}`} className="sa-btn">Watch it work</Link>
            </div>
          </Card>
        ) : (
          <div className="sa-spread">
            <p className="sa-muted">
              {status.worker.state === "paused"
                ? "The agent is paused, so it won't start new tasks. Resume it in Settings."
                : "The agent isn't working on anything right now."}
            </p>
            <Link to="/app/agent/new" className="sa-btn sa-btn-primary">New task</Link>
          </div>
        )}
      </s-section>

      <s-section heading="Recent tasks">
        {status.recent_jobs.length ? (
          <div className="sa-table-wrap">
            <table className="sa-table">
              <thead>
                <tr><th>Task</th><th>Type</th><th>Status</th><th>Added</th><th>Took</th><th /></tr>
              </thead>
              <tbody>
                {status.recent_jobs.map((job) => (
                  <tr key={job.id}>
                    <td className="sa-break"><Link to={`/app/agent/tasks/${seg(job.id)}`}>{job.title}</Link></td>
                    <td className="sa-nowrap">{taskTypeLabel(job.type)}</td>
                    <td><JobStatusBadge job={job} /></td>
                    <td className="sa-nowrap"><AgentTime iso={job.created_at} relative /></td>
                    <td className="sa-nowrap">{job.started_at ? elapsed(job.started_at, job.finished_at) : "—"}</td>
                    <td><JobActions job={job} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="No tasks yet" action={<Link to="/app/agent/new" className="sa-btn sa-btn-primary">Create your first task</Link>}>
            Pick a template, fill in a few details, and the agent researches it on the web and reports back with its sources.
          </EmptyState>
        )}
        {status.recent_jobs.length ? <p className="sa-small"><Link to="/app/agent/tasks?tab=all">See all tasks →</Link></p> : null}
      </s-section>
    </>
  );
}
