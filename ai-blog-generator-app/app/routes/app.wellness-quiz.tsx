import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, useActionData, useLoaderData, useNavigation } from "react-router";
import { authenticate } from "../shopify.server";
import { wellnessQuizBackendFetch, wellnessQuizBackendConfigured } from "../lib/wellness-quiz.server";

type Goal = { id: string; label: string; description: string };
type QuizProduct = { handle: string; title: string; available: boolean; goal_scores: Record<string, number>; landing_page_url?: string; guide_url?: string };
type QuizData = {
  store_id: string;
  shop: string;
  storefront_domain: string;
  config: { goals: Goal[] };
  summary: {
    period_days: number; starts: number; completions: number; completion_rate: number;
    recommendation_clickers: number; click_through_rate: number; catalogue_products: number;
    last_synced: number; goals: Array<{ goal: string; completions: number }>;
    top_products: Array<{ product_handle: string; clicks: number }>;
  };
  goal_counts: Record<string, number>;
  products: QuizProduct[];
};

const EMPTY: QuizData = {
  store_id: "", shop: "", storefront_domain: "", config: { goals: [] },
  summary: { period_days: 90, starts: 0, completions: 0, completion_rate: 0, recommendation_clickers: 0, click_through_rate: 0, catalogue_products: 0, last_synced: 0, goals: [], top_products: [] },
  goal_counts: {}, products: [],
};

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const context = await authenticate.admin(request);
  const shop = context.session.shop;
  if (!wellnessQuizBackendConfigured()) return { data: EMPTY, error: "Backend API key is not configured.", apiKey: "", shop };
  try {
    const data = await wellnessQuizBackendFetch(`/api/wellness-quiz?shop=${encodeURIComponent(shop)}&period_days=90`) as unknown as QuizData;
    return { data, error: "", apiKey: process.env.SHOPIFY_API_KEY || "", shop };
  } catch (error) {
    return { data: EMPTY, error: error instanceof Error ? error.message : "Could not load Wellness Quiz.", apiKey: process.env.SHOPIFY_API_KEY || "", shop };
  }
};

export const action = async ({ request }: ActionFunctionArgs) => {
  const context = await authenticate.admin(request);
  if (!wellnessQuizBackendConfigured()) return { ok: false, error: "Backend API key is not configured." };
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  try {
    if (intent === "sync") {
      const result = await wellnessQuizBackendFetch("/api/wellness-quiz/sync", {
        method: "POST",
        body: JSON.stringify({ shop: context.session.shop }),
      });
      return { ok: true, message: `Catalogue ready: ${Number(result.available || 0)} recommendable products synced.` };
    }
    return { ok: false, error: "Unknown action." };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "Quiz action failed." };
  }
};

function Alert({ tone, children }: { tone: "success" | "error" | "info"; children: React.ReactNode }) {
  const colours = tone === "success" ? ["#ecfdf5", "#a7f3d0", "#065f46"] : tone === "error" ? ["#fef2f2", "#fecaca", "#991b1b"] : ["#eff6ff", "#bfdbfe", "#1e40af"];
  return <div style={{ background: colours[0], border: `1px solid ${colours[1]}`, color: colours[2], borderRadius: 12, padding: "12px 16px", fontSize: ".875rem" }}>{children}</div>;
}

function Metric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return <div style={{ border: "1px solid #e5e7eb", borderRadius: 14, padding: 16, background: "white" }}>
    <div style={{ color: "#6b7280", fontSize: ".72rem", textTransform: "uppercase", letterSpacing: ".05em", fontWeight: 700 }}>{label}</div>
    <div style={{ fontSize: "1.55rem", fontWeight: 750, marginTop: 5 }}>{value}</div>
    {detail ? <div style={{ color: "#6b7280", fontSize: ".78rem", marginTop: 2 }}>{detail}</div> : null}
  </div>;
}

function dateTime(value: number) {
  return value ? new Date(value * 1000).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : "Not synced yet";
}

export default function WellnessQuizAdmin() {
  const { data, error, apiKey, shop } = useLoaderData<typeof loader>();
  const actionData = useActionData<typeof action>() as { ok?: boolean; message?: string; error?: string } | undefined;
  const navigation = useNavigation();
  const syncing = navigation.state !== "idle" && navigation.formData?.get("intent") === "sync";
  const goalLabels = Object.fromEntries(data.config.goals.map(goal => [goal.id, goal.label]));
  const themeUrl = `https://${shop}/admin/themes/current/editor?template=index&addAppBlockId=${encodeURIComponent(apiKey)}/wellness-quiz&target=newAppsSection`;

  return <s-page heading="Wellness Quiz & Routine Builder">
    {error ? <s-section><Alert tone="error">{error}</Alert></s-section> : null}
    {actionData?.message ? <s-section><Alert tone="success">{actionData.message}</Alert></s-section> : null}
    {actionData?.error ? <s-section><Alert tone="error">{actionData.error}</Alert></s-section> : null}

    <s-section heading="Native Shopify quiz, connected to Intelligence">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 18, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div style={{ maxWidth: 720, color: "#4b5563", lineHeight: 1.55 }}>
          Visitors answer four quick questions directly in your theme and immediately receive a three-step routine. Results prefer the product&apos;s landing page and can also surface its related guide. Anonymous, consent-aware behaviour feeds Customer Intelligence.
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Form method="post"><input type="hidden" name="intent" value="sync" /><button type="submit" disabled={syncing} style={{ border: 0, borderRadius: 999, background: "#111827", color: "white", padding: "10px 18px", fontWeight: 700, cursor: "pointer", opacity: syncing ? .65 : 1 }}>{syncing ? "Syncing…" : "Sync product catalogue"}</button></Form>
          <a href={themeUrl} target="_top" style={{ border: "1px solid #c9cccf", borderRadius: 999, color: "#202223", padding: "9px 17px", fontWeight: 700, textDecoration: "none" }}>Add quiz to theme</a>
        </div>
      </div>
      <div style={{ marginTop: 12, color: "#6b7280", fontSize: ".8rem" }}>Last catalogue sync: {dateTime(data.summary.last_synced)}. Refresh after products, availability, prices, landing pages or guide links change.</div>
    </s-section>

    <s-section heading="Last 90 days">
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(145px, 1fr))", gap: 10 }}>
        <Metric label="Quiz starts" value={data.summary.starts.toLocaleString("en-GB")} />
        <Metric label="Completed" value={data.summary.completions.toLocaleString("en-GB")} detail={`${data.summary.completion_rate.toFixed(1)}% completion`} />
        <Metric label="Recommendation clicks" value={data.summary.recommendation_clickers.toLocaleString("en-GB")} detail={`${data.summary.click_through_rate.toFixed(1)}% of completions`} />
        <Metric label="Catalogue products" value={data.summary.catalogue_products.toLocaleString("en-GB")} detail={`${data.products.filter(item => item.available).length} currently recommendable`} />
      </div>
    </s-section>

    <s-section heading="Routine coverage">
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
        {data.config.goals.map(goal => <div key={goal.id} style={{ border: "1px solid #e5e7eb", borderRadius: 12, padding: 14, background: "white" }}>
          <strong>{goal.label}</strong><div style={{ color: "#6b7280", fontSize: ".8rem", marginTop: 4 }}>{data.goal_counts[goal.id] || 0} matching available products</div>
        </div>)}
      </div>
      {!data.summary.catalogue_products ? <div style={{ marginTop: 12 }}><Alert tone="info">Sync the product catalogue before adding the block to the live theme.</Alert></div> : null}
    </s-section>

    <s-section heading="What customers are asking for">
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 18 }}>
        <div><h3 style={{ marginTop: 0, fontSize: "1rem" }}>Completed quiz goals</h3>{data.summary.goals.length ? data.summary.goals.map(row => <div key={row.goal} style={{ display: "flex", justifyContent: "space-between", borderTop: "1px solid #e5e7eb", padding: "9px 0", fontSize: ".85rem" }}><span>{goalLabels[row.goal] || row.goal}</span><strong>{row.completions}</strong></div>) : <div style={{ color: "#6b7280", fontSize: ".85rem" }}>No completed quizzes yet.</div>}</div>
        <div><h3 style={{ marginTop: 0, fontSize: "1rem" }}>Most-clicked recommendations</h3>{data.summary.top_products.length ? data.summary.top_products.map(row => <div key={row.product_handle} style={{ display: "flex", justifyContent: "space-between", gap: 12, borderTop: "1px solid #e5e7eb", padding: "9px 0", fontSize: ".85rem" }}><span>{row.product_handle}</span><strong>{row.clicks}</strong></div>) : <div style={{ color: "#6b7280", fontSize: ".85rem" }}>Recommendation clicks will appear here.</div>}</div>
      </div>
    </s-section>

    <s-section heading="Privacy and recommendation safety">
      <p style={{ color: "#4b5563", lineHeight: 1.55 }}>The quiz asks about wellness goals and shopping preferences—not diagnoses, medication or sensitive medical history. It works without tracking; analytics events are sent only when Shopify says analytics processing is allowed. Recommendations are based on current catalogue facts and availability, not medical advice.</p>
    </s-section>
  </s-page>;
}
