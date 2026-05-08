import React, { useState, useEffect } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, useActionData, useLoaderData, useNavigation } from "react-router";
import { authenticate } from "../shopify.server";

const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

type ScheduledJob = {
  id: string;
  store_id: string;
  store_name?: string;
  name: string;
  prompt_id: string;
  blog_handle: string;
  author: string;
  cron_expr: string;
  timezone: string;
  is_active: number;
  is_product_blog: number;
  use_keyword_pool: number;
  last_run_at: number | null;
  next_run_at: number | null;
  created_at: number;
};

type RecentRun = {
  id: number;
  title: string;
  article_url: string | null;
  blog_handle: string;
  created_at: number;
};

type Prompt = { id: string; name: string; text: string };
type Store = { id: string; name: string; myshopify_domain: string; default_blog_handle: string; default_author: string };

async function backendFetch(path: string, opts: RequestInit = {}) {
  const res = await fetch(`${BACKEND_URL}${path}`, {
    ...opts,
    headers: { "x-api-key": BACKEND_KEY, "content-type": "application/json", ...(opts.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`Backend ${res.status}: ${await res.text()}`);
  return res.json();
}

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  if (!BACKEND_KEY) return { jobs: [], prompts: [], stores: [], storeId: "", recentRuns: {} as Record<string, RecentRun[]>, error: "AI_BLOG_BACKEND_API_KEY is not configured." };
  try {
    const [jobsData, storesData] = await Promise.all([
      backendFetch("/api/schedule/jobs"),
      backendFetch("/api/stores"),
    ]);
    const storeId: string = jobsData.store_id || storesData.stores?.[0]?.id || "";
    const jobs = jobsData.jobs as ScheduledJob[];
    const [promptsData, ...runsResults] = await Promise.all([
      storeId ? backendFetch(`/api/prompts?store_id=${encodeURIComponent(storeId)}`) : Promise.resolve({ prompts: [] }),
      ...jobs.map((j: ScheduledJob) =>
        backendFetch(`/api/schedule/recent-runs?job_id=${encodeURIComponent(j.id)}&limit=10`).catch(() => ({ runs: [] }))
      ),
    ]);
    const recentRuns: Record<string, RecentRun[]> = {};
    jobs.forEach((j: ScheduledJob, i: number) => {
      recentRuns[j.id] = runsResults[i]?.runs ?? [];
    });
    return { jobs, prompts: promptsData.prompts as Prompt[], stores: storesData.stores as Store[], storeId, recentRuns, error: null };
  } catch (e) {
    return { jobs: [], prompts: [], stores: [], storeId: "", recentRuns: {} as Record<string, RecentRun[]>, error: e instanceof Error ? e.message : "Failed to reach backend" };
  }
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  if (!BACKEND_KEY) return { ok: false, error: "AI_BLOG_BACKEND_API_KEY not configured" };
  try {
    if (intent === "save") {
      await backendFetch("/api/schedule/save", {
        method: "POST",
        body: JSON.stringify({
          store_id: form.get("store_id") || "",
          job_id: form.get("job_id") || "",
          name: form.get("name") || "",
          prompt_id: form.get("prompt_id") || "",
          blog_handle: form.get("blog_handle") || "news",
          author: form.get("author") || "",
          cron_expr: form.get("cron_expr") || "",
          timezone: form.get("timezone") || "UTC",
          is_active: form.get("is_active") === "1",
          is_product_blog: form.get("is_product_blog") === "1",
          use_keyword_pool: form.get("use_keyword_pool") === "1",
        }),
      });
      return { ok: true, message: "Schedule saved." };
    }
    if (intent === "delete") {
      await backendFetch("/api/schedule/delete", { method: "POST", body: JSON.stringify({ job_id: form.get("job_id") }) });
      return { ok: true, message: "Schedule deleted." };
    }
    if (intent === "toggle") {
      await backendFetch("/api/schedule/toggle", { method: "POST", body: JSON.stringify({ job_id: form.get("job_id"), is_active: form.get("is_active") === "1" }) });
      return { ok: true, message: null };
    }
    return { ok: false, error: "Unknown intent" };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Action failed" };
  }
};

function fmt(epoch: number | null) {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

const TIMEZONES = ["UTC", "Europe/London", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "Asia/Tokyo", "Asia/Singapore", "Australia/Sydney"];

const inputStyle: React.CSSProperties = { borderRadius: "10px", border: "1px solid #d1d5db", padding: "9px 12px", font: "inherit", width: "100%", boxSizing: "border-box" as const };
const labelStyle: React.CSSProperties = { display: "grid", gap: "5px", fontSize: "0.875rem" };

function JobForm({ job, prompts, stores, storeId, onCancel }: {
  job?: ScheduledJob;
  prompts: Prompt[];
  stores: Store[];
  storeId: string;
  onCancel: () => void;
}) {
  const navigation = useNavigation();
  const saving = navigation.state !== "idle";
  const defaultStore = stores.find(s => s.id === storeId) || stores[0];
  return (
    <Form method="post">
      <input type="hidden" name="intent" value="save" />
      <input type="hidden" name="job_id" value={job?.id || ""} />
      <div style={{ display: "grid", gap: "14px" }}>
        {stores.length > 1 && (
          <label style={labelStyle}>
            <span>Store</span>
            <select name="store_id" defaultValue={job?.store_id || storeId} style={inputStyle}>
              {stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
        )}
        {stores.length <= 1 && <input type="hidden" name="store_id" value={job?.store_id || storeId} />}
        <label style={labelStyle}>
          <span>Name <span style={{ color: "#dc2626" }}>*</span></span>
          <input name="name" defaultValue={job?.name || ""} required style={inputStyle} placeholder="Daily wellness blog" />
        </label>
        <label style={labelStyle}>
          <span>Prompt <span style={{ color: "#dc2626" }}>*</span></span>
          <select name="prompt_id" defaultValue={job?.prompt_id || ""} required style={inputStyle}>
            <option value="">— select prompt —</option>
            {prompts.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          {prompts.length === 0 && <span style={{ color: "#92400e", fontSize: "0.8rem" }}>No prompts yet — add one in the Prompts page first.</span>}
        </label>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
          <label style={labelStyle}>
            <span>Blog handle</span>
            <input name="blog_handle" defaultValue={job?.blog_handle || defaultStore?.default_blog_handle || "news"} style={inputStyle} placeholder="news" />
          </label>
          <label style={labelStyle}>
            <span>Author</span>
            <input name="author" defaultValue={job?.author || defaultStore?.default_author || ""} style={inputStyle} placeholder="Store Team" />
          </label>
        </div>
        <label style={labelStyle}>
          <span>Cron expression <span style={{ color: "#dc2626" }}>*</span></span>
          <input name="cron_expr" defaultValue={job?.cron_expr || "0 9 * * *"} required style={inputStyle} placeholder="0 9 * * *" />
          <span style={{ color: "#6b7280", fontSize: "0.78rem" }}>5-field cron: minute hour day-of-month month day-of-week. E.g. <code>0 9 * * 1</code> = every Monday 9am</span>
        </label>
        <label style={labelStyle}>
          <span>Timezone</span>
          <select name="timezone" defaultValue={job?.timezone || "UTC"} style={inputStyle}>
            {TIMEZONES.map(tz => <option key={tz} value={tz}>{tz}</option>)}
          </select>
        </label>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "12px" }}>
          <label style={{ ...labelStyle, flexDirection: "row", alignItems: "center", flexWrap: "wrap" }}>
            <input type="checkbox" name="is_active" value="1" defaultChecked={job ? !!job.is_active : true} style={{ marginRight: 6 }} />
            <span>Active</span>
          </label>
          <label style={{ ...labelStyle, flexDirection: "row", alignItems: "center", flexWrap: "wrap" }}>
            <input type="checkbox" name="is_product_blog" value="1" defaultChecked={!!job?.is_product_blog} style={{ marginRight: 6 }} />
            <span>Product blog</span>
          </label>
          <label style={{ ...labelStyle, flexDirection: "row", alignItems: "center", flexWrap: "wrap" }}>
            <input type="checkbox" name="use_keyword_pool" value="1" defaultChecked={!!job?.use_keyword_pool} style={{ marginRight: 6 }} />
            <span>Use keyword pool</span>
          </label>
        </div>
        <div style={{ display: "flex", gap: "10px", marginTop: 4 }}>
          <button type="submit" disabled={saving} style={{ borderRadius: "999px", border: 0, background: "#111827", color: "white", padding: "10px 20px", fontWeight: 700, cursor: "pointer" }}>
            {saving ? "Saving…" : job ? "Update schedule" : "Create schedule"}
          </button>
          <button type="button" onClick={onCancel} style={{ borderRadius: "999px", border: "1px solid #d1d5db", background: "white", padding: "10px 20px", cursor: "pointer" }}>
            Cancel
          </button>
        </div>
      </div>
    </Form>
  );
}

export default function ScheduleRoute() {
  const { jobs, prompts, stores, storeId, recentRuns, error } = useLoaderData<typeof loader>();
  const actionData = useActionData<typeof action>() as { ok: boolean; message?: string | null; error?: string } | undefined;
  const [editing, setEditing] = useState<string | null>(null);
  const navigation = useNavigation();

  useEffect(() => {
    if (actionData?.ok) setEditing(null);
  }, [actionData]);

  return (
    <s-page heading="Scheduled Blog Generation">
      {error || actionData?.error ? (
        <s-section>
          <div style={{ borderRadius: "12px", padding: "12px 16px", background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.3)", color: "#991b1b", fontSize: "0.875rem" }}>
            {error || actionData?.error}
          </div>
        </s-section>
      ) : null}
      {actionData?.ok && actionData.message ? (
        <s-section>
          <div style={{ borderRadius: "12px", padding: "12px 16px", background: "#ecfdf5", border: "1px solid #a7f3d0", color: "#065f46", fontSize: "0.875rem" }}>
            {actionData.message}
          </div>
        </s-section>
      ) : null}

      {editing === "new" ? (
        <s-section heading="New schedule">
          <JobForm prompts={prompts} stores={stores} storeId={storeId} onCancel={() => setEditing(null)} />
        </s-section>
      ) : (
        <s-section>
          <button onClick={() => setEditing("new")} style={{ borderRadius: "999px", border: 0, background: "#111827", color: "white", padding: "10px 20px", fontWeight: 700, cursor: "pointer" }}>
            + New schedule
          </button>
        </s-section>
      )}

      <s-section heading={`Schedules (${jobs.length})`}>
        {jobs.length === 0 && !error ? (
          <s-paragraph>No schedules yet. Create one above.</s-paragraph>
        ) : (
          <div style={{ display: "grid", gap: "12px" }}>
            {jobs.map(job => (
              <div key={job.id} style={{ border: "1px solid #e5e7eb", borderRadius: "12px", padding: "16px", background: job.is_active ? "white" : "#f9fafb" }}>
                {editing === job.id ? (
                  <JobForm job={job} prompts={prompts} stores={stores} storeId={storeId} onCancel={() => setEditing(null)} />
                ) : (
                  <>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "8px" }}>
                      <div>
                        <div style={{ fontWeight: 700, fontSize: "1rem" }}>
                          {job.name}
                          {!job.is_active && <span style={{ marginLeft: 8, fontSize: "0.75rem", background: "#f3f4f6", color: "#6b7280", borderRadius: "999px", padding: "2px 8px" }}>paused</span>}
                        </div>
                        <div style={{ fontSize: "0.8rem", color: "#6b7280", marginTop: 2 }}>
                          {job.store_name || job.store_id} · {job.blog_handle} · <code style={{ background: "#f3f4f6", padding: "1px 5px", borderRadius: 4 }}>{job.cron_expr}</code> ({job.timezone})
                        </div>
                        <div style={{ fontSize: "0.78rem", color: "#9ca3af", marginTop: 4 }}>
                          Last: {fmt(job.last_run_at)} · Next: <span style={{ color: job.next_run_at && job.next_run_at * 1000 < Date.now() ? "#dc2626" : "#059669" }}>{fmt(job.next_run_at)}</span>
                        </div>
                        {(job.is_product_blog || job.use_keyword_pool) && (
                          <div style={{ marginTop: 4, display: "flex", gap: 6 }}>
                            {job.is_product_blog ? <span style={{ fontSize: "0.7rem", background: "#ede9fe", color: "#5b21b6", borderRadius: "999px", padding: "2px 7px" }}>product blog</span> : null}
                            {job.use_keyword_pool ? <span style={{ fontSize: "0.7rem", background: "#fef3c7", color: "#92400e", borderRadius: "999px", padding: "2px 7px" }}>keyword pool</span> : null}
                          </div>
                        )}
                        {(recentRuns[job.id] ?? []).length > 0 && (
                          <div style={{ marginTop: 10 }}>
                            <div style={{ fontSize: "0.75rem", fontWeight: 600, color: "#6b7280", marginBottom: 4, textTransform: "uppercase", letterSpacing: "0.05em" }}>Recent runs</div>
                            <div style={{ display: "grid", gap: "4px" }}>
                              {(recentRuns[job.id] ?? []).map(run => (
                                <div key={run.id} style={{ display: "flex", alignItems: "baseline", gap: "8px", fontSize: "0.8rem" }}>
                                  <span style={{ color: "#9ca3af", whiteSpace: "nowrap", flexShrink: 0 }}>{fmt(run.created_at)}</span>
                                  {run.article_url ? (
                                    <a href={run.article_url} target="_blank" rel="noopener noreferrer" style={{ color: "#2563eb", textDecoration: "none", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                      {run.title}
                                    </a>
                                  ) : (
                                    <span style={{ color: "#374151", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{run.title}</span>
                                  )}
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                      <div style={{ display: "flex", gap: "8px", flexShrink: 0 }}>
                        <Form method="post" style={{ display: "inline" }}>
                          <input type="hidden" name="intent" value="toggle" />
                          <input type="hidden" name="job_id" value={job.id} />
                          <input type="hidden" name="is_active" value={job.is_active ? "0" : "1"} />
                          <button type="submit" disabled={navigation.state !== "idle"} style={{ borderRadius: "999px", border: "1px solid #d1d5db", background: "white", padding: "6px 14px", fontSize: "0.8rem", cursor: "pointer" }}>
                            {job.is_active ? "Pause" : "Resume"}
                          </button>
                        </Form>
                        <button onClick={() => setEditing(job.id)} style={{ borderRadius: "999px", border: "1px solid #d1d5db", background: "white", padding: "6px 14px", fontSize: "0.8rem", cursor: "pointer" }}>
                          Edit
                        </button>
                        <Form method="post" style={{ display: "inline" }} onSubmit={e => { if (!confirm(`Delete "${job.name}"?`)) e.preventDefault(); }}>
                          <input type="hidden" name="intent" value="delete" />
                          <input type="hidden" name="job_id" value={job.id} />
                          <button type="submit" style={{ borderRadius: "999px", border: "1px solid #fca5a5", background: "#fff5f5", color: "#dc2626", padding: "6px 14px", fontSize: "0.8rem", cursor: "pointer" }}>
                            Delete
                          </button>
                        </Form>
                      </div>
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        )}
      </s-section>
    </s-page>
  );
}

