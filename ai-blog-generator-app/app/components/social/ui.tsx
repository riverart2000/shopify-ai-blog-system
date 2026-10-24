import { useEffect, type ReactNode } from "react";
import { Link, useNavigation, useRevalidator } from "react-router";

import type { ActionResult } from "../../lib/social-engine.types";
import { formatDateTime, formatFull, relativeTime } from "../../lib/social-time";
import { statusInfo, type Tone } from "./vocab";

type BannerTone = "success" | "critical" | "warning" | "info";

export function Banner({ tone = "info", title, children }: { tone?: BannerTone; title?: string; children?: ReactNode }) {
  return (
    <div className={`sa-banner sa-banner-${tone}`} role={tone === "critical" ? "alert" : "status"}>
      {title ? <strong>{title}</strong> : null}
      {title && children ? <div>{children}</div> : children}
    </div>
  );
}

/** Success / error banner for the last action (exact backend message). */
export function ActionBanner({ result }: { result?: ActionResult | null }) {
  if (!result) return null;
  return (
    <div className="sa-stack-sm">
      {result.ok && result.message ? <Banner tone="success">{result.message}</Banner> : null}
      {result.error ? <Banner tone="critical" title={result.ok ? "Partly done" : "That didn't work"}>{result.error}</Banner> : null}
    </div>
  );
}

export function LoadError({ error }: { error?: string }) {
  if (!error) return null;
  return <Banner tone="critical" title="Couldn't load this page from the Social Autopilot backend">{error}</Banner>;
}

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return <span className={`sa-badge sa-badge-${tone}`} title={title}>{children}</span>;
}

export function StatusBadge({ map, status }: { map: Parameters<typeof statusInfo>[0]; status: string }) {
  const info = statusInfo(map, status);
  return <Badge tone={info.tone} title={info.help}>{info.label}</Badge>;
}

export function Card({ children, accent, muted, className = "" }: { children: ReactNode; accent?: Tone; muted?: boolean; className?: string }) {
  return (
    <div className={`sa-card ${accent ? `sa-card-accent-${accent}` : ""} ${muted ? "sa-card-muted" : ""} ${className}`.trim()}>
      {children}
    </div>
  );
}

export function Kpi({ label, value, detail, to }: { label: string; value: ReactNode; detail?: ReactNode; to?: string }) {
  const body = (
    <>
      <div className="sa-kpi-label">{label}</div>
      <div className="sa-kpi-value">{value}</div>
      {detail ? <div className="sa-kpi-detail">{detail}</div> : null}
    </>
  );
  return to ? <Link to={to} className="sa-kpi">{body}</Link> : <div className="sa-kpi">{body}</div>;
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="sa-empty">
      <h3>{title}</h3>
      {children ? <div style={{ maxWidth: 520, margin: "0 auto" }}>{children}</div> : null}
      {action ? <div style={{ marginTop: 12 }}>{action}</div> : null}
    </div>
  );
}

export function Field({ label, help, error, children, className = "" }: {
  label: ReactNode; help?: ReactNode; error?: string; children: ReactNode; className?: string;
}) {
  return (
    <label className={`sa-field ${className}`.trim()}>
      <span className="sa-field-label">{label}</span>
      {children}
      {help ? <span className="sa-help">{help}</span> : null}
      {error ? <span className="sa-error-text">{error}</span> : null}
    </label>
  );
}

export function Hidden({ values }: { values: Record<string, string | number | null | undefined> }) {
  return (
    <>
      {Object.entries(values).map(([name, value]) => (
        <input key={name} type="hidden" name={name} value={value === null || value === undefined ? "" : String(value)} />
      ))}
    </>
  );
}

/** True while any form on the page is submitting or loaders are reloading after it. */
export function useBusy(): { busy: boolean; isSubmitting: (intent: string, key?: [string, string]) => boolean } {
  const navigation = useNavigation();
  const busy = navigation.state === "submitting" || (navigation.state === "loading" && Boolean(navigation.formMethod));
  return {
    busy,
    isSubmitting: (intent, key) => {
      const data = navigation.formData;
      if (!busy || !data) return false;
      if (data.get("intent") !== intent) return false;
      return key ? data.get(key[0]) === key[1] : true;
    },
  };
}

/** Revalidate every `ms` while `active` (paused while the tab is hidden). */
export function usePolling(active: boolean, ms = 10_000) {
  const revalidator = useRevalidator();
  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && revalidator.state === "idle") revalidator.revalidate();
    }, ms);
    return () => window.clearInterval(timer);
  }, [active, ms, revalidator]);
}

export function TimeText({ epoch, tz, relative = false, full = false }: { epoch?: number | null; tz: string; relative?: boolean; full?: boolean }) {
  if (!epoch) return <span className="sa-muted">—</span>;
  const absolute = full ? formatFull(epoch, tz) : formatDateTime(epoch, tz);
  return (
    <time dateTime={new Date(epoch * 1000).toISOString()} title={formatFull(epoch, tz)} suppressHydrationWarning>
      {relative ? relativeTime(epoch) : absolute}
    </time>
  );
}

export function JsonBlock({ value, label = "Show data" }: { value: unknown; label?: string }) {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value === "object" && !Array.isArray(value) && !Object.keys(value as object).length) return null;
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <details>
      <summary className="sa-small">{label}</summary>
      <pre>{text}</pre>
    </details>
  );
}

export function SectionHeader({ title, description, actions }: { title?: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="sa-spread" style={{ marginBottom: 10 }}>
      <div className="sa-stack-sm sa-grow">
        {title ? <h3>{title}</h3> : null}
        {description ? <p className="sa-muted">{description}</p> : null}
      </div>
      {actions ? <div className="sa-row">{actions}</div> : null}
    </div>
  );
}

export function Tabs({ items }: { items: Array<{ to: string; label: string; count?: number; active: boolean }> }) {
  return (
    <nav className="sa-tabs" aria-label="Filter">
      {items.map((item) => (
        <Link key={item.label} to={item.to} className={item.active ? "active" : ""} preventScrollReset>
          {item.label}
          {item.count !== undefined ? <span className="sa-count">{item.count}</span> : null}
        </Link>
      ))}
    </nav>
  );
}

export function Toggle({ name, checked, label, help, onChange }: {
  name?: string; checked?: boolean; label: ReactNode; help?: ReactNode; onChange?: (checked: boolean) => void;
}) {
  return (
    <label className="sa-check">
      {onChange ? (
        <input type="checkbox" name={name} value="1" checked={Boolean(checked)} onChange={(event) => onChange(event.target.checked)} />
      ) : (
        <input type="checkbox" name={name} value="1" defaultChecked={checked} />
      )}
      <span>
        {label}
        {help ? <span className="sa-help" style={{ display: "block" }}>{help}</span> : null}
      </span>
    </label>
  );
}

/** A card-style radio/checkbox option. Pass the <input> as children. */
export function Choice({ title, description, children, dimmed = false }: {
  title: ReactNode; description?: ReactNode; children: ReactNode; dimmed?: boolean;
}) {
  const body = (
    <span className="sa-stack-sm" style={{ minWidth: 0 }}>
      <strong>{title}</strong>
      {description ? <span className="sa-small sa-muted">{description}</span> : null}
    </span>
  );
  return <label className="sa-choice" style={dimmed ? { opacity: 0.55 } : undefined}>{children}{body}</label>;
}
