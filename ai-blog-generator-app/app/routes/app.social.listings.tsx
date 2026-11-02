import { useState } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, useActionData, useLoaderData } from "react-router";

import { useSocial } from "../components/social/context";
import { ActionBanner, Badge, Banner, Card, Field, Hidden, LoadError, SectionHeader, TimeText, useBusy, usePolling } from "../components/social/ui";
import { SocialEngineError, errorMessage, formBool, formString, runIntent, safeId, seFetch, seg } from "../lib/social-engine.server";
import type { ListingProfile } from "../lib/social-engine.types";
import { authenticate } from "../shopify.server";

export const handle = { title: "Listings" };

type FillSummary = {
  state?: "queued" | "running" | "filled" | "no_form" | "error"; message?: string; page?: string; tab?: string;
  filled?: Array<{ label: string; field: string; chars: number; source: string }>;
  left_for_you?: Array<{ label: string; reason: string }>; unmatched?: string[];
};
type CheckSummary = { ok?: boolean; status_code?: number | null; message?: string; checked_at?: number };
type DirectoryRow = {
  id: string; name: string; kind: string; kind_label: string; url: string; submit_url: string; why: string; notes: string;
  cost: string; trust: number; reach: number; needs: string[]; needs_labels: string[]; fill: "relay" | "manual" | "feed" | "done";
  status: string; status_label: string; listing_url: string; notes_owner: string; last_fill: FillSummary; last_check: CheckSummary;
  filled_at: number | null; submitted_at: number | null; live_at: number | null; checked_at: number | null; blocked: string;
};
type Established = { score: number; checks: Array<{ label: string; points: number; ok: boolean }>; next: string[] };
type PresenceQuery = {
  query: string; store_found: boolean; directories_found: string[]; missing: string[]; social: string[]; established: Established;
  results: Array<{ title: string; url: string; host: string; who: string; label: string; first: boolean }>;
};
type Presence = { checked_at: number; queries: PresenceQuery[]; summary?: { established?: Established } } | null;
type ListingsResponse = {
  profile: ListingProfile; missing: { required: string[]; recommended: string[] }; profile_ready: boolean;
  directories: DirectoryRow[]; counts: Record<string, number>; kinds: Record<string, string>;
  status_labels: Record<string, string>; presence: Presence;
  settings: { relay_dir: string; check_interval_days: number };
};

const EMPTY: ListingsResponse = {
  profile: {} as ListingProfile, missing: { required: [], recommended: [] }, profile_ready: false, directories: [], counts: {},
  kinds: {}, status_labels: {}, presence: null, settings: { relay_dir: "", check_interval_days: 7 },
};

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  try {
    return { ...(await seFetch<ListingsResponse>("/listings")), error: "" };
  } catch (error) {
    return { ...EMPTY, error: errorMessage(error) };
  }
};

const LIST_FIELDS = ["categories", "keywords", "services", "payment_methods"] as const;
const SOCIAL_KEYS = ["instagram", "facebook", "tiktok", "youtube", "linkedin", "x", "pinterest"] as const;
const TEXT_FIELDS = [
  "business_name", "tagline", "short_description", "description", "long_description", "founder_name", "founder_bio", "email",
  "phone", "website", "address_line1", "address_line2", "city", "postcode", "country", "service_area", "hours", "logo_url",
  "cover_image_url", "price_range",
] as const;

function splitList(value: string): string[] {
  return value.split(/[\n,]/).map((v) => v.trim()).filter(Boolean);
}

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  const directoryId = formString(form, "directory_id");
  return runIntent(intent, async () => {
    switch (intent) {
      case "save_profile": {
        const profile: Record<string, unknown> = {};
        for (const key of TEXT_FIELDS) profile[key] = formString(form, key);
        for (const key of LIST_FIELDS) profile[key] = splitList(formString(form, key));
        const year = formString(form, "founded_year");
        profile.founded_year = year ? Number(year) : null;
        const social: Record<string, string> = {};
        for (const key of SOCIAL_KEYS) {
          const link = formString(form, `social_${key}`);
          if (link) social[key] = link;
        }
        profile.social_links = social;
        await seFetch("/listings/profile", { body: { profile } });
        return { message: "Profile saved. Every directory is filled from it." };
      }
      case "suggest": {
        const result = await seFetch<{ changed: string[]; warnings: string[]; model: string }>("/listings/profile/suggest", {
          body: { overwrite: formBool(form, "overwrite") }, timeoutMs: 600_000,
        });
        const changed = result.changed.length ? `Filled in: ${result.changed.map((c) => c.replace(/_/g, " ")).join(", ")}.` : "Nothing was empty, so nothing changed (tick 'replace what is there' to rewrite).";
        const warnings = result.warnings.length ? ` Check before you use it: ${result.warnings.join(" · ")}` : "";
        return { message: `${changed} Written by ${result.model}; edit anything, then Save.${warnings}` };
      }
      case "fill": {
        const result = await seFetch<{ already_queued: boolean }>(`/listings/${seg(safeId(directoryId))}/fill`, { body: { tab: formString(form, "tab") } });
        return { message: result.already_queued ? "Already filling; watch Chrome." : "Filling the form in Chrome now. Check the fields there, then press the site's own submit button." };
      }
      case "set_status": {
        await seFetch(`/listings/${seg(safeId(directoryId))}/status`, {
          body: { status: formString(form, "status"), listing_url: formString(form, "listing_url"), notes: formString(form, "notes") },
        });
        return { message: "Updated." };
      }
      case "check": {
        const result = await seFetch<{ check: CheckSummary }>(`/listings/${seg(safeId(directoryId))}/check`, { method: "POST", timeoutMs: 90_000 });
        return { message: result.check.message || "Checked." };
      }
      case "presence": {
        const result = await seFetch<{ already_queued: boolean }>("/listings/presence", { method: "POST" });
        return { message: result.already_queued ? "A search is already running." : "Searching the brand in Chrome; results appear here in a minute." };
      }
      case "relay": {
        const result = await seFetch<{ ok: boolean; tabs?: number; error?: string; relay_dir: string }>("/listings/relay", { query: {} });
        if (!result.ok) throw new SocialEngineError(`Chrome is not reachable: ${result.error} (relay folder ${result.relay_dir}).`);
        return { message: `Chrome is connected: ${result.tabs} open tab${result.tabs === 1 ? "" : "s"}.` };
      }
      default:
        throw new SocialEngineError(`Unknown action "${intent}".`);
    }
  }, directoryId || undefined);
};

const STATUS_TONE: Record<string, "neutral" | "info" | "success" | "warning" | "critical"> = {
  todo: "neutral", filled: "info", submitted: "info", verifying: "warning", live: "success", broken: "critical", skipped: "neutral",
};
const COST_LABEL: Record<string, string> = { free: "Free", freemium: "Free plan", paid: "Paid", pay_per_lead: "Pay per lead" };

export default function Listings() {
  const data = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>();
  const { tz } = useSocial();
  const { busy, isSubmitting } = useBusy();
  const working = data.directories.some((d) => d.last_fill?.state === "queued" || d.last_fill?.state === "running");
  usePolling(working || isSubmitting("presence"), 5_000);

  if (data.error) return <LoadError error={data.error} />;
  const live = data.counts.live || 0;
  const total = data.directories.length;
  const presence = data.presence?.queries?.[0];
  const established = presence?.established;

  return (
    <div className="sa-stack">
      <ActionBanner result={result} />

      <Card>
        <SectionHeader
          title="Look established everywhere a buyer checks"
          description={
            <>
              One profile, copied word for word to every directory a careful client might look at: Google, a review site, the UK
              directories, the places the workshops are sold. The engine prefills each form in your Chrome and stops before the
              submit button; accounts, submits and verifications are yours. {live} of {total} live.
            </>
          }
          actions={
            <div className="sa-row">
              <Form method="post"><Hidden values={{ intent: "relay" }} /><button type="submit" className="sa-btn" disabled={busy}>{isSubmitting("relay") ? "Checking…" : "Test Chrome link"}</button></Form>
              <Form method="post"><Hidden values={{ intent: "presence" }} /><button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>{isSubmitting("presence") ? "Searching…" : "What does a client find?"}</button></Form>
            </div>
          }
        />
        {presence && established ? (
          <div className="sa-grid sa-grid-2">
            <Card muted>
              <h3 style={{ margin: "0 0 6px" }}>Established score: {established.score}/100</h3>
              <div className="sa-muted sa-small">Searched {presence.query} in your Chrome <TimeText epoch={data.presence?.checked_at} tz={tz} relative />.</div>
              <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                {established.checks.map((check) => (
                  <li key={check.label}>
                    <Badge tone={check.ok ? "success" : "neutral"}>{check.ok ? "yes" : "no"}</Badge> {check.label} <span className="sa-muted">({check.points})</span>
                  </li>
                ))}
              </ul>
            </Card>
            <Card muted>
              <h3 style={{ margin: "0 0 6px" }}>Do next</h3>
              {established.next.length ? <ol style={{ margin: 0, paddingLeft: 18 }}>{established.next.map((step) => <li key={step}>{step}</li>)}</ol> : <p>Everything a buyer checks is in place. Keep the reviews coming.</p>}
              <details style={{ marginTop: 8 }}>
                <summary>First page of results ({presence.results.length})</summary>
                <ul style={{ paddingLeft: 18 }}>
                  {presence.results.map((r) => (
                    <li key={r.url} className="sa-small sa-break">
                      <Badge tone={r.who === "store" ? "success" : r.who === "directory" ? "info" : "neutral"}>{r.label}</Badge>{" "}
                      <a href={r.url} target="_blank" rel="noreferrer">{r.title || r.url}</a>
                    </li>
                  ))}
                </ul>
                {data.presence?.queries?.[1] ? (
                  <p className="sa-small sa-muted">
                    {data.presence.queries[1].query}: {data.presence.queries[1].results.length} results, store {data.presence.queries[1].store_found ? "found" : "not found"}.
                  </p>
                ) : null}
              </details>
            </Card>
          </div>
        ) : (
          <p className="sa-muted">No search yet. Press “What does a client find?” and the engine searches the business name in your Chrome, then scores how established it looks.</p>
        )}
      </Card>

      <ProfileCard data={data} busy={busy} suggesting={isSubmitting("suggest")} />

      <Card>
        <SectionHeader title="Directories" description="Highest trust first. Fill in Chrome prefills the open form (sign in and get to the form first if the site needs it); then press the site's submit, come back and mark it submitted, and paste the live link once it is up." />
        {Object.entries(data.kinds).map(([kind, label]) => {
          const rows = data.directories.filter((d) => d.kind === kind);
          if (!rows.length) return null;
          return (
            <div key={kind} className="sa-stack" style={{ marginBottom: 14 }}>
              <h3 style={{ margin: "6px 0 0" }}>{label}</h3>
              {rows.map((row) => <DirectoryCard key={row.id} row={row} busy={busy} filling={isSubmitting("fill", ["directory_id", row.id])} tz={tz} />)}
            </div>
          );
        })}
      </Card>
    </div>
  );
}

function ProfileCard({ data, busy, suggesting }: { data: ListingsResponse; busy: boolean; suggesting: boolean }) {
  const p = data.profile;
  const [open, setOpen] = useState(!data.profile_ready);
  return (
    <Card accent={data.profile_ready ? undefined : "warning"}>
      <SectionHeader
        title="The one profile every directory copies"
        description={
          data.profile_ready
            ? <>Complete. {data.missing.recommended.length ? <>Nice to add: {data.missing.recommended.join(", ")}.</> : "Keep it identical to the Google Business Profile."}</>
            : <>Still needed before anything can be filled: <strong>{data.missing.required.join(", ")}</strong>. Copy them from the Google Business Profile so every listing matches.</>
        }
        actions={<button type="button" className="sa-btn" onClick={() => setOpen((v) => !v)}>{open ? "Hide" : "Edit profile"}</button>}
      />
      {open ? (
        <div className="sa-stack">
          <Form method="post" className="sa-row">
            <Hidden values={{ intent: "suggest" }} />
            <button type="submit" className="sa-btn" disabled={busy}>{suggesting ? "Writing… (a minute or two)" : "Write the descriptions for me"}</button>
            <label className="sa-small"><input type="checkbox" name="overwrite" value="1" /> replace what is there</label>
            <span className="sa-muted sa-small">Gemma drafts tagline, descriptions, founder bio, keywords and categories from the brand kit and the catalogue; every line is checked for claims.</span>
          </Form>
          <Form method="post" className="sa-stack">
            <Hidden values={{ intent: "save_profile" }} />
            <div className="sa-grid sa-grid-2">
              <Field label="Business name" help="Exactly as on Google."><input name="business_name" type="text" defaultValue={p.business_name} required className="sa-input" /></Field>
              <Field label="Tagline" help="Under 100 characters."><input name="tagline" type="text" maxLength={120} defaultValue={p.tagline} className="sa-input" /></Field>
              <Field label="Email"><input name="email" type="email" defaultValue={p.email} className="sa-input" /></Field>
              <Field label="Phone" help="Same digits everywhere; it is how directories are matched."><input name="phone" type="text" defaultValue={p.phone} className="sa-input" /></Field>
              <Field label="Website"><input name="website" type="url" defaultValue={p.website} className="sa-input" /></Field>
              <Field label="Founded (year)"><input name="founded_year" type="number" min={1900} max={2100} defaultValue={p.founded_year ?? ""} className="sa-input" /></Field>
            </div>
            <Field label="Short description" help="One or two sentences, under 250 characters; used where space is tight.">
              <textarea name="short_description" maxLength={300} defaultValue={p.short_description} rows={2} />
            </Field>
            <Field label="Description" help="Under 700 characters; the standard directory text.">
              <textarea name="description" maxLength={1200} defaultValue={p.description} rows={4} />
            </Field>
            <Field label="Long description" help="Up to 2,000 characters for sites that allow it (Hotfrog, Eventbrite, LinkedIn).">
              <textarea name="long_description" maxLength={4000} defaultValue={p.long_description} rows={6} />
            </Field>
            <div className="sa-grid sa-grid-2">
              <Field label="Founder name"><input name="founder_name" type="text" defaultValue={p.founder_name} className="sa-input" /></Field>
              <Field label="Categories" help="Comma separated; the first is used where a site asks for one."><input name="categories" type="text" defaultValue={p.categories?.join(", ")} className="sa-input" /></Field>
            </div>
            <Field label="Founder bio" help="Third person, under 900 characters; used on coaching and event sites.">
              <textarea name="founder_bio" maxLength={1500} defaultValue={p.founder_bio} rows={3} />
            </Field>
            <div className="sa-grid sa-grid-2">
              <Field label="Services" help="Comma separated."><input name="services" type="text" defaultValue={p.services?.join(", ")} className="sa-input" /></Field>
              <Field label="Keywords" help="8 to 12 search phrases, comma separated."><input name="keywords" type="text" defaultValue={p.keywords?.join(", ")} className="sa-input" /></Field>
              <Field label="Address line 1" help="Leave empty if there is no public address; sites that need one are skipped."><input name="address_line1" type="text" defaultValue={p.address_line1} className="sa-input" /></Field>
              <Field label="Address line 2"><input name="address_line2" type="text" defaultValue={p.address_line2} className="sa-input" /></Field>
              <Field label="City"><input name="city" type="text" defaultValue={p.city} className="sa-input" /></Field>
              <Field label="Postcode"><input name="postcode" type="text" defaultValue={p.postcode} className="sa-input" /></Field>
              <Field label="Country"><input name="country" type="text" defaultValue={p.country} className="sa-input" /></Field>
              <Field label="Service area"><input name="service_area" type="text" defaultValue={p.service_area} className="sa-input" /></Field>
              <Field label="Opening hours"><input name="hours" type="text" defaultValue={p.hours} className="sa-input" /></Field>
              <Field label="Price range"><input name="price_range" type="text" defaultValue={p.price_range} className="sa-input" /></Field>
              <Field label="Payment methods" help="Comma separated."><input name="payment_methods" type="text" defaultValue={p.payment_methods?.join(", ")} className="sa-input" /></Field>
              <Field label="Logo URL" help="A square PNG on the store CDN."><input name="logo_url" type="url" defaultValue={p.logo_url} className="sa-input" /></Field>
              <Field label="Cover image URL"><input name="cover_image_url" type="url" defaultValue={p.cover_image_url} className="sa-input" /></Field>
            </div>
            <fieldset className="sa-fieldset">
              <legend>Social links (same name and logo everywhere)</legend>
              <div className="sa-grid sa-grid-2">
                {SOCIAL_KEYS.map((key) => (
                  <Field key={key} label={key === "x" ? "X (Twitter)" : key[0].toUpperCase() + key.slice(1)}>
                    <input name={`social_${key}`} type="url" defaultValue={p.social_links?.[key] || ""} className="sa-input" />
                  </Field>
                ))}
              </div>
            </fieldset>
            <div><button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>Save profile</button></div>
          </Form>
        </div>
      ) : null}
    </Card>
  );
}

function DirectoryCard({ row, busy, filling, tz }: { row: DirectoryRow; busy: boolean; filling: boolean; tz: string }) {
  const [editing, setEditing] = useState(false);
  const fill = row.last_fill || {};
  const inProgress = fill.state === "queued" || fill.state === "running";
  const canFill = row.fill === "relay" && !row.blocked && row.status !== "live" && row.status !== "skipped";
  return (
    <Card muted accent={row.status === "broken" ? "critical" : undefined}>
      <div className="sa-row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div style={{ minWidth: 0 }}>
          <strong><a href={row.listing_url || row.url} target="_blank" rel="noreferrer">{row.name}</a></strong>
          <span className="sa-muted sa-small"> · trust {row.trust}/10 · reach {row.reach}/10 · {COST_LABEL[row.cost] || row.cost}{row.needs_labels.length ? ` · needs ${row.needs_labels.join(", ")}` : ""}</span>
          <div>{row.why}</div>
          {row.notes ? <div className="sa-muted sa-small">{row.notes}</div> : null}
        </div>
        <Badge tone={STATUS_TONE[row.status] || "neutral"}>{row.status_label}</Badge>
      </div>

      {row.blocked ? <p className="sa-muted sa-small">Not now: {row.blocked}.</p> : null}
      {fill.state === "filled" ? (
        <Banner tone="info" title={`Filled in Chrome · ${fill.filled?.length || 0} fields`}>
          {fill.message}
          {fill.left_for_you?.length ? <div className="sa-small">Left for you: {fill.left_for_you.map((f) => `${f.label} (${f.reason})`).join("; ")}.</div> : null}
          {fill.unmatched?.length ? <div className="sa-small">Not recognised: {fill.unmatched.join(", ")}.</div> : null}
        </Banner>
      ) : null}
      {fill.state === "no_form" || fill.state === "error" ? <Banner tone="warning">{fill.message}</Banner> : null}
      {inProgress ? <Banner tone="info">Filling in Chrome…</Banner> : null}
      {row.last_check?.message ? (
        <p className="sa-small">
          <Badge tone={row.last_check.ok ? "success" : "warning"}>{row.last_check.ok ? "checked" : "check"}</Badge> {row.last_check.message}
          {row.checked_at ? <span className="sa-muted"> · <TimeText epoch={row.checked_at} tz={tz} relative /></span> : null}
        </p>
      ) : null}
      {row.notes_owner ? <p className="sa-small sa-muted">Your note: {row.notes_owner}</p> : null}

      <div className="sa-row">
        {canFill ? (
          <Form method="post"><Hidden values={{ intent: "fill", directory_id: row.id }} /><button type="submit" className="sa-btn sa-btn-primary" disabled={busy || inProgress}>{filling || inProgress ? "Filling…" : row.status === "filled" ? "Fill again" : "Fill in Chrome"}</button></Form>
        ) : null}
        {row.fill !== "done" && row.status !== "live" ? (
          <>
            {row.status !== "submitted" && row.status !== "verifying" ? <StatusButton row={row} status="submitted" label="Mark submitted" busy={busy} /> : null}
            {row.status === "submitted" ? <StatusButton row={row} status="verifying" label="Waiting for verification" busy={busy} /> : null}
          </>
        ) : null}
        <button type="button" className="sa-btn" onClick={() => setEditing((v) => !v)}>{row.status === "live" ? "Edit link or note" : "Mark live / add note"}</button>
        {row.listing_url && row.fill !== "done" ? (
          <Form method="post"><Hidden values={{ intent: "check", directory_id: row.id }} /><button type="submit" className="sa-btn" disabled={busy}>Check it is still up</button></Form>
        ) : null}
        {row.status !== "skipped" && row.status !== "live" ? <StatusButton row={row} status="skipped" label="Skip" plain busy={busy} /> : null}
        {row.status === "skipped" ? <StatusButton row={row} status="todo" label="Un-skip" plain busy={busy} /> : null}
      </div>

      {editing ? (
        <Form method="post" className="sa-stack" style={{ marginTop: 8 }}>
          <Hidden values={{ intent: "set_status", directory_id: row.id }} />
          <div className="sa-grid sa-grid-2">
            <Field label="Status">
              <select name="status" defaultValue="live" className="sa-input">
                {["live", "submitted", "verifying", "todo", "broken", "skipped"].map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </Field>
            <Field label="Link to the live listing" help="Needed for the weekly check."><input name="listing_url" type="url" defaultValue={row.listing_url} className="sa-input" placeholder="https://…" /></Field>
          </div>
          <Field label="Note (optional)"><input name="notes" type="text" maxLength={300} defaultValue={row.notes_owner} className="sa-input" placeholder="login email used, what is pending…" /></Field>
          <div><button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>Save</button></div>
        </Form>
      ) : null}
    </Card>
  );
}

function StatusButton({ row, status, label, busy, plain = false }: { row: DirectoryRow; status: string; label: string; busy: boolean; plain?: boolean }) {
  return (
    <Form method="post">
      <Hidden values={{ intent: "set_status", directory_id: row.id, status, listing_url: row.listing_url, notes: row.notes_owner }} />
      <button type="submit" className={`sa-btn ${plain ? "sa-btn-plain" : ""}`} disabled={busy}>{label}</button>
    </Form>
  );
}
