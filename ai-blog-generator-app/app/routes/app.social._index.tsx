import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useLoaderData } from "react-router";

import { NetworkDot } from "../components/social/ChannelChip";
import { useSocial } from "../components/social/context";
import {
  ActionBanner, Badge, Banner, Card, EmptyState, Hidden, Kpi, LoadError, StatusBadge, TimeText, usePolling, useBusy,
} from "../components/social/ui";
import { DailySlotsGrid } from "../components/social/DailyFill";
import { LiveBadge, PostizCredits, usePostizTools } from "../components/social/PostizParts";
import {
  ACTIVITY_LEVEL, CAMPAIGN_STATUS, CHECK_LABELS, CHECK_STATUS, POST_STATUS, contentTypeLabel, formatNumber, humanize, providerLabel,
} from "../components/social/vocab";
import {
  errorMessage, formBool, runIntent, seFetch, type DoctorReport, type HealthCheck, type OverviewResponse,
} from "../lib/social-engine.server";
import { authenticate } from "../shopify.server";

export const handle = { title: "Overview" };

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  try {
    return { overview: await seFetch<OverviewResponse>("/overview"), error: "" };
  } catch (error) {
    return { overview: null, error: errorMessage(error) };
  }
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  return runIntent<DoctorReport>(intent, async () => {
    switch (intent) {
      case "enable": {
        const enabled = formBool(form, "enabled");
        await seFetch("/settings/enable", { body: { enabled } });
        return {
          message: enabled
            ? "Social Autopilot is on. Channels and your product catalogue are syncing now."
            : "Social Autopilot is off. Nothing new will be planned or published; scheduled posts in Postiz are untouched.",
        };
      }
      case "doctor": {
        const report = await seFetch<DoctorReport>("/doctor", { method: "POST", timeoutMs: 300_000 });
        const problems = report.checks.filter((check) => check.status !== "ok").length;
        return { message: problems ? `Diagnostics finished: ${problems} item(s) need attention (details below).` : "Diagnostics finished: everything looks good.", data: report };
      }
      case "sync_catalog": {
        const result = await seFetch<{ already_queued: boolean }>("/products/sync", { body: { full: false } });
        return { message: result.already_queued ? "A catalogue sync is already queued." : "Catalogue sync started." };
      }
      case "sync_channels": {
        const result = await seFetch<{ already_queued: boolean }>("/channels/sync", { method: "POST" });
        return { message: result.already_queued ? "A channel sync is already queued." : "Channel sync started." };
      }
      case "collect_analytics": {
        const result = await seFetch<{ already_queued: boolean }>("/analytics/collect", { method: "POST" });
        return { message: result.already_queued ? "Analytics collection is already queued." : "Collecting analytics from Postiz." };
      }
      default:
        throw new Error(`Unknown action "${intent}".`);
    }
  });
};

type ChecklistStatus = "ok" | "warning" | "error" | "unknown" | "skipped";
type ChecklistItem = { key: string; label: string; status: ChecklistStatus; message: string; fix?: { to: string; label: string } };

function findCheck(checks: HealthCheck[], name: string): HealthCheck | undefined {
  return checks.find((check) => check.name === name);
}

function buildChecklist(overview: OverviewResponse): ChecklistItem[] {
  const checks = overview.health?.checks || [];
  const live = Boolean(overview.health?.live);
  const items: ChecklistItem[] = [];
  const enabled = overview.settings.enabled;
  items.push({
    key: "enabled", label: "Social Autopilot switched on", status: enabled ? "ok" : "warning",
    message: enabled ? "Campaigns are planning posts." : "Turn it on above when the rest of this list is green.",
  });
  const fill = findCheck(checks, "daily_fill");
  const perDay = overview.settings.schedule.posts_per_channel_per_day;
  items.push({
    key: "daily_fill", label: `Tomorrow fully planned (${perDay} post${perDay === 1 ? "" : "s"} per channel)`,
    status: fill?.status || "unknown",
    message: fill?.message || "Run diagnostics to check tomorrow's slots.",
    fix: fill && fill.status !== "ok" ? { to: "/app/social/campaigns", label: "Campaigns" } : undefined,
  });
  const postiz = findCheck(checks, "postiz");
  items.push({
    key: "postiz", label: "Postiz connected",
    status: postiz?.status || "unknown",
    message: postiz?.message || "Run diagnostics to check the Postiz connection.",
    fix: postiz?.status === "error" ? { to: "/app/social/settings#postiz", label: "Postiz settings" } : undefined,
  });
  const usable = overview.channels.filter((channel) => channel.enabled && !channel.disabled && !channel.missing_at);
  const channelCheck = findCheck(checks, "channels");
  items.push({
    key: "channels", label: "Social channels connected",
    status: usable.length ? (channelCheck?.status === "error" ? "error" : "ok") : "warning",
    message: usable.length
      ? `${usable.length} channel(s) ready: ${usable.map((channel) => `${providerLabel(channel.provider)} (${channel.name})`).join(", ")}`
      : channelCheck?.message || "Connect your networks in Postiz, then press “Sync channels”.",
    fix: { to: "/app/social/channels", label: "Channels" },
  });
  const pinterest = overview.channels.filter((channel) => channel.provider === "pinterest" && channel.enabled && !channel.missing_at);
  if (pinterest.length) {
    const missing = pinterest.filter((channel) => channel.needs_setup);
    items.push({
      key: "pinterest", label: "Pinterest board chosen",
      status: missing.length ? "error" : "ok",
      message: missing.length ? `Choose a board for: ${missing.map((channel) => channel.name).join(", ")}. Pins can't be published without one.` : "Every Pinterest channel has a board.",
      fix: missing.length ? { to: "/app/social/channels", label: "Choose board" } : undefined,
    });
  }
  const gemma = findCheck(checks, "gemma");
  items.push({
    key: "gemma", label: "Gemma (local AI) reachable",
    status: !gemma ? "unknown" : live ? gemma.status : "unknown",
    message: gemma ? (live ? gemma.message : `${gemma.message} — press “Run diagnostics” to test it.`) : "Run diagnostics to test Gemma.",
    fix: gemma?.status === "error" ? { to: "/app/social/settings#ai", label: "AI settings" } : undefined,
  });
  const worker = findCheck(checks, "worker");
  items.push({
    key: "worker", label: "Background worker running",
    status: overview.worker?.alive ? (worker?.status === "warning" ? "warning" : "ok") : "error",
    message: worker?.message || (overview.worker?.alive ? "Worker alive." : "No worker heartbeat — jobs will not run."),
  });
  const shopify = findCheck(checks, "shopify");
  items.push({
    key: "catalog", label: "Product catalogue synced",
    status: shopify?.status || (overview.products.total ? "ok" : "warning"),
    message: shopify?.message || `${formatNumber(overview.products.total)} products synced.`,
    fix: { to: "/app/social/products", label: "Products" },
  });
  const webhooks = findCheck(checks, "webhooks");
  items.push({
    key: "webhooks", label: "Postiz webhook (optional)",
    status: webhooks?.status || "unknown",
    message: webhooks?.message || "Optional: lets Postiz tell us the moment a post goes live.",
    fix: webhooks?.status !== "ok" ? { to: "/app/social/channels#webhook", label: "Set up" } : undefined,
  });
  return items;
}

const ICON: Record<string, string> = { ok: "✓", warning: "!", error: "✕", unknown: "?", skipped: "–" };

function PostizCard() {
  const { tools, error, loading } = usePostizTools();
  return (
    <Card>
      <div className="sa-stack-sm">
        <div className="sa-spread">
          <div className="sa-row"><strong>Postiz</strong>{tools ? <LiveBadge live={tools.live_publishing} /> : null}</div>
          <Link to="/app/social/postiz" className="sa-small">Postiz tools →</Link>
        </div>
        {loading ? <span className="sa-small sa-muted">Checking Postiz credits…</span> : null}
        {!loading && error ? <span className="sa-small sa-error-text">{error}</span> : null}
        {tools ? (
          <>
            <span className="sa-small sa-muted">AI credits used this month</span>
            <PostizCredits budget={tools.budget} />
          </>
        ) : null}
      </div>
    </Card>
  );
}

export default function OverviewPage() {
  const { overview, error } = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>();
  const { tz, contentTypes, rules } = useSocial();
  const { busy, isSubmitting } = useBusy();
  const activeJobs = overview?.active_jobs ?? 0;
  usePolling(activeJobs > 0, 10_000);

  if (!overview) {
    return <s-section><LoadError error={error} /></s-section>;
  }
  const settings = overview.settings;
  const checklist = buildChecklist(overview);
  const report: DoctorReport | undefined = result?.intent === "doctor" && result.data ? result.data : undefined;
  const health = report || overview.health;
  const posts = overview.posts;

  return (
    <>
      <ActionBanner result={result} />

      <s-section>
        <div className="sa-spread">
          <div className="sa-power">
            <span className={`sa-power-light ${settings.enabled ? "on" : ""}`} aria-hidden="true" />
            <div className="sa-stack-sm">
              <h2 style={{ fontSize: "1.05rem" }}>{settings.enabled ? "Social Autopilot is on" : "Social Autopilot is off"}</h2>
              <p className="sa-muted">
                {settings.enabled
                  ? "Active campaigns plan posts, Gemma writes them, and approved posts are scheduled in Postiz."
                  : "Nothing is planned or published automatically. You can still create posts by hand."}
              </p>
              <div className="sa-row">
                {settings.publisher === "dry_run"
                  ? <Badge tone="warning">Practice mode — posts are never sent to Postiz</Badge>
                  : <Badge tone="info">Publishing through Postiz</Badge>}
                <Badge tone={overview.worker?.alive ? "success" : "critical"}>{overview.worker?.alive ? "Worker running" : "Worker not running"}</Badge>
                {activeJobs ? <Badge tone="attention">{activeJobs} job(s) in progress</Badge> : null}
              </div>
            </div>
          </div>
          <Form method="post">
            <Hidden values={{ intent: "enable", enabled: settings.enabled ? "0" : "1" }} />
            <button type="submit" className={`sa-btn ${settings.enabled ? "" : "sa-btn-primary"}`} disabled={busy}>
              {isSubmitting("enable") ? "Saving…" : settings.enabled ? "Turn off" : "Turn on Social Autopilot"}
            </button>
          </Form>
        </div>
      </s-section>

      <s-section heading="Daily slots">
        <div className="sa-split">
          <div className="sa-stack-sm">
            <p className="sa-muted sa-small">
              Every channel posts {settings.schedule.posts_per_channel_per_day} time{settings.schedule.posts_per_channel_per_day === 1 ? "" : "s"} a day
              {settings.schedule.always_fill_daily_slots ? "; SmartQ keeps every slot filled" : ""}. Green = all slots used, amber = slots still free.
            </p>
            {overview.daily_fill?.length ? <DailySlotsGrid days={overview.daily_fill} /> : <p className="sa-muted">No slot information yet.</p>}
            {(() => {
              const fill = (overview.health?.checks || []).find((check) => check.name === "daily_fill");
              return fill && fill.status !== "ok" ? <Banner tone="warning">{fill.message}</Banner> : null;
            })()}
          </div>
          <PostizCard />
        </div>
      </s-section>

      <s-section>
        <div className="sa-grid-sm">
          <Kpi label="Needs approval" value={formatNumber(posts.pending_approval)} to="/app/social/queue" detail={posts.pending_approval ? "Review them now" : "All caught up"} />
          <Kpi label="Scheduled" value={formatNumber((posts.scheduled || 0) + (posts.approved || 0) + (posts.publishing || 0))} to="/app/social/queue?tab=scheduled" />
          <Kpi label="Published" value={formatNumber((posts.published || 0) + (posts.partially_published || 0))} to="/app/social/queue?tab=published" />
          <Kpi label="Failed" value={formatNumber(posts.failed)} to="/app/social/queue?tab=failed" detail={posts.failed ? "Open to see the exact error" : undefined} />
          <Kpi label="Being created" value={formatNumber((posts.planned || 0) + (posts.generating || 0))} to="/app/social/queue?tab=preparing" />
          <Kpi label="Products synced" value={formatNumber(overview.products.total)} detail={`${formatNumber(overview.products.available)} in stock · ${formatNumber(overview.products.on_sale)} on sale`} to="/app/social/products" />
        </div>
      </s-section>

      <s-section heading="Setup checklist">
        <div className="sa-stack">
          <div>
            {checklist.map((item) => (
              <div key={item.key} className="sa-check-item">
                <span className={`sa-check-icon sa-check-icon-${item.status}`} aria-label={CHECK_STATUS[item.status]?.label}>{ICON[item.status]}</span>
                <div className="sa-stack-sm">
                  <strong>{item.label}</strong>
                  <span className="sa-muted sa-break">{item.message}</span>
                </div>
                {item.fix ? <Link to={item.fix.to} className="sa-btn sa-btn-sm">{item.fix.label}</Link> : <span />}
              </div>
            ))}
          </div>
          <div className="sa-spread">
            <span className="sa-small sa-muted">
              {health?.checked_at ? <>Last checked <TimeText epoch={health.checked_at} tz={tz} relative />{health.live ? "" : " (quick check — connections not tested)"}</> : "Not checked yet."}
            </span>
            <Form method="post">
              <Hidden values={{ intent: "doctor" }} />
              <button type="submit" className="sa-btn" disabled={busy}>{isSubmitting("doctor") ? "Running diagnostics… (can take a minute)" : "Run diagnostics"}</button>
            </Form>
          </div>
          {health?.checks?.length ? (
            <details open={Boolean(report)}>
              <summary>All diagnostic checks ({health.checks.length})</summary>
              <div className="sa-table-wrap">
                <table className="sa-table">
                  <thead><tr><th>Check</th><th>Status</th><th>Details</th></tr></thead>
                  <tbody>
                    {health.checks.map((check) => (
                      <tr key={check.name}>
                        <td className="sa-nowrap">{CHECK_LABELS[check.name] || humanize(check.name)}</td>
                        <td><StatusBadge map={CHECK_STATUS} status={check.status} /></td>
                        <td className="sa-break">{check.message}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          ) : null}
        </div>
      </s-section>

      <s-section heading="Quick actions">
        <div className="sa-row">
          {[
            ["sync_catalog", "Sync product catalogue", "Syncing…"],
            ["sync_channels", "Sync channels from Postiz", "Syncing…"],
            ["collect_analytics", "Collect analytics now", "Collecting…"],
          ].map(([intent, label, busyLabel]) => (
            <Form method="post" key={intent}>
              <Hidden values={{ intent }} />
              <button type="submit" className="sa-btn" disabled={busy}>{isSubmitting(intent) ? busyLabel : label}</button>
            </Form>
          ))}
          <Link to="/app/social/studio" className="sa-btn sa-btn-primary">Create a post</Link>
        </div>
      </s-section>

      <s-section heading="Next 14 days">
        {overview.upcoming.length ? (
          <div className="sa-table-wrap">
            <table className="sa-table">
              <thead><tr><th>When</th><th>Network</th><th>Post</th><th>Type</th><th>Status</th></tr></thead>
              <tbody>
                {overview.upcoming.map((item) => (
                  <tr key={item.id}>
                    <td className="sa-nowrap"><TimeText epoch={item.scheduled_at} tz={tz} /></td>
                    <td className="sa-nowrap"><span className="sa-row"><NetworkDot provider={item.provider} /> {providerLabel(item.provider)}</span></td>
                    <td><Link to={`/app/social/posts/${item.post_id}`}>{item.post_title || "Untitled post"}</Link></td>
                    <td>{contentTypeLabel(item.content_type, contentTypes)}</td>
                    <td><StatusBadge map={POST_STATUS} status={item.post_status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="Nothing scheduled yet">
            Turn on a campaign to fill your calendar automatically, or <Link to="/app/social/studio">create a post</Link> by hand.
          </EmptyState>
        )}
        <div style={{ marginTop: 8 }}><Link to="/app/social/calendar">Open the calendar →</Link></div>
      </s-section>

      <s-section heading="Campaigns">
        {overview.campaigns.length ? (
          <div className="sa-stack-sm">
            {overview.campaigns.map((campaign) => (
              <Card key={campaign.id} accent={campaign.last_error ? "critical" : campaign.status === "active" ? "success" : "neutral"}>
                <div className="sa-spread">
                  <div className="sa-row">
                    <strong>{campaign.name}</strong>
                    <StatusBadge map={CAMPAIGN_STATUS} status={campaign.status} />
                    <span className="sa-small sa-muted">{rules[campaign.rule_type]?.label || humanize(campaign.rule_type)}</span>
                  </div>
                  <span className="sa-small sa-muted">
                    Last run <TimeText epoch={campaign.last_evaluated_at} tz={tz} relative />
                    {campaign.status === "active" && campaign.next_evaluation_at ? <> · next <TimeText epoch={campaign.next_evaluation_at} tz={tz} relative /></> : null}
                  </span>
                </div>
                {campaign.last_error ? <div className="sa-error-text sa-break" style={{ marginTop: 6 }}>{campaign.last_error}</div> : null}
              </Card>
            ))}
            <div><Link to="/app/social/campaigns">Manage campaigns →</Link></div>
          </div>
        ) : (
          <EmptyState title="No campaigns yet" action={<Link to="/app/social/campaigns?new=smartq" className="sa-btn sa-btn-primary">Create a SmartQ auto-pilot</Link>}>
            Campaigns post your catalogue automatically: daily product posts, sale alerts, new arrivals, holidays and more.
          </EmptyState>
        )}
      </s-section>

      <s-section heading="Recent activity">
        {overview.activity.length ? (
          <div className="sa-stack-sm">
            {overview.activity.map((item) => (
              <div key={item.id} className="sa-row-top" style={{ borderTop: "1px solid var(--sa-border)", paddingTop: 6 }}>
                <span className="sa-small sa-muted sa-nowrap" style={{ minWidth: 92 }}><TimeText epoch={item.created_at} tz={tz} relative /></span>
                <StatusBadge map={ACTIVITY_LEVEL} status={item.level} />
                <span className="sa-grow sa-break">
                  {item.entity_type === "post" && item.entity_id ? <Link to={`/app/social/posts/${item.entity_id}`}>{item.message}</Link> : item.message}
                </span>
              </div>
            ))}
            <div><Link to="/app/social/activity">All activity →</Link></div>
          </div>
        ) : <p className="sa-muted">No activity yet.</p>}
      </s-section>
    </>
  );
}
