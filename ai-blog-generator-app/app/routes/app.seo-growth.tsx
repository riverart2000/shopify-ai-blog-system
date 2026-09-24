import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import {
  Form, useActionData, useLoaderData, useNavigation, useRevalidator,
} from "react-router";
import { useEffect } from "react";
import { authenticate } from "../shopify.server";

const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

type SeoRun = {
  id: string; trigger_type: string; status: string; stage: string; progress: number; period_days: number;
  started_at: number; completed_at?: number; error_type?: string; error_message?: string;
  summary?: {
    opportunity_count?: number;
    severity_counts?: Record<string, number>;
    category_counts?: Record<string, number>;
    search_console?: {
      connected?: boolean; status?: string; message?: string; site_url?: string;
      current_period?: { start?: string; end?: string };
      totals?: { clicks?: number; impressions?: number; ctr?: number; position?: number };
      previous_totals?: { clicks?: number; impressions?: number; ctr?: number; position?: number };
      top_queries?: Array<{ query: string; clicks: number; impressions: number; ctr: number; position: number }>;
      top_pages?: Array<{ page: string; clicks: number; impressions: number; ctr: number; position: number }>;
    };
    shopify?: {
      products?: Record<string, number>;
      articles?: Record<string, number>;
    };
  };
};

type Opportunity = {
  id: string; kind: string; category: string; severity: string; score: number;
  title: string; evidence: string; action: string; page_url: string;
  search_query: string; source: string; status: string; metrics: Record<string, unknown>;
};

type Backlink = {
  id: string; domain: string; prospect_url: string; contact_name: string;
  contact_email: string; target_url: string; outreach_angle: string;
  relationship_type: string; status: string; link_url: string; link_rel: string;
  notes: string; updated_at: number;
};

type SeoData = {
  store_id: string;
  latest: SeoRun | null;
  results_run_id: string;
  opportunities: Opportunity[];
  history: SeoRun[];
  backlinks: Backlink[];
  settings: {
    gsc_site_url: string; gsc_credentials_saved: boolean;
    ga4_credentials_available: boolean; use_ga4_credentials: boolean;
    auto_enabled: boolean; period_days: number;
  };
};

const emptyData: SeoData = {
  store_id: "", latest: null, results_run_id: "", opportunities: [], history: [], backlinks: [],
  settings: {
    gsc_site_url: "", gsc_credentials_saved: false, ga4_credentials_available: false,
    use_ga4_credentials: true, auto_enabled: false, period_days: 90,
  },
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
      const text = await response.text().catch(() => "");
      if (text) detail = text.slice(0, 500);
    }
    throw new Error(detail);
  }
  return response.json() as Promise<Record<string, unknown>>;
}

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  if (!BACKEND_KEY) {
    return { backendConfigured: false, data: emptyData, error: "Backend API key is not configured." };
  }
  try {
    const init = await backendFetch("/api/init") as { store_id?: string };
    const storeId = String(init.store_id || "");
    const data = await backendFetch(`/api/seo-growth?store_id=${encodeURIComponent(storeId)}`) as unknown as SeoData;
    return { backendConfigured: true, data, error: "" };
  } catch (error) {
    return {
      backendConfigured: true, data: emptyData,
      error: error instanceof Error ? error.message : "SEO Growth could not be loaded.",
    };
  }
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  if (!BACKEND_KEY) return { ok: false, error: "Backend API key is not configured." };
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  const storeId = String(form.get("store_id") || "");
  try {
    if (intent === "run") {
      const result = await backendFetch("/api/seo-growth/run", {
        method: "POST",
        body: JSON.stringify({ store_id: storeId, period_days: Number(form.get("period_days") || 90) }),
      });
      return {
        ok: true, intent,
        message: result.already_running ? "The existing SEO audit is still running." : "SEO audit started in the background.",
      };
    }
    if (intent === "settings") {
      await backendFetch("/api/seo-growth/settings", {
        method: "POST",
        body: JSON.stringify({
          store_id: storeId,
          gsc_site_url: String(form.get("gsc_site_url") || ""),
          gsc_service_account_json: String(form.get("gsc_service_account_json") || ""),
          clear_gsc_credentials: form.get("clear_gsc_credentials") === "1",
          use_ga4_credentials: form.get("use_ga4_credentials") === "1",
          auto_enabled: form.get("auto_enabled") === "1",
          period_days: Number(form.get("period_days") || 90),
        }),
      });
      return { ok: true, intent, message: "SEO connections and schedule saved." };
    }
    if (intent === "opportunity_status") {
      const status = String(form.get("status") || "open");
      await backendFetch("/api/seo-growth/opportunity-status", {
        method: "POST",
        body: JSON.stringify({
          store_id: storeId,
          opportunity_id: String(form.get("opportunity_id") || ""),
          status,
        }),
      });
      return { ok: true, intent, message: `Opportunity marked ${status.replaceAll("_", " ")}.` };
    }
    if (intent === "backlink_save" || intent === "backlink_update") {
      const result = await backendFetch("/api/seo-growth/backlinks", {
        method: "POST",
        body: JSON.stringify({
          store_id: storeId,
          id: String(form.get("id") || ""),
          domain: String(form.get("domain") || ""),
          prospect_url: String(form.get("prospect_url") || ""),
          contact_name: String(form.get("contact_name") || ""),
          contact_email: String(form.get("contact_email") || ""),
          target_url: String(form.get("target_url") || ""),
          outreach_angle: String(form.get("outreach_angle") || ""),
          relationship_type: String(form.get("relationship_type") || "earned"),
          status: String(form.get("status") || "prospect"),
          link_url: String(form.get("link_url") || ""),
          link_rel: String(form.get("link_rel") || ""),
          notes: String(form.get("notes") || ""),
        }),
      });
      return { ok: true, intent, message: String(result.warning || "Backlink prospect saved.") };
    }
    if (intent === "backlink_delete") {
      await backendFetch("/api/seo-growth/backlinks/delete", {
        method: "POST",
        body: JSON.stringify({ store_id: storeId, prospect_id: String(form.get("prospect_id") || "") }),
      });
      return { ok: true, intent, message: "Backlink prospect removed." };
    }
    return { ok: false, error: "Unknown SEO Growth action." };
  } catch (error) {
    return { ok: false, intent, error: error instanceof Error ? error.message : "SEO Growth action failed." };
  }
};

function Alert({ children, tone }: { children: React.ReactNode; tone: "success" | "error" | "info" | "warning" }) {
  const colours = tone === "success"
    ? { background: "#ecfdf5", border: "#a7f3d0", color: "#065f46" }
    : tone === "error"
      ? { background: "#fef2f2", border: "#fecaca", color: "#991b1b" }
      : tone === "warning"
        ? { background: "#fffbeb", border: "#fde68a", color: "#92400e" }
        : { background: "#eff6ff", border: "#bfdbfe", color: "#1e40af" };
  return <div style={{ ...colours, borderWidth: 1, borderStyle: "solid", borderRadius: 12, padding: "12px 16px", fontSize: ".875rem", lineHeight: 1.5 }}>{children}</div>;
}

function Kpi({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return <div style={{ border: "1px solid #e5e7eb", borderRadius: 14, padding: 16, background: "white" }}>
    <div style={{ color: "#6b7280", fontSize: ".72rem", textTransform: "uppercase", letterSpacing: ".05em", fontWeight: 700 }}>{label}</div>
    <div style={{ fontSize: "1.55rem", fontWeight: 750, marginTop: 5 }}>{value}</div>
    {detail ? <div style={{ color: "#6b7280", fontSize: ".78rem", marginTop: 2 }}>{detail}</div> : null}
  </div>;
}

function formatDate(epoch?: number) {
  return epoch ? new Date(epoch * 1000).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : "—";
}

function stageLabel(value: string) {
  return value.replaceAll("_", " ").replace(/^./, letter => letter.toUpperCase());
}

const inputStyle: React.CSSProperties = {
  width: "100%", boxSizing: "border-box", border: "1px solid #c9cccf", borderRadius: 8,
  padding: "9px 11px", font: "inherit", background: "white",
};
const primaryButton: React.CSSProperties = {
  border: 0, borderRadius: 999, background: "#111827", color: "white",
  padding: "10px 20px", fontWeight: 700, cursor: "pointer",
};

export default function SeoGrowthPage() {
  const { backendConfigured, data, error } = useLoaderData<typeof loader>();
  const actionData = useActionData<typeof action>() as { ok?: boolean; error?: string; message?: string; intent?: string } | undefined;
  const navigation = useNavigation();
  const revalidator = useRevalidator();
  const busy = navigation.state !== "idle";
  const running = data.latest?.status === "queued" || data.latest?.status === "running";
  const summary = data.latest?.status === "complete"
    ? data.latest.summary
    : data.history.find(run => run.status === "complete")?.summary;
  const search = summary?.search_console;
  const searchTotals = search?.totals || {};
  const previousSearchTotals = search?.previous_totals || {};
  const products = summary?.shopify?.products || {};
  const articles = summary?.shopify?.articles || {};

  useEffect(() => {
    if (!running) return undefined;
    const timer = window.setInterval(() => revalidator.revalidate(), 2500);
    return () => window.clearInterval(timer);
  }, [running, revalidator]);

  return <s-page heading="SEO Growth">
    {!backendConfigured || error ? <s-section><Alert tone="error">{error || "Backend connection is unavailable."}</Alert></s-section> : null}
    {actionData?.message ? <s-section><Alert tone={actionData.ok ? "success" : "error"}>{actionData.message}</Alert></s-section> : null}
    {actionData?.error ? <s-section><Alert tone="error">{actionData.error}</Alert></s-section> : null}

    <s-section heading="Organic growth control centre">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 18, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div style={{ maxWidth: 720, color: "#4b5563", lineHeight: 1.55 }}>
          Search demand, published content and merchant data are checked together. Recommendations are evidence-led; this screen never silently rewrites pages, creates redirects or exchanges backlinks.
        </div>
        <Form method="post" style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input type="hidden" name="intent" value="run" />
          <input type="hidden" name="store_id" value={data.store_id} />
          <input type="hidden" name="period_days" value={data.settings.period_days} />
          <button type="submit" disabled={busy || running || !backendConfigured} style={{ ...primaryButton, opacity: busy || running ? .6 : 1 }}>
            {running ? "Audit running…" : "Run SEO audit"}
          </button>
        </Form>
      </div>
      {running ? <div style={{ marginTop: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: ".8rem", color: "#4b5563", marginBottom: 5 }}>
          <span>{stageLabel(data.latest?.stage || "queued")}</span><strong>{data.latest?.progress || 0}%</strong>
        </div>
        <div style={{ height: 9, borderRadius: 99, background: "#e5e7eb", overflow: "hidden" }}><div style={{ width: `${data.latest?.progress || 0}%`, height: "100%", background: "#16a34a", transition: "width .25s" }} /></div>
      </div> : null}
    </s-section>

    {data.latest?.status === "failed" ? <s-section><Alert tone="error">
      <strong>{data.latest.error_type || "SEO audit failed"}:</strong> {data.latest.error_message}<br />
      This run is final. No retry or fallback was attempted. Correlation ID: <code>{data.latest.id}</code>
    </Alert></s-section> : null}

    {summary ? <>
      <s-section heading="Latest measured snapshot">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(145px, 1fr))", gap: 10 }}>
          <Kpi label="Search clicks" value={Number(searchTotals.clicks || 0).toLocaleString("en-GB")} detail={search?.connected && Number(previousSearchTotals.clicks || 0) ? `${(((Number(searchTotals.clicks || 0) / Number(previousSearchTotals.clicks)) - 1) * 100).toFixed(1)}% vs previous period` : search?.connected ? "Google Search Console" : "Connect Search Console"} />
          <Kpi label="Impressions" value={Number(searchTotals.impressions || 0).toLocaleString("en-GB")} />
          <Kpi label="Search CTR" value={`${(Number(searchTotals.ctr || 0) * 100).toFixed(2)}%`} />
          <Kpi label="Avg. position" value={Number(searchTotals.position || 0) ? Number(searchTotals.position).toFixed(1) : "—"} />
          <Kpi label="Active products" value={Number(products.active_products || 0).toLocaleString("en-GB")} detail={`${Number(products.missing_gtins || 0)} without GTIN`} />
          <Kpi label="Published articles" value={Number(articles.articles || 0).toLocaleString("en-GB")} detail={`${Number(articles.visible_keyword_blocks || 0)} visible SEO blocks`} />
        </div>
        {!search?.connected ? <div style={{ marginTop: 12 }}><Alert tone="warning">{search?.message || "Search Console is not connected. The Shopify audit is still real, but no Google performance data has been inferred."}</Alert></div> : <p style={{ color: "#6b7280", fontSize: ".8rem", marginBottom: 0 }}>Search Console property: {search.site_url} · period {search.current_period?.start} to {search.current_period?.end}</p>}
      </s-section>

      {search?.connected && (search.top_queries?.length || search.top_pages?.length) ? <s-section heading="Organic search winners">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(310px, 1fr))", gap: 18 }}>
          <div><h3 style={{ marginTop: 0, fontSize: ".95rem" }}>Top queries</h3><div style={{ overflowX: "auto" }}><table style={{ width: "100%", borderCollapse: "collapse", fontSize: ".78rem" }}><thead><tr><th style={{ textAlign: "left", padding: 7 }}>Query</th><th style={{ textAlign: "right", padding: 7 }}>Clicks</th><th style={{ textAlign: "right", padding: 7 }}>Pos.</th></tr></thead><tbody>{(search.top_queries || []).slice(0, 10).map(row => <tr key={row.query} style={{ borderTop: "1px solid #e5e7eb" }}><td style={{ padding: 7 }}>{row.query}</td><td style={{ textAlign: "right", padding: 7 }}>{row.clicks.toLocaleString("en-GB")}</td><td style={{ textAlign: "right", padding: 7 }}>{row.position.toFixed(1)}</td></tr>)}</tbody></table></div></div>
          <div><h3 style={{ marginTop: 0, fontSize: ".95rem" }}>Top pages</h3><div style={{ overflowX: "auto" }}><table style={{ width: "100%", borderCollapse: "collapse", fontSize: ".78rem" }}><thead><tr><th style={{ textAlign: "left", padding: 7 }}>Page</th><th style={{ textAlign: "right", padding: 7 }}>Clicks</th><th style={{ textAlign: "right", padding: 7 }}>CTR</th></tr></thead><tbody>{(search.top_pages || []).slice(0, 10).map(row => <tr key={row.page} style={{ borderTop: "1px solid #e5e7eb" }}><td style={{ padding: 7, maxWidth: 250, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}><a href={row.page} target="_blank" rel="noreferrer">{new URL(row.page).pathname || "/"}</a></td><td style={{ textAlign: "right", padding: 7 }}>{row.clicks.toLocaleString("en-GB")}</td><td style={{ textAlign: "right", padding: 7 }}>{(row.ctr * 100).toFixed(2)}%</td></tr>)}</tbody></table></div></div>
        </div>
      </s-section> : null}

      <s-section heading={`Prioritised opportunities (${data.opportunities.length})`}>
        <p style={{ color: "#6b7280", fontSize: ".85rem" }}>Scores rank measured urgency; they are not Google scores. Workflow choices persist when the same issue appears in a later audit.</p>
        <div style={{ display: "grid", gap: 12 }}>
          {data.opportunities.map((item, index) => {
            const colour = item.severity === "high" ? "#dc2626" : item.severity === "medium" ? "#d97706" : "#16a34a";
            return <article key={item.id} style={{ border: "1px solid #e5e7eb", borderLeft: `4px solid ${colour}`, borderRadius: 12, padding: 16, background: "white" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                <h3 style={{ margin: 0, fontSize: "1rem" }}>{index + 1}. {item.title}</h3>
                <span style={{ color: colour, fontWeight: 800, fontSize: ".72rem", textTransform: "uppercase", whiteSpace: "nowrap" }}>{item.severity} · {item.score}</span>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, margin: "9px 0" }}>
                {[item.category, item.source.replaceAll("_", " "), item.status.replaceAll("_", " ")].map(tag => <span key={tag} style={{ background: "#f3f4f6", borderRadius: 999, padding: "3px 8px", fontSize: ".72rem", color: "#4b5563" }}>{tag}</span>)}
              </div>
              <div style={{ lineHeight: 1.5, fontSize: ".875rem" }}><strong>Evidence:</strong> {item.evidence}</div>
              <div style={{ lineHeight: 1.5, fontSize: ".875rem", color: "#4b5563", marginTop: 6 }}><strong>Recommended action:</strong> {item.action}</div>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap", marginTop: 12 }}>
                {item.page_url ? <a href={item.page_url} target="_blank" rel="noreferrer" style={{ color: "#005bd3", fontSize: ".82rem" }}>Open affected page ↗</a> : <span />}
                <Form method="post" style={{ display: "flex", gap: 6 }}>
                  <input type="hidden" name="intent" value="opportunity_status" /><input type="hidden" name="store_id" value={data.store_id} /><input type="hidden" name="opportunity_id" value={item.id} />
                  <select name="status" defaultValue={item.status} style={{ ...inputStyle, width: 135, padding: "6px 8px", fontSize: ".78rem" }}>
                    <option value="open">Open</option><option value="planned">Planned</option><option value="in_progress">In progress</option><option value="done">Done</option><option value="dismissed">Dismissed</option>
                  </select>
                  <button type="submit" disabled={busy} style={{ border: "1px solid #d1d5db", borderRadius: 999, background: "white", padding: "6px 12px", cursor: "pointer", fontSize: ".75rem" }}>Update</button>
                </Form>
              </div>
            </article>;
          })}
          {!data.opportunities.length ? <Alert tone="info">Run the first audit to create an evidence-led opportunity queue.</Alert> : null}
        </div>
      </s-section>
    </> : <s-section><Alert tone="info">No completed SEO audit yet. The first run will inspect Shopify immediately, even if Search Console has not been connected.</Alert></s-section>}

    <s-section heading="Google Search Console and automatic audits">
      <p style={{ color: "#4b5563", lineHeight: 1.5 }}>Use a read-only Google service account. Add its <code>client_email</code> as an Owner or Full user of the exact Search Console property. Credentials are stored server-side and never returned to this page.</p>
      <Form method="post">
        <input type="hidden" name="intent" value="settings" /><input type="hidden" name="store_id" value={data.store_id} />
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))", gap: 12 }}>
          <label style={{ display: "grid", gap: 5, fontSize: ".85rem" }}><span>Search Console property</span><input name="gsc_site_url" defaultValue={data.settings.gsc_site_url} placeholder="sc-domain:bioluxelab.com" style={inputStyle} /></label>
          <label style={{ display: "grid", gap: 5, fontSize: ".85rem" }}><span>Comparison period</span><select name="period_days" defaultValue={String(data.settings.period_days)} style={inputStyle}><option value="28">28 days</option><option value="90">90 days</option><option value="180">180 days</option><option value="365">365 days</option></select></label>
        </div>
        <label style={{ display: "grid", gap: 5, fontSize: ".85rem", marginTop: 12 }}><span>Search Console service-account JSON {data.settings.gsc_credentials_saved ? "(saved)" : ""}</span><textarea name="gsc_service_account_json" rows={4} style={{ ...inputStyle, resize: "vertical", fontFamily: "monospace", fontSize: ".75rem" }} placeholder="Leave blank to keep existing credentials" /></label>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 18, margin: "12px 0", fontSize: ".85rem" }}>
          <label><input type="checkbox" name="use_ga4_credentials" value="1" defaultChecked={data.settings.use_ga4_credentials} /> Reuse saved GA4 credentials if separate credentials are blank {data.settings.ga4_credentials_available ? "(available)" : "(not currently available)"}</label>
          <label><input type="checkbox" name="auto_enabled" value="1" defaultChecked={data.settings.auto_enabled} /> Audit automatically once a week</label>
          {data.settings.gsc_credentials_saved ? <label><input type="checkbox" name="clear_gsc_credentials" value="1" /> Remove separate Search Console credentials</label> : null}
        </div>
        <button type="submit" disabled={busy || !backendConfigured} style={{ ...primaryButton, opacity: busy ? .6 : 1 }}>Save SEO connections</button>
      </Form>
    </s-section>

    <s-section heading="Safe backlink outreach tracker">
      <Alert tone="warning">This does not buy, exchange or send links automatically. Reciprocal and paid prospects are visibly flagged because link schemes can create Google risk.</Alert>
      <Form method="post" style={{ marginTop: 14 }}>
        <input type="hidden" name="intent" value="backlink_save" /><input type="hidden" name="store_id" value={data.store_id} />
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
          <label style={{ display: "grid", gap: 4, fontSize: ".8rem" }}><span>Domain *</span><input name="domain" required placeholder="example.com" style={inputStyle} /></label>
          <label style={{ display: "grid", gap: 4, fontSize: ".8rem" }}><span>Relevant page</span><input name="prospect_url" type="url" placeholder="https://…" style={inputStyle} /></label>
          <label style={{ display: "grid", gap: 4, fontSize: ".8rem" }}><span>Target BioLuxeLab URL</span><input name="target_url" type="url" placeholder="https://bioluxelab.com/…" style={inputStyle} /></label>
          <label style={{ display: "grid", gap: 4, fontSize: ".8rem" }}><span>Contact</span><input name="contact_name" style={inputStyle} /></label>
          <label style={{ display: "grid", gap: 4, fontSize: ".8rem" }}><span>Email</span><input name="contact_email" type="email" style={inputStyle} /></label>
          <label style={{ display: "grid", gap: 4, fontSize: ".8rem" }}><span>Relationship</span><select name="relationship_type" defaultValue="outreach" style={inputStyle}><option value="earned">Earned mention</option><option value="outreach">Editorial outreach</option><option value="partner">Genuine partner</option><option value="paid">Paid</option><option value="reciprocal">Reciprocal</option></select></label>
        </div>
        <label style={{ display: "grid", gap: 4, fontSize: ".8rem", marginTop: 10 }}><span>Why this site would genuinely value the resource</span><textarea name="outreach_angle" rows={2} style={inputStyle} /></label>
        <label style={{ display: "grid", gap: 4, fontSize: ".8rem", marginTop: 10 }}><span>Notes</span><textarea name="notes" rows={2} style={inputStyle} /></label>
        <input type="hidden" name="status" value="prospect" />
        <button type="submit" disabled={busy} style={{ ...primaryButton, marginTop: 12 }}>Add prospect</button>
      </Form>
      {data.backlinks.length ? <div style={{ display: "grid", gap: 10, marginTop: 18 }}>{data.backlinks.map(item => <article key={item.id} style={{ border: "1px solid #e5e7eb", borderRadius: 12, padding: 13 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}><strong>{item.prospect_url ? <a href={item.prospect_url} target="_blank" rel="noreferrer">{item.domain}</a> : item.domain}</strong><span style={{ color: item.relationship_type === "reciprocal" || item.relationship_type === "paid" ? "#b45309" : "#6b7280", fontSize: ".78rem" }}>{item.relationship_type}</span></div>
        <Form method="post" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8, marginTop: 10 }}>
          <input type="hidden" name="intent" value="backlink_update" /><input type="hidden" name="store_id" value={data.store_id} /><input type="hidden" name="id" value={item.id} /><input type="hidden" name="domain" value={item.domain} /><input type="hidden" name="prospect_url" value={item.prospect_url} /><input type="hidden" name="contact_name" value={item.contact_name} /><input type="hidden" name="contact_email" value={item.contact_email} /><input type="hidden" name="target_url" value={item.target_url} /><input type="hidden" name="outreach_angle" value={item.outreach_angle} /><input type="hidden" name="relationship_type" value={item.relationship_type} /><input type="hidden" name="notes" value={item.notes} />
          <label style={{ display: "grid", gap: 3, fontSize: ".75rem" }}><span>Status</span><select name="status" defaultValue={item.status} style={{ ...inputStyle, padding: "6px 8px" }}><option value="prospect">Prospect</option><option value="contacted">Contacted</option><option value="replied">Replied</option><option value="won">Link won</option><option value="lost">Lost</option><option value="rejected">Rejected</option></select></label>
          <label style={{ display: "grid", gap: 3, fontSize: ".75rem" }}><span>Live link URL</span><input name="link_url" type="url" defaultValue={item.link_url} placeholder="https://…" style={{ ...inputStyle, padding: "6px 8px" }} /></label>
          <label style={{ display: "grid", gap: 3, fontSize: ".75rem" }}><span>Link rel</span><select name="link_rel" defaultValue={item.link_rel} style={{ ...inputStyle, padding: "6px 8px" }}><option value="">Normal editorial</option><option value="nofollow">nofollow</option><option value="sponsored">sponsored</option><option value="ugc">ugc</option></select></label>
          <div style={{ display: "flex", alignItems: "end" }}><button type="submit" disabled={busy} style={{ border: "1px solid #d1d5db", borderRadius: 999, background: "white", padding: "7px 13px", cursor: "pointer" }}>Save status</button></div>
        </Form>
        <Form method="post" style={{ marginTop: 6 }}><input type="hidden" name="intent" value="backlink_delete" /><input type="hidden" name="store_id" value={data.store_id} /><input type="hidden" name="prospect_id" value={item.id} /><button type="submit" disabled={busy} style={{ border: 0, background: "transparent", color: "#b91c1c", cursor: "pointer", padding: 0, fontSize: ".75rem" }}>Remove</button></Form>
      </article>)}</div> : <p style={{ color: "#6b7280", fontSize: ".82rem" }}>No backlink prospects recorded yet.</p>}
    </s-section>

    {data.history.length ? <s-section heading="Audit history"><div style={{ overflowX: "auto" }}><table style={{ width: "100%", borderCollapse: "collapse", fontSize: ".82rem" }}><thead><tr><th style={{ textAlign: "left", padding: 8 }}>Started</th><th style={{ textAlign: "left", padding: 8 }}>Trigger</th><th style={{ textAlign: "left", padding: 8 }}>Period</th><th style={{ textAlign: "left", padding: 8 }}>Status</th><th style={{ textAlign: "left", padding: 8 }}>Details</th></tr></thead><tbody>{data.history.map(run => <tr key={run.id} style={{ borderTop: "1px solid #e5e7eb" }}><td style={{ padding: 8 }}>{formatDate(run.started_at)}</td><td style={{ padding: 8 }}>{run.trigger_type}</td><td style={{ padding: 8 }}>{run.period_days} days</td><td style={{ padding: 8 }}>{run.status}</td><td style={{ padding: 8 }}>{run.status === "failed" ? `${run.error_type}: ${run.error_message}` : stageLabel(run.stage)}</td></tr>)}</tbody></table></div></s-section> : null}
  </s-page>;
}
