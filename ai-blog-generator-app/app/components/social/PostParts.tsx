import { useState, type ReactNode } from "react";
import { Form, Link } from "react-router";

import type { PostReview, PostSummary, PostTarget } from "../../lib/social-engine.types";
import { epochToZonedInput, timezoneLabel } from "../../lib/social-time";
import { ChannelChip } from "./ChannelChip";
import { Badge, Field, Hidden, StatusBadge, TimeText, useBusy } from "./ui";
import { POSTIZ_TOOLS, PROVIDERS, TARGET_STATUS, humanize } from "./vocab";

export type ChannelLookup = Record<string, { name: string; picture: string; provider: string; maxLength?: number | null }>;

/** Channel names/pictures come with each target; build a lookup for review notes and editors. */
export function channelLookupFor(posts: PostSummary[]): ChannelLookup {
  const lookup: ChannelLookup = {};
  for (const post of posts) {
    for (const target of post.targets) {
      lookup[target.channel_id] = { name: target.channel_name || target.channel_id, picture: target.channel_picture, provider: target.provider };
    }
  }
  return lookup;
}

/** The post's products (title, image, link); ids the catalogue no longer has are shown as such. */
export function ProductChips({ post }: { post: Pick<PostSummary, "product_ids" | "products"> }) {
  const known = new Map((post.products || []).map((product) => [String(product.product_id), product]));
  if (!post.product_ids.length) return null;
  return (
    <div className="sa-row sa-small">
      {post.product_ids.map((id) => {
        const product = known.get(String(id));
        const label = product?.title || `Product #${id} (no longer synced)`;
        const body = (
          <>
            {product?.image ? <img className="sa-avatar" src={product.image} alt="" /> : null}
            <span className="sa-chip-label">{label}</span>
            {product && !product.available ? <Badge tone="warning">Unavailable</Badge> : null}
          </>
        );
        return product?.url
          ? <a key={id} href={product.url} target="_blank" rel="noreferrer" className="sa-chip">{body}</a>
          : <span key={id} className="sa-chip">{body}</span>;
      })}
    </div>
  );
}

/** Badges for the Postiz tools a post was made with. */
export function PostizToolBadges({ tools }: { tools?: string[] }) {
  if (!tools?.length) return null;
  return (
    <span className="sa-row" style={{ gap: 4 }}>
      {tools.map((tool) => <Badge key={tool} tone="magic">{POSTIZ_TOOLS[tool] || humanize(tool)}</Badge>)}
    </span>
  );
}

const EDITABLE_POST = new Set(["pending_approval", "approved", "failed"]);
const EDITABLE_TARGET = new Set(["pending", "failed"]);

export function canApprove(post: PostSummary): boolean {
  return ["pending_approval", "failed"].includes(post.status)
    && post.targets.some((target) => EDITABLE_TARGET.has(target.status));
}
export const canPublishNow = (post: PostSummary) => EDITABLE_POST.has(post.status);
export const canReject = (post: PostSummary) => ["planned", "pending_approval", "approved", "failed"].includes(post.status);
export const canRegenerate = (post: PostSummary) => ["planned", "pending_approval", "approved", "failed", "rejected"].includes(post.status);
export const canCancel = (post: PostSummary) => !["published", "cancelled"].includes(post.status);
export const canRetry = (post: PostSummary) =>
  post.status === "failed" || post.targets.some((target) => target.status === "failed");
export const isEditable = (post: PostSummary) =>
  EDITABLE_POST.has(post.status) && post.targets.some((target) => EDITABLE_TARGET.has(target.status));

function ActionButton({ post, intent, label, busyLabel, className = "sa-btn" }: {
  post: PostSummary; intent: string; label: string; busyLabel: string; className?: string;
}) {
  const { busy, isSubmitting } = useBusy();
  const mine = isSubmitting(intent, ["post_id", post.id]);
  return (
    <Form method="post" preventScrollReset className="sa-inline-form">
      <Hidden values={{ intent, post_id: post.id }} />
      <button type="submit" className={className} disabled={busy}>{mine ? busyLabel : label}</button>
    </Form>
  );
}

/** Approve / publish now / retry / regenerate / reject / cancel for one post. */
export function PostActions({ post }: { post: PostSummary }) {
  const { busy, isSubmitting } = useBusy();
  return (
    <div className="sa-stack-sm">
      <div className="sa-row">
        {canApprove(post) ? <ActionButton post={post} intent="approve" label="Approve" busyLabel="Approving…" className="sa-btn sa-btn-success" /> : null}
        {canPublishNow(post) ? <ActionButton post={post} intent="publish_now" label="Publish now" busyLabel="Publishing…" /> : null}
        {canRetry(post) ? <ActionButton post={post} intent="retry" label="Retry failed" busyLabel="Retrying…" /> : null}
      </div>
      {canRegenerate(post) || canReject(post) || canCancel(post) ? (
        <details className="sa-plain">
          <summary className="sa-small sa-muted">More actions: regenerate, reject, cancel</summary>
          <div className="sa-stack" style={{ marginTop: 8 }}>
            {canRegenerate(post) ? (
              <Form method="post" preventScrollReset className="sa-stack-sm">
                <Hidden values={{ intent: "regenerate", post_id: post.id }} />
                <div className="sa-grid-2">
                  <Field label="New brief (optional)" help="Extra instructions for Gemma. Leave blank to keep the current brief.">
                    <textarea name="brief" rows={2} maxLength={2000} placeholder={post.brief || "e.g. Focus on the morning routine"} />
                  </Field>
                  <Field label="New angle (optional)">
                    <input type="text" name="angle" maxLength={500} placeholder={post.angle || "e.g. Gift idea"} />
                  </Field>
                </div>
                <div>
                  <button type="submit" className="sa-btn" disabled={busy}>
                    {isSubmitting("regenerate", ["post_id", post.id]) ? "Regenerating…" : "Regenerate captions & images"}
                  </button>
                </div>
              </Form>
            ) : null}
            {canReject(post) ? (
              <Form method="post" preventScrollReset className="sa-row">
                <Hidden values={{ intent: "reject", post_id: post.id }} />
                <input type="text" name="reason" maxLength={1000} placeholder="Reason (optional)" aria-label="Reason for rejecting" style={{ maxWidth: 320 }} />
                <button type="submit" className="sa-btn sa-btn-critical" disabled={busy}>
                  {isSubmitting("reject", ["post_id", post.id]) ? "Rejecting…" : "Reject"}
                </button>
              </Form>
            ) : null}
            {canCancel(post) ? (
              <Form method="post" preventScrollReset className="sa-row">
                <Hidden values={{ intent: "cancel", post_id: post.id }} />
                <button type="submit" className="sa-btn sa-btn-critical" disabled={busy}>
                  {isSubmitting("cancel", ["post_id", post.id]) ? "Cancelling…" : "Cancel post"}
                </button>
                <span className="sa-help">Also deletes it from Postiz if it was already scheduled there.</span>
              </Form>
            ) : null}
          </div>
        </details>
      ) : null}
    </div>
  );
}

function CaptionField({ name, label, defaultValue, max, rows = 4, required }: {
  name: string; label: string; defaultValue: string; max?: number | null; rows?: number; required?: boolean;
}) {
  const [length, setLength] = useState(defaultValue.length);
  const over = Boolean(max && length > max);
  return (
    <Field label={label}>
      <textarea name={name} rows={rows} defaultValue={defaultValue} required={required} onChange={(event) => setLength(event.target.value.length)} />
      {max ? <span className={`sa-counter ${over ? "sa-counter-over" : ""}`}>{length.toLocaleString("en-GB")} / {max.toLocaleString("en-GB")} characters{over ? " — too long for this network" : ""}</span> : null}
    </Field>
  );
}

function targetMax(target: PostTarget, channels: ChannelLookup): number | null {
  return channels[target.channel_id]?.maxLength || PROVIDERS[target.provider]?.maxLength || null;
}

export function TargetHeader({ target, channels, tz }: { target: PostTarget; channels: ChannelLookup; tz: string }) {
  const channel = channels[target.channel_id];
  return (
    <div className="sa-spread">
      <ChannelChip provider={target.provider} name={target.channel_name || channel?.name || target.channel_id} picture={target.channel_picture || channel?.picture} />
      <div className="sa-row">
        {target.format ? <Badge tone="neutral">{humanize(target.format)}</Badge> : null}
        <StatusBadge map={TARGET_STATUS} status={target.status} />
        {target.scheduled_at ? <span className="sa-small sa-muted"><TimeText epoch={target.scheduled_at} tz={tz} /></span> : null}
      </div>
    </div>
  );
}

/** Per-channel captions, first comment, title and time — editable before publishing. */
export function PostEditor({ post, channels, tz }: { post: PostSummary; channels: ChannelLookup; tz: string }) {
  const { busy, isSubmitting } = useBusy();
  const editable = isEditable(post);
  const scheduled = epochToZonedInput(post.scheduled_at, tz);

  if (!editable) {
    return (
      <div className="sa-stack-sm">
        {post.targets.map((target) => (
          <div key={target.id} className="sa-target sa-stack-sm">
            <TargetHeader target={target} channels={channels} tz={tz} />
            {target.title ? <div><span className="sa-muted">Title:</span> {target.title}</div> : null}
            <div className="sa-prewrap">{target.caption || <span className="sa-muted">No caption yet.</span>}</div>
            {target.first_comment ? <div className="sa-small"><span className="sa-muted">First comment:</span> <span className="sa-prewrap">{target.first_comment}</span></div> : null}
            {target.release_url ? <a href={target.release_url} target="_blank" rel="noreferrer">View the live post ↗</a> : null}
            {target.error_message ? <div className="sa-error-text">{target.error_type ? `${target.error_type}: ` : ""}{target.error_message}</div> : null}
          </div>
        ))}
      </div>
    );
  }

  return (
    <Form method="post" preventScrollReset className="sa-stack">
      <Hidden values={{ intent: "edit", post_id: post.id, tz, scheduled_original: scheduled }} />
      {post.targets.map((target) => {
        const provider = PROVIDERS[target.provider];
        const open = EDITABLE_TARGET.has(target.status);
        return (
          <div key={target.id} className="sa-target sa-stack-sm">
            <TargetHeader target={target} channels={channels} tz={tz} />
            {target.error_message ? <div className="sa-error-text">{target.error_type ? `${target.error_type}: ` : ""}{target.error_message}</div> : null}
            {open ? (
              <>
                {(provider?.titleMax || target.title) ? (
                  <Field label="Title" help={provider?.titleMax ? `Up to ${provider.titleMax} characters.` : undefined}>
                    <input type="text" name={`title:${target.channel_id}`} defaultValue={target.title} maxLength={provider?.titleMax || undefined} />
                  </Field>
                ) : null}
                <CaptionField name={`caption:${target.channel_id}`} label="Caption" defaultValue={target.caption} max={targetMax(target, channels)} rows={5} required />
                {(provider?.supportsComments ?? true) || target.first_comment ? (
                  <CaptionField name={`first_comment:${target.channel_id}`} label="First comment (optional)" defaultValue={target.first_comment} rows={2} />
                ) : null}
                <label className="sa-check sa-small">
                  <input type="checkbox" name="remove_channel_ids" value={target.channel_id} />
                  <span>Don&apos;t post to this channel</span>
                </label>
              </>
            ) : (
              <div className="sa-prewrap sa-muted">{target.caption}</div>
            )}
          </div>
        );
      })}
      <div className="sa-row-top">
        <Field label="Scheduled time" help={`Store time zone: ${timezoneLabel(tz)}. At least 2 minutes from now.`}>
          <input type="datetime-local" name="scheduled_local" defaultValue={scheduled} />
        </Field>
      </div>
      <div className="sa-form-actions">
        <button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>
          {isSubmitting("edit", ["post_id", post.id]) ? "Saving…" : "Save edits"}
        </button>
      </div>
    </Form>
  );
}

const SEVERITY_TONE: Record<string, "critical" | "warning" | "info"> = { block: "critical", warning: "warning", info: "info" };

/** Content-guard issues, Gemma's review and generation warnings. */
export function PostReviewPanel({ review, warnings, channels }: { review?: PostReview; warnings?: string[]; channels?: ChannelLookup }) {
  const guard = review?.guard;
  const ai = review?.ai_review;
  const issues = guard?.issues || [];
  if (!issues.length && !ai && !(warnings || []).length && !guard?.hold) return null;
  return (
    <div className="sa-stack-sm">
      {guard?.hold ? (
        <div className="sa-banner sa-banner-warning">
          <strong>Held for review by the content guard.</strong> This post needs a person to approve it, even in automatic mode.
        </div>
      ) : null}
      {issues.length ? (
        <div className="sa-stack-sm">
          <h4>Content guard</h4>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {issues.map((issue, index) => (
              <li key={`${issue.code}-${index}`}>
                <Badge tone={SEVERITY_TONE[issue.severity] || "neutral"}>{issue.severity === "block" ? "Must fix" : humanize(issue.severity)}</Badge>{" "}
                {issue.channel_id && channels?.[issue.channel_id] ? <span className="sa-muted">{channels[issue.channel_id].name}: </span> : null}
                <span className="sa-break">{issue.message}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {ai ? (
        <div className="sa-stack-sm">
          <div className="sa-row">
            <h4>Gemma review</h4>
            {ai.error ? <Badge tone="warning">Review failed</Badge> : (
              <>
                {typeof ai.score === "number" ? <Badge tone={ai.score >= 8 ? "success" : ai.score >= 6 ? "warning" : "critical"}>{ai.score}/10</Badge> : null}
                {ai.approve !== undefined ? <Badge tone={ai.approve ? "success" : "attention"}>{ai.approve ? "Recommends publishing" : "Recommends changes"}</Badge> : null}
              </>
            )}
            {ai.model ? <span className="sa-small sa-muted">{ai.model}</span> : null}
          </div>
          {ai.error ? <div className="sa-error-text">{ai.error}</div> : null}
          {ai.issues?.length ? (
            <div><span className="sa-muted">Issues:</span>
              <ul style={{ margin: "2px 0 0", paddingLeft: 18 }}>{ai.issues.map((item, index) => <li key={index}>{item}</li>)}</ul>
            </div>
          ) : null}
          {ai.suggestions?.length ? (
            <div><span className="sa-muted">Suggestions:</span>
              <ul style={{ margin: "2px 0 0", paddingLeft: 18 }}>{ai.suggestions.map((item, index) => <li key={index}>{item}</li>)}</ul>
            </div>
          ) : null}
        </div>
      ) : null}
      {(warnings || []).length ? (
        <div className="sa-stack-sm">
          <h4>Warnings</h4>
          <ul style={{ margin: 0, paddingLeft: 18 }}>{(warnings || []).map((warning, index) => <li key={index} className="sa-break">{warning}</li>)}</ul>
        </div>
      ) : null}
    </div>
  );
}

export function PostLink({ post, children }: { post: Pick<PostSummary, "id">; children: ReactNode }) {
  return <Link to={`/app/social/posts/${post.id}`}>{children}</Link>;
}
