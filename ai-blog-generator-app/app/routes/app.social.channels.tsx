import { useState } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useFetcher, useLoaderData } from "react-router";

import { ChannelChip } from "../components/social/ChannelChip";
import { useSocial } from "../components/social/context";
import { PostizToolsPanel, usePostizTools } from "../components/social/PostizParts";
import { ActionBanner, Badge, Banner, Card, EmptyState, Field, Hidden, JsonBlock, LoadError, TimeText, useBusy } from "../components/social/ui";
import { LINK_MODES, humanize, providerLabel } from "../components/social/vocab";
import {
  SocialEngineError, errorMessage, formBool, formList, formString, runIntent, safeId, seFetch, seg,
  type ActionResult, type Channel, type ChannelSync, type ChannelTool, type ChannelsResponse, type SettingsResponse,
} from "../lib/social-engine.server";
import { authenticate } from "../shopify.server";

export const handle = { title: "Channels" };

type SettingField = {
  key: string; label: string; type: "select" | "checkbox"; options?: Array<[string, string]>; help?: string; fallback: string | boolean;
};

const INSTAGRAM_FIELDS: SettingField[] = [
  { key: "post_type", label: "Post as", type: "select", fallback: "post", options: [["post", "Feed post (videos become Reels)"], ["story", "Story"]], help: "Stories take exactly one image or video." },
];

const PROVIDER_FIELDS: Record<string, SettingField[]> = {
  instagram: INSTAGRAM_FIELDS,
  "instagram-standalone": INSTAGRAM_FIELDS,
  facebook: [
    { key: "post_type", label: "Post as", type: "select", fallback: "post", options: [["post", "Page post"], ["story", "Story"]], help: "Stories need an image or video and don't show the caption link." },
  ],
  tiktok: [
    { key: "privacy_level", label: "Who can watch", type: "select", fallback: "PUBLIC_TO_EVERYONE", options: [["PUBLIC_TO_EVERYONE", "Everyone"], ["MUTUAL_FOLLOW_FRIENDS", "Friends"], ["FOLLOWER_OF_CREATOR", "Followers"], ["SELF_ONLY", "Only me"]] },
    { key: "comment", label: "Allow comments", type: "checkbox", fallback: true },
    { key: "duet", label: "Allow duets", type: "checkbox", fallback: false },
    { key: "stitch", label: "Allow stitches", type: "checkbox", fallback: false },
  ],
  youtube: [
    { key: "type", label: "Visibility", type: "select", fallback: "public", options: [["public", "Public"], ["unlisted", "Unlisted"], ["private", "Private"]] },
  ],
  x: [
    { key: "who_can_reply_post", label: "Who can reply", type: "select", fallback: "everyone", options: [["everyone", "Everyone"], ["following", "People you follow"], ["mentionedUsers", "Only people you mention"], ["subscribers", "Subscribers"], ["verified", "Verified accounts"]] },
  ],
};

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  const appUrl = (process.env.SHOPIFY_REACT_APP_URL || new URL(request.url).origin).replace(/\/+$/, "");
  try {
    const [channels, settings] = await Promise.all([
      seFetch<ChannelsResponse>("/channels"),
      seFetch<SettingsResponse>("/settings"),
    ]);
    let webhookUrl = "";
    if (settings.settings.postiz.webhook_token_set) {
      // Idempotent when a token already exists: returns the current path.
      const token = await seFetch<{ path: string }>("/settings/webhook-token", { method: "POST" }).catch(() => null);
      webhookUrl = token?.path ? `${appUrl}${token.path}` : "";
    }
    return { channels: channels.channels, lastSync: channels.last_sync, webhookUrl, appUrl, error: "" };
  } catch (error) {
    return { channels: [] as Channel[], lastSync: null as ChannelSync | null, webhookUrl: "", appUrl, error: errorMessage(error) };
  }
};

type BoardsData = { boards: Array<{ id: string; name: string }>; tools: string[] };

function normaliseBoards(result: unknown): Array<{ id: string; name: string }> {
  const list = Array.isArray(result) ? result
    : result && typeof result === "object" && Array.isArray((result as { items?: unknown[] }).items) ? (result as { items: unknown[] }).items
      : [];
  return list.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const record = item as Record<string, unknown>;
    const id = String(record.id ?? record.value ?? "");
    const name = String(record.name ?? record.label ?? record.title ?? id);
    return id ? [{ id, name }] : [];
  });
}

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  const appUrl = (process.env.SHOPIFY_REACT_APP_URL || new URL(request.url).origin).replace(/\/+$/, "");
  const target = formString(form, "channel_id") || undefined;
  return runIntent<BoardsData | { url: string }>(intent, async () => {
    if (intent === "sync") {
      const result = await seFetch<{ already_queued: boolean }>("/channels/sync", { method: "POST" });
      return { message: result.already_queued ? "A channel sync is already queued." : "Syncing channels from Postiz. Refresh in a few seconds." };
    }
    if (intent === "webhook" || intent === "webhook_rotate") {
      const result = await seFetch<{ path: string }>("/settings/webhook-token", { method: "POST", query: { rotate: intent === "webhook_rotate" } });
      return {
        message: intent === "webhook_rotate" ? "New webhook URL created. Update it in Postiz — the old one no longer works." : "Webhook URL ready. Paste it into Postiz.",
        data: { url: `${appUrl}${result.path}` },
      };
    }
    const channelId = safeId(formString(form, "channel_id"));
    const base = `/channels/${seg(channelId)}`;
    switch (intent) {
      case "toggle": {
        const enabled = formBool(form, "enabled");
        await seFetch(base, { body: { enabled } });
        return { message: enabled ? "Channel turned on for Social Autopilot." : "Channel turned off. Campaigns will skip it." };
      }
      case "cadence": {
        await seFetch(base, { body: { cadence: { max_per_day: formString(form, "max_per_day"), min_gap_minutes: formString(form, "min_gap_minutes") } } });
        return { message: "Posting limits saved." };
      }
      case "settings": {
        const channels = await seFetch<ChannelsResponse>("/channels");
        const current = channels.channels.find((channel) => channel.channel_id === channelId);
        if (!current) throw new SocialEngineError("Channel not found. Sync channels and try again.");
        const merged: Record<string, unknown> = { ...(current.default_settings || {}) };
        for (const key of formList(form, "field_keys")) {
          const type = formString(form, `type:${key}`);
          merged[key] = type === "checkbox" ? form.has(`setting:${key}`) : formString(form, `setting:${key}`);
        }
        await seFetch(base, { body: { default_settings: merged } });
        return { message: `${providerLabel(current.provider)} settings saved for ${current.name}.` };
      }
      case "boards": {
        const listing = await seFetch<{ tools: ChannelTool[] }>(`${base}/tool`, { body: { method: "", data: {} } });
        const names = (listing.tools || []).map((tool) => String(tool.methodName || "")).filter(Boolean);
        const boardTool = names.find((name) => /board/i.test(name));
        if (!boardTool) {
          throw new SocialEngineError(`Postiz offers no board list for this channel (available tools: ${names.join(", ") || "none"}). Reconnect the channel in Postiz and try again.`);
        }
        const run = await seFetch<{ result: unknown }>(`${base}/tool`, { body: { method: boardTool, data: {} } });
        const boards = normaliseBoards(run.result);
        if (!boards.length) throw new SocialEngineError("Postiz returned no boards. Create a board in Pinterest first.");
        return { message: `Found ${boards.length} board(s).`, data: { boards, tools: names } };
      }
      default:
        throw new SocialEngineError(`Unknown action "${intent}".`);
    }
  }, target);
};

function PinterestBoardPicker({ channel }: { channel: Channel }) {
  const fetcher = useFetcher<ActionResult<BoardsData>>();
  const saver = useFetcher<ActionResult>();
  const current = String(channel.default_settings.board || "");
  const currentName = String(channel.default_settings.board_name || "");
  const boards = fetcher.data?.ok ? fetcher.data.data?.boards || [] : [];
  const [board, setBoard] = useState(current);
  const chosen = boards.find((item) => item.id === board);

  return (
    <div className="sa-stack-sm">
      <div className="sa-row">
        <strong>Pinterest board</strong>
        {current ? <Badge tone="success">{currentName || current}</Badge> : <Badge tone="critical">Not chosen — pins can&apos;t be published</Badge>}
      </div>
      <div className="sa-row">
        <fetcher.Form method="post">
          <Hidden values={{ intent: "boards", channel_id: channel.channel_id }} />
          <button type="submit" className="sa-btn sa-btn-sm" disabled={fetcher.state !== "idle"}>
            {fetcher.state !== "idle" ? "Loading boards…" : boards.length ? "Reload boards" : "Load my boards"}
          </button>
        </fetcher.Form>
        {boards.length ? (
          <saver.Form method="post" className="sa-row">
            <Hidden values={{ intent: "settings", channel_id: channel.channel_id, field_keys: "board", "type:board": "text" }} />
            <input type="hidden" name="field_keys" value="board_name" />
            <input type="hidden" name="type:board_name" value="text" />
            <input type="hidden" name="setting:board_name" value={chosen?.name || ""} />
            <select name="setting:board" value={board} onChange={(event) => setBoard(event.target.value)} aria-label="Pinterest board" style={{ width: "auto", maxWidth: 260 }}>
              <option value="">Choose a board…</option>
              {boards.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
            <button type="submit" className="sa-btn sa-btn-sm sa-btn-primary" disabled={!board || saver.state !== "idle"}>
              {saver.state !== "idle" ? "Saving…" : "Use this board"}
            </button>
          </saver.Form>
        ) : null}
      </div>
      {fetcher.data && !fetcher.data.ok ? <span className="sa-error-text">{fetcher.data.error}</span> : null}
      {saver.data ? (saver.data.ok ? <span className="sa-small" style={{ color: "#0c5132" }}>{saver.data.message}</span> : <span className="sa-error-text">{saver.data.error}</span>) : null}
    </div>
  );
}

function ProviderSettings({ channel }: { channel: Channel }) {
  const fields = PROVIDER_FIELDS[channel.provider];
  const { busy, isSubmitting } = useBusy();
  if (!fields) return null;
  const settings = channel.default_settings || {};
  return (
    <Form method="post" className="sa-stack-sm" preventScrollReset>
      <Hidden values={{ intent: "settings", channel_id: channel.channel_id }} />
      <div className="sa-grid">
        {fields.map((field) => {
          const value = settings[field.key] ?? field.fallback;
          return (
            <div key={field.key}>
              <input type="hidden" name="field_keys" value={field.key} />
              <input type="hidden" name={`type:${field.key}`} value={field.type} />
              {field.type === "checkbox" ? (
                <label className="sa-check">
                  <input type="checkbox" name={`setting:${field.key}`} value="1" defaultChecked={value === true || value === "true"} />
                  <span>{field.label}</span>
                </label>
              ) : (
                <Field label={field.label} help={field.help}>
                  <select name={`setting:${field.key}`} defaultValue={String(value)}>
                    {(field.options || []).map(([option, label]) => <option key={option} value={option}>{label}</option>)}
                  </select>
                </Field>
              )}
            </div>
          );
        })}
      </div>
      <div>
        <button type="submit" className="sa-btn sa-btn-sm" disabled={busy}>
          {isSubmitting("settings", ["channel_id", channel.channel_id]) ? "Saving…" : `Save ${providerLabel(channel.provider)} settings`}
        </button>
      </div>
    </Form>
  );
}

function PostizToolsSection() {
  const { tools, error } = usePostizTools();
  return (
    <s-section heading="Postiz tools" id="postiz-tools">
      <div className="sa-stack">
        <PostizToolsPanel tools={tools} error={error} />
        <div><Link to="/app/social/postiz">Open Postiz tools →</Link></div>
      </div>
    </s-section>
  );
}

export default function ChannelsPage() {
  const data = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>();
  const { settings, tz } = useSocial();
  const { busy, isSubmitting } = useBusy();
  const webhookUrl = (result?.ok && result.data && "url" in result.data ? result.data.url : "") || data.webhookUrl;
  const syncErrors = (data.lastSync?.errors || []).map(String);

  return (
    <>
      <ActionBanner result={result?.target && data.channels.some((channel) => channel.channel_id === result.target) ? null : result} />
      <LoadError error={data.error} />

      <s-section>
        <div className="sa-spread">
          <p className="sa-muted" style={{ maxWidth: 640 }}>
            Your social accounts are connected in <strong>Postiz</strong>. Connect or reconnect networks there, then press “Sync channels”.
            Here you choose which channels Social Autopilot may use and how each network posts.
          </p>
          <div className="sa-row">
            <span className="sa-small sa-muted">
              {data.lastSync?.synced_at
                ? <>Last synced <TimeText epoch={data.lastSync.synced_at} tz={tz} relative /> · {data.lastSync.count ?? 0} channel(s) in Postiz</>
                : "Not synced yet"}
            </span>
            <Form method="post"><Hidden values={{ intent: "sync" }} /><button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>{isSubmitting("sync") ? "Syncing…" : "Sync channels"}</button></Form>
          </div>
        </div>
        {syncErrors.length ? (
          <div style={{ marginTop: 10 }}>
            <Banner tone="warning" title="Some channel rules couldn't be read from Postiz">
              <ul style={{ margin: 0, paddingLeft: 18 }}>{syncErrors.map((error) => <li key={error}>{error}</li>)}</ul>
            </Banner>
          </div>
        ) : null}
        {settings && !settings.postiz.api_key_configured && settings.publisher === "postiz" ? (
          <div style={{ marginTop: 10 }}>
            <Banner tone="critical" title="Postiz API key missing">
              Add <code>{settings.postiz.api_key_env || "POSTIZ_API_KEY"}=&lt;your key&gt;</code> to the server&apos;s <code>.env</code> (Postiz → Settings → Developers → Public API) and restart the backend.
            </Banner>
          </div>
        ) : null}
      </s-section>

      {data.channels.length ? (
        <div className="sa-stack">
          {data.channels.map((channel) => {
            const problem = channel.missing_at ? "This channel no longer exists in Postiz. It will be skipped."
              : channel.disabled ? "Disconnected in Postiz — reconnect it in Postiz (the channel's menu → Reconnect), then sync channels."
                : "";
            return (
              <Card key={channel.channel_id} accent={problem || channel.needs_setup ? "critical" : channel.enabled ? "success" : "neutral"}>
                <div className="sa-stack">
                  <div className="sa-spread">
                    <div className="sa-row">
                      <ChannelChip provider={channel.provider} name={channel.name} picture={channel.picture} />
                      {channel.missing_at ? <Badge tone="critical">Missing</Badge> : channel.disabled ? <Badge tone="critical">Reconnect in Postiz</Badge>
                        : channel.enabled ? <Badge tone="success">In use</Badge> : <Badge tone="neutral">Off</Badge>}
                      {channel.customer_name ? <span className="sa-small sa-muted">Customer: {channel.customer_name}</span> : null}
                    </div>
                    <Form method="post" preventScrollReset>
                      <Hidden values={{ intent: "toggle", channel_id: channel.channel_id, enabled: channel.enabled ? "0" : "1" }} />
                      <button type="submit" className={`sa-btn sa-btn-sm ${channel.enabled ? "" : "sa-btn-primary"}`} disabled={busy}>
                        {channel.enabled ? "Turn off" : "Use this channel"}
                      </button>
                    </Form>
                  </div>
                  {result?.target === channel.channel_id ? <ActionBanner result={result} /> : null}
                  {problem ? <Banner tone="critical">{problem}</Banner> : null}

                  <div className="sa-small sa-muted">
                    {channel.profile ? `@${channel.profile.replace(/^@/, "")} · ` : ""}
                    {channel.network?.label || providerLabel(channel.provider)} · up to {(channel.max_length || channel.network?.max_length || 0).toLocaleString("en-GB")} characters
                    {channel.network?.link_policy ? ` · ${LINK_MODES[channel.network.link_policy] || humanize(channel.network.link_policy)}` : ""}
                    {channel.network?.video_only ? " · videos only" : ""}
                    {channel.network?.image_formats?.length ? ` · image format: ${channel.network.image_formats.map(humanize).join(", ")}` : ""}
                  </div>

                  {channel.provider === "pinterest" ? <PinterestBoardPicker channel={channel} /> : null}
                  <ProviderSettings channel={channel} />

                  <Form method="post" className="sa-row" preventScrollReset>
                    <Hidden values={{ intent: "cadence", channel_id: channel.channel_id }} />
                    <Field label="Max posts per day" help={`Empty = store cap (${settings?.schedule.daily_caps[channel.provider] ?? "none"}).`}>
                      <input type="number" name="max_per_day" min={0} max={100} defaultValue={channel.cadence?.max_per_day ?? ""} style={{ width: 120 }} />
                    </Field>
                    <Field label="Minimum gap (minutes)" help={`Empty = store setting (${settings?.schedule.min_gap_minutes ?? "—"}).`}>
                      <input type="number" name="min_gap_minutes" min={0} max={1440} defaultValue={channel.cadence?.min_gap_minutes ?? ""} style={{ width: 140 }} />
                    </Field>
                    <button type="submit" className="sa-btn sa-btn-sm" disabled={busy} style={{ alignSelf: "center" }}>
                      {isSubmitting("cadence", ["channel_id", channel.channel_id]) ? "Saving…" : "Save limits"}
                    </button>
                  </Form>

                  {channel.rules ? (
                    <details className="sa-plain">
                      <summary className="sa-small sa-muted">Postiz rules for this network</summary>
                      <p className="sa-small sa-prewrap">{channel.rules}</p>
                    </details>
                  ) : null}
                  <JsonBlock value={channel.default_settings} label="Saved network settings (advanced)" />
                </div>
              </Card>
            );
          })}
        </div>
      ) : !data.error ? (
        <EmptyState title="No channels yet" action={<Form method="post"><Hidden values={{ intent: "sync" }} /><button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>Sync channels from Postiz</button></Form>}>
          Connect Instagram, Facebook, Pinterest, TikTok and the rest in Postiz first. Then sync them here.
        </EmptyState>
      ) : null}

      <PostizToolsSection />

      <s-section heading="Postiz webhook (optional)">
        <div className="sa-stack" id="webhook">
          <p className="sa-muted">
            Lets Postiz tell Social Autopilot the moment a post goes live. Without it, statuses still update every 10 minutes.
            In Postiz open <strong>Settings → Webhooks</strong>, add a webhook for “post published” and paste this URL.
          </p>
          {webhookUrl ? (
            <div className="sa-row">
              <input type="text" readOnly value={webhookUrl} aria-label="Postiz webhook URL" onFocus={(event) => event.currentTarget.select()} style={{ flex: "1 1 320px", fontFamily: "ui-monospace, Menlo, monospace", fontSize: "0.75rem" }} />
              <button type="button" className="sa-btn" onClick={() => { void navigator.clipboard?.writeText(webhookUrl); }}>Copy</button>
            </div>
          ) : null}
          <div className="sa-row">
            {!webhookUrl ? (
              <Form method="post"><Hidden values={{ intent: "webhook" }} /><button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>Create webhook URL</button></Form>
            ) : (
              <Form method="post"><Hidden values={{ intent: "webhook_rotate" }} /><button type="submit" className="sa-btn sa-btn-critical" disabled={busy}>{isSubmitting("webhook_rotate") ? "Rotating…" : "Rotate URL (invalidates the old one)"}</button></Form>
            )}
          </div>
          {webhookUrl.startsWith("http://") ? <span className="sa-help">This address is not HTTPS — Postiz must be able to reach it over the internet. Check SHOPIFY_REACT_APP_URL.</span> : null}
        </div>
      </s-section>
    </>
  );
}
