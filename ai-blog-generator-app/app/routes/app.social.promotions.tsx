import { useState } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs, ShouldRevalidateFunction } from "react-router";
import { Form, Link, useActionData, useLoaderData, useSearchParams } from "react-router";

import { NetworkDot } from "../components/social/ChannelChip";
import { useSocial } from "../components/social/context";
import { toPicked, type PickedProduct } from "../components/social/ProductPicker";
import {
  PromotionForm, ThemePicker, daysBetween, formatDay, inDays, type PromotionPrefill,
} from "../components/social/PromotionParts";
import { ActionBanner, Badge, Banner, Card, EmptyState, Hidden, LoadError, StatusBadge, TimeText, useBusy } from "../components/social/ui";
import { PROMOTION_STATUS, approvalLabel, contentTypeLabel, providerLabel } from "../components/social/vocab";
import {
  SocialEngineError, errorMessage, formJson, formString, lookupProducts, runIntent, safeId, seFetch, seg,
  type ActionResult, type CampaignPreview, type Channel, type ChannelsResponse, type Collection, type OccasionDate,
  type ProductsResponse, type PromotionRow, type PromotionTheme, type PromotionsResponse,
} from "../lib/social-engine.server";
import { addDays, formatDateKey, isDateKey, todayKey } from "../lib/social-time";
import { authenticate } from "../shopify.server";

export const handle = { title: "Promotions" };

const EMPTY: PromotionsResponse = {
  today: "", categories: {}, themes: {}, calendar: [], promotions: [], defaults: {}, postiz_budget: {},
};

/**
 * ?edit=<id>                                  → edit that promotion
 * ?theme=<key>&start=<date>&duration=<days>   → new promotion, prefilled (from the occasion calendar or a theme card)
 * ?new=1                                      → new promotion, starting with the theme picker
 */
export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  const url = new URL(request.url);
  const editId = url.searchParams.get("edit") || "";
  const themeKey = url.searchParams.get("theme") || "";
  const start = url.searchParams.get("start") || "";
  const duration = Math.trunc(Number(url.searchParams.get("duration") || 0)) || 0;
  const creating = url.searchParams.has("new") || Boolean(themeKey);
  try {
    const [promotions, channels, catalogue] = await Promise.all([
      seFetch<PromotionsResponse>("/promotions"),
      seFetch<ChannelsResponse>("/channels"),
      seFetch<ProductsResponse>("/products", { query: { page_size: 1 } }).catch(() => ({ collections: [] as Collection[] })),
    ]);
    const editing = editId ? promotions.promotions.find((promotion) => promotion.id === editId) || null : null;
    let products: Record<string, PickedProduct> = {};
    const ids = (editing?.config.product_ids || []).map(String);
    if (ids.length) {
      const found = await lookupProducts(ids);
      products = Object.fromEntries(Object.entries(found).filter(([id]) => ids.includes(id)).map(([id, product]) => [id, toPicked(product)]));
    }
    const knownTheme = Object.values(promotions.themes).flat().some((theme) => theme.key === themeKey);
    const prefill: PromotionPrefill | null = !editId && creating
      ? { theme_key: knownTheme ? themeKey : "", start_date: isDateKey(start) ? start : "", duration_days: Math.min(15, Math.max(0, duration)) }
      : null;
    return {
      ...promotions, channels: channels.channels, collections: catalogue.collections || [], editing, editId, prefill, products, error: "",
    };
  } catch (error) {
    return {
      ...EMPTY, channels: [] as Channel[], collections: [] as Collection[], editing: null as PromotionRow | null, editId,
      prefill: null as PromotionPrefill | null, products: {} as Record<string, PickedProduct>, error: errorMessage(error),
    };
  }
};

/** "Suggest with Gemma" runs through a fetcher: don't reload the page (and lose nothing) for it. */
export const shouldRevalidate: ShouldRevalidateFunction = ({ formData, defaultShouldRevalidate }) =>
  formData?.get("intent") === "suggest" ? false : defaultShouldRevalidate;

type PreviewData = CampaignPreview & { productTitles: Record<string, string> };
type SuggestData = { texts: string[]; source: string };

export const action = async ({ request }: ActionFunctionArgs) => {
  const { redirect } = await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");

  if (intent === "save") {
    let savedId = "";
    const result = await runIntent(intent, async () => {
      const promotion = formJson<Record<string, unknown>>(form, "promotion");
      if (!promotion || typeof promotion !== "object") throw new SocialEngineError("Promotion data is missing.");
      const status = formString(form, "status") === "paused" ? "paused" : "active";
      const saved = await seFetch<{ id: string }>("/promotions", { body: { ...promotion, status } });
      savedId = saved.id;
      return { message: "Promotion saved." };
    });
    if (result.ok && savedId) return redirect(`/app/social/promotions?saved=${encodeURIComponent(savedId)}`);
    return result;
  }

  if (intent === "suggest") {
    return runIntent<SuggestData>(intent, async () => {
      const body = formJson<Record<string, unknown>>(form, "request");
      if (!body || typeof body !== "object" || !body.theme_key) throw new SocialEngineError("Pick a theme first.");
      const suggestion = await seFetch<SuggestData>("/promotions/suggest-texts", { body, timeoutMs: 180_000 });
      return {
        message: suggestion.source === "gemma"
          ? "Gemma wrote these lines for you:"
          : "Gemma isn't available right now, so here are the theme's own lines:",
        data: suggestion,
      };
    });
  }

  const promotionId = formString(form, "promotion_id");
  return runIntent<PreviewData | { cancelled: number }>(intent, async () => {
    const id = safeId(promotionId);
    const base = `/promotions/${seg(id)}`;
    switch (intent) {
      case "stop":
        await seFetch(`${base}/stop`, { body: {} });
        return { message: "Promotion stopped. Nothing new will be planned for it. Posts already planned still go out — use Remove to cancel them." };
      case "resume":
        await seFetch(`${base}/resume`, { body: {} });
        return { message: "Promotion resumed. It's planning its posts now." };
      case "remove": {
        const removed = await seFetch<{ cancelled: number }>(`${base}/remove`, { body: {} });
        return {
          message: `Promotion removed. ${removed.cancelled} unpublished post(s) were cancelled (including any scheduled in Postiz). Published posts stay up.`,
          data: removed,
        };
      }
      case "preview": {
        const preview = await seFetch<CampaignPreview>(`${base}/preview`, { method: "POST", timeoutMs: 300_000 });
        if (preview.error) throw new SocialEngineError(preview.error);
        const found = await lookupProducts((preview.items || []).flatMap((item) => item.product_ids));
        const productTitles = Object.fromEntries(Object.entries(found).map(([key, product]) => [key, product.title]));
        return {
          message: `Dry run: ${preview.planned} post(s) would be planned right now. Nothing was saved or changed.`,
          data: { ...preview, productTitles },
        };
      }
      default:
        throw new SocialEngineError(`Unknown action "${intent}".`);
    }
  }, promotionId || undefined);
};

// ---------------------------------------------------------------------------- helpers

const GROUPS: Array<{ status: string; title: string }> = [
  { status: "running", title: "Running now" },
  { status: "upcoming", title: "Coming up" },
  { status: "paused", title: "Stopped" },
  { status: "invalid", title: "Needs fixing" },
  { status: "ended", title: "Finished" },
];

function countsOf(counts: Record<string, number>) {
  const sum = (keys: string[]) => keys.reduce((total, key) => total + (counts[key] || 0), 0);
  return {
    approval: counts.pending_approval || 0,
    planned: sum(["planned", "generating", "pending_approval", "approved"]),
    scheduled: sum(["publishing", "scheduled"]),
    published: sum(["published", "partially_published"]),
    failed: counts.failed || 0,
    unpublished: sum(["planned", "generating", "pending_approval", "approved", "publishing", "scheduled"]),
  };
}

function occasionLink(occasion: OccasionDate): string {
  const params = new URLSearchParams({ theme: occasion.theme_key, start: occasion.suggested_start, duration: String(occasion.suggested_duration) });
  return `?${params.toString()}`;
}

function UpcomingOccasions({ calendar, promotions, today }: { calendar: OccasionDate[]; promotions: PromotionRow[]; today: string }) {
  const [showAll, setShowAll] = useState(false);
  const soon = calendar.filter((occasion) => occasion.days_until <= 90);
  const shown = showAll ? calendar : soon;
  const months: Array<[string, OccasionDate[]]> = [];
  for (const occasion of shown) {
    const month = formatDateKey(occasion.date, { month: "long", year: "numeric" });
    const last = months[months.length - 1];
    if (last && last[0] === month) last[1].push(occasion);
    else months.push([month, [occasion]]);
  }
  const existingFor = (occasion: OccasionDate) => promotions.find((promotion) => (
    promotion.theme_key === occasion.theme_key && !["archived", "ended"].includes(promotion.status)
    && promotion.start_date <= occasion.date && promotion.end_date >= addDays(occasion.date, -30)
  ));

  if (!calendar.length) return <p className="sa-muted">No occasions in the next 12 months.</p>;
  return (
    <div className="sa-stack">
      {!shown.length ? <p className="sa-muted">Nothing in the next 90 days.</p> : null}
      {months.map(([month, rows]) => (
        <div key={month} className="sa-stack-sm">
          <h4 className="sa-muted">{month}</h4>
          <div>
            {rows.map((occasion) => {
              const existing = existingFor(occasion);
              const startsIn = daysBetween(today, occasion.suggested_start);
              return (
                <div key={`${occasion.theme_key}-${occasion.date}`} className="sa-occasion">
                  <span className="sa-occasion-emoji" aria-hidden="true">{occasion.emoji || "📅"}</span>
                  <div className="sa-stack-sm" style={{ gap: 2, minWidth: 0 }}>
                    <div className="sa-row">
                      <strong>{occasion.label}</strong>
                      <span>{formatDay(occasion.date)}</span>
                      <Badge tone={occasion.days_until <= 14 ? "attention" : "neutral"}>{inDays(occasion.days_until)}</Badge>
                      {existing ? <Badge tone="success">Promotion planned</Badge> : null}
                    </div>
                    <span className="sa-small sa-muted">
                      {startsIn <= 0 ? "Start today" : `Start posting ${formatDay(occasion.suggested_start)} (${inDays(startsIn)})`}
                      {` and run ${occasion.suggested_duration} day${occasion.suggested_duration === 1 ? "" : "s"}`}
                    </span>
                  </div>
                  <div className="sa-row">
                    {existing
                      ? <Link to={`?edit=${encodeURIComponent(existing.id)}`} className="sa-btn sa-btn-sm">View your promotion</Link>
                      : <Link to={occasionLink(occasion)} className="sa-btn sa-btn-sm sa-btn-primary">Create promotion</Link>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
      {calendar.length > soon.length ? (
        <div>
          <button type="button" className="sa-btn sa-btn-sm" onClick={() => setShowAll(!showAll)}>
            {showAll ? "Show the next 90 days only" : `Show all 12 months (${calendar.length - soon.length} more)`}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function PromotionCard({ promotion, today, channelById, preview, result, planDaysAhead }: {
  promotion: PromotionRow;
  today: string;
  channelById: Record<string, Channel>;
  preview?: PreviewData;
  result: ActionResult | null;
  planDaysAhead: number;
}) {
  const { tz, contentTypes } = useSocial();
  const { busy, isSubmitting } = useBusy();
  const status = promotion.status;
  const counts = countsOf(promotion.post_counts || {});
  const duration = Number(promotion.config.duration_days || 0) || daysBetween(promotion.start_date, promotion.end_date) + 1;
  const channels = promotion.channel_ids.map((id) => channelById[id]).filter(Boolean);
  const active = promotion.campaign_status === "active";
  const notOver = Boolean(promotion.end_date) && promotion.end_date >= today;
  const daysLeft = daysBetween(today, promotion.end_date);
  const timing = status === "running"
    ? (daysLeft === 0 ? "Last day today" : `${daysLeft + 1} days left`)
    : status === "upcoming" ? `Starts ${inDays(daysBetween(today, promotion.start_date))}`
      : status === "ended" ? `Ended ${formatDay(promotion.end_date)}` : "";
  const target = (intent: string) => isSubmitting(intent, ["promotion_id", promotion.id]);
  const accent = promotion.last_error || status === "invalid" ? "critical" : status === "running" ? "success" : status === "upcoming" ? "info" : "neutral";
  const startsIn = daysBetween(today, promotion.start_date);

  return (
    <Card accent={accent}>
      <div className="sa-stack">
        <div className="sa-spread">
          <div className="sa-row">
            <span style={{ fontSize: "1.3rem", lineHeight: 1 }} aria-hidden="true">{promotion.emoji || "📣"}</span>
            <strong>{promotion.name}</strong>
            <StatusBadge map={PROMOTION_STATUS} status={status} />
            <Badge tone="info">{promotion.theme_label}</Badge>
          </div>
          <div className="sa-row">
            {active && status !== "ended" ? (
              <Form method="post" preventScrollReset>
                <Hidden values={{ intent: "stop", promotion_id: promotion.id }} />
                <button type="submit" className="sa-btn sa-btn-sm" disabled={busy}>{target("stop") ? "Stopping…" : "Stop"}</button>
              </Form>
            ) : null}
            {!active && notOver ? (
              <Form method="post" preventScrollReset>
                <Hidden values={{ intent: "resume", promotion_id: promotion.id }} />
                <button type="submit" className="sa-btn sa-btn-sm sa-btn-primary" disabled={busy}>{target("resume") ? "Resuming…" : "Resume"}</button>
              </Form>
            ) : null}
            {notOver && status !== "invalid" ? (
              <Form method="post" preventScrollReset>
                <Hidden values={{ intent: "preview", promotion_id: promotion.id }} />
                <button type="submit" className="sa-btn sa-btn-sm" disabled={busy}>{target("preview") ? "Previewing…" : "Preview"}</button>
              </Form>
            ) : null}
            <Link to={`?edit=${encodeURIComponent(promotion.id)}`} className="sa-btn sa-btn-sm">Edit</Link>
          </div>
        </div>

        <div className="sa-row sa-small">
          <span>📅 {formatDay(promotion.start_date)} – {formatDay(promotion.end_date, true)} · {duration} day{duration === 1 ? "" : "s"}</span>
          {timing ? <Badge tone={status === "running" ? "success" : "neutral"}>{timing}</Badge> : null}
          {promotion.coupon_code ? <Badge tone="magic">Code {promotion.coupon_code}</Badge> : null}
          {promotion.config.discount_text ? <span className="sa-muted">{promotion.config.discount_text}</span> : null}
        </div>

        <div className="sa-row sa-small sa-muted">
          <span>{promotion.posts_per_day} post{promotion.posts_per_day === 1 ? "" : "s"} a day per channel</span>
          <span>·</span>
          <span className="sa-row" style={{ gap: 4 }}>
            {channels.map((channel) => <NetworkDot key={channel.channel_id} provider={channel.provider} />)}
            {channels.length ? `${channels.length} channel(s)` : "All enabled channels"}
          </span>
          <span>·</span>
          <span>{approvalLabel(promotion.approval_mode)}</span>
        </div>

        <div className="sa-row sa-small">
          <span><strong>{counts.planned}</strong> planned</span>
          <span><strong>{counts.scheduled}</strong> scheduled</span>
          <span><strong>{counts.published}</strong> published</span>
          {counts.approval ? <Link to="/app/social/queue"><Badge tone="attention">{counts.approval} need your approval</Badge></Link> : null}
          {counts.failed ? <Badge tone="critical">{counts.failed} failed</Badge> : null}
        </div>

        {result ? <ActionBanner result={result} /> : null}
        {promotion.last_error ? <div className="sa-banner sa-banner-critical"><strong>Last run had a problem:</strong> {promotion.last_error}</div> : null}
        {status === "invalid" ? <Banner tone="critical">This promotion&apos;s settings no longer pass the checks. Open it with Edit, fix what&apos;s flagged and save.</Banner> : null}

        {preview ? (
          <div className="sa-card sa-card-muted sa-stack-sm">
            <strong>Preview — {preview.planned} post(s) would be planned right now{preview.duration_ms ? ` (${(preview.duration_ms / 1000).toFixed(1)} s)` : ""}. Nothing was saved.</strong>
            {preview.note ? <div className="sa-small">Note: {preview.note}</div> : null}
            {preview.items?.length ? (
              <div className="sa-table-wrap">
                <table className="sa-table">
                  <thead><tr><th>When</th><th>Channels</th><th>Post</th><th>Products</th><th>Promo line</th></tr></thead>
                  <tbody>
                    {preview.items.map((item) => (
                      <tr key={item.dedupe_key}>
                        <td className="sa-nowrap"><TimeText epoch={item.scheduled_at} tz={tz} /></td>
                        <td>{item.channel_ids.map((id) => channelById[id] ? `${providerLabel(channelById[id].provider)} (${channelById[id].name})` : id).join(", ")}</td>
                        <td>
                          {contentTypeLabel(item.content_type, contentTypes)}
                          {item.content_type === "ai_video" ? <div><Badge tone="magic">Hero AI video</Badge></div> : null}
                          {item.texts?.countdown_label ? <div><Badge tone="attention">{item.texts.countdown_label}</Badge></div> : null}
                        </td>
                        <td>{item.product_ids.map((id) => preview.productTitles[id] || `#${id}`).join(", ") || "—"}</td>
                        <td className="sa-small">{item.texts?.promo_text || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <span className="sa-small sa-muted">
                {startsIn > planDaysAhead
                  ? `Posts are planned ${planDaysAhead} day(s) ahead, so nothing is planned until ${formatDay(addDays(promotion.start_date, -planDaysAhead))}.`
                  : "Nothing new would be planned right now — the posts may already be planned, or the note above explains why."}
              </span>
            )}
            <span className="sa-help">Real posts may land at slightly different times: the times are picked when they&apos;re planned.</span>
          </div>
        ) : null}

        <details className="sa-plain">
          <summary className="sa-small sa-muted">Remove promotion</summary>
          <Form method="post" preventScrollReset className="sa-stack-sm" style={{ marginTop: 6 }}>
            <Hidden values={{ intent: "remove", promotion_id: promotion.id }} />
            <span className="sa-small">
              This stops “{promotion.name}” and cancels its {counts.unpublished} post(s) that haven&apos;t been published yet — including any already scheduled in Postiz. Posts that are already live stay up.
            </span>
            <div>
              <button type="submit" className="sa-btn sa-btn-sm sa-btn-critical" disabled={busy}>{target("remove") ? "Removing…" : `Yes, remove “${promotion.name}”`}</button>
            </div>
          </Form>
        </details>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------- page

export default function PromotionsPage() {
  const data = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>();
  const { tz, settings } = useSocial();
  const [params] = useSearchParams();
  const actionResult = result && "intent" in result ? (result as ActionResult) : null;
  const preview = actionResult?.intent === "preview" && actionResult.ok ? (actionResult.data as PreviewData | undefined) : undefined;
  const today = data.today || todayKey(tz);
  const formOpen = Boolean(data.editing || data.prefill);
  const savedId = params.get("saved");
  const saved = savedId ? data.promotions.find((promotion) => promotion.id === savedId) : undefined;
  const visible = data.promotions.filter((promotion) => promotion.status !== "archived");
  const removed = data.promotions.length - visible.length;
  const channelById = Object.fromEntries(data.channels.map((channel) => [channel.channel_id, channel]));
  const cap = settings?.schedule.posts_per_channel_per_day;
  const planDaysAhead = settings?.schedule.plan_days_ahead ?? 1;
  const known = new Set(GROUPS.map((group) => group.status));
  const other = visible.filter((promotion) => !known.has(promotion.status));
  const themeLink = (theme: PromotionTheme) => {
    const occasion = data.calendar.find((item) => item.theme_key === theme.key);
    return occasion ? occasionLink(occasion) : `?${new URLSearchParams({ theme: theme.key }).toString()}`;
  };

  return (
    <>
      {savedId && !formOpen ? (
        <Banner tone="success">
          {saved ? `“${saved.name}” saved. ` : "Promotion saved. "}
          {saved?.campaign_status === "paused"
            ? "It's stopped for now — press Resume when you want it to run."
            : "It plans its posts a day or so ahead; you'll find them in the approval queue and the calendar."}
        </Banner>
      ) : null}
      <ActionBanner result={actionResult?.target && visible.some((promotion) => promotion.id === actionResult.target) ? null : actionResult} />
      <LoadError error={data.error} />
      {data.editId && !data.editing && !data.error ? <Banner tone="warning">That promotion no longer exists.</Banner> : null}

      {formOpen ? (
        <s-section heading={data.editing ? `Edit “${data.editing.name}”` : "Create a promotion"}>
          <PromotionForm
            key={data.editing?.id || `${data.prefill?.theme_key}-${data.prefill?.start_date}-${data.prefill?.duration_days}`}
            promotion={data.editing}
            prefill={data.prefill}
            themes={data.themes}
            categories={data.categories}
            calendar={data.calendar}
            defaults={data.defaults}
            today={today}
            budget={data.postiz_budget}
            channels={data.channels}
            collections={data.collections}
            products={data.products}
          />
        </s-section>
      ) : null}

      <s-section heading="Upcoming occasions">
        <div className="sa-stack">
          <p className="sa-muted">Plan ahead for the big days. “Create promotion” fills in the theme, start date and length for you.</p>
          <UpcomingOccasions calendar={data.calendar} promotions={visible} today={today} />
        </div>
      </s-section>

      <s-section heading={`Your promotions (${visible.length})`}>
        <div className="sa-stack">
          <div className="sa-spread">
            <span className="sa-muted">Holiday sales, flash sales, new arrivals — each runs for up to 15 days with rotating promo lines.</span>
            {!formOpen ? <Link to="?new=1" className="sa-btn sa-btn-primary">Create promotion</Link> : null}
          </div>
          <Banner tone="info">
            <strong>Promotions come first.</strong> On a day that&apos;s already full, a promotion post takes the slot of a SmartQ post that hasn&apos;t been sent to Postiz yet (nothing already in Postiz is touched).
            {cap ? ` Each channel still posts at most ${cap} time${cap === 1 ? "" : "s"} a day — ` : " Each channel still keeps its daily limit — "}
            <Link to="/app/social/settings#schedule">change it in Settings</Link>.
          </Banner>
          {GROUPS.map((group) => {
            const rows = visible.filter((promotion) => promotion.status === group.status);
            if (!rows.length) return null;
            return (
              <div key={group.status} className="sa-stack-sm">
                <h3>{group.title} <span className="sa-count">{rows.length}</span></h3>
                {rows.map((promotion) => (
                  <PromotionCard
                    key={promotion.id} promotion={promotion} today={today} channelById={channelById} planDaysAhead={planDaysAhead}
                    preview={preview && preview.campaign_id === promotion.id ? preview : undefined}
                    result={actionResult?.target === promotion.id ? actionResult : null}
                  />
                ))}
              </div>
            );
          })}
          {other.length ? (
            <div className="sa-stack-sm">
              <h3>Other <span className="sa-count">{other.length}</span></h3>
              {other.map((promotion) => (
                <PromotionCard
                  key={promotion.id} promotion={promotion} today={today} channelById={channelById} planDaysAhead={planDaysAhead}
                  preview={preview && preview.campaign_id === promotion.id ? preview : undefined}
                  result={actionResult?.target === promotion.id ? actionResult : null}
                />
              ))}
            </div>
          ) : null}
          {!visible.length && !data.error ? (
            <EmptyState title="No promotions yet" action={<Link to="?new=1" className="sa-btn sa-btn-primary">Create your first promotion</Link>}>
              Pick an occasion above, or a theme below: Black Friday, a flash sale, free delivery, new arrivals or your own event.
              Social Autopilot posts it on every network each day it runs, with your promo lines, coupon and a countdown at the end.
            </EmptyState>
          ) : null}
          {removed ? <span className="sa-small sa-muted">{removed} removed promotion{removed === 1 ? " is" : "s are"} hidden.</span> : null}
        </div>
      </s-section>

      {!formOpen && Object.keys(data.categories).length ? (
        <s-section heading="Create a promotion">
          <div className="sa-stack">
            <p className="sa-muted">Pick a theme to start. Every theme comes with promo lines, colours, a sticker and a recommended length.</p>
            <ThemePicker themes={data.themes} categories={data.categories} calendar={data.calendar} hrefFor={themeLink} />
          </div>
        </s-section>
      ) : null}
    </>
  );
}
