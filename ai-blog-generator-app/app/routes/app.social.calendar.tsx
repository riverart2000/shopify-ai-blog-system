import { useMemo, useState } from "react";
import type { LoaderFunctionArgs } from "react-router";
import { Link, useLoaderData } from "react-router";

import { NetworkDot } from "../components/social/ChannelChip";
import { useSocial } from "../components/social/context";
import { Badge, EmptyState, LoadError, usePolling } from "../components/social/ui";
import { contentTypeLabel, providerLabel, type Tone } from "../components/social/vocab";
import { errorMessage, seFetch, type CalendarItem, type CalendarResponse } from "../lib/social-engine.server";
import {
  addDays, addMonths, formatDateKey, formatTime, isDateKey, keyToUtcEpoch, startOfMonth, startOfWeek, todayKey,
  zonedDateKey,
} from "../lib/social-time";
import { authenticate } from "../shopify.server";

export const handle = { title: "Calendar" };

type View = "week" | "month";

function gridFor(view: View, anchor: string): { start: string; days: number } {
  if (view === "week") return { start: startOfWeek(anchor), days: 7 };
  const first = startOfMonth(anchor);
  const start = startOfWeek(first);
  const nextMonth = addMonths(first, 1);
  const end = addDays(startOfWeek(addDays(nextMonth, -1)), 7);
  const days = Math.round((keyToUtcEpoch(end) - keyToUtcEpoch(start)) / 86_400);
  return { start, days };
}

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  const url = new URL(request.url);
  const view: View = url.searchParams.get("view") === "month" ? "month" : "week";
  const requested = url.searchParams.get("date");
  const anchor = isDateKey(requested) ? requested : todayKey("UTC");
  const grid = gridFor(view, anchor);
  // Pad by a day either side: the API range is UTC, the grid is in the store's time zone.
  const start = keyToUtcEpoch(grid.start) - 86_400;
  const end = keyToUtcEpoch(addDays(grid.start, grid.days)) + 86_400;
  try {
    const calendar = await seFetch<CalendarResponse>("/calendar", { query: { start, end } });
    return { view, anchor, grid, items: calendar.items, error: "" };
  } catch (error) {
    return { view, anchor, grid, items: [] as CalendarItem[], error: errorMessage(error) };
  }
};

function toneFor(item: CalendarItem): Tone {
  if (item.status === "failed" || item.post_status === "failed") return "critical";
  if (item.status === "published") return "success";
  if (item.status === "scheduled") return "info";
  if (item.status === "draft") return "magic";
  if (item.status === "publishing" || item.status === "uncertain") return "warning";
  if (item.status === "cancelled" || ["rejected", "cancelled"].includes(item.post_status)) return "neutral";
  if (item.post_status === "pending_approval") return "attention";
  return "neutral";
}

const LEGEND: Array<[Tone, string]> = [
  ["attention", "Needs approval"], ["neutral", "Approved / waiting"], ["info", "Scheduled in Postiz"],
  ["magic", "Postiz draft"], ["success", "Published"], ["warning", "Sending"], ["critical", "Failed"],
];

export default function CalendarPage() {
  const { view, anchor, grid, items, error } = useLoaderData<typeof loader>();
  const { tz, contentTypes } = useSocial();
  const [network, setNetwork] = useState("");
  usePolling(items.some((item) => ["publishing", "uncertain"].includes(item.status)), 15_000);

  const providers = useMemo(() => Array.from(new Set(items.map((item) => item.provider))).sort(), [items]);
  const byDay = useMemo(() => {
    const map = new Map<string, CalendarItem[]>();
    for (const item of items) {
      if (!item.scheduled_at || (network && item.provider !== network)) continue;
      const key = zonedDateKey(item.scheduled_at, tz);
      map.set(key, [...(map.get(key) || []), item]);
    }
    for (const list of map.values()) list.sort((a, b) => (a.scheduled_at || 0) - (b.scheduled_at || 0));
    return map;
  }, [items, tz, network]);

  const days = Array.from({ length: grid.days }, (_, index) => addDays(grid.start, index));
  const today = todayKey(tz);
  const month = anchor.slice(0, 7);
  const prev = view === "week" ? addDays(anchor, -7) : addMonths(anchor, -1);
  const next = view === "week" ? addDays(anchor, 7) : addMonths(anchor, 1);
  const title = view === "week"
    ? `${formatDateKey(days[0], { day: "numeric", month: "short" })} – ${formatDateKey(days[6], { day: "numeric", month: "short", year: "numeric" })}`
    : formatDateKey(`${month}-01`, { month: "long", year: "numeric" });
  const visibleCount = days.reduce((sum, day) => sum + (byDay.get(day)?.length || 0), 0);
  const maxPerCell = view === "month" ? 4 : 50;

  return (
    <>
      <LoadError error={error} />
      <s-section>
        <div className="sa-stack">
          <div className="sa-spread">
            <div className="sa-row">
              <Link to={`?view=${view}&date=${prev}`} className="sa-btn" aria-label="Previous">←</Link>
              <Link to={`?view=${view}&date=${todayKey(tz)}`} className="sa-btn">Today</Link>
              <Link to={`?view=${view}&date=${next}`} className="sa-btn" aria-label="Next">→</Link>
              <h3 style={{ marginLeft: 6 }}>{title}</h3>
            </div>
            <div className="sa-row">
              {providers.length > 1 ? (
                <select value={network} onChange={(event) => setNetwork(event.target.value)} aria-label="Filter by network" style={{ width: "auto" }}>
                  <option value="">All networks</option>
                  {providers.map((provider) => <option key={provider} value={provider}>{providerLabel(provider)}</option>)}
                </select>
              ) : null}
              <Link to={`?view=week&date=${anchor}`} className={`sa-btn ${view === "week" ? "sa-btn-primary" : ""}`}>Week</Link>
              <Link to={`?view=month&date=${anchor}`} className={`sa-btn ${view === "month" ? "sa-btn-primary" : ""}`}>Month</Link>
            </div>
          </div>
          <div className="sa-row sa-small">
            {LEGEND.map(([tone, label]) => <span key={label} className={`sa-cal-item sa-cal-item-${tone}`} style={{ display: "inline-block" }}>{label}</span>)}
            <span className="sa-muted">Times in {tz}</span>
          </div>

          <div className={`sa-cal ${view === "week" ? "sa-cal-week" : "sa-cal-month"}`}>
            {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((label) => <div key={label} className="sa-cal-head">{label}</div>)}
            {days.map((day) => {
              const list = byDay.get(day) || [];
              const outside = view === "month" && day.slice(0, 7) !== month;
              return (
                <div key={day} className={`sa-cal-day ${outside ? "other" : ""} ${day === today ? "today" : ""}`}>
                  <div className="sa-spread">
                    <span className="sa-cal-date">{formatDateKey(day, view === "week" ? { weekday: "short", day: "numeric", month: "short" } : { day: "numeric" })}</span>
                    {list.length ? <span className="sa-small sa-muted">{list.length}</span> : null}
                  </div>
                  {list.slice(0, maxPerCell).map((item) => (
                    <Link
                      key={item.target_id}
                      to={`/app/social/posts/${item.post_id}`}
                      className={`sa-cal-item sa-cal-item-${toneFor(item)}`}
                      title={`${formatTime(item.scheduled_at, tz)} · ${providerLabel(item.provider)}${item.channel_name ? ` (${item.channel_name})` : ""} · ${item.title}${item.error ? ` — ${item.error}` : ""}`}
                    >
                      <span className="sa-row" style={{ gap: 4, flexWrap: "nowrap" }}>
                        <NetworkDot provider={item.provider} />
                        <strong>{formatTime(item.scheduled_at, tz)}</strong>
                        <span className="sa-cal-item-text sa-ellipsis">{view === "week" ? providerLabel(item.provider) : ""}</span>
                      </span>
                      <span className="sa-cal-item-text" style={{ display: "block" }}>
                        <span className="sa-ellipsis" style={{ display: "block" }}>{item.title || "Untitled post"}</span>
                        {view === "week" ? <span className="sa-muted" style={{ display: "block" }}>{contentTypeLabel(item.content_type, contentTypes)}</span> : null}
                        {view === "week" && item.error ? <span style={{ display: "block", color: "#8e0b21" }} className="sa-break">{item.error}</span> : null}
                      </span>
                    </Link>
                  ))}
                  {list.length > maxPerCell ? <Link to={`?view=week&date=${day}`} className="sa-small">+{list.length - maxPerCell} more</Link> : null}
                </div>
              );
            })}
          </div>
          {!visibleCount && !error ? (
            <EmptyState title="Nothing planned in this period" action={<Link to="/app/social/studio" className="sa-btn sa-btn-primary">Create a post</Link>}>
              Active campaigns fill the calendar automatically (they plan {view === "week" ? "a day or two" : "a little"} ahead). <Link to="/app/social/campaigns">Check your campaigns</Link>.
            </EmptyState>
          ) : null}
          {visibleCount ? <div className="sa-row sa-small sa-muted"><Badge tone="neutral">{visibleCount}</Badge> channel posts in view.</div> : null}
        </div>
      </s-section>
    </>
  );
}
