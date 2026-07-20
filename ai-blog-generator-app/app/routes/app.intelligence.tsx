import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, useActionData, useLoaderData, useNavigation } from "react-router";
import { authenticate } from "../shopify.server";

const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

type Recommendation = {
  id: string; severity: "high" | "medium" | "low"; category: string;
  title: string; evidence: string; action: string; confidence: string;
  impact: string; effort: string;
};
type Funnel = {
  sessions: number; cart_additions: number; checkouts: number; purchases: number;
  conversion_rate: number; add_to_cart_rate: number; checkout_completion_rate: number;
  total_sales: number; average_order_value: number;
};
type SourceRow = { referrer_source?: string; sessions: number; purchases: number; conversion_rate: number };
type Summary = {
  period_days?: number; executive_summary?: string;
  data_sufficiency?: { level: string; message: string; sessions: number };
  shopify?: {
    funnel?: Funnel; sources?: SourceRow[];
    catalog?: Record<string, number>; content?: { blogs: number; articles: number };
  };
  ga4?: {
    connected?: boolean; property_id?: string; status?: string;
    channels?: Array<Record<string, string | number>>;
    devices?: Array<Record<string, string | number>>;
  };
};
type IntelligenceData = {
  store_id: string;
  latest: null | { id: string; status: string; started_at: number; completed_at?: number; error_message?: string; summary: Summary };
  recommendations: Recommendation[];
  history: Array<{ id: string; status: string; trigger_type: string; period_days: number; started_at: number }>;
  settings: { ga4_property_id: string; ga4_credentials_saved: boolean; auto_enabled: boolean; period_days: number };
};

async function backendFetch(path: string, opts: RequestInit = {}) {
  const response = await fetch(`${BACKEND_URL}${path}`, {
    ...opts,
    headers: { "x-api-key": BACKEND_KEY, "content-type": "application/json", ...(opts.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = `Backend ${response.status}`;
    try {
      const body = await response.json() as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      const text = await response.text().catch(() => "");
      if (text) detail = text.slice(0, 300);
    }
    throw new Error(detail);
  }
  return response.json() as Promise<Record<string, unknown>>;
}

const emptyData: IntelligenceData = {
  store_id: "", latest: null, recommendations: [], history: [],
  settings: { ga4_property_id: "", ga4_credentials_saved: false, auto_enabled: true, period_days: 90 },
};

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  if (!BACKEND_KEY) return { backendConfigured: false, data: emptyData, error: "Backend API key is not configured." };
  try {
    const init = await backendFetch("/api/init") as { store_id?: string };
    const storeId = String(init.store_id || "");
    const data = await backendFetch(`/api/intelligence?store_id=${encodeURIComponent(storeId)}`) as unknown as IntelligenceData;
    return { backendConfigured: true, data, error: "" };
  } catch (error) {
    return { backendConfigured: true, data: emptyData, error: error instanceof Error ? error.message : "Could not load intelligence data." };
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
      await backendFetch("/api/intelligence/run", {
        method: "POST",
        body: JSON.stringify({ store_id: storeId, period_days: Number(form.get("period_days") || 90) }),
      });
      return { ok: true, intent, message: "Analysis complete. Recommendations have been refreshed." };
    }
    if (intent === "settings") {
      await backendFetch("/api/intelligence/settings", {
        method: "POST",
        body: JSON.stringify({
          store_id: storeId,
          ga4_property_id: String(form.get("ga4_property_id") || ""),
          ga4_service_account_json: String(form.get("ga4_service_account_json") || ""),
          clear_ga4_credentials: form.get("clear_ga4_credentials") === "1",
          auto_enabled: form.get("auto_enabled") === "1",
          period_days: Number(form.get("period_days") || 90),
        }),
      });
      return { ok: true, intent, message: "Connections and automatic refresh settings saved." };
    }
    if (intent === "dismiss") {
      await backendFetch("/api/intelligence/dismiss", {
        method: "POST",
        body: JSON.stringify({ store_id: storeId, recommendation_id: String(form.get("recommendation_id") || "") }),
      });
      return { ok: true, intent, message: "Recommendation dismissed." };
    }
    return { ok: false, error: "Unknown action." };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "Intelligence action failed." };
  }
};

function Alert({ children, tone }: { children: React.ReactNode; tone: "success" | "error" | "info" }) {
  const colours = tone === "success"
    ? { background: "#ecfdf5", border: "#a7f3d0", color: "#065f46" }
    : tone === "error"
      ? { background: "#fef2f2", border: "#fecaca", color: "#991b1b" }
      : { background: "#eff6ff", border: "#bfdbfe", color: "#1e40af" };
  return <div style={{ ...colours, borderWidth: 1, borderStyle: "solid", borderRadius: 12, padding: "12px 16px", fontSize: ".875rem" }}>{children}</div>;
}

function Kpi({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return <div style={{ border: "1px solid #e5e7eb", borderRadius: 14, padding: 16, background: "white" }}>
    <div style={{ color: "#6b7280", fontSize: ".72rem", textTransform: "uppercase", letterSpacing: ".05em", fontWeight: 700 }}>{label}</div>
    <div style={{ fontSize: "1.55rem", fontWeight: 750, marginTop: 5 }}>{value}</div>
    {detail ? <div style={{ color: "#6b7280", fontSize: ".78rem", marginTop: 2 }}>{detail}</div> : null}
  </div>;
}

function formatDate(value?: number) {
  return value ? new Date(value * 1000).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : "—";
}

const inputStyle: React.CSSProperties = { width: "100%", boxSizing: "border-box", border: "1px solid #c9cccf", borderRadius: 8, padding: "9px 11px", font: "inherit", background: "white" };
const buttonStyle: React.CSSProperties = { border: 0, borderRadius: 999, background: "#111827", color: "white", padding: "10px 20px", fontWeight: 700, cursor: "pointer" };

export default function IntelligenceRoute() {
  const { backendConfigured, data, error } = useLoaderData<typeof loader>();
  const actionData = useActionData<typeof action>() as { ok?: boolean; error?: string; message?: string; intent?: string } | undefined;
  const navigation = useNavigation();
  const busy = navigation.state !== "idle";
  const summary = data.latest?.summary || {};
  const funnel = summary.shopify?.funnel;
  const catalogue = summary.shopify?.catalog || {};
  const sources = summary.shopify?.sources || [];
  const ga4 = summary.ga4 || {};

  return <s-page heading="Customer Intelligence">
    {!backendConfigured || error ? <s-section><Alert tone="error">{error || "Backend connection is unavailable."}</Alert></s-section> : null}
    {actionData?.message ? <s-section><Alert tone={actionData.ok ? "success" : "error"}>{actionData.message}</Alert></s-section> : null}
    {actionData?.error ? <s-section><Alert tone="error">{actionData.error}</Alert></s-section> : null}

    <s-section heading="Store behaviour and recommendations">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 18, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div style={{ maxWidth: 700, color: "#4b5563", lineHeight: 1.55 }}>
          Shopify funnel data, Meta/social attribution and catalogue health are measured directly. Grok ranks only verified findings and is never given customer identifiers.
        </div>
        <Form method="post" style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input type="hidden" name="intent" value="run" /><input type="hidden" name="store_id" value={data.store_id} />
          <select name="period_days" defaultValue={String(data.settings.period_days)} style={{ ...inputStyle, width: 120 }}>
            <option value="30">30 days</option><option value="90">90 days</option><option value="180">180 days</option><option value="365">365 days</option>
          </select>
          <button type="submit" disabled={busy || !backendConfigured} style={{ ...buttonStyle, opacity: busy ? .6 : 1 }}>{busy && navigation.formData?.get("intent") === "run" ? "Analysing…" : "Run analysis"}</button>
        </Form>
      </div>
    </s-section>

    {data.latest?.status === "failed" ? <s-section><Alert tone="error">Last analysis failed: {data.latest.error_message}</Alert></s-section> : null}
    {funnel ? <>
      <s-section heading={`Latest ${summary.period_days || data.settings.period_days}-day snapshot`}>
        <div style={{ marginBottom: 14 }}><Alert tone={summary.data_sufficiency?.level === "limited" ? "info" : "success"}>
          <strong style={{ textTransform: "capitalize" }}>{summary.data_sufficiency?.level || "Measured"} confidence.</strong> {summary.data_sufficiency?.message} Updated {formatDate(data.latest?.completed_at)}.
        </Alert></div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(135px, 1fr))", gap: 10 }}>
          <Kpi label="Sessions" value={funnel.sessions.toLocaleString("en-GB")} />
          <Kpi label="Added to cart" value={String(funnel.cart_additions)} detail={`${funnel.add_to_cart_rate.toFixed(2)}% of sessions`} />
          <Kpi label="Checkouts" value={String(funnel.checkouts)} />
          <Kpi label="Purchases" value={String(funnel.purchases)} />
          <Kpi label="Conversion" value={`${funnel.conversion_rate.toFixed(2)}%`} />
          <Kpi label="Shopify sales" value={funnel.total_sales.toLocaleString("en-GB", { style: "currency", currency: "GBP" })} />
        </div>
        {summary.executive_summary ? <div style={{ marginTop: 14, padding: 16, borderRadius: 12, background: "#f3f4f6", lineHeight: 1.55 }}><strong>Grok management summary</strong><div style={{ marginTop: 5, color: "#4b5563" }}>{summary.executive_summary}</div></div> : null}
      </s-section>

      <s-section heading={`Prioritised recommendations (${data.recommendations.length})`}>
        <div style={{ color: "#6b7280", marginBottom: 10, fontSize: ".875rem" }}>Every recommendation shows the measured evidence, confidence, likely impact and implementation effort.</div>
        <div style={{ display: "grid", gap: 12 }}>
          {data.recommendations.map((item, index) => {
            const colour = item.severity === "high" ? "#dc2626" : item.severity === "medium" ? "#d97706" : "#16a34a";
            return <article key={item.id} style={{ border: "1px solid #e5e7eb", borderLeft: `4px solid ${colour}`, borderRadius: 12, padding: 16, background: "white" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}><h3 style={{ margin: 0, fontSize: "1rem" }}>{index + 1}. {item.title}</h3><span style={{ color: colour, fontWeight: 800, fontSize: ".7rem", textTransform: "uppercase" }}>{item.severity}</span></div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, margin: "9px 0" }}>{[item.category, `Confidence: ${item.confidence}`, `Impact: ${item.impact}`, `Effort: ${item.effort}`].map(tag => <span key={tag} style={{ background: "#f3f4f6", borderRadius: 999, padding: "3px 8px", fontSize: ".72rem", color: "#4b5563" }}>{tag}</span>)}</div>
              <div style={{ lineHeight: 1.5, fontSize: ".875rem" }}><strong>Evidence:</strong> {item.evidence}</div>
              <div style={{ lineHeight: 1.5, fontSize: ".875rem", color: "#4b5563", marginTop: 6 }}><strong>Recommended action:</strong> {item.action}</div>
              <Form method="post" style={{ marginTop: 10 }}><input type="hidden" name="intent" value="dismiss" /><input type="hidden" name="store_id" value={data.store_id} /><input type="hidden" name="recommendation_id" value={item.id} /><button type="submit" disabled={busy} style={{ border: "1px solid #d1d5db", borderRadius: 999, background: "white", padding: "6px 12px", cursor: "pointer", fontSize: ".75rem" }}>Dismiss</button></Form>
            </article>;
          })}
          {!data.recommendations.length ? <Alert tone="info">No recommendation crossed its evidence and sample-size threshold.</Alert> : null}
        </div>
      </s-section>

      <s-section heading="Acquisition and store health">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 18 }}>
          <div><h3 style={{ marginTop: 0 }}>Shopify attribution</h3><p style={{ color: "#6b7280", fontSize: ".82rem" }}>Meta traffic is included in Shopify’s social attribution.</p><table style={{ width: "100%", borderCollapse: "collapse", fontSize: ".82rem" }}><thead><tr><th style={{ textAlign: "left", padding: 8 }}>Source</th><th style={{ textAlign: "right", padding: 8 }}>Sessions</th><th style={{ textAlign: "right", padding: 8 }}>Purchases</th><th style={{ textAlign: "right", padding: 8 }}>CVR</th></tr></thead><tbody>{sources.map(row => <tr key={row.referrer_source || "unknown"} style={{ borderTop: "1px solid #e5e7eb" }}><td style={{ padding: 8 }}>{row.referrer_source || "Unknown"}</td><td style={{ textAlign: "right", padding: 8 }}>{row.sessions}</td><td style={{ textAlign: "right", padding: 8 }}>{row.purchases}</td><td style={{ textAlign: "right", padding: 8 }}>{row.conversion_rate.toFixed(2)}%</td></tr>)}</tbody></table></div>
          <div><h3 style={{ marginTop: 0 }}>Catalogue and content</h3><div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {[['Products', catalogue.products], ['Active', catalogue.active_products], ['Missing SEO descriptions', catalogue.missing_seo_descriptions], ['Invalid product types', catalogue.invalid_product_types], ['Active with zero stock', catalogue.active_zero_stock], ['Blog articles', summary.shopify?.content?.articles]].map(([label, value]) => <div key={String(label)} style={{ padding: 12, background: "#f9fafb", borderRadius: 10 }}><strong style={{ display: "block", fontSize: "1.2rem" }}>{Number(value || 0).toLocaleString("en-GB")}</strong><span style={{ color: "#6b7280", fontSize: ".75rem" }}>{label}</span></div>)}
          </div></div>
        </div>
      </s-section>
    </> : <s-section><Alert tone="info">No analysis yet. Choose a period and run the first analysis to create the store baseline.</Alert></s-section>}

    <s-section heading="GA4 and automatic refresh">
      <p style={{ color: "#4b5563", lineHeight: 1.5 }}>Shopify is connected. The Meta Shopify app contributes social/referral behaviour. Add read-only GA4 access for channel, device and landing-page analysis; no customer-level data is requested.</p>
      {ga4.connected ? <div style={{ marginBottom: 12 }}><Alert tone="success">GA4 property {ga4.property_id} is connected to the latest report.</Alert></div> : ga4.status && data.latest ? <div style={{ marginBottom: 12 }}><Alert tone="info">GA4: {ga4.status}</Alert></div> : null}
      <Form method="post"><input type="hidden" name="intent" value="settings" /><input type="hidden" name="store_id" value={data.store_id} />
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
          <label style={{ display: "grid", gap: 5, fontSize: ".85rem" }}><span>GA4 property ID</span><input name="ga4_property_id" defaultValue={data.settings.ga4_property_id} placeholder="123456789" style={inputStyle} /></label>
          <label style={{ display: "grid", gap: 5, fontSize: ".85rem" }}><span>Automatic analysis period</span><select name="period_days" defaultValue={String(data.settings.period_days)} style={inputStyle}><option value="30">30 days</option><option value="90">90 days</option><option value="180">180 days</option><option value="365">365 days</option></select></label>
        </div>
        <label style={{ display: "grid", gap: 5, fontSize: ".85rem", marginTop: 12 }}><span>New read-only service-account JSON {data.settings.ga4_credentials_saved ? "(credentials already saved)" : ""}</span><textarea name="ga4_service_account_json" rows={4} style={{ ...inputStyle, resize: "vertical", fontFamily: "monospace", fontSize: ".75rem" }} placeholder="Leave blank to keep existing credentials" /></label>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 18, margin: "12px 0", fontSize: ".85rem" }}><label><input type="checkbox" name="auto_enabled" value="1" defaultChecked={data.settings.auto_enabled} /> Refresh automatically every 24 hours</label>{data.settings.ga4_credentials_saved ? <label><input type="checkbox" name="clear_ga4_credentials" value="1" /> Remove saved GA4 credentials</label> : null}</div>
        <button type="submit" disabled={busy || !backendConfigured} style={{ ...buttonStyle, opacity: busy ? .6 : 1 }}>{busy && navigation.formData?.get("intent") === "settings" ? "Saving…" : "Save connections"}</button>
      </Form>
    </s-section>

    {data.history.length ? <s-section heading="Analysis history"><div style={{ overflowX: "auto" }}><table style={{ width: "100%", borderCollapse: "collapse", fontSize: ".82rem" }}><thead><tr><th style={{ textAlign: "left", padding: 9 }}>Started</th><th style={{ textAlign: "left", padding: 9 }}>Trigger</th><th style={{ textAlign: "left", padding: 9 }}>Period</th><th style={{ textAlign: "left", padding: 9 }}>Status</th></tr></thead><tbody>{data.history.map(run => <tr key={run.id} style={{ borderTop: "1px solid #e5e7eb" }}><td style={{ padding: 9 }}>{formatDate(run.started_at)}</td><td style={{ padding: 9, textTransform: "capitalize" }}>{run.trigger_type}</td><td style={{ padding: 9 }}>{run.period_days} days</td><td style={{ padding: 9, textTransform: "capitalize" }}>{run.status}</td></tr>)}</tbody></table></div></s-section> : null}
  </s-page>;
}
