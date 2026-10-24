import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useLoaderData, useSearchParams } from "react-router";

import { CampaignEditor } from "../components/social/CampaignEditor";
import { NetworkDot } from "../components/social/ChannelChip";
import { toPicked, type PickedProduct } from "../components/social/ProductPicker";
import { useSocial } from "../components/social/context";
import { ActionBanner, Badge, Banner, Card, EmptyState, Hidden, LoadError, StatusBadge, TimeText, useBusy } from "../components/social/ui";
import { CAMPAIGN_STATUS, POST_STATUS, approvalLabel, contentTypeLabel, humanize, providerLabel } from "../components/social/vocab";
import {
  SocialEngineError, errorMessage, formJson, formString, lookupProducts, runIntent, safeId, seFetch, seg,
  type Campaign, type CampaignPreview, type CampaignsResponse, type Channel, type ChannelsResponse, type Collection,
  type ProductsResponse, type RuleInfo, type Template, type TemplatesResponse,
} from "../lib/social-engine.server";
import { authenticate } from "../shopify.server";

export const handle = { title: "Campaigns" };

/** Promotions (PromoQ) have their own tab with themes, dates and promo lines. */
const PROMOTION_RULE = "promotion";
const PROMOTIONS_PATH = "/app/social/promotions";

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { redirect } = await authenticate.admin(request);
  const url = new URL(request.url);
  const editId = url.searchParams.get("edit") || "";
  const newRule = url.searchParams.get("new") || "";
  if (newRule === PROMOTION_RULE) throw redirect(`${PROMOTIONS_PATH}?new=1`);
  try {
    const [campaigns, channels, templates, catalogue] = await Promise.all([
      seFetch<CampaignsResponse>("/campaigns"),
      seFetch<ChannelsResponse>("/channels"),
      seFetch<TemplatesResponse>("/templates").catch(() => ({ templates: [] as Template[] })),
      seFetch<ProductsResponse>("/products", { query: { page_size: 1 } }).catch(() => ({ collections: [] as Collection[] })),
    ]);
    const editing = editId ? campaigns.campaigns.find((campaign) => campaign.id === editId) || null : null;
    if (editing?.rule_type === PROMOTION_RULE) throw redirect(`${PROMOTIONS_PATH}?edit=${encodeURIComponent(editing.id)}`);
    let products: Record<string, PickedProduct> = {};
    if (editing) {
      const config = editing.config as { product_ids?: string[] };
      const ids = [
        ...(editing.product_filter.product_ids || []), ...(editing.product_filter.exclude_product_ids || []),
        ...(Array.isArray(config.product_ids) ? config.product_ids : []),
      ].map(String);
      const found = await lookupProducts(ids);
      products = Object.fromEntries(Object.entries(found).filter(([id]) => ids.includes(id)).map(([id, product]) => [id, toPicked(product)]));
    }
    return {
      campaigns: campaigns.campaigns, rules: campaigns.rules, channels: channels.channels, templates: templates.templates,
      collections: catalogue.collections || [], editing, editId, newRule, products, error: "",
    };
  } catch (error) {
    if (error instanceof Response) throw error;
    return {
      campaigns: [] as Campaign[], rules: {} as Record<string, RuleInfo>, channels: [] as Channel[], templates: [] as Template[],
      collections: [] as Collection[], editing: null as Campaign | null, editId, newRule, products: {} as Record<string, PickedProduct>,
      error: errorMessage(error),
    };
  }
};

type PreviewData = CampaignPreview & { productTitles: Record<string, string> };

export const action = async ({ request }: ActionFunctionArgs) => {
  const { redirect } = await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  if (intent === "save") {
    let savedId = "";
    const result = await runIntent(intent, async () => {
      const campaign = formJson<Record<string, unknown>>(form, "campaign");
      if (!campaign || typeof campaign !== "object") throw new SocialEngineError("Campaign data is missing.");
      const saved = await seFetch<{ id: string }>("/campaigns", { body: campaign });
      savedId = saved.id;
      return { message: "Campaign saved." };
    });
    if (result.ok && savedId) return redirect(`/app/social/campaigns?edit=${encodeURIComponent(savedId)}&saved=1`);
    return result;
  }
  const campaignId = formString(form, "campaign_id");
  return runIntent<PreviewData>(intent, async () => {
    const id = safeId(campaignId);
    switch (intent) {
      case "status": {
        const status = formString(form, "status");
        await seFetch(`/campaigns/${seg(id)}/status`, { body: { status } });
        return { message: status === "active" ? "Campaign switched on. It is planning posts now." : "Campaign paused. Already planned posts are kept." };
      }
      case "run": {
        const result = await seFetch<{ already_queued: boolean }>(`/campaigns/${seg(id)}/run`, { method: "POST" });
        return { message: result.already_queued ? "This campaign is already queued to run." : "Campaign is running now. New posts appear in the approval queue." };
      }
      case "preview": {
        const preview = await seFetch<CampaignPreview>(`/campaigns/${seg(id)}/preview`, { method: "POST", timeoutMs: 300_000 });
        if (preview.error) throw new SocialEngineError(preview.error);
        const ids = (preview.items || []).flatMap((item) => item.product_ids);
        const found = await lookupProducts(ids);
        const productTitles = Object.fromEntries(Object.entries(found).map(([key, product]) => [key, product.title]));
        return {
          message: `Dry run: this campaign would plan ${preview.planned} post(s) right now. Nothing was saved.`,
          data: { ...preview, productTitles },
        };
      }
      case "delete":
        await seFetch(`/campaigns/${seg(id)}/delete`, { method: "POST" });
        return { message: "Campaign deleted." };
      default:
        throw new SocialEngineError(`Unknown action "${intent}".`);
    }
  }, campaignId || undefined);
};

export default function CampaignsPage() {
  const data = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>();
  const { tz, contentTypes } = useSocial();
  const { busy, isSubmitting } = useBusy();
  const [params] = useSearchParams();
  const channelById = Object.fromEntries(data.channels.map((channel) => [channel.channel_id, channel]));
  const actionResult = result && "intent" in result ? result : null;
  const preview = actionResult?.intent === "preview" && actionResult.ok ? (actionResult.data as PreviewData | undefined) : undefined;
  const editorOpen = Boolean(data.editing || (data.newRule && data.rules[data.newRule]));

  return (
    <>
      {params.get("saved") ? <Banner tone="success">Campaign saved.</Banner> : null}
      <ActionBanner result={actionResult?.target && data.campaigns.some((campaign) => campaign.id === actionResult.target) ? null : actionResult} />
      <LoadError error={data.error} />
      {data.editId && !data.editing && !data.error ? <Banner tone="warning">That campaign no longer exists.</Banner> : null}

      {editorOpen ? (
        <s-section>
          <CampaignEditor
            key={data.editing?.id || data.newRule}
            campaign={data.editing}
            ruleType={data.newRule}
            rules={data.rules}
            channels={data.channels}
            templates={data.templates}
            collections={data.collections}
            products={data.products}
            contentTypes={contentTypes}
          />
        </s-section>
      ) : null}

      <s-section heading="Add a campaign">
        <div className="sa-grid">
          {Object.entries(data.rules).map(([key, rule]) => (
            <Link
              key={key} to={key === PROMOTION_RULE ? `${PROMOTIONS_PATH}?new=1` : `?new=${encodeURIComponent(key)}`}
              className="sa-choice" style={{ color: "inherit", textDecoration: "none" }}
            >
              <span className="sa-stack-sm">
                <strong>{rule.label}</strong>
                <span className="sa-small sa-muted">{rule.description}</span>
                {key === PROMOTION_RULE ? <span className="sa-small">Set up on the Promotions tab →</span> : null}
              </span>
            </Link>
          ))}
        </div>
      </s-section>

      <s-section heading={`Your campaigns (${data.campaigns.length})`}>
        {data.campaigns.length ? (
          <div className="sa-stack">
            {data.campaigns.map((campaign) => {
              const rule = data.rules[campaign.rule_type];
              const counts = Object.entries(campaign.post_counts || {}).filter(([, count]) => count > 0);
              const channels = campaign.channel_ids.length ? campaign.channel_ids.map((id) => channelById[id]).filter(Boolean) : [];
              const showPreview = preview && preview.campaign_id === campaign.id;
              return (
                <Card key={campaign.id} accent={campaign.last_error ? "critical" : campaign.status === "active" ? "success" : "neutral"}>
                  <div className="sa-stack">
                    <div className="sa-spread">
                      <div className="sa-row">
                        <strong>{campaign.name}</strong>
                        <StatusBadge map={CAMPAIGN_STATUS} status={campaign.status} />
                        <Badge tone="info">{rule?.label || humanize(campaign.rule_type)}</Badge>
                      </div>
                      <div className="sa-row">
                        <Form method="post" preventScrollReset>
                          <Hidden values={{ intent: "status", campaign_id: campaign.id, status: campaign.status === "active" ? "paused" : "active" }} />
                          <button type="submit" className={`sa-btn sa-btn-sm ${campaign.status === "active" ? "" : "sa-btn-primary"}`} disabled={busy}>
                            {campaign.status === "active" ? "Pause" : "Turn on"}
                          </button>
                        </Form>
                        <Form method="post" preventScrollReset>
                          <Hidden values={{ intent: "preview", campaign_id: campaign.id }} />
                          <button type="submit" className="sa-btn sa-btn-sm" disabled={busy}>{isSubmitting("preview", ["campaign_id", campaign.id]) ? "Previewing…" : "Preview (dry run)"}</button>
                        </Form>
                        <Form method="post" preventScrollReset>
                          <Hidden values={{ intent: "run", campaign_id: campaign.id }} />
                          <button type="submit" className="sa-btn sa-btn-sm" disabled={busy}>{isSubmitting("run", ["campaign_id", campaign.id]) ? "Starting…" : "Run now"}</button>
                        </Form>
                        {campaign.rule_type === PROMOTION_RULE
                          ? <Link to={`${PROMOTIONS_PATH}?edit=${encodeURIComponent(campaign.id)}`} className="sa-btn sa-btn-sm">Edit on Promotions tab</Link>
                          : <Link to={`?edit=${encodeURIComponent(campaign.id)}`} className="sa-btn sa-btn-sm">Edit</Link>}
                      </div>
                    </div>
                    <div className="sa-row sa-small sa-muted">
                      <span>{approvalLabel(campaign.approval_mode)}</span>
                      <span>·</span>
                      <span className="sa-row" style={{ gap: 4 }}>
                        {channels.length ? channels.map((channel) => <NetworkDot key={channel.channel_id} provider={channel.provider} />) : null}
                        {channels.length ? `${channels.length} channel(s)` : "All enabled channels"}
                      </span>
                      <span>·</span>
                      <span>Last run <TimeText epoch={campaign.last_evaluated_at} tz={tz} relative /></span>
                      {campaign.status === "active" && campaign.next_evaluation_at ? <><span>·</span><span>Next check <TimeText epoch={campaign.next_evaluation_at} tz={tz} relative /></span></> : null}
                    </div>
                    {counts.length ? (
                      <div className="sa-row sa-small">
                        {counts.map(([status, count]) => <span key={status}><StatusBadge map={POST_STATUS} status={status} /> {count}</span>)}
                      </div>
                    ) : <span className="sa-small sa-muted">No posts planned yet.</span>}
                    {actionResult?.target === campaign.id ? <ActionBanner result={actionResult} /> : null}
                    {campaign.last_error ? <div className="sa-banner sa-banner-critical"><strong>Last run failed:</strong> {campaign.last_error}</div> : null}

                    {showPreview ? (
                      <div className="sa-card sa-card-muted sa-stack-sm">
                        <strong>Dry run — {preview.planned} post(s) would be planned{preview.duration_ms ? ` (${(preview.duration_ms / 1000).toFixed(1)} s)` : ""}</strong>
                        {preview.note ? <div className="sa-small">Note: {preview.note}</div> : null}
                        {preview.items?.length ? (
                          <div className="sa-table-wrap">
                            <table className="sa-table">
                              <thead><tr><th>When</th><th>Channels</th><th>Type</th><th>Products</th><th>Why</th></tr></thead>
                              <tbody>
                                {preview.items.map((item) => (
                                  <tr key={item.dedupe_key}>
                                    <td className="sa-nowrap"><TimeText epoch={item.scheduled_at} tz={tz} /></td>
                                    <td>{item.channel_ids.map((id) => channelById[id] ? `${providerLabel(channelById[id].provider)} (${channelById[id].name})` : id).join(", ")}</td>
                                    <td>{contentTypeLabel(item.content_type, contentTypes)}{item.template_key ? <div className="sa-small sa-muted">{item.template_key}</div> : null}</td>
                                    <td>{item.product_ids.map((id) => preview.productTitles[id] || `#${id}`).join(", ") || "—"}</td>
                                    <td className="sa-small">
                                      {item.title}
                                      {item.options?.decided_by ? <div className="sa-muted">Chosen by {String(item.options.decided_by) === "gemma" ? "Gemma" : humanize(String(item.options.decided_by))}{item.options.decision_reason ? `: ${String(item.options.decision_reason)}` : ""}</div> : null}
                                      {item.angle ? <div className="sa-muted">Angle: {item.angle}</div> : null}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        ) : <span className="sa-small sa-muted">Nothing would be planned right now — everything due may already be planned, or the note above explains why.</span>}
                      </div>
                    ) : null}

                    <details className="sa-plain">
                      <summary className="sa-small sa-muted">Delete campaign</summary>
                      <Form method="post" preventScrollReset className="sa-row" style={{ marginTop: 6 }}>
                        <Hidden values={{ intent: "delete", campaign_id: campaign.id }} />
                        <button type="submit" className="sa-btn sa-btn-sm sa-btn-critical" disabled={busy}>Delete “{campaign.name}”</button>
                        <span className="sa-help">Campaigns with scheduled posts can&apos;t be deleted — pause them instead.</span>
                      </Form>
                    </details>
                  </div>
                </Card>
              );
            })}
          </div>
        ) : !data.error ? (
          <EmptyState title="No campaigns yet" action={<Link to="?new=smartq" className="sa-btn sa-btn-primary">Start with SmartQ auto-pilot</Link>}>
            SmartQ posts your catalogue every day across your networks, rotating products. Add sale alerts, new-arrival launches and holiday posts alongside it.
          </EmptyState>
        ) : null}
      </s-section>
    </>
  );
}
