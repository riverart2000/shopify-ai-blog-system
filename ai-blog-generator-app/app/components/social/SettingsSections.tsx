/**
 * Settings page sections. Each section keeps a local draft and saves only its own
 * part of the settings as a JSON patch (intent "save"), so concurrent edits elsewhere
 * (e.g. product exclusions) are never overwritten.
 */
import { useEffect, useState, type ReactNode } from "react";
import { Form, Link } from "react-router";

import type {
  ActionResult, AISettings, CaptionSettings, Collection, ContentSettings, EngineSettings, FallbackModel, GuardSettings,
  HashtagCloud, PostizSettings, ScheduleSettings, UtmSettings,
} from "../../lib/social-engine.types";
import { timezoneLabel } from "../../lib/social-time";
import { usePostizTools } from "./PostizParts";
import { CollectionPicker, KeyValueRows } from "./SchemaForm";
import { ActionBanner, Badge, Banner, Choice, Field, Hidden, Toggle, useBusy } from "./ui";
import {
  APPROVAL_MODES, CAPTION_PLACEHOLDERS, CAPTION_SOURCES, EMOJI_LEVELS, LANGUAGES, LINK_POLICIES, TONES, WEEKDAYS, providerLabel,
  splitList,
} from "./vocab";

type Update<T> = (patch: Partial<T>) => void;

function Section<T extends object>({ id, title, description, section, initial, result, render, toPatch }: {
  id: string;
  title: string;
  description?: ReactNode;
  /** Settings key this section edits (also identifies the section in the action result). */
  section: string;
  initial: T;
  result?: ActionResult<{ section: string }> | null;
  render: (draft: T, update: Update<T>) => ReactNode;
  /** Defaults to `{[section]: draft}`. */
  toPatch?: (draft: T) => Record<string, unknown>;
}) {
  const [draft, setDraft] = useState<T>(initial);
  const { busy, isSubmitting } = useBusy();
  const mine = result?.data?.section === section || (result && !result.ok && result.intent === `save:${section}`);
  return (
    <s-section heading={title} id={id}>
      <Form method="post" preventScrollReset className="sa-stack">
        <Hidden values={{ intent: `save:${section}`, section, patch: JSON.stringify(toPatch ? toPatch(draft) : { [section]: draft }) }} />
        {description ? <p className="sa-muted">{description}</p> : null}
        {render(draft, (patch) => setDraft((current) => ({ ...current, ...patch })))}
        {mine ? <ActionBanner result={result} /> : null}
        <div className="sa-form-actions">
          <button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>
            {isSubmitting(`save:${section}`) ? "Saving…" : `Save ${title.toLowerCase()}`}
          </button>
        </div>
      </Form>
    </s-section>
  );
}

function num(value: string): number | undefined {
  return value === "" ? undefined : Number(value);
}

function NumberField({ label, help, value, min, max, step = 1, onChange }: {
  label: string; help?: string; value: number | undefined; min?: number; max?: number; step?: number; onChange: (value: number | undefined) => void;
}) {
  const range = min !== undefined && max !== undefined ? `${min}–${max}.` : "";
  return (
    <Field label={label} help={[help, range].filter(Boolean).join(" ") || undefined}>
      <input type="number" min={min} max={max} step={step} value={value ?? ""} onChange={(event) => onChange(num(event.target.value))} />
    </Field>
  );
}

function hash(value: unknown): string {
  const text = JSON.stringify(value ?? "");
  let h = 0;
  for (let index = 0; index < text.length; index += 1) h = (h * 31 + text.charCodeAt(index)) | 0;
  return (h >>> 0).toString(36);
}

export function SettingsSections({ settings, providers, fallbackModels, collections, result }: {
  settings: EngineSettings;
  providers: Record<string, { label: string; max_length: number }>;
  fallbackModels: FallbackModel[];
  collections: Collection[];
  result?: ActionResult<{ section: string }> | null;
}) {
  const providerKeys = Object.keys(providers).length ? Object.keys(providers) : Object.keys(settings.schedule.daily_caps);
  const providerOptions: Array<[string, string]> = providerKeys.map((key) => [key, providers[key]?.label || providerLabel(key)]);
  // Client-only (server and browser ICU lists can differ, which would break hydration).
  const [timezones, setTimezones] = useState<string[]>([]);
  useEffect(() => {
    try {
      setTimezones((Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.("timeZone") || []);
    } catch {
      setTimezones([]);
    }
  }, []);
  const common = { result };

  return (
    <>
      <datalist id="sa-languages">{LANGUAGES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</datalist>
      <datalist id="sa-timezones">{timezones.map((zone) => <option key={zone} value={zone} />)}</datalist>

      <Section<Pick<EngineSettings, "publisher" | "default_approval_mode">>
        key={`auto-${hash([settings.publisher, settings.default_approval_mode])}`}
        id="automation" title="Automation" section="automation" toPatch={(draft) => ({ ...draft })} {...common}
        initial={{ publisher: settings.publisher, default_approval_mode: settings.default_approval_mode }}
        description="Switch Social Autopilot on or off on the Overview page."
        render={(draft, update) => (
          <div className="sa-stack">
            <div className="sa-grid">
              <Choice title="Publish through Postiz" description="Approved posts are scheduled in Postiz and go live.">
                <input type="radio" checked={draft.publisher === "postiz"} onChange={() => update({ publisher: "postiz" })} />
              </Choice>
              <Choice title="Practice mode (dry run)" description="Everything is planned, written and rendered, but nothing is sent to Postiz.">
                <input type="radio" checked={draft.publisher === "dry_run"} onChange={() => update({ publisher: "dry_run" })} />
              </Choice>
            </div>
            <strong>Default approval for new posts</strong>
            <div className="sa-grid">
              {APPROVAL_MODES.map((mode) => (
                <Choice key={mode.value} title={mode.label} description={mode.help}>
                  <input type="radio" checked={draft.default_approval_mode === mode.value} onChange={() => update({ default_approval_mode: mode.value })} />
                </Choice>
              ))}
            </div>
          </div>
        )}
      />

      <CaptionsSection key={`captions-${hash(settings.captions)}`} initial={settings.captions} providerOptions={providerOptions} collections={collections} result={result} />

      <Section<ScheduleSettings>
        key={`schedule-${hash(settings.schedule)}`}
        id="schedule" title="Schedule" section="schedule" initial={settings.schedule} {...common}
        description="When posts may go out. Campaigns can override days and times."
        render={(draft, update) => (
          <div className="sa-stack">
            <div className="sa-grid">
              <Field label="Store time zone" help={timezoneLabel(draft.timezone)}>
                <input type="text" list="sa-timezones" value={draft.timezone} onChange={(event) => update({ timezone: event.target.value.trim() })} />
              </Field>
              <Field label="Posting window starts"><input type="time" value={draft.window_start} onChange={(event) => update({ window_start: event.target.value })} /></Field>
              <Field label="Posting window ends"><input type="time" value={draft.window_end} onChange={(event) => update({ window_end: event.target.value })} /></Field>
              <NumberField label="Minimum gap between posts (minutes)" value={draft.min_gap_minutes} min={0} max={1440} onChange={(value) => update({ min_gap_minutes: value })} />
              <NumberField label="Plan this many days ahead" help="Outfy plans one day ahead so prices and stock are current." value={draft.plan_days_ahead} min={0} max={14} onChange={(value) => update({ plan_days_ahead: value })} />
              <NumberField label="Posts per channel per day" help="Daily slots on every channel (the network's daily limit still applies)." value={draft.posts_per_channel_per_day} min={1} max={24} onChange={(value) => update({ posts_per_channel_per_day: value })} />
            </div>
            <Toggle
              checked={draft.always_fill_daily_slots} onChange={(value) => update({ always_fill_daily_slots: value })}
              label="Always fill every daily slot"
              help="Every channel always gets its daily posts. If you have no SmartQ campaign, a 'Daily auto-fill' campaign is created for you (Gemma checks each post; anything flagged waits for you). Failed or rejected posts are replaced while the day's window is open; promotions take priority. A post still waiting for approval when its time passes is moved once, then released."
            />
            <fieldset className="sa-fieldset">
              <legend>Posting days</legend>
              <div className="sa-row">
                {WEEKDAYS.map(([value, label]) => (
                  <label key={value} className="sa-check">
                    <input type="checkbox" checked={draft.days.includes(value)} onChange={(event) => update({ days: event.target.checked ? [...draft.days, value].sort() : draft.days.filter((day) => day !== value) })} />
                    <span>{label}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="sa-row">
              <Toggle checked={draft.randomize_times} onChange={(value) => update({ randomize_times: value })} label="Pick a random time inside the window" help="Looks more natural than posting on the hour." />
              <Toggle checked={draft.use_best_times} onChange={(value) => update({ use_best_times: value })} label="Prefer the best-performing times" help="Learned from your analytics." />
            </div>
            <KeyValueRows
              label="Daily limit per network" numeric keyLabel="Network" keyOptions={providerOptions}
              help="Posts over the limit move to the next day (Outfy defaults: Instagram 24, X 3, Facebook 96, Pinterest 24, TikTok 10)."
              value={draft.daily_caps} onChange={(next) => update({ daily_caps: next as Record<string, number> })}
            />
          </div>
        )}
      />

      <Section<Omit<ContentSettings, "excluded_product_ids">>
        key={`content-${hash(settings.content)}`}
        id="content" title="Content" section="content" {...common}
        initial={{
          multi_image: settings.content.multi_image, rotate_images: settings.content.rotate_images,
          allow_reposting: settings.content.allow_reposting, repost_after_days: settings.content.repost_after_days,
          excluded_tags: settings.content.excluded_tags,
        }}
        render={(draft, update) => (
          <div className="sa-stack">
            <div className="sa-grid">
              <Toggle checked={draft.multi_image} onChange={(value) => update({ multi_image: value })} label="Use several product photos when a network allows it" />
              <Toggle checked={draft.rotate_images} onChange={(value) => update({ rotate_images: value })} label="Rotate images" help="Each repost of a product uses its next photo." />
              <Toggle checked={draft.allow_reposting} onChange={(value) => update({ allow_reposting: value })} label="Allow reposting" help="Off: a product is never posted twice. On: only after every eligible product has had a turn." />
              <NumberField label="Wait before reposting a product (days)" value={draft.repost_after_days} min={1} max={365} onChange={(value) => update({ repost_after_days: value })} />
            </div>
            <Field label="Never post products tagged" help="Comma separated Shopify tags.">
              <input type="text" defaultValue={draft.excluded_tags.join(", ")} onChange={(event) => update({ excluded_tags: splitList(event.target.value) })} />
            </Field>
            <span className="sa-small sa-muted">
              {settings.content.excluded_product_ids.length} product(s) individually excluded. <Link to="/app/social/products">Manage on the Products page</Link>.
            </span>
          </div>
        )}
      />

      <Section<Omit<AISettings, "api_key_configured" | "api_key_env">>
        key={`ai-${hash(settings.ai)}`}
        id="ai" title="AI (Gemma)" section="ai" {...common}
        initial={{
          base_url: settings.ai.base_url, model: settings.ai.model, timeout_seconds: settings.ai.timeout_seconds,
          temperature: settings.ai.temperature, decision_temperature: settings.ai.decision_temperature, max_tokens: settings.ai.max_tokens,
          fallback_model_id: settings.ai.fallback_model_id, ai_decisions: settings.ai.ai_decisions, ai_review: settings.ai.ai_review,
          vision_model: settings.ai.vision_model,
        }}
        description="Gemma runs locally (oMLX) and writes captions, plans campaigns and reviews posts."
        render={(draft, update) => (
          <div className="sa-stack">
            <div className="sa-row sa-small">
              {settings.ai.api_key_configured
                ? <Badge tone="success">API key set ({settings.ai.api_key_env})</Badge>
                : <Badge tone="neutral">No API key ({settings.ai.api_key_env || "none"}) — fine for a local server</Badge>}
            </div>
            <div className="sa-grid">
              <Field label="Gemma server URL" help="OpenAI-compatible base URL."><input type="url" value={draft.base_url} onChange={(event) => update({ base_url: event.target.value })} /></Field>
              <Field label="Model"><input type="text" value={draft.model} onChange={(event) => update({ model: event.target.value })} /></Field>
              <Field label="Vision model" help="Used to look at rendered images during review."><input type="text" value={draft.vision_model} onChange={(event) => update({ vision_model: event.target.value })} /></Field>
              <Field label="Fallback model" help="Used only if Gemma fails. Comes from the app's Models page.">
                <select value={draft.fallback_model_id} onChange={(event) => update({ fallback_model_id: event.target.value })}>
                  <option value="">No fallback</option>
                  {fallbackModels.map((model) => <option key={model.id} value={model.id}>{model.name} ({model.provider}{model.model_name ? ` · ${model.model_name}` : ""})</option>)}
                </select>
              </Field>
              <NumberField label="Caption creativity (temperature)" value={draft.temperature} min={0} max={2} step={0.05} onChange={(value) => update({ temperature: value })} />
              <NumberField label="Decision temperature" help="Lower = more predictable planning." value={draft.decision_temperature} min={0} max={2} step={0.05} onChange={(value) => update({ decision_temperature: value })} />
              <NumberField label="Timeout (seconds)" value={draft.timeout_seconds} min={10} max={1800} onChange={(value) => update({ timeout_seconds: value })} />
              <NumberField label="Max answer length (tokens)" value={draft.max_tokens} min={128} max={16384} onChange={(value) => update({ max_tokens: value })} />
            </div>
            <div className="sa-row">
              <Toggle checked={draft.ai_decisions} onChange={(value) => update({ ai_decisions: value })} label="Let Gemma make automation decisions" help="Which products, content mix and angles. Off = simple rotation rules." />
              <Toggle checked={draft.ai_review} onChange={(value) => update({ ai_review: value })} label="Gemma reviews every post" help="Scores each post and lists issues before approval." />
            </div>
          </div>
        )}
      />

      <Section<GuardSettings>
        key={`guards-${hash(settings.guards)}`}
        id="guards" title="Content guards" section="guards" initial={settings.guards} {...common}
        description="Safety checks that hold risky posts for a person, even in automatic mode."
        render={(draft, update) => (
          <div className="sa-grid">
            <Toggle checked={draft.hold_health_claims} onChange={(value) => update({ hold_health_claims: value })} label="Hold posts with health claims" help="Recommended for supplements and wellness products." />
            <NumberField label="Hold posts Gemma scores below" help="0 = never hold on score." value={draft.hold_low_review_score} min={0} max={10} onChange={(value) => update({ hold_low_review_score: value })} />
            <NumberField label="Rewrite attempts for a bad caption" value={draft.max_caption_retries} min={0} max={3} onChange={(value) => update({ max_caption_retries: value })} />
          </div>
        )}
      />

      <Section<UtmSettings>
        key={`utm-${hash(settings.utm)}`}
        id="utm" title="Tracking links (UTM)" section="utm" initial={settings.utm} {...common}
        description="Adds utm_source / utm_medium / utm_campaign to product links so sales show up in Shopify and Google Analytics."
        render={(draft, update) => (
          <div className="sa-stack">
            <div className="sa-grid">
              <Toggle checked={draft.enabled} onChange={(value) => update({ enabled: value })} label="Add tracking parameters to links" />
              <Field label="utm_medium"><input type="text" value={draft.medium} onChange={(event) => update({ medium: event.target.value })} /></Field>
            </div>
            <KeyValueRows
              label="utm_source per network (optional)" keyLabel="Network" keyOptions={providerOptions} numeric={false}
              help="By default the network name is used, e.g. instagram."
              value={draft.source_overrides} onChange={(next) => update({ source_overrides: next as Record<string, string> })}
            />
          </div>
        )}
      />

      <Section<Omit<PostizSettings, "api_key_configured" | "webhook_token_set" | "api_key_env" | "base_url">>
        key={`postiz-${hash(settings.postiz)}`}
        id="postiz" title="Postiz" section="postiz" {...common}
        initial={{
          create_post_hourly_budget: settings.postiz.create_post_hourly_budget, short_links: settings.postiz.short_links,
          tag_posts: settings.postiz.tag_posts, upload_adjust_video: settings.postiz.upload_adjust_video,
          customer_id: settings.postiz.customer_id, ai_video_monthly_budget: settings.postiz.ai_video_monthly_budget,
          ai_image_monthly_budget: settings.postiz.ai_image_monthly_budget, mcp_url: settings.postiz.mcp_url,
          video_voice_id: settings.postiz.video_voice_id, ai_backgrounds_for_promotions: settings.postiz.ai_backgrounds_for_promotions,
          trending_tiktok_music: settings.postiz.trending_tiktok_music,
        }}
        render={(draft, update) => (
          <div className="sa-stack">
            {settings.postiz.api_key_configured ? (
              <Banner tone="success">Postiz API key is set on the server ({settings.postiz.api_key_env}). Connected to {settings.postiz.base_url}.</Banner>
            ) : (
              <Banner tone="critical" title="Postiz API key missing">
                For security the key is never entered here. Add <code>{settings.postiz.api_key_env || "POSTIZ_API_KEY"}=&lt;your key&gt;</code> to the
                Python server&apos;s <code>.env</code> (find it in Postiz → Settings → Developers → Public API), then restart the backend.
              </Banner>
            )}
            <div className="sa-grid">
              <NumberField label="Max new posts sent to Postiz per hour" help="Postiz allows 30 API requests per hour; leave room for other calls." value={draft.create_post_hourly_budget} min={1} max={100} onChange={(value) => update({ create_post_hourly_budget: value })} />
              <NumberField label="AI images per month" help="Postiz AI background scenes and AI image ads (Postiz credits). 0 turns them off." value={draft.ai_image_monthly_budget} min={0} max={500} onChange={(value) => update({ ai_image_monthly_budget: value })} />
              <NumberField label="AI videos per month" help="Postiz AI videos (credits). 0 turns them off." value={draft.ai_video_monthly_budget} min={0} max={100} onChange={(value) => update({ ai_video_monthly_budget: value })} />
              <Field label="Postiz customer ID (optional)" help="Only if you use Postiz customers to separate brands."><input type="text" value={draft.customer_id} onChange={(event) => update({ customer_id: event.target.value })} /></Field>
              <Field label="Postiz MCP address" help="Used for Postiz AI images, videos, voices and the media library."><input type="url" value={draft.mcp_url} onChange={(event) => update({ mcp_url: event.target.value })} /></Field>
            </div>
            <VoicePicker value={draft.video_voice_id} onChange={(value) => update({ video_voice_id: value })} />
            <div className="sa-row">
              <Toggle checked={draft.ai_backgrounds_for_promotions} onChange={(value) => update({ ai_backgrounds_for_promotions: value })} label="AI background scenes on promotion posts" help="A seasonal Postiz AI scene behind your real product photo (uses AI image credits). Each promotion can override this." />
              <Toggle checked={draft.trending_tiktok_music} onChange={(value) => update({ trending_tiktok_music: value })} label="Trending TikTok music on videos" help="Picked with Postiz's TikTok music search." />
              <Toggle checked={draft.short_links} onChange={(value) => update({ short_links: value })} label="Shorten links with Postiz" />
              <Toggle checked={draft.tag_posts} onChange={(value) => update({ tag_posts: value })} label="Tag posts in Postiz" help="Makes Social Autopilot posts easy to find in Postiz." />
              <Toggle checked={draft.upload_adjust_video} onChange={(value) => update({ upload_adjust_video: value })} label="Let Postiz adjust videos on upload" />
            </div>
          </div>
        )}
      />
    </>
  );
}

/** ElevenLabs voices offered by Postiz (for slide videos), with audio previews. */
function VoicePicker({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const { tools, error, loading } = usePostizTools();
  const voices = tools?.voices || [];
  const current = voices.find((voice) => voice.id === value);
  return (
    <div className="sa-stack-sm">
      <Field label="Voice for Postiz slide videos" help={error ? `Voices couldn't be loaded from Postiz: ${error}` : "Empty = the first voice Postiz offers."}>
        <select value={value} onChange={(event) => onChange(event.target.value)} disabled={loading && !voices.length}>
          <option value="">{loading ? "Loading voices from Postiz…" : "Postiz default"}</option>
          {voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.name || voice.id}</option>)}
          {value && !current && !loading ? <option value={value}>{value} (not offered by Postiz now)</option> : null}
        </select>
      </Field>
      {current?.preview_url ? (
        <audio controls preload="none" src={current.preview_url} style={{ maxWidth: 320 }}>
          <track kind="captions" />
        </audio>
      ) : null}
    </div>
  );
}

function CaptionsSection({ initial, providerOptions, collections, result }: {
  initial: CaptionSettings; providerOptions: Array<[string, string]>; collections: Collection[]; result?: ActionResult<{ section: string }> | null;
}) {
  const [newTemplate, setNewTemplate] = useState("");
  return (
    <Section<CaptionSettings>
      id="captions" title="Captions" section="captions" initial={initial} result={result}
      description="How Gemma (or your templates) write captions. Campaigns and single posts can override these."
      render={(draft, update) => {
        const clouds = draft.hashtag_clouds;
        const setCloud = (index: number, patch: Partial<HashtagCloud>) => update({ hashtag_clouds: clouds.map((cloud, other) => (other === index ? { ...cloud, ...patch } : cloud)) });
        const templateKeys = Object.keys(draft.templates);
        return (
          <div className="sa-stack">
            <div className="sa-grid">
              <Field label="Captions written by">
                <select value={draft.source} onChange={(event) => update({ source: event.target.value as CaptionSettings["source"] })}>
                  {CAPTION_SOURCES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </Field>
              <Field label="Language" help="e.g. en-GB, fr-FR."><input type="text" list="sa-languages" value={draft.language} onChange={(event) => update({ language: event.target.value })} /></Field>
              <Field label="Tone">
                <select value={draft.tone} onChange={(event) => update({ tone: event.target.value as CaptionSettings["tone"] })}>
                  {TONES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </Field>
              <Field label="Emojis">
                <select value={draft.emoji_level} onChange={(event) => update({ emoji_level: event.target.value as CaptionSettings["emoji_level"] })}>
                  {EMOJI_LEVELS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </Field>
              <NumberField label="Hashtags per post" value={draft.hashtags_count} min={0} max={10} onChange={(value) => update({ hashtags_count: value })} />
              <Field label="Product category hint" help="e.g. “natural sleep supplements”."><input type="text" value={draft.product_category_hint} onChange={(event) => update({ product_category_hint: event.target.value })} /></Field>
              <Field label="Links">
                <select value={draft.link_policy} onChange={(event) => update({ link_policy: event.target.value as CaptionSettings["link_policy"] })}>
                  {LINK_POLICIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </Field>
              {draft.link_policy === "custom" ? (
                <Field label="Custom URL"><input type="url" value={draft.custom_url} onChange={(event) => update({ custom_url: event.target.value })} placeholder="https://…" /></Field>
              ) : null}
            </div>
            <div className="sa-row">
              <Toggle checked={draft.include_price} onChange={(value) => update({ include_price: value })} label="Mention the price" />
              <Toggle checked={draft.use_product_tags} onChange={(value) => update({ use_product_tags: value })} label="Use product tags as hashtags" />
              <Toggle checked={draft.include_cta} onChange={(value) => update({ include_cta: value })} label="End with a call to action" />
              <Toggle checked={draft.link_in_first_comment} onChange={(value) => update({ link_in_first_comment: value })} label="Put the link in the first comment where links aren't clickable" />
            </div>
            <Field label="Extra instructions for Gemma" help={`${draft.custom_instructions.length}/1000 characters.`} error={draft.custom_instructions.length > 1000 ? "Too long — 1000 characters maximum." : undefined}>
              <textarea rows={3} maxLength={1000} value={draft.custom_instructions} onChange={(event) => update({ custom_instructions: event.target.value })} placeholder="e.g. Never call products a cure. Mention free UK delivery over £30." />
            </Field>
            <Field label="Banned words and phrases" help="One per line or comma separated. Posts containing them are held for review.">
              <textarea rows={2} defaultValue={draft.banned_terms.join("\n")} onChange={(event) => update({ banned_terms: splitList(event.target.value) })} />
            </Field>

            <fieldset className="sa-fieldset">
              <legend>Hashtag clouds</legend>
              <div className="sa-stack-sm">
                <span className="sa-help">Named sets of hashtags, optionally tied to collections. A few random tags from the matching cloud are added to each post (not on Pinterest or Threads).</span>
                {clouds.map((cloud, index) => (
                  <div key={index} className="sa-card sa-card-muted sa-stack-sm">
                    <div className="sa-grid-2">
                      <Field label="Name"><input type="text" value={cloud.name} onChange={(event) => setCloud(index, { name: event.target.value })} /></Field>
                      <Field label="Hashtags" help="Comma or space separated, with or without #.">
                        <input type="text" defaultValue={cloud.tags.join(", ")} onChange={(event) => setCloud(index, { tags: event.target.value.split(/[\s,]+/).map((tag) => tag.trim()).filter(Boolean) })} />
                      </Field>
                    </div>
                    <Field label="Use for these collections" help="None = any product.">
                      <CollectionPicker value={cloud.collection_ids} onChange={(next) => setCloud(index, { collection_ids: next })} collections={collections} />
                    </Field>
                    <div className="sa-spread">
                      <Toggle checked={cloud.is_default} onChange={(value) => update({ hashtag_clouds: clouds.map((other, position) => ({ ...other, is_default: position === index ? value : value ? false : other.is_default })) })} label="Default cloud (used when no collection matches)" />
                      <button type="button" className="sa-btn sa-btn-sm sa-btn-critical" onClick={() => update({ hashtag_clouds: clouds.filter((_, other) => other !== index) })}>Remove cloud</button>
                    </div>
                  </div>
                ))}
                <div><button type="button" className="sa-btn sa-btn-sm" onClick={() => update({ hashtag_clouds: [...clouds, { name: `Cloud ${clouds.length + 1}`, tags: [], collection_ids: [], is_default: clouds.length === 0 }] })}>Add hashtag cloud</button></div>
              </div>
            </fieldset>

            <fieldset className="sa-fieldset">
              <legend>Caption templates (used when captions are written by templates, or as a fallback)</legend>
              <div className="sa-stack-sm">
                <details className="sa-plain">
                  <summary className="sa-small">Placeholders you can use</summary>
                  <div className="sa-grid-sm sa-small" style={{ marginTop: 6 }}>
                    {CAPTION_PLACEHOLDERS.map(([key, help]) => <span key={key}><code>{key}</code> <span className="sa-muted">— {help}</span></span>)}
                  </div>
                </details>
                {templateKeys.map((key) => (
                  <Field key={key} label={key === "default" ? "Every network (default)" : providerOptions.find(([option]) => option === key)?.[1] || key}>
                    <textarea rows={3} value={draft.templates[key]} onChange={(event) => update({ templates: { ...draft.templates, [key]: event.target.value } })} />
                    <button type="button" className="sa-btn sa-btn-sm sa-btn-plain" style={{ alignSelf: "flex-start" }} onClick={() => {
                      const copy = { ...draft.templates };
                      delete copy[key];
                      update({ templates: copy });
                    }}>Remove</button>
                  </Field>
                ))}
                <div className="sa-row">
                  <select value={newTemplate} onChange={(event) => setNewTemplate(event.target.value)} aria-label="Add a caption template" style={{ width: "auto" }}>
                    <option value="">Add a template for…</option>
                    {[["default", "Every network (default)"] as [string, string], ...providerOptions].filter(([key]) => !(key in draft.templates)).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                  </select>
                  <button type="button" className="sa-btn sa-btn-sm" disabled={!newTemplate} onClick={() => {
                    update({ templates: { ...draft.templates, [newTemplate]: "PRODUCT_TITLE\n\nPRODUCT_DESC\n\nPRODUCT_PRICE\n\nTAG_CLOUD" } });
                    setNewTemplate("");
                  }}>Add</button>
                </div>
              </div>
            </fieldset>
          </div>
        );
      }}
    />
  );
}
