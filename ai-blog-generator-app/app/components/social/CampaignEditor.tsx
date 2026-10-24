import { useMemo, useState } from "react";
import { Form, Link } from "react-router";

import type {
  Campaign, CampaignSchedule, CaptionSettings, Channel, Collection, ProductFilter, RuleInfo, Template,
} from "../../lib/social-engine.types";
import { todayKey } from "../../lib/social-time";
import { ChannelChip } from "./ChannelChip";
import { useSocial } from "./context";
import type { PickedProduct } from "./ProductPicker";
import { ProductPicker } from "./ProductPicker";
import { CollectionPicker, SchemaForm, defaultsFor } from "./SchemaForm";
import { Choice, Field, Hidden, useBusy } from "./ui";
import { APPROVAL_MODES, EMOJI_LEVELS, LANGUAGES, LINK_POLICIES, TONES, WEEKDAYS, splitList } from "./vocab";

type Draft = {
  id: string;
  name: string;
  rule_type: string;
  status: Campaign["status"];
  approval_mode: Campaign["approval_mode"];
  channel_ids: string[];
  product_filter: ProductFilter;
  config: Record<string, unknown>;
  schedule: CampaignSchedule;
  caption: Partial<CaptionSettings>;
};

function initialDraft(campaign: Campaign | null, ruleType: string, rule: RuleInfo | undefined, today: string): Draft {
  if (campaign) {
    const { schedule, ...config } = campaign.config || {};
    return {
      id: campaign.id, name: campaign.name, rule_type: campaign.rule_type, status: campaign.status,
      approval_mode: campaign.approval_mode, channel_ids: campaign.channel_ids || [],
      product_filter: { in_stock_only: true, ...(campaign.product_filter || {}) },
      config, schedule: (schedule as CampaignSchedule) || {}, caption: campaign.caption || {},
    };
  }
  return {
    id: "", name: rule?.label || "New campaign", rule_type: ruleType, status: "paused", approval_mode: "manual",
    channel_ids: [], product_filter: { in_stock_only: true },
    // The rule catalogue's `defaults` include list/dict defaults (default_factory) that the JSON schema leaves out.
    config: rule
      ? {
        ...defaultsFor(rule.schema),
        ...(rule.defaults || {}),
        ...(rule.schema.properties?.start_date && rule.schema.required?.includes("start_date") ? { start_date: today } : {}),
      }
      : {},
    schedule: {}, caption: {},
  };
}

function clean<T extends Record<string, unknown>>(value: T): Partial<T> {
  return Object.fromEntries(Object.entries(value).filter(([, entry]) => entry !== null && entry !== undefined && entry !== "")) as Partial<T>;
}

/** Create / edit form for a campaign. Submits the whole campaign as JSON (intent "save"). */
export function CampaignEditor({ campaign, ruleType, rules, channels, templates, collections, products, contentTypes }: {
  campaign: Campaign | null;
  ruleType: string;
  rules: Record<string, RuleInfo>;
  channels: Channel[];
  templates: Template[];
  collections: Collection[];
  products: Record<string, PickedProduct>;
  contentTypes: Record<string, string>;
}) {
  const rule = rules[campaign?.rule_type || ruleType];
  const { tz } = useSocial();
  const [draft, setDraft] = useState<Draft>(() => initialDraft(campaign, ruleType, rule, todayKey(tz)));
  const { busy, isSubmitting } = useBusy();
  const productNames = useMemo(() => ({ ...products }), [products]);
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft((current) => ({ ...current, [key]: value }));
  const setFilter = (patch: Partial<ProductFilter>) => setDraft((current) => ({ ...current, product_filter: { ...current.product_filter, ...patch } }));
  const setSchedule = (patch: Partial<CampaignSchedule>) => setDraft((current) => ({ ...current, schedule: { ...current.schedule, ...patch } }));
  const setCaption = (patch: Partial<CaptionSettings>) => setDraft((current) => ({ ...current, caption: clean({ ...current.caption, ...patch }) as Partial<CaptionSettings> }));

  if (!rule) {
    return <p className="sa-error-text">Unknown campaign type “{campaign?.rule_type || ruleType}”. The backend does not offer this rule.</p>;
  }

  const schedule = clean(draft.schedule as Record<string, unknown>);
  // `content` is deliberately not sent: the backend keeps the campaign's stored content when it is omitted.
  const payload = {
    id: draft.id,
    name: draft.name,
    rule_type: draft.rule_type,
    status: draft.status,
    approval_mode: draft.approval_mode,
    channel_ids: draft.channel_ids,
    product_filter: draft.product_filter,
    caption: draft.caption,
    config: { ...draft.config, ...(Object.keys(schedule).length ? { schedule } : {}) },
  };
  const allChannels = draft.channel_ids.length === 0;
  const days = draft.schedule.days ?? null;

  return (
    <Form method="post" className="sa-stack" preventScrollReset>
      <Hidden values={{ intent: "save", campaign: JSON.stringify(payload) }} />
      <div className="sa-stack-sm">
        <h3>{draft.id ? `Edit “${campaign?.name}”` : `New ${rule.label}`}</h3>
        <p className="sa-muted">{rule.description}</p>
      </div>

      <div className="sa-grid-2">
        <Field label="Campaign name *">
          <input type="text" value={draft.name} maxLength={120} required onChange={(event) => set("name", event.target.value)} />
        </Field>
        <Field label="Status">
          <select value={draft.status} onChange={(event) => set("status", event.target.value as Draft["status"])}>
            <option value="active">On — plan posts automatically</option>
            <option value="paused">Paused</option>
            <option value="archived">Archived</option>
          </select>
        </Field>
      </div>

      <fieldset className="sa-fieldset">
        <legend>Approval</legend>
        <div className="sa-grid">
          {APPROVAL_MODES.map((mode) => (
            <Choice key={mode.value} title={mode.label} description={mode.help}>
              <input type="radio" name="approval_choice" checked={draft.approval_mode === mode.value} onChange={() => set("approval_mode", mode.value)} />
            </Choice>
          ))}
        </div>
      </fieldset>

      <fieldset className="sa-fieldset">
        <legend>Channels</legend>
        <div className="sa-stack-sm">
          <label className="sa-check">
            <input type="checkbox" checked={allChannels} onChange={(event) => set("channel_ids", event.target.checked ? [] : channels.filter((channel) => channel.enabled && !channel.disabled).map((channel) => channel.channel_id))} />
            <span>Post to every enabled channel (including ones you connect later)</span>
          </label>
          {!allChannels ? (
            <div className="sa-grid">
              {channels.map((channel) => (
                <label key={channel.channel_id} className="sa-check">
                  <input
                    type="checkbox" checked={draft.channel_ids.includes(channel.channel_id)}
                    onChange={(event) => set("channel_ids", event.target.checked ? [...draft.channel_ids, channel.channel_id] : draft.channel_ids.filter((id) => id !== channel.channel_id))}
                  />
                  <ChannelChip provider={channel.provider} name={channel.name} picture={channel.picture} />
                </label>
              ))}
            </div>
          ) : null}
          {!channels.length ? <span className="sa-small sa-muted">No channels synced yet. <Link to="/app/social/channels">Set up channels</Link>.</span> : null}
        </div>
      </fieldset>

      <fieldset className="sa-fieldset">
        <legend>{rule.label} settings</legend>
        <SchemaForm
          schema={rule.schema}
          value={draft.config}
          onChange={(config) => set("config", config)}
          context={{ contentTypes, collections, templates, products: productNames, ruleType: draft.rule_type, allowedContentTypes: rule.allowed_content_types }}
          omit={["schedule"]}
        />
      </fieldset>

      <details>
        <summary>Which products (filter)</summary>
        <div className="sa-stack" style={{ marginTop: 8 }}>
          <div className="sa-row">
            <label className="sa-check"><input type="checkbox" checked={draft.product_filter.in_stock_only !== false} onChange={(event) => setFilter({ in_stock_only: event.target.checked })} /><span>In-stock products only</span></label>
            <label className="sa-check"><input type="checkbox" checked={Boolean(draft.product_filter.on_sale_only)} onChange={(event) => setFilter({ on_sale_only: event.target.checked })} /><span>On-sale products only</span></label>
          </div>
          <Field label="Only these collections" help="Leave empty for the whole catalogue.">
            <CollectionPicker value={draft.product_filter.collection_ids || []} onChange={(next) => setFilter({ collection_ids: next })} collections={collections} />
          </Field>
          <div className="sa-grid-2">
            <Field label="Only products tagged" help="Comma separated. Empty = any tag.">
              <input type="text" defaultValue={(draft.product_filter.include_tags || []).join(", ")} onChange={(event) => setFilter({ include_tags: splitList(event.target.value) })} />
            </Field>
            <Field label="Skip products tagged" help="Comma separated. Store-wide excluded tags always apply.">
              <input type="text" defaultValue={(draft.product_filter.exclude_tags || []).join(", ")} onChange={(event) => setFilter({ exclude_tags: splitList(event.target.value) })} />
            </Field>
          </div>
          <fieldset className="sa-fieldset">
            <legend>Only these products</legend>
            <ProductPicker
              selected={(draft.product_filter.product_ids || []).map((id) => productNames[id] || { product_id: id, title: "", image: "" })}
              onChange={(next) => { for (const item of next) productNames[item.product_id] = item; setFilter({ product_ids: next.map((item) => item.product_id) }); }}
              emptyText="Any product that matches the filters above."
            />
          </fieldset>
          <fieldset className="sa-fieldset">
            <legend>Never post these products</legend>
            <ProductPicker
              selected={(draft.product_filter.exclude_product_ids || []).map((id) => productNames[id] || { product_id: id, title: "", image: "" })}
              onChange={(next) => { for (const item of next) productNames[item.product_id] = item; setFilter({ exclude_product_ids: next.map((item) => item.product_id) }); }}
              emptyText="None (products excluded on the Products page are always skipped)."
            />
          </fieldset>
        </div>
      </details>

      <details>
        <summary>Posting times for this campaign (otherwise your Settings apply)</summary>
        <div className="sa-stack" style={{ marginTop: 8 }}>
          <div className="sa-stack-sm">
            <label className="sa-check">
              <input type="checkbox" checked={days === null} onChange={(event) => setSchedule({ days: event.target.checked ? null : [0, 1, 2, 3, 4, 5, 6] })} />
              <span>Use the store&apos;s posting days</span>
            </label>
            {days !== null ? (
              <div className="sa-row">
                {WEEKDAYS.map(([value, label]) => (
                  <label key={value} className="sa-check">
                    <input type="checkbox" checked={days.includes(value)} onChange={(event) => setSchedule({ days: event.target.checked ? [...days, value].sort() : days.filter((day) => day !== value) })} />
                    <span>{label}</span>
                  </label>
                ))}
              </div>
            ) : null}
          </div>
          <div className="sa-grid">
            <Field label="Window starts" help="Empty = store setting.">
              <input type="time" value={draft.schedule.window_start || ""} onChange={(event) => setSchedule({ window_start: event.target.value || null })} />
            </Field>
            <Field label="Window ends" help="Empty = store setting.">
              <input type="time" value={draft.schedule.window_end || ""} onChange={(event) => setSchedule({ window_end: event.target.value || null })} />
            </Field>
            <Field label="Random time inside the window">
              <select value={draft.schedule.randomize === true ? "true" : draft.schedule.randomize === false ? "false" : ""} onChange={(event) => setSchedule({ randomize: event.target.value === "" ? null : event.target.value === "true" })}>
                <option value="">Store default</option><option value="true">Yes</option><option value="false">No</option>
              </select>
            </Field>
            <Field label="Prefer best-performing times">
              <select value={draft.schedule.use_best_times === true ? "true" : draft.schedule.use_best_times === false ? "false" : ""} onChange={(event) => setSchedule({ use_best_times: event.target.value === "" ? null : event.target.value === "true" })}>
                <option value="">Store default</option><option value="true">Yes</option><option value="false">No</option>
              </select>
            </Field>
          </div>
        </div>
      </details>

      <details>
        <summary>Caption options for this campaign (otherwise your Settings apply)</summary>
        <div className="sa-grid" style={{ marginTop: 8 }}>
          <Field label="Tone">
            <select value={draft.caption.tone || ""} onChange={(event) => setCaption({ tone: (event.target.value || undefined) as CaptionSettings["tone"] })}>
              <option value="">Store default</option>{TONES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </Field>
          <Field label="Language">
            <select value={draft.caption.language || ""} onChange={(event) => setCaption({ language: event.target.value || undefined })}>
              <option value="">Store default</option>{LANGUAGES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </Field>
          <Field label="Emojis">
            <select value={draft.caption.emoji_level || ""} onChange={(event) => setCaption({ emoji_level: (event.target.value || undefined) as CaptionSettings["emoji_level"] })}>
              <option value="">Store default</option>{EMOJI_LEVELS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </Field>
          <Field label="Hashtags">
            <select value={draft.caption.hashtags_count === undefined ? "" : String(draft.caption.hashtags_count)} onChange={(event) => setCaption({ hashtags_count: event.target.value === "" ? undefined : Number(event.target.value) })}>
              <option value="">Store default</option>{Array.from({ length: 11 }, (_, count) => <option key={count} value={count}>{count}</option>)}
            </select>
          </Field>
          <Field label="Mention the price">
            <select value={draft.caption.include_price === undefined ? "" : String(draft.caption.include_price)} onChange={(event) => setCaption({ include_price: event.target.value === "" ? undefined : event.target.value === "true" })}>
              <option value="">Store default</option><option value="true">Yes</option><option value="false">No</option>
            </select>
          </Field>
          <Field label="Links">
            <select value={draft.caption.link_policy || ""} onChange={(event) => setCaption({ link_policy: (event.target.value || undefined) as CaptionSettings["link_policy"] })}>
              <option value="">Store default</option>{LINK_POLICIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </Field>
          {draft.caption.link_policy === "custom" ? (
            <Field label="Custom URL">
              <input type="url" value={draft.caption.custom_url || ""} onChange={(event) => setCaption({ custom_url: event.target.value })} placeholder="https://…" />
            </Field>
          ) : null}
          <Field label="Extra instructions for Gemma" help="Up to 1000 characters." className="sa-grow">
            <textarea rows={2} maxLength={1000} value={draft.caption.custom_instructions || ""} onChange={(event) => setCaption({ custom_instructions: event.target.value })} />
          </Field>
        </div>
      </details>

      <div className="sa-form-actions">
        <button type="submit" className="sa-btn sa-btn-primary" disabled={busy || !draft.name.trim()}>
          {isSubmitting("save") ? "Saving…" : draft.id ? "Save campaign" : "Create campaign"}
        </button>
        <Link to="/app/social/campaigns" className="sa-btn">Close</Link>
        <span className="sa-help">Settings are checked by the backend when you save; any problem is shown above exactly as reported.</span>
      </div>
    </Form>
  );
}
