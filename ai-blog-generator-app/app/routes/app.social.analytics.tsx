import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useLoaderData } from "react-router";

import { useSocial } from "../components/social/context";
import { ActionBanner, Badge, Card, EmptyState, Hidden, Kpi, LoadError, TimeText, useBusy } from "../components/social/ui";
import { contentTypeLabel, formatNumber } from "../components/social/vocab";
import {
  errorMessage, runIntent, seFetch, type AnalyticsResponse, type Insights, type RollupRow,
} from "../lib/social-engine.server";
import { authenticate } from "../shopify.server";

export const handle = { title: "Analytics" };

const PERIODS = [7, 30, 90];

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  const requested = Number(new URL(request.url).searchParams.get("days") || 30);
  const days = PERIODS.includes(requested) ? requested : 30;
  try {
    return { days, analytics: await seFetch<AnalyticsResponse>("/analytics", { query: { days } }), error: "" };
  } catch (error) {
    return { days, analytics: null, error: errorMessage(error) };
  }
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  const days = Number(form.get("days") || 30);
  return runIntent<Insights>(intent, async () => {
    if (intent === "insights") {
      const result = await seFetch<{ insights: Insights }>("/analytics/insights", { method: "POST", query: { days }, timeoutMs: 300_000 });
      return { message: "Gemma analysed your results.", data: result.insights };
    }
    if (intent === "collect") {
      const result = await seFetch<{ already_queued: boolean }>("/analytics/collect", { method: "POST" });
      return { message: result.already_queued ? "Collection is already queued." : "Collecting the latest numbers from Postiz. Refresh in a minute." };
    }
    throw new Error(`Unknown action "${intent}".`);
  });
};

function RollupTable({ title, rows, labelHeader = "Name", max = 15 }: { title: string; rows: RollupRow[]; labelHeader?: string; max?: number }) {
  if (!rows.length) return null;
  const best = Math.max(...rows.map((row) => row.engagement_rate || 0), 0.01);
  return (
    <div className="sa-stack-sm">
      <h3>{title}</h3>
      <div className="sa-table-wrap">
        <table className="sa-table">
          <thead>
            <tr><th>{labelHeader}</th><th className="num">Posts</th><th className="num">Impressions</th><th className="num">Engagements</th><th className="num">Clicks</th><th>Engagement rate</th></tr>
          </thead>
          <tbody>
            {rows.slice(0, max).map((row) => (
              <tr key={String(row.dimension)}>
                <td className="sa-break">{row.label || String(row.dimension ?? "—")}</td>
                <td className="num">{formatNumber(row.targets)}</td>
                <td className="num">{formatNumber(row.impressions || row.views || row.reach)}</td>
                <td className="num">{formatNumber(row.engagement)}</td>
                <td className="num">{formatNumber(row.clicks)}</td>
                <td>
                  <div className="sa-row" style={{ flexWrap: "nowrap" }}>
                    <div className="sa-bar" style={{ width: 80 }}><span style={{ width: `${Math.min(100, (row.engagement_rate / best) * 100)}%` }} /></div>
                    <span className="sa-nowrap">{row.engagement_rate.toFixed(2)}%</span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function AnalyticsPage() {
  const { days, analytics, error } = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>();
  const { tz, contentTypes } = useSocial();
  const { busy, isSubmitting } = useBusy();
  const insights: Insights | null = (result?.intent === "insights" && result.ok ? result.data : null) || analytics?.insights || null;
  const report = analytics?.report;
  const totals = report?.totals || {};
  const hasData = Boolean(report && (report.channel.length || report.top_posts.length));

  return (
    <>
      <ActionBanner result={result} />
      <LoadError error={error} />
      <s-section>
        <div className="sa-spread">
          <div className="sa-row">
            {PERIODS.map((period) => (
              <Link key={period} to={`?days=${period}`} className={`sa-btn sa-btn-sm ${period === days ? "sa-btn-primary" : ""}`} preventScrollReset>Last {period} days</Link>
            ))}
          </div>
          <div className="sa-row">
            <Form method="post"><Hidden values={{ intent: "collect", days }} /><button type="submit" className="sa-btn" disabled={busy}>{isSubmitting("collect") ? "Starting…" : "Collect now"}</button></Form>
            <Form method="post"><Hidden values={{ intent: "insights", days }} /><button type="submit" className="sa-btn sa-btn-primary" disabled={busy || !hasData}>{isSubmitting("insights") ? "Gemma is thinking… (up to a few minutes)" : "Ask Gemma for insights"}</button></Form>
          </div>
        </div>
      </s-section>

      {report ? (
        <s-section>
          <div className="sa-grid-sm">
            <Kpi label="Impressions" value={formatNumber(totals.impressions || totals.views)} />
            <Kpi label="Reach" value={formatNumber(totals.reach)} />
            <Kpi label="Engagements" value={formatNumber(totals.engagement)} detail={`${formatNumber(totals.likes)} likes · ${formatNumber(totals.comments)} comments`} />
            <Kpi label="Clicks" value={formatNumber(totals.clicks)} />
            <Kpi label="Engagement rate" value={`${Number(totals.engagement_rate || 0).toFixed(2)}%`} detail="Engagements ÷ impressions" />
          </div>
        </s-section>
      ) : null}

      {insights?.summary || insights?.recommendations?.length ? (
        <s-section heading="Gemma's insights">
          <Card>
            <div className="sa-stack-sm">
              {insights.summary ? <p>{insights.summary}</p> : null}
              {insights.recommendations?.length ? (
                <ul style={{ margin: 0, paddingLeft: 18 }}>{insights.recommendations.map((item, index) => <li key={index}>{item}</li>)}</ul>
              ) : null}
              {insights.best_hours?.length ? <div className="sa-small"><span className="sa-muted">Best hours:</span> {insights.best_hours.map((hour) => `${String(hour).padStart(2, "0")}:00`).join(", ")}</div> : null}
              {insights.content_type_weights && Object.keys(insights.content_type_weights).length ? (
                <div className="sa-row sa-small">
                  <span className="sa-muted">Suggested mix:</span>
                  {Object.entries(insights.content_type_weights).map(([type, weight]) => (
                    <Badge key={type} tone={weight > 1.05 ? "success" : weight < 0.95 ? "warning" : "neutral"}>{contentTypeLabel(type, contentTypes)} ×{Number(weight).toFixed(1)}</Badge>
                  ))}
                </div>
              ) : null}
              {insights.generated_at ? <span className="sa-small sa-muted">Generated <TimeText epoch={insights.generated_at} tz={tz} relative /></span> : null}
            </div>
          </Card>
        </s-section>
      ) : null}

      {report && hasData ? (
        <>
          <s-section heading="Top posts">
            {report.top_posts.length ? (
              <div className="sa-table-wrap">
                <table className="sa-table">
                  <thead><tr><th>Post</th><th>Type</th><th>Published</th><th className="num">Impressions</th><th className="num">Engagements</th><th className="num">Clicks</th><th className="num">Rate</th></tr></thead>
                  <tbody>
                    {report.top_posts.map((post) => (
                      <tr key={post.post_id}>
                        <td><Link to={`/app/social/posts/${post.post_id}`}>{post.title || "Untitled post"}</Link></td>
                        <td>{contentTypeLabel(post.content_type, contentTypes)}</td>
                        <td className="sa-nowrap"><TimeText epoch={post.published_at} tz={tz} /></td>
                        <td className="num">{formatNumber(post.impressions || post.views)}</td>
                        <td className="num">{formatNumber(post.engagement)}</td>
                        <td className="num">{formatNumber(post.clicks)}</td>
                        <td className="num">{post.engagement_rate.toFixed(2)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="sa-muted">No published posts with numbers in this period.</p>}
          </s-section>
          <s-section heading="What works">
            <div className="sa-stack-lg">
              <RollupTable title="By channel" rows={report.channel} labelHeader="Channel" />
              <RollupTable title="By content type" rows={report.content_type} labelHeader="Content type" />
              <RollupTable title="By product" rows={report.product} labelHeader="Product" />
              <RollupTable title="By template" rows={report.template} labelHeader="Template" />
              <RollupTable title="By campaign" rows={report.campaign} labelHeader="Campaign" />
              <RollupTable title="By hour posted" rows={[...report.hour].sort((a, b) => Number(a.dimension) - Number(b.dimension))} labelHeader="Hour" max={24} />
            </div>
          </s-section>
          {analytics?.learned ? (
            <s-section heading="What the autopilot has learned">
              <div className="sa-stack-sm">
                <p className="sa-muted">From the last 60 days (needs at least 3 posts per group). Gemma uses this when planning.</p>
                {Object.keys(analytics.learned.content_type_rates).length ? (
                  <div className="sa-row">
                    {Object.entries(analytics.learned.content_type_rates).sort(([, a], [, b]) => b - a).map(([type, rate]) => (
                      <Badge key={type} tone="info">{contentTypeLabel(type, contentTypes)}: {rate.toFixed(2)}%</Badge>
                    ))}
                  </div>
                ) : <span className="sa-small sa-muted">Not enough data per content type yet.</span>}
                <div className="sa-small">
                  <span className="sa-muted">Best hours to post:</span>{" "}
                  {analytics.learned.best_hours.length ? analytics.learned.best_hours.map((hour) => `${String(hour).padStart(2, "0")}:00`).join(", ") : "not enough data yet"}
                </div>
              </div>
            </s-section>
          ) : null}
        </>
      ) : analytics ? (
        <s-section>
          <EmptyState title="No numbers yet">
            Analytics appear once posts are published and Postiz reports their results (collected automatically a few hours after publishing). Press “Collect now” to fetch them immediately.
          </EmptyState>
        </s-section>
      ) : null}
    </>
  );
}
