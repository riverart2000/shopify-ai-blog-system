import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, useLoaderData } from "react-router";
import { authenticate } from "../shopify.server";

const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

type SystemEvent = {
  id: number;
  created_at: number;
  level: string;
  component: string;
  operation: string;
  store_id: string;
  correlation_id: string;
  message: string;
  details: string;
  resolved: number;
};

type HealthSummary = {
  errors_24h: number;
  warnings_24h: number;
  unresolved: number;
  latest_at: number | null;
  error?: string;
};

async function backendFetch(path: string, options: RequestInit = {}) {
  const response = await fetch(`${BACKEND_URL}${path}`, {
    ...options,
    headers: {
      "x-api-key": BACKEND_KEY,
      "content-type": "application/json",
      ...(options.headers ?? {}),
    },
  });
  if (!response.ok) {
    let detail = `Backend returned ${response.status}`;
    try {
      const body = await response.json() as { detail?: string };
      detail = body.detail || detail;
    } catch {
      // Keep the status-based message if the backend did not return JSON.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<Record<string, unknown>>;
}

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  const url = new URL(request.url);
  const level = url.searchParams.get("level") || "";
  const unresolvedOnly = url.searchParams.get("unresolved") === "1";
  let error = "";
  let events: SystemEvent[] = [];
  let summary: HealthSummary = { errors_24h: 0, warnings_24h: 0, unresolved: 0, latest_at: null };

  if (!BACKEND_KEY) {
    error = "AI_BLOG_BACKEND_API_KEY is not configured, so system diagnostics cannot be loaded.";
  } else {
    try {
      const query = new URLSearchParams({ limit: "200" });
      if (level) query.set("level", level);
      if (unresolvedOnly) query.set("unresolved_only", "true");
      const data = await backendFetch(`/api/system-health?${query.toString()}`) as {
        summary?: HealthSummary;
        events?: SystemEvent[];
      };
      summary = data.summary ?? summary;
      events = data.events ?? [];
    } catch (caught) {
      error = caught instanceof Error ? caught.message : "System diagnostics could not be loaded.";
    }
  }

  return { summary, events, error, level, unresolvedOnly };
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  const eventId = Number(form.get("eventId"));
  const resolved = String(form.get("resolved")) !== "false";
  if (!Number.isFinite(eventId)) return { ok: false, error: "Invalid event ID" };
  try {
    await backendFetch("/api/system-health/resolve", {
      method: "POST",
      body: JSON.stringify({ event_id: eventId, resolved }),
    });
    return { ok: true };
  } catch (caught) {
    return { ok: false, error: caught instanceof Error ? caught.message : "Update failed" };
  }
};

function formatDate(epoch: number | null) {
  if (!epoch) return "No events recorded";
  return new Date(epoch * 1000).toLocaleString("en-GB", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function levelColours(level: string) {
  if (level === "ERROR" || level === "CRITICAL") return { bg: "#fde7e9", border: "#d72c0d", text: "#8e1f0b" };
  return { bg: "#fff5d6", border: "#d89b00", text: "#704700" };
}

export default function SystemHealthPage() {
  const { summary, events, error, level, unresolvedOnly } = useLoaderData<typeof loader>();
  const healthy = summary.errors_24h === 0;

  return (
    <s-page heading="System Health">
      <s-section>
        <div style={{ padding: "14px 16px", borderRadius: 12, border: `1px solid ${healthy ? "#8ecf9d" : "#ef9a9a"}`, background: healthy ? "#eaf7ed" : "#fff0f0" }}>
          <div style={{ fontWeight: 700, color: healthy ? "#176b32" : "#9b1c1c" }}>
            {healthy ? "No system errors detected in the last 24 hours" : `${summary.errors_24h} system error${summary.errors_24h === 1 ? "" : "s"} detected in the last 24 hours`}
          </div>
          <div style={{ marginTop: 4, color: "#4a5560", fontSize: "0.85rem" }}>
            Captures warnings and errors from the blog engine, Shopify publishing, image providers, scheduled jobs, quality checks, and connected services. Sensitive keys and image data are automatically hidden.
          </div>
        </div>
      </s-section>

      {error ? <s-section><div style={{ padding: 14, borderRadius: 10, background: "#fde7e9", color: "#8e1f0b" }}>{error}</div></s-section> : null}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 14, marginBottom: 16 }}>
        {[
          ["Errors · 24 hours", summary.errors_24h, "#b42318"],
          ["Warnings · 24 hours", summary.warnings_24h, "#9a6700"],
          ["Needs review", summary.unresolved, "#374151"],
        ].map(([label, value, colour]) => (
          <div key={String(label)} style={{ background: "white", border: "1px solid #e1e3e5", borderRadius: 12, padding: 16 }}>
            <div style={{ color: "#616e75", fontSize: "0.82rem" }}>{label}</div>
            <div style={{ color: String(colour), fontSize: "1.8rem", fontWeight: 750, marginTop: 3 }}>{value}</div>
          </div>
        ))}
        <div style={{ background: "white", border: "1px solid #e1e3e5", borderRadius: 12, padding: 16 }}>
          <div style={{ color: "#616e75", fontSize: "0.82rem" }}>Latest report</div>
          <div style={{ color: "#374151", fontSize: "0.84rem", fontWeight: 650, marginTop: 8 }}>{formatDate(summary.latest_at)}</div>
        </div>
      </div>

      <s-section heading="Warnings and errors">
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 14 }}>
          <a href="/app/system-health" style={{ padding: "7px 11px", borderRadius: 8, border: "1px solid #c9cccf", background: !level && !unresolvedOnly ? "#202223" : "white", color: !level && !unresolvedOnly ? "white" : "#202223", textDecoration: "none" }}>All</a>
          <a href="/app/system-health?level=ERROR" style={{ padding: "7px 11px", borderRadius: 8, border: "1px solid #c9cccf", background: level === "ERROR" ? "#202223" : "white", color: level === "ERROR" ? "white" : "#202223", textDecoration: "none" }}>Errors</a>
          <a href="/app/system-health?level=WARNING" style={{ padding: "7px 11px", borderRadius: 8, border: "1px solid #c9cccf", background: level === "WARNING" ? "#202223" : "white", color: level === "WARNING" ? "white" : "#202223", textDecoration: "none" }}>Warnings</a>
          <a href="/app/system-health?unresolved=1" style={{ padding: "7px 11px", borderRadius: 8, border: "1px solid #c9cccf", background: unresolvedOnly ? "#202223" : "white", color: unresolvedOnly ? "white" : "#202223", textDecoration: "none" }}>Needs review</a>
          <a href={`/app/system-health${level ? `?level=${level}` : unresolvedOnly ? "?unresolved=1" : ""}`} style={{ marginLeft: "auto", padding: "7px 11px", borderRadius: 8, border: "1px solid #8c9196", color: "#202223", textDecoration: "none" }}>Refresh</a>
        </div>

        {events.length === 0 && !error ? (
          <div style={{ padding: 24, textAlign: "center", color: "#616e75", background: "#f8fafb", borderRadius: 10 }}>No matching warnings or errors.</div>
        ) : (
          <div style={{ display: "grid", gap: 10 }}>
            {events.map(event => {
              const colours = levelColours(event.level);
              return (
                <details key={event.id} style={{ border: `1px solid ${event.resolved ? "#d8dcdf" : colours.border}`, borderRadius: 10, background: event.resolved ? "#fafafa" : colours.bg, opacity: event.resolved ? 0.7 : 1 }}>
                  <summary style={{ cursor: "pointer", padding: "12px 14px", display: "flex", alignItems: "flex-start", gap: 10 }}>
                    <span style={{ color: colours.text, fontSize: "0.72rem", fontWeight: 800, minWidth: 58 }}>{event.level}</span>
                    <span style={{ flex: 1, color: "#202223", fontWeight: 600 }}>{event.message}</span>
                    <span style={{ color: "#616e75", fontSize: "0.74rem", whiteSpace: "nowrap" }}>{formatDate(event.created_at)}</span>
                  </summary>
                  <div style={{ borderTop: "1px solid rgba(0,0,0,0.1)", padding: "12px 14px", fontSize: "0.82rem" }}>
                    <div><strong>Component:</strong> {event.component || "Unknown"}</div>
                    {event.operation ? <div><strong>Operation:</strong> {event.operation}</div> : null}
                    {event.store_id ? <div><strong>Store:</strong> {event.store_id}</div> : null}
                    {event.correlation_id ? <div><strong>Run ID:</strong> {event.correlation_id}</div> : null}
                    {event.details ? <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", background: "#202223", color: "#f4f4f4", padding: 12, borderRadius: 8, maxHeight: 320, overflowY: "auto" }}>{event.details}</pre> : null}
                    <Form method="post" style={{ marginTop: 10 }}>
                      <input type="hidden" name="eventId" value={event.id} />
                      <input type="hidden" name="resolved" value={event.resolved ? "false" : "true"} />
                      <button type="submit" style={{ padding: "6px 10px", border: "1px solid #8c9196", borderRadius: 7, background: "white", cursor: "pointer" }}>
                        {event.resolved ? "Mark as needing review" : "Mark as reviewed"}
                      </button>
                    </Form>
                  </div>
                </details>
              );
            })}
          </div>
        )}
      </s-section>
    </s-page>
  );
}
