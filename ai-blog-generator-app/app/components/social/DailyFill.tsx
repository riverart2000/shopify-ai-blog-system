import { Fragment } from "react";

import type { DailyFillDay } from "../../lib/social-engine.types";
import { formatDateKey } from "../../lib/social-time";
import { NetworkDot } from "./ChannelChip";
import { providerLabel } from "./vocab";

/**
 * Compact "Daily slots" grid: one row per channel, one column per day, planned/target in each cell.
 * Green = every slot used, amber = slots still free, grey = today's posting window has closed.
 */
export function DailySlotsGrid({ days }: { days: DailyFillDay[] }) {
  if (!days.length) return null;
  const channels = days[0].channels;
  if (!channels.length) return <p className="sa-muted">No usable channels yet — connect and enable channels to see daily slots.</p>;
  return (
    <div className="sa-fill" style={{ gridTemplateColumns: `minmax(120px, 1.4fr) repeat(${days.length}, minmax(64px, 1fr))` }}>
      <div className="sa-fill-head">Channel</div>
      {days.map((day, index) => {
        const full = day.slots > 0 && day.filled >= day.slots;
        return (
          <div key={day.date} className="sa-fill-head">
            {index === 0 ? "Today" : index === 1 ? "Tomorrow" : formatDateKey(day.date, { weekday: "short", day: "numeric" })}
            <span className={`sa-fill-total ${full ? "full" : ""}`}>{day.filled}/{day.slots}</span>
          </div>
        );
      })}
      {channels.map((channel) => (
        <Fragment key={channel.channel_id}>
          <div className="sa-fill-name" title={`${providerLabel(channel.provider)} · ${channel.name}`}>
            <NetworkDot provider={channel.provider} />
            <span className="sa-ellipsis">{channel.name}</span>
          </div>
          {days.map((day) => {
            const row = day.channels.find((item) => item.channel_id === channel.channel_id);
            if (!row) return <div key={day.date} className="sa-fill-cell">—</div>;
            const state = row.full ? "full" : !day.window_open ? "closed" : "short";
            const label = row.full
              ? `${row.planned} of ${row.target} slots used`
              : !day.window_open ? `Posting window closed (${row.planned} of ${row.target} used)` : `${row.target - row.planned} slot(s) still free`;
            return (
              <div key={day.date} className={`sa-fill-cell sa-fill-${state}`} title={label} aria-label={`${channel.name}, ${day.date}: ${label}`}>
                {row.planned}/{row.target}
              </div>
            );
          })}
        </Fragment>
      ))}
    </div>
  );
}
