import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useLoaderData, useSearchParams } from "react-router";

import { APPROVAL_STATUS, AgentTime, LoadError } from "../components/agent/parts";
import { ActionBanner, Card, EmptyState, Hidden, StatusBadge, Tabs, useBusy, usePolling } from "../components/social/ui";
import {
  errorMessage, formString, runAgentIntent, taFetch,
  type Approval, type ApprovalDecideResponse, type ApprovalsResponse,
} from "../lib/task-agent.server";
import { seg } from "../lib/task-agent.shared";
import { authenticate } from "../shopify.server";

export const handle = { title: "Approvals" };

const TABS = [
  { key: "pending", label: "Waiting for you" },
  { key: "approved", label: "Approved" },
  { key: "rejected", label: "Rejected" },
  { key: "all", label: "All" },
] as const;

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  const requested = new URL(request.url).searchParams.get("status") || "pending";
  const tab = TABS.find((item) => item.key === requested)?.key || "pending";
  try {
    const data = await taFetch<ApprovalsResponse>("/approvals", { query: { status: tab } });
    return { tab, approvals: data.approvals, counts: data.counts, error: "" };
  } catch (error) {
    return { tab, approvals: [] as Approval[], counts: {} as Record<string, number>, error: errorMessage(error) };
  }
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  const id = formString(form, "approval_id");
  return runAgentIntent(intent, async () => {
    const decision = intent === "approve" ? "approve" : intent === "reject" ? "reject" : "";
    if (!decision) return { ok: false, intent, error: `Unknown action “${intent}”.` };
    const result = await taFetch<ApprovalDecideResponse>(`/approvals/${seg(id)}/decide`, { body: { decision } });
    return { message: result.message };
  }, id);
};

function ApprovalCard({ approval }: { approval: Approval }) {
  const { busy, isSubmitting } = useBusy();
  const id = String(approval.id);
  const pending = approval.status === "pending";
  return (
    <Card accent={pending ? "attention" : approval.status === "approved" ? "success" : "neutral"}>
      <div className="sa-stack-sm">
        <div className="sa-spread">
          <div className="sa-stack-sm">
            <strong>{approval.action}</strong>
            <span className="sa-small sa-muted">
              From task <Link to={`/app/agent/tasks/${seg(approval.job_id)}`}>{approval.job_title || `#${approval.job_id}`}</Link>
              {" · "}asked <AgentTime iso={approval.created_at} relative />
            </span>
          </div>
          <StatusBadge map={APPROVAL_STATUS} status={approval.status} />
        </div>
        <div className="sa-prewrap sa-small" style={{ background: "var(--sa-surface-muted)", borderRadius: 8, padding: "8px 10px" }}>
          {approval.preview}
        </div>
        {pending ? (
          <div className="sa-row">
            <Form method="post" className="sa-inline-form" preventScrollReset>
              <Hidden values={{ intent: "approve", approval_id: id }} />
              <button type="submit" className="sa-btn sa-btn-success" disabled={busy}>
                {isSubmitting("approve", ["approval_id", id]) ? "Approving…" : "Approve"}
              </button>
            </Form>
            <Form
              method="post"
              className="sa-inline-form"
              preventScrollReset
              onSubmit={(event) => { if (!window.confirm("Reject this? The agent won't do it.")) event.preventDefault(); }}
            >
              <Hidden values={{ intent: "reject", approval_id: id }} />
              <button type="submit" className="sa-btn sa-btn-critical" disabled={busy}>
                {isSubmitting("reject", ["approval_id", id]) ? "Rejecting…" : "Reject"}
              </button>
            </Form>
          </div>
        ) : (
          <span className="sa-small sa-muted">
            {approval.status === "approved" ? "Approved" : "Rejected"} <AgentTime iso={approval.decided_at} relative />
            {approval.decided_by ? ` by ${approval.decided_by}` : ""}
          </span>
        )}
      </div>
    </Card>
  );
}

export default function AgentApprovals() {
  const data = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>();
  const [params] = useSearchParams();
  usePolling(data.tab === "pending", 15_000);

  const tabLink = (key: string) => {
    const next = new URLSearchParams(params);
    next.set("status", key);
    return `?${next.toString()}`;
  };
  const total = Object.values(data.counts).reduce((sum, value) => sum + (value || 0), 0);

  return (
    <>
      <ActionBanner result={result} />
      <LoadError error={data.error} />
      <s-section>
        <Tabs
          items={TABS.map((tab) => ({
            to: tabLink(tab.key),
            label: tab.label,
            count: tab.key === "all" ? total : data.counts[tab.key] || 0,
            active: tab.key === data.tab,
          }))}
        />
        {data.approvals.length ? (
          <div className="sa-stack">
            {data.approvals.map((approval) => <ApprovalCard key={approval.id} approval={approval} />)}
          </div>
        ) : (
          <EmptyState title={data.tab === "pending" ? "Nothing waiting for your approval" : "Nothing here"}>
            The agent never sends an email or deletes anything on its own. Once email is set up, every outgoing email
            and every delete waits here until you approve it. Payments are never made by the agent — it hands those to you.
          </EmptyState>
        )}
      </s-section>
    </>
  );
}
