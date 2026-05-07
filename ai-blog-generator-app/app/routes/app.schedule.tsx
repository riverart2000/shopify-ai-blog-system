import type { LoaderFunctionArgs } from "react-router";
import { useLoaderData } from "react-router";
import { authenticate } from "../shopify.server";

const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

type ScheduledJob = {
  id: string;
  store_id: string;
  store_name: string;
  name: string;
  prompt_id: string;
  blog_handle: string;
  author: string;
  cron_expr: string;
  timezone: string;
  is_active: number;
  last_run_at: number | null;
  next_run_at: number | null;
  created_at: number;
};

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);

  let jobs: ScheduledJob[] = [];
  let error: string | null = null;

  if (!BACKEND_KEY) {
    error = "AI_BLOG_BACKEND_API_KEY is not configured.";
  } else {
    try {
      const res = await fetch(`${BACKEND_URL}/api/schedule`, {
        headers: { "x-api-key": BACKEND_KEY },
      });
      if (!res.ok) {
        error = `Backend returned ${res.status}: ${await res.text()}`;
      } else {
        const data = await res.json() as { jobs: ScheduledJob[] };
        jobs = data.jobs ?? [];
      }
    } catch (e) {
      error = e instanceof Error ? e.message : "Failed to reach backend";
    }
  }

  return { jobs, error };
};

function formatDate(epochSeconds: number | null) {
  if (!epochSeconds) return "—";
  return new Date(epochSeconds * 1000).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function ScheduleRoute() {
  const { jobs, error } = useLoaderData<typeof loader>();

  const activeJobs = jobs.filter((j) => j.is_active);
  const inactiveJobs = jobs.filter((j) => !j.is_active);

  return (
    <s-page heading="Scheduled Blog Generation">
      {error ? (
        <s-section>
          <div
            style={{
              borderRadius: "12px",
              padding: "14px 16px",
              background: "rgba(239,68,68,0.08)",
              border: "1px solid rgba(239,68,68,0.3)",
              color: "#991b1b",
              fontSize: "0.9rem",
            }}
          >
            {error}
          </div>
        </s-section>
      ) : null}

      <s-section heading={`Active schedules (${activeJobs.length})`}>
        {activeJobs.length === 0 && !error ? (
          <s-paragraph>No active schedules. Configure them in the Python backend at /schedule.</s-paragraph>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: "0.875rem",
              }}
            >
              <thead>
                <tr style={{ borderBottom: "2px solid #e5e7eb", textAlign: "left" }}>
                  <th style={{ padding: "10px 12px", fontWeight: 700 }}>Name</th>
                  <th style={{ padding: "10px 12px", fontWeight: 700 }}>Store</th>
                  <th style={{ padding: "10px 12px", fontWeight: 700 }}>Blog</th>
                  <th style={{ padding: "10px 12px", fontWeight: 700 }}>Cron</th>
                  <th style={{ padding: "10px 12px", fontWeight: 700 }}>Timezone</th>
                  <th style={{ padding: "10px 12px", fontWeight: 700 }}>Last run</th>
                  <th style={{ padding: "10px 12px", fontWeight: 700 }}>Next run</th>
                </tr>
              </thead>
              <tbody>
                {activeJobs.map((j, i) => (
                  <tr
                    key={j.id}
                    style={{
                      borderBottom: "1px solid #f3f4f6",
                      background: i % 2 === 0 ? "transparent" : "#f9fafb",
                    }}
                  >
                    <td style={{ padding: "10px 12px", fontWeight: 500 }}>{j.name}</td>
                    <td style={{ padding: "10px 12px", color: "#374151" }}>{j.store_name}</td>
                    <td style={{ padding: "10px 12px", color: "#374151" }}>{j.blog_handle}</td>
                    <td style={{ padding: "10px 12px", fontFamily: "monospace", fontSize: "0.8rem", color: "#4b5563" }}>
                      {j.cron_expr}
                    </td>
                    <td style={{ padding: "10px 12px", color: "#6b7280" }}>{j.timezone}</td>
                    <td style={{ padding: "10px 12px", color: "#6b7280", whiteSpace: "nowrap" }}>
                      {formatDate(j.last_run_at)}
                    </td>
                    <td
                      style={{
                        padding: "10px 12px",
                        whiteSpace: "nowrap",
                        color: j.next_run_at && j.next_run_at * 1000 < Date.now() ? "#dc2626" : "#059669",
                        fontWeight: 500,
                      }}
                    >
                      {formatDate(j.next_run_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </s-section>

      {inactiveJobs.length > 0 ? (
        <s-section heading={`Paused schedules (${inactiveJobs.length})`}>
          <div style={{ overflowX: "auto" }}>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: "0.875rem",
                opacity: 0.65,
              }}
            >
              <thead>
                <tr style={{ borderBottom: "2px solid #e5e7eb", textAlign: "left" }}>
                  <th style={{ padding: "10px 12px", fontWeight: 700 }}>Name</th>
                  <th style={{ padding: "10px 12px", fontWeight: 700 }}>Store</th>
                  <th style={{ padding: "10px 12px", fontWeight: 700 }}>Blog</th>
                  <th style={{ padding: "10px 12px", fontWeight: 700 }}>Cron</th>
                </tr>
              </thead>
              <tbody>
                {inactiveJobs.map((j, i) => (
                  <tr
                    key={j.id}
                    style={{
                      borderBottom: "1px solid #f3f4f6",
                      background: i % 2 === 0 ? "transparent" : "#f9fafb",
                    }}
                  >
                    <td style={{ padding: "10px 12px" }}>{j.name}</td>
                    <td style={{ padding: "10px 12px" }}>{j.store_name}</td>
                    <td style={{ padding: "10px 12px" }}>{j.blog_handle}</td>
                    <td style={{ padding: "10px 12px", fontFamily: "monospace", fontSize: "0.8rem" }}>
                      {j.cron_expr}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </s-section>
      ) : null}
    </s-page>
  );
}
