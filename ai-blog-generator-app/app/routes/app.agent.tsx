import type { LinksFunction, LoaderFunctionArgs, ShouldRevalidateFunction } from "react-router";
import { NavLink, Outlet, useLoaderData, useMatches } from "react-router";

import socialStyles from "../components/social/social.css?url";
import { WORKER_STATE } from "../components/agent/parts";
import { Badge, Banner } from "../components/social/ui";
import { errorMessage, isOffline, taFetch, type StatusResponse } from "../lib/task-agent.server";
import { authenticate } from "../shopify.server";

export const links: LinksFunction = () => [{ rel: "stylesheet", href: socialStyles }];

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  try {
    return { status: await taFetch<StatusResponse>("/status", { timeoutMs: 8_000 }), error: "", offline: false };
  } catch (error) {
    return { status: null as StatusResponse | null, error: errorMessage(error, "The Task Agent couldn't be reached."), offline: isOffline(error) };
  }
};

/** Refresh the status badges after any action or when moving between sections. */
export const shouldRevalidate: ShouldRevalidateFunction = ({ formMethod, currentUrl, nextUrl, defaultShouldRevalidate }) => {
  if (formMethod && formMethod.toUpperCase() !== "GET") return true;
  if (currentUrl.pathname !== nextUrl.pathname) return true;
  return defaultShouldRevalidate;
};

const SECTIONS: Array<{ to: string; label: string; end?: boolean }> = [
  { to: "/app/agent", label: "Overview", end: true },
  { to: "/app/agent/tasks", label: "Tasks" },
  { to: "/app/agent/new", label: "New task" },
  { to: "/app/agent/templates", label: "Templates" },
  { to: "/app/agent/approvals", label: "Approvals" },
  { to: "/app/agent/settings", label: "Settings" },
];

export const handle = { title: "Task Agent" };

export default function AgentLayout() {
  const { status, error, offline } = useLoaderData<typeof loader>();
  const matches = useMatches();
  const title = [...matches].reverse()
    .map((match) => (match.handle as { title?: string } | undefined)?.title)
    .find(Boolean) || "Task Agent";
  const state = status ? WORKER_STATE[status.worker.state] : offline ? WORKER_STATE.offline : null;

  return (
    <s-page heading={title === "Task Agent" ? title : `Task Agent · ${title}`} inlineSize="large">
      <div className="sa sa-stack">
        <nav className="sa-subnav" aria-label="Task Agent sections">
          {SECTIONS.map((section) => (
            <NavLink key={section.to} to={section.to} end={section.end} className={({ isActive }) => (isActive ? "active" : "")}>
              {section.label}
              {section.to === "/app/agent/approvals" && status?.pending_approvals ? <span className="sa-count">{status.pending_approvals}</span> : null}
            </NavLink>
          ))}
        </nav>
        {state ? (
          <div className="sa-row sa-small">
            <Badge tone={state.tone} title={state.help}>{state.label}</Badge>
            {status ? (
              <>
                <Badge tone="info" title="The local AI model the agent uses">Model: {status.model}</Badge>
                <Badge tone={status.thinking ? "magic" : "neutral"} title="Thinking makes the agent slower but more careful">
                  {status.thinking ? "Thinking on" : "Thinking off"}
                </Badge>
                {status.worker.current_job ? <span className="sa-muted">Working on: {status.worker.current_job.title}</span> : null}
              </>
            ) : null}
          </div>
        ) : null}
        {error ? (
          <Banner tone={offline ? "warning" : "critical"} title={offline ? "The Task Agent is offline" : "Task Agent unavailable"}>
            {error}
          </Banner>
        ) : null}
        <Outlet />
      </div>
    </s-page>
  );
}
