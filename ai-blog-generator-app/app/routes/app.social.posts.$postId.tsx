import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useLoaderData } from "react-router";

import { MediaPreview, mediaCaption } from "../components/social/MediaPreview";
import {
  PostActions, PostEditor, PostReviewPanel, PostizToolBadges, ProductChips, TargetHeader, channelLookupFor,
} from "../components/social/PostParts";
import { useSocial } from "../components/social/context";
import {
  ActionBanner, Badge, Card, Hidden, JsonBlock, LoadError, StatusBadge, TimeText, usePolling, useBusy,
} from "../components/social/ui";
import {
  ACTIVITY_LEVEL, JOB_STATUS, POST_STATUS, PUBLISH_MODE_LABEL, approvalLabel, contentTypeLabel, formatNumber, humanize,
  providerLabel,
} from "../components/social/vocab";
import {
  errorMessage, formString, handlePostIntent, runIntent, safeId, seFetch, seg, withSignedMedia, type PostDetailResponse,
} from "../lib/social-engine.server";
import { authenticate } from "../shopify.server";

export const handle = { title: "Post details" };

export const loader = async ({ request, params }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  try {
    const postId = safeId(params.postId);
    const detail = await seFetch<PostDetailResponse>(`/posts/${seg(postId)}`);
    return { detail: { ...detail, post: withSignedMedia(detail.post) }, error: "" };
  } catch (error) {
    return { detail: null, error: errorMessage(error) };
  }
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  if (intent === "job_retry" || intent === "job_cancel") {
    return runIntent(intent, async () => {
      const jobId = safeId(formString(form, "job_id"));
      await seFetch(`/jobs/${seg(jobId)}/${intent === "job_retry" ? "retry" : "cancel"}`, { body: {} });
      return { message: intent === "job_retry" ? "Job queued to run again." : "Job cancelled." };
    });
  }
  return handlePostIntent(intent, form);
};

const IN_PROGRESS = new Set(["planned", "generating", "approved", "publishing"]);

export default function PostDetailPage() {
  const { detail, error } = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>();
  const { tz, contentTypes, rules } = useSocial();
  const { busy, isSubmitting } = useBusy();
  const running = Boolean(detail && (IN_PROGRESS.has(detail.post.status)
    || detail.post.targets.some((target) => ["publishing", "uncertain"].includes(target.status))
    || detail.jobs.some((job) => ["queued", "running"].includes(job.status))));
  usePolling(running, 8_000);

  if (!detail) {
    return (
      <>
        <LoadError error={error} />
        <s-section><Link to="/app/social/queue">← Back to the approval queue</Link></s-section>
      </>
    );
  }
  const { post, generation, timeline, jobs, api_calls: apiCalls, decisions, metrics } = detail;
  const channels = channelLookupFor([post]);
  const postizTools = post.postiz_tools?.length ? post.postiz_tools : generation.postiz_tools || [];
  const formats = Array.from(new Set(post.media.map((media) => media.format || media.kind)));

  return (
    <>
      <div className="sa-row"><Link to="/app/social/queue">← Approval queue</Link></div>
      <ActionBanner result={result} />

      <s-section>
        <div className="sa-stack">
          <div className="sa-spread">
            <div className="sa-row">
              <h2 style={{ fontSize: "1.05rem" }} className="sa-break">{post.title || "Untitled post"}</h2>
              <StatusBadge map={POST_STATUS} status={post.status} />
              {running ? <Badge tone="attention">Updating…</Badge> : null}
            </div>
            <span className="sa-small sa-muted">ID <code>{post.id}</code></span>
          </div>
          {post.error_message ? <div className="sa-banner sa-banner-critical"><strong>{post.error_type || "Error"}:</strong> {post.error_message}</div> : null}
          <dl className="sa-kv">
            <dt>Content type</dt><dd>{contentTypeLabel(post.content_type, contentTypes)}</dd>
            <dt>Template</dt><dd>{post.template_key || generation.template_key || "Automatic"}</dd>
            <dt>Scheduled</dt><dd><TimeText epoch={post.scheduled_at} tz={tz} full /></dd>
            {post.published_at ? <><dt>Published</dt><dd><TimeText epoch={post.published_at} tz={tz} full /></dd></> : null}
            <dt>Approval</dt><dd>{approvalLabel(post.approval_mode)}{post.approved_at ? <> · approved by {post.approved_by || "—"} <TimeText epoch={post.approved_at} tz={tz} relative /></> : null}</dd>
            <dt>Publishing</dt><dd>{PUBLISH_MODE_LABEL[post.publish_mode] || humanize(post.publish_mode)}</dd>
            <dt>Origin</dt><dd>{post.campaign_id ? <Link to={`/app/social/campaigns?edit=${post.campaign_id}`}>{rules[post.rule_type]?.label || humanize(post.rule_type)} campaign</Link> : "Created by hand in the studio"}</dd>
            {post.options?.decided_by ? <><dt>Chosen by</dt><dd>{post.options.decided_by === "gemma" ? "Gemma" : humanize(post.options.decided_by)}{post.options.decision_reason ? ` — ${post.options.decision_reason}` : ""}</dd></> : null}
            {post.angle ? <><dt>Angle</dt><dd>{post.angle}</dd></> : null}
            {post.brief ? <><dt>Brief</dt><dd className="sa-prewrap">{post.brief}</dd></> : null}
            {post.link_url ? <><dt>Link</dt><dd className="sa-break"><a href={post.link_url} target="_blank" rel="noreferrer">{post.link_url}</a></dd></> : null}
            {post.rejected_reason ? <><dt>Rejected because</dt><dd>{post.rejected_reason}</dd></> : null}
            {post.options?.texts && Object.keys(post.options.texts).length ? (
              <><dt>Extra texts</dt><dd>{Object.entries(post.options.texts).filter(([, value]) => value).map(([key, value]) => <div key={key}><span className="sa-muted">{humanize(key)}:</span> {value}</div>)}</dd></>
            ) : null}
          </dl>
          <ProductChips post={post} />
          {postizTools.length ? <div className="sa-row sa-small"><span className="sa-muted">Made with Postiz:</span> <PostizToolBadges tools={postizTools} /></div> : null}
          <PostActions post={post} />
        </div>
      </s-section>

      <s-section heading="Review">
        <PostReviewPanel review={detail.review} warnings={post.warnings} channels={channels} />
        {!detail.review?.guard?.issues?.length && !detail.review?.ai_review && !post.warnings.length ? <p className="sa-muted">No guard issues, review notes or warnings.</p> : null}
      </s-section>

      <s-section heading={`Channel versions (${post.targets.length})`}>
        {post.targets.length ? (
          <div className="sa-stack">
            <PostEditor post={post} channels={channels} tz={tz} />
            <div className="sa-table-wrap">
              <table className="sa-table">
                <thead><tr><th>Channel</th><th>Status</th><th>Postiz ID</th><th>Live post</th><th>Published</th><th>Error</th></tr></thead>
                <tbody>
                  {post.targets.map((target) => (
                    <tr key={target.id}>
                      <td>{providerLabel(target.provider)} · {target.channel_name || target.channel_id}</td>
                      <td>{target.status}</td>
                      <td><code>{target.external_id || "—"}</code>{target.external_group ? <div className="sa-small sa-muted">group {target.external_group}</div> : null}</td>
                      <td>{target.release_url ? <a href={target.release_url} target="_blank" rel="noreferrer">Open ↗</a> : "—"}</td>
                      <td className="sa-nowrap"><TimeText epoch={target.published_at} tz={tz} /></td>
                      <td className="sa-break">{target.error_message ? `${target.error_type ? `${target.error_type}: ` : ""}${target.error_message}` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <details>
              <summary className="sa-small">Network settings sent to Postiz</summary>
              <div className="sa-stack-sm">
                {post.targets.map((target) => (
                  <div key={target.id}>
                    <TargetHeader target={target} channels={channels} tz={tz} />
                    <pre>{JSON.stringify({ format: target.format, link_url: target.link_url, media_ids: target.media_ids, settings: target.settings }, null, 2)}</pre>
                  </div>
                ))}
              </div>
            </details>
          </div>
        ) : <p className="sa-muted">No channel versions yet — they are created when the post is generated.</p>}
      </s-section>

      <s-section heading={`Media (${post.media.length})`}>
        {post.media.length ? (
          <div className="sa-stack">
            {formats.map((format) => (
              <div key={format} className="sa-stack-sm">
                <h4>{humanize(format)}</h4>
                <div className="sa-grid">
                  {post.media.filter((media) => (media.format || media.kind) === format).map((media) => (
                    <figure key={media.id} style={{ margin: 0 }} className="sa-stack-sm">
                      <MediaPreview media={media} alt={post.alt_text || post.title} />
                      <figcaption className="sa-small sa-muted">{mediaCaption(media)}</figcaption>
                    </figure>
                  ))}
                </div>
              </div>
            ))}
            {post.alt_text ? <p className="sa-small"><span className="sa-muted">Alt text:</span> {post.alt_text}</p> : null}
          </div>
        ) : <p className="sa-muted">No media rendered yet.</p>}
      </s-section>

      <s-section heading="Generation">
        <dl className="sa-kv">
          <dt>Captions written by</dt><dd>{generation.caption_source === "template" ? "Caption templates" : generation.model || post.model || "—"}</dd>
          <dt>Fallback model used</dt><dd>{generation.used_fallback ? <Badge tone="warning">Yes — Gemma was unavailable</Badge> : "No"}</dd>
          <dt>Postiz tools used</dt><dd>{postizTools.length ? <PostizToolBadges tools={postizTools} /> : "None (our own templates)"}</dd>
          {generation.rendered_as && generation.rendered_as !== post.content_type ? (
            <><dt>Rendered as</dt><dd>{contentTypeLabel(generation.rendered_as, contentTypes)}</dd></>
          ) : null}
          <dt>Template</dt><dd>{generation.template_key || "—"}</dd>
          <dt>Formats</dt><dd>{(generation.formats || []).map(humanize).join(", ") || "—"}</dd>
          {generation.hook ? <><dt>Video hook</dt><dd>{generation.hook}</dd></> : null}
          <dt>Took</dt><dd>{generation.duration_ms ? `${(generation.duration_ms / 1000).toFixed(1)} s` : "—"}</dd>
          <dt>Generated</dt><dd><TimeText epoch={generation.generated_at} tz={tz} full /></dd>
          {post.hashtags?.length ? <><dt>Hashtags</dt><dd>{post.hashtags.join(" ")}</dd></> : null}
        </dl>
        {post.base_caption ? <details style={{ marginTop: 8 }}><summary className="sa-small">Base caption</summary><div className="sa-prewrap">{post.base_caption}</div></details> : null}
        <div style={{ marginTop: 8 }}><JsonBlock value={generation} label="Raw generation data" /></div>
      </s-section>

      {metrics.length ? (
        <s-section heading="Performance">
          <div className="sa-table-wrap">
            <table className="sa-table">
              <thead><tr><th>Network</th><th className="num">Impressions</th><th className="num">Reach</th><th className="num">Views</th><th className="num">Likes</th><th className="num">Comments</th><th className="num">Shares</th><th className="num">Saves</th><th className="num">Clicks</th><th>Fetched</th></tr></thead>
              <tbody>
                {metrics.map((row) => (
                  <tr key={row.id}>
                    <td>{providerLabel(row.provider)}</td>
                    {(["impressions", "reach", "views", "likes", "comments", "shares", "saves", "clicks"] as const).map((key) => <td key={key} className="num">{formatNumber(row[key])}</td>)}
                    <td className="sa-nowrap"><TimeText epoch={row.fetched_at} tz={tz} relative /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </s-section>
      ) : null}

      <s-section heading={`Timeline (${timeline.length})`}>
        {timeline.length ? (
          <div className="sa-stack-sm">
            {timeline.map((item) => (
              <div key={item.id} className="sa-row-top" style={{ borderTop: "1px solid var(--sa-border)", paddingTop: 6 }}>
                <span className="sa-small sa-muted sa-nowrap" style={{ minWidth: 130 }}><TimeText epoch={item.created_at} tz={tz} full /></span>
                <StatusBadge map={ACTIVITY_LEVEL} status={item.level} />
                {item.stage ? <Badge tone="neutral">{humanize(item.stage)}</Badge> : null}
                <div className="sa-grow sa-break">
                  {item.message}
                  <JsonBlock value={item.data} label="Details" />
                </div>
              </div>
            ))}
          </div>
        ) : <p className="sa-muted">No activity recorded.</p>}
      </s-section>

      <s-section heading={`Jobs (${jobs.length})`}>
        {jobs.length ? (
          <div className="sa-table-wrap">
            <table className="sa-table">
              <thead><tr><th>Job</th><th>Status</th><th>Attempts</th><th>Created</th><th>Finished</th><th>Error</th><th /></tr></thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id}>
                    <td><code>{job.type}</code></td>
                    <td><StatusBadge map={JOB_STATUS} status={job.status} /></td>
                    <td className="num">{job.attempts}/{job.max_attempts}</td>
                    <td className="sa-nowrap"><TimeText epoch={job.created_at} tz={tz} /></td>
                    <td className="sa-nowrap"><TimeText epoch={job.finished_at} tz={tz} /></td>
                    <td className="sa-break">{job.last_error ? `${job.last_error_type ? `${job.last_error_type}: ` : ""}${job.last_error}` : "—"}<JsonBlock value={job.result} label="Result" /></td>
                    <td>
                      {["failed", "cancelled"].includes(job.status) ? (
                        <Form method="post" preventScrollReset>
                          <Hidden values={{ intent: "job_retry", job_id: job.id }} />
                          <button type="submit" className="sa-btn sa-btn-sm" disabled={busy}>{isSubmitting("job_retry", ["job_id", job.id]) ? "…" : "Retry"}</button>
                        </Form>
                      ) : job.status === "queued" ? (
                        <Form method="post" preventScrollReset>
                          <Hidden values={{ intent: "job_cancel", job_id: job.id }} />
                          <button type="submit" className="sa-btn sa-btn-sm sa-btn-critical" disabled={busy}>Cancel</button>
                        </Form>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="sa-muted">No jobs for this post.</p>}
      </s-section>

      <s-section heading={`API calls (${apiCalls.length})`}>
        {apiCalls.length ? (
          <div className="sa-stack-sm">
            {apiCalls.map((call) => (
              <details key={call.id}>
                <summary>
                  <span className="sa-row" style={{ display: "inline-flex" }}>
                    <Badge tone={call.ok ? "success" : "critical"}>{call.status_code ?? "no response"}</Badge>
                    <span>{call.service} · {call.operation || `${call.method} ${call.url_path}`}</span>
                    <span className="sa-muted sa-small">{call.duration_ms} ms{call.attempt > 1 ? ` · attempt ${call.attempt}` : ""} · <TimeText epoch={call.created_at} tz={tz} /></span>
                  </span>
                </summary>
                <div className="sa-stack-sm">
                  <div className="sa-small"><code>{call.method} {call.url_path}</code></div>
                  {call.error ? <div className="sa-error-text sa-break">{call.error}</div> : null}
                  {call.request_excerpt ? <div><span className="sa-small sa-muted">Request</span><pre>{call.request_excerpt}</pre></div> : null}
                  {call.response_excerpt ? <div><span className="sa-small sa-muted">Response</span><pre>{call.response_excerpt}</pre></div> : null}
                </div>
              </details>
            ))}
          </div>
        ) : <p className="sa-muted">No external API calls recorded for this post.</p>}
      </s-section>

      <s-section heading={`AI decisions (${decisions.length})`}>
        {decisions.length ? (
          <div className="sa-stack-sm">
            {decisions.map((decision) => (
              <Card key={decision.id}>
                <div className="sa-row">
                  <strong>{humanize(decision.decision_type)}</strong>
                  <Badge tone={decision.valid ? "success" : "critical"}>{decision.valid ? "Valid answer" : "Invalid answer"}</Badge>
                  {decision.used_fallback ? <Badge tone="warning">Fallback used</Badge> : null}
                  <span className="sa-small sa-muted">{decision.model} · {decision.duration_ms} ms · <TimeText epoch={decision.created_at} tz={tz} /></span>
                </div>
                {decision.error ? <div className="sa-error-text sa-break">{decision.error}</div> : null}
                <div className="sa-grid-2" style={{ marginTop: 6 }}>
                  <JsonBlock value={decision.input} label="Input" />
                  <JsonBlock value={decision.output} label="Output" />
                </div>
              </Card>
            ))}
          </div>
        ) : <p className="sa-muted">No AI decisions recorded for this post.</p>}
      </s-section>
    </>
  );
}
