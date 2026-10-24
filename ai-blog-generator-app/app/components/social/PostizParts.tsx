/**
 * Postiz creative tools: credit meters, the "what's automated vs. what lives in Postiz" panel,
 * and the design picker (Postiz media library, e.g. files made with the design-editor templates).
 */
import { useEffect, useRef, useState } from "react";
import { useFetcher } from "react-router";

import type { PostizBudget, PostizMediaItem, PostizToolsResponse } from "../../lib/social-engine.types";
import { Badge, Banner } from "./ui";
import { humanize } from "./vocab";

export function creditsLeft(budget: PostizBudget | undefined, kind: "image" | "video"): { used: number; total: number; left: number } | null {
  if (!budget || budget.error) return null;
  const used = Number(kind === "image" ? budget.images_used : budget.videos_used) || 0;
  const total = Number(kind === "image" ? budget.images_budget : budget.videos_budget) || 0;
  return { used, total, left: Math.max(0, total - used) };
}

/** "12 of 80 AI images left this month" style meters. */
export function PostizCredits({ budget, compact = false }: { budget?: PostizBudget; compact?: boolean }) {
  if (!budget) return null;
  if (budget.error) return <span className="sa-small sa-muted">Postiz credits unavailable: {budget.error}</span>;
  const rows: Array<["image" | "video", string]> = [["image", "AI images"], ["video", "AI videos"]];
  return (
    <div className={compact ? "sa-row sa-small" : "sa-stack-sm"}>
      {rows.map(([kind, label]) => {
        const credit = creditsLeft(budget, kind);
        if (!credit) return null;
        const pct = credit.total ? Math.min(100, (credit.used / credit.total) * 100) : 100;
        return (
          <div key={kind} className="sa-stack-sm" style={{ minWidth: compact ? 150 : undefined }}>
            <div className="sa-spread sa-small">
              <span>{label}</span>
              <strong>{credit.used} / {credit.total} used</strong>
            </div>
            <div className="sa-bar"><span style={{ width: `${pct}%`, background: pct >= 90 ? "#e51c00" : pct >= 70 ? "#e8a400" : "#29845a" }} /></div>
          </div>
        );
      })}
    </div>
  );
}

export function LiveBadge({ live }: { live?: boolean }) {
  if (live === undefined) return null;
  return live
    ? <Badge tone="success" title="This system may create posts and AI media in Postiz">Live publishing</Badge>
    : <Badge tone="warning" title="SOCIAL_ENGINE_LIVE_PUBLISHING is not set here: Postiz is read-only from this system">Read-only (not the live system)</Badge>;
}

/** What Social Autopilot does with Postiz automatically, and what you do in Postiz itself. */
export function PostizToolsPanel({ tools, error }: { tools: PostizToolsResponse | null; error?: string }) {
  if (!tools) {
    return error ? <Banner tone="warning" title="Postiz tools unavailable">{error}</Banner> : <p className="sa-muted">Loading Postiz tools…</p>;
  }
  const settings = tools.settings || {};
  const automated: Array<[string, string, boolean | undefined]> = [
    ["AI background scenes", "A Postiz AI scene behind your real product photo on promotion posts and “AI image ad” posts.", settings.ai_backgrounds_for_promotions],
    ["Your Postiz designs", "Designs from your Postiz media library are used when a post or promotion names a design search term.", true],
    ["AI videos", `Postiz video generators (${tools.video_generators.map((generator) => String(generator.title || generator.name || generator.type || "")).filter(Boolean).join(", ") || "none offered"}) for “AI video” posts and promotion launch videos.`, (settings.ai_video_monthly_budget ?? 0) > 0],
    ["Voice-over", `${tools.voices.length} ElevenLabs voice(s) available for slide videos.`, tools.voices.length > 0],
    ["Trending TikTok music", "Music picked with Postiz's TikTok music search for video posts.", settings.trending_tiktok_music],
    ["Short links & tags", "Postiz shortens links and tags every post so you can find them in Postiz.", settings.short_links || settings.tag_posts],
  ];
  return (
    <div className="sa-stack">
      <div className="sa-spread">
        <LiveBadge live={tools.live_publishing} />
        <div style={{ minWidth: 260, flex: "0 1 360px" }}><PostizCredits budget={tools.budget} /></div>
      </div>
      {!tools.live_publishing ? (
        <span className="sa-help">This copy of the app can read Postiz but never changes it (no posts, uploads or AI credits) — only the live system publishes.</span>
      ) : null}
      <div className="sa-grid-2">
        <div className="sa-stack-sm">
          <h4>Automated by Social Autopilot</h4>
          <ul style={{ margin: 0, paddingLeft: 18 }} className="sa-stack-sm">
            {automated.map(([name, how, on]) => (
              <li key={name}>
                <strong>{name}</strong> {on === false ? <Badge tone="neutral">Off</Badge> : <Badge tone="success">On</Badge>}
                <div className="sa-small sa-muted">{how}</div>
              </li>
            ))}
          </ul>
        </div>
        <div className="sa-stack-sm">
          <h4>Done in Postiz itself</h4>
          <ul style={{ margin: 0, paddingLeft: 18 }} className="sa-stack-sm">
            {tools.ui_only_features.map((feature) => (
              <li key={feature.name}><strong>{feature.name}</strong><div className="sa-small sa-muted">{feature.how}</div></li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

type MediaData = { media?: { pages: number; items: PostizMediaItem[] } | null; error?: string };
type ToolsData = { tools?: PostizToolsResponse | null; error?: string };

/** Load GET /postiz/tools once per page view (it calls Postiz, so never on polling). */
export function usePostizTools(): { tools: PostizToolsResponse | null; error: string; loading: boolean } {
  const fetcher = useFetcher<ToolsData>();
  const { load } = fetcher;
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    load("/app/social/postiz");
  }, [load]);
  return { tools: fetcher.data?.tools ?? null, error: fetcher.data?.error ?? "", loading: !fetcher.data };
}

export function mediaName(item: PostizMediaItem): string {
  return item.originalName || item.name || "";
}

/**
 * Search the Postiz media library (designs made with Postiz's design-editor templates)
 * and choose a search term; the engine rotates through the matching designs.
 */
export function PostizDesignPicker({ value, onChange }: { value: string; onChange: (search: string) => void }) {
  const fetcher = useFetcher<MediaData>();
  const [query, setQuery] = useState(value);
  const [page, setPage] = useState(1);
  const items = fetcher.data?.media?.items || [];
  const pages = fetcher.data?.media?.pages || 1;
  const search = (nextPage: number) => {
    setPage(nextPage);
    const params = new URLSearchParams({ view: "media", page: String(nextPage) });
    if (query.trim()) params.set("search", query.trim());
    fetcher.load(`/app/social/postiz?${params.toString()}`);
  };
  return (
    <div className="sa-stack-sm">
      <span className="sa-help">
        Design your graphics with Postiz&apos;s design-editor templates and save them to the Postiz media library with a common name,
        e.g. <code>black-friday-1.png</code>, <code>black-friday-2.png</code>. Enter that name here — matching designs are used in rotation.
      </span>
      <div className="sa-row">
        <input
          type="text" value={query} maxLength={80} onChange={(event) => setQuery(event.target.value)} placeholder="e.g. black-friday"
          aria-label="Postiz design search" style={{ maxWidth: 240 }}
          // Enter searches Postiz instead of submitting the form this picker sits in.
          onKeyDown={(event) => {
            if (event.key !== "Enter") return;
            event.preventDefault();
            if (fetcher.state === "idle") search(1);
          }}
        />
        <button type="button" className="sa-btn sa-btn-sm" onClick={() => search(1)} disabled={fetcher.state !== "idle"}>
          {fetcher.state !== "idle" ? "Searching Postiz…" : "Preview matches"}
        </button>
        <button type="button" className="sa-btn sa-btn-sm sa-btn-primary" onClick={() => onChange(query.trim())} disabled={query.trim() === value}>
          Use “{query.trim() || "…"}”
        </button>
        {value ? (
          <>
            <Badge tone="magic">Using: {value}</Badge>
            <button type="button" className="sa-btn sa-btn-sm sa-btn-plain" onClick={() => { onChange(""); setQuery(""); }}>Don&apos;t use designs</button>
          </>
        ) : null}
      </div>
      {fetcher.data?.error ? <span className="sa-error-text">{fetcher.data.error}</span> : null}
      {fetcher.data?.media && !items.length ? <span className="sa-small sa-muted">No designs in Postiz match “{query}”. Posts fall back to our own templates.</span> : null}
      {items.length ? (
        <>
          <div className="sa-grid-sm">
            {items.map((item, index) => {
              const name = mediaName(item);
              const src = item.thumbnail || item.path;
              return (
                <button
                  key={item.id || `${name}-${index}`} type="button" className="sa-card" style={{ padding: 6, cursor: "pointer", textAlign: "left" }}
                  onClick={() => { setQuery(name); onChange(name); }} title={`Use “${name}”`}
                >
                  {src && /\.(png|jpe?g|webp|gif|avif)(\?|$)/i.test(src) ? (
                    <img className="sa-media" src={src} alt={name} loading="lazy" style={{ aspectRatio: "1 / 1", objectFit: "cover" }} />
                  ) : <span className="sa-placeholder" style={{ aspectRatio: "1 / 1" }}>{humanize((src || "").split(".").pop() || "file")}</span>}
                  <span className="sa-small sa-ellipsis" style={{ display: "block", marginTop: 4 }}>{name || "Untitled"}</span>
                </button>
              );
            })}
          </div>
          {pages > 1 ? (
            <div className="sa-row sa-small">
              <button type="button" className="sa-btn sa-btn-sm" disabled={page <= 1 || fetcher.state !== "idle"} onClick={() => search(page - 1)}>← Previous</button>
              <span>Page {page} of {pages}</span>
              <button type="button" className="sa-btn sa-btn-sm" disabled={page >= pages || fetcher.state !== "idle"} onClick={() => search(page + 1)}>Next →</button>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
