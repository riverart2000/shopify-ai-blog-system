import { useMemo, useState } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useLoaderData, useSearchParams } from "react-router";

import { PostPreviewImage } from "../components/social/MediaPreview";
import {
  PostActions, PostEditor, PostReviewPanel, PostizToolBadges, ProductChips, canApprove, channelLookupFor,
} from "../components/social/PostParts";
import { useSocial } from "../components/social/context";
import { ActionBanner, Badge, Card, EmptyState, Hidden, LoadError, StatusBadge, Tabs, TimeText, usePolling, useBusy } from "../components/social/ui";
import { POST_STATUS, approvalLabel, contentTypeLabel, humanize, type Tone } from "../components/social/vocab";
import {
  errorMessage, handlePostIntent, seFetch, withSignedMedia, type PostSummary, type PostsResponse,
} from "../lib/social-engine.server";
import { authenticate } from "../shopify.server";

export const handle = { title: "Approval queue" };

const TABS: Array<{ key: string; label: string; statuses: string[] }> = [
  { key: "approval", label: "Needs approval", statuses: ["pending_approval"] },
  { key: "scheduled", label: "Approved & scheduled", statuses: ["approved", "publishing", "scheduled"] },
  { key: "published", label: "Published", statuses: ["published", "partially_published"] },
  { key: "failed", label: "Failed", statuses: ["failed"] },
  { key: "preparing", label: "Being created", statuses: ["planned", "generating"] },
  { key: "rejected", label: "Rejected & cancelled", statuses: ["rejected", "cancelled"] },
  { key: "all", label: "All", statuses: [] },
];
const PAGE_SIZE = 20;

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  const url = new URL(request.url);
  const tab = TABS.find((item) => item.key === url.searchParams.get("tab")) || TABS[0];
  const page = Math.max(1, Number(url.searchParams.get("page") || 1) || 1);
  const search = url.searchParams.get("q") || "";
  try {
    const posts = await seFetch<PostsResponse>("/posts", {
      query: {
        status: tab.statuses.join(","), page, page_size: PAGE_SIZE, search,
        order: tab.key === "published" || tab.key === "rejected" || tab.key === "all" ? "updated" : "scheduled",
      },
    });
    return { tab: tab.key, page, search, error: "", posts: posts.posts.map(withSignedMedia), counts: posts.counts };
  } catch (error) {
    return { tab: tab.key, page, search, error: errorMessage(error), posts: [] as PostSummary[], counts: {} as Record<string, number> };
  }
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  return handlePostIntent(String(form.get("intent") || ""), form);
};

const IN_PROGRESS = new Set(["planned", "generating", "approved", "publishing"]);

function accentFor(status: string): Tone {
  return POST_STATUS[status]?.tone || "neutral";
}

export default function QueuePage() {
  const data = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>();
  const { tz, contentTypes, rules } = useSocial();
  const { busy, isSubmitting } = useBusy();
  const [params] = useSearchParams();
  const [selected, setSelected] = useState<string[]>([]);
  usePolling(data.posts.some((post) => IN_PROGRESS.has(post.status)), 10_000);

  const approvable = useMemo(() => data.posts.filter(canApprove).map((post) => post.id), [data.posts]);
  const channels = useMemo(() => channelLookupFor(data.posts), [data.posts]);
  const selectedApprovable = selected.filter((id) => approvable.includes(id));

  const tabLink = (key: string) => {
    const next = new URLSearchParams(params);
    next.set("tab", key);
    next.delete("page");
    return `?${next.toString()}`;
  };
  const pageLink = (page: number) => {
    const next = new URLSearchParams(params);
    next.set("page", String(page));
    return `?${next.toString()}`;
  };
  const countFor = (statuses: string[]) => statuses.length
    ? statuses.reduce((sum, status) => sum + (data.counts[status] || 0), 0)
    : Object.values(data.counts).reduce((sum, value) => sum + value, 0);

  return (
    <>
      <ActionBanner result={result?.target && data.posts.some((post) => post.id === result.target) ? null : result} />
      <LoadError error={data.error} />
      <s-section>
        <Tabs items={TABS.map((tab) => ({ to: tabLink(tab.key), label: tab.label, count: countFor(tab.statuses), active: tab.key === data.tab }))} />
        <div className="sa-spread">
          <Form method="get" className="sa-row" preventScrollReset>
            <input type="hidden" name="tab" value={data.tab} />
            <input type="search" name="q" defaultValue={data.search} placeholder="Search posts by title" aria-label="Search posts" style={{ width: 240 }} />
            <button type="submit" className="sa-btn">Search</button>
          </Form>
          {approvable.length ? (
            <Form method="post" className="sa-row" preventScrollReset>
              <Hidden values={{ intent: "approve_bulk" }} />
              {selectedApprovable.map((id) => <input key={id} type="hidden" name="post_ids" value={id} />)}
              <label className="sa-check">
                <input
                  type="checkbox"
                  checked={selectedApprovable.length === approvable.length}
                  onChange={(event) => setSelected(event.target.checked ? approvable : [])}
                />
                <span>Select all ({approvable.length})</span>
              </label>
              <button type="submit" className="sa-btn sa-btn-success" disabled={busy || !selectedApprovable.length}>
                {isSubmitting("approve_bulk") ? "Approving…" : `Approve selected (${selectedApprovable.length})`}
              </button>
            </Form>
          ) : null}
        </div>
      </s-section>

      {data.posts.length ? (
        <div className="sa-stack">
          {data.posts.map((post) => (
            <Card key={post.id} accent={accentFor(post.status)}>
              <div className="sa-post">
                <div className="sa-stack-sm">
                  <PostPreviewImage url={post.preview_url} media={post.media} title={post.title} />
                  {canApprove(post) ? (
                    <label className="sa-check sa-small">
                      <input
                        type="checkbox"
                        checked={selected.includes(post.id)}
                        onChange={(event) => setSelected((current) => event.target.checked ? [...current, post.id] : current.filter((id) => id !== post.id))}
                      />
                      <span>Select</span>
                    </label>
                  ) : null}
                </div>
                <div className="sa-stack" style={{ minWidth: 0 }}>
                  <div className="sa-spread">
                    <div className="sa-row" style={{ minWidth: 0 }}>
                      <Link to={`/app/social/posts/${post.id}`} className="sa-strong sa-break">{post.title || "Untitled post"}</Link>
                      <StatusBadge map={POST_STATUS} status={post.status} />
                    </div>
                    <span className="sa-small sa-muted sa-nowrap">
                      {post.scheduled_at ? <>Goes out <TimeText epoch={post.scheduled_at} tz={tz} /></> : "No time yet"}
                    </span>
                  </div>
                  <div className="sa-row sa-small">
                    <Badge tone="info">{contentTypeLabel(post.content_type, contentTypes)}</Badge>
                    {post.template_key ? <Badge tone="neutral">Template: {post.template_key}</Badge> : null}
                    <span className="sa-muted">{post.campaign_id ? `Campaign · ${rules[post.rule_type]?.label || humanize(post.rule_type)}` : "Created by hand"}</span>
                    <span className="sa-muted">· {approvalLabel(post.approval_mode)}</span>
                    {post.options?.decided_by ? <span className="sa-muted" title={post.options.decision_reason || ""}>· Chosen by {post.options.decided_by === "gemma" ? "Gemma" : humanize(post.options.decided_by)}</span> : null}
                  </div>
                  <ProductChips post={post} />
                  <PostizToolBadges tools={post.postiz_tools} />
                  {post.options?.decision_reason ? <div className="sa-small sa-muted">Why: {post.options.decision_reason}</div> : null}
                  {post.error_message ? <div className="sa-banner sa-banner-critical"><strong>{post.error_type || "Error"}:</strong> {post.error_message}</div> : null}
                  {post.status === "rejected" && post.rejected_reason ? <div className="sa-small sa-muted">Rejected: {post.rejected_reason}</div> : null}
                  {result?.target === post.id ? <ActionBanner result={result} /> : null}
                  <PostReviewPanel review={post.review} warnings={post.warnings} channels={channels} />
                  {post.targets.length ? (
                    <PostEditor post={post} channels={channels} tz={tz} />
                  ) : (
                    <p className="sa-muted">{["planned", "generating"].includes(post.status) ? "Gemma is writing the captions and the images are rendering…" : "No channel versions yet."}</p>
                  )}
                  <PostActions post={post} />
                </div>
              </div>
            </Card>
          ))}
          <div className="sa-spread">
            {data.page > 1 ? <Link to={pageLink(data.page - 1)} className="sa-btn">← Newer</Link> : <span />}
            {data.posts.length >= PAGE_SIZE ? <Link to={pageLink(data.page + 1)} className="sa-btn">More →</Link> : <span />}
          </div>
        </div>
      ) : !data.error ? (
        <EmptyState
          title={data.tab === "approval" ? "Nothing waiting for approval" : "No posts here"}
          action={<Link to="/app/social/studio" className="sa-btn sa-btn-primary">Create a post</Link>}
        >
          {data.tab === "approval"
            ? "New posts from your campaigns appear here for you to check, edit and approve before they go out."
            : "Try another tab, or create a post in the studio."}
        </EmptyState>
      ) : null}
    </>
  );
}
