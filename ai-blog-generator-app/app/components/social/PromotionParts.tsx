/**
 * Promotions (Outfy PromoQ): theme picker, the create / edit form and date helpers.
 * Backend truth: routes/social_engine/promotions.py and campaigns/rules/promotion.py (PromotionConfig).
 */
import { useState, type KeyboardEvent } from "react";
import { Form, Link, useFetcher } from "react-router";

import type {
  ActionResult, ApprovalMode, CampaignSchedule, Channel, Collection, OccasionDate, PostizBudget, ProductFilter,
  PromotionConfig, PromotionRow, PromotionTheme,
} from "../../lib/social-engine.types";
import { addDays, formatDateKey, isDateKey, keyToUtcEpoch, weekdayOfKey } from "../../lib/social-time";
import { ChannelChip } from "./ChannelChip";
import { useSocial } from "./context";
import { PostizCredits, PostizDesignPicker, creditsLeft } from "./PostizParts";
import { ProductPicker, type PickedProduct } from "./ProductPicker";
import { CollectionPicker } from "./SchemaForm";
import { Badge, Banner, Choice, Field, Hidden, Toggle, useBusy } from "./ui";
import { APPROVAL_MODES, CAPTION_PLACEHOLDERS, WEEKDAYS, contentTypeInfo, contentTypeLabel } from "./vocab";

export const PROMO_TEXT_MAX = 60;
const MAX_PROMO_LINES = 12;
const MAX_DURATION = 15;

// ---------------------------------------------------------------------------- date helpers

/** Whole days from one date key to another (negative when `to` is earlier). */
export function daysBetween(from: string, to: string): number {
  if (!isDateKey(from) || !isDateKey(to)) return 0;
  return Math.round((keyToUtcEpoch(to) - keyToUtcEpoch(from)) / 86_400);
}

export function inDays(days: number): string {
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  return days > 0 ? `in ${days} days` : `${-days} days ago`;
}

export function formatDay(key: string | undefined, withYear = false): string {
  if (!key || !isDateKey(key)) return "—";
  return formatDateKey(key, { weekday: "short", day: "numeric", month: "short", ...(withYear ? { year: "numeric" } : {}) });
}

export function promotionEnd(start: string, duration: number): string {
  return isDateKey(start) ? addDays(start, Math.max(1, duration || 1) - 1) : "";
}

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

// ---------------------------------------------------------------------------- theme cards

const SWATCH_KEYS = ["background", "primary", "secondary", "accent", "cta"] as const;

export function Palette({ colors }: { colors?: PromotionTheme["colors"] }) {
  const values = SWATCH_KEYS.map((key) => colors?.[key]).filter((value): value is string => Boolean(value));
  if (!values.length) return <span className="sa-small sa-muted">Your brand colours</span>;
  return (
    <span className="sa-swatches" title="Colours used on the post images">
      {values.map((color, index) => <span key={`${color}-${index}`} className="sa-swatch" style={{ background: color }} />)}
    </span>
  );
}

function ThemeCardBody({ theme, occasion }: { theme: PromotionTheme; occasion?: OccasionDate }) {
  return (
    <>
      <span className="sa-spread" style={{ flexWrap: "nowrap" }}>
        <span className="sa-theme-emoji" aria-hidden="true">{theme.emoji || "📣"}</span>
        <Palette colors={theme.colors} />
      </span>
      <strong>{theme.label}</strong>
      {theme.sticker ? <span><Badge tone="magic">{theme.sticker}</Badge></span> : null}
      <span className="sa-small sa-muted">
        {occasion ? `${formatDay(occasion.date)} · ${inDays(occasion.days_until)}` : `Usually runs ${plural(theme.duration_days, "day")}`}
      </span>
    </>
  );
}

/** Outfy's theme tabs: Occasions | Offers & Specials | New Arrivals | Custom. */
export function ThemePicker({ themes, categories, calendar, selected, onPick, hrefFor }: {
  themes: Record<string, PromotionTheme[]>;
  categories: Record<string, string>;
  calendar: OccasionDate[];
  selected?: string;
  onPick?: (theme: PromotionTheme) => void;
  /** Render the cards as links (e.g. to open the form prefilled) instead of buttons. */
  hrefFor?: (theme: PromotionTheme) => string;
}) {
  const tabs = Object.entries(categories);
  const initial = tabs.find(([key]) => (themes[key] || []).some((theme) => theme.key === selected))?.[0] || tabs[0]?.[0] || "occasion";
  const [tab, setTab] = useState(initial);
  const occasionFor = (key: string) => calendar.find((occasion) => occasion.theme_key === key);
  const list = [...(themes[tab] || [])];
  if (tab === "occasion") list.sort((a, b) => (occasionFor(a.key)?.days_until ?? 9999) - (occasionFor(b.key)?.days_until ?? 9999));

  return (
    <div className="sa-stack-sm">
      <div className="sa-tabs" role="tablist" aria-label="Promotion themes" style={{ marginBottom: 4 }}>
        {tabs.map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>
            {label}<span className="sa-count">{(themes[key] || []).length}</span>
          </button>
        ))}
      </div>
      <div className="sa-grid-sm" role="tabpanel">
        {list.map((theme) => {
          const className = `sa-theme ${selected === theme.key ? "selected" : ""}`.trim();
          const body = <ThemeCardBody theme={theme} occasion={occasionFor(theme.key)} />;
          return hrefFor ? (
            <Link key={theme.key} to={hrefFor(theme)} className={className} style={{ color: "inherit", textDecoration: "none" }}>{body}</Link>
          ) : (
            <button key={theme.key} type="button" className={className} aria-pressed={selected === theme.key} onClick={() => onPick?.(theme)}>{body}</button>
          );
        })}
      </div>
      {tab === "custom" ? (
        <span className="sa-help">Your own event — a launch day, an anniversary or a sale of your own. Give it a title and promo lines below.</span>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------- form

type Config = Omit<PromotionConfig, "schedule">;

type Draft = {
  id: string;
  name: string;
  approval_mode: ApprovalMode;
  channel_ids: string[];
  product_filter: ProductFilter;
  config: Config;
  /** null = the store's posting days and times. */
  schedule: CampaignSchedule | null;
};

type Touched = { texts: boolean; types: boolean; dates: boolean };

export type PromotionPrefill = { theme_key: string; start_date: string; duration_days: number };

const FALLBACK_CONFIG: Config = {
  theme_key: "", theme: "", scope: "store", collection_ids: [], product_ids: [], coupon_code: "", discount_text: "",
  promo_texts: [], start_date: "", duration_days: 7, posts_per_day: 1, content_types: [], countdown_days: 2, landing_url: "",
  caption_mode: "ai", custom_caption: "", show_logo: true, ai_background: null, postiz_media_search: "", hero_ai_video: false,
};

const CUSTOM_CAPTION_KEYS = [
  "PROMO_TEXT", "DISCOUNT_TEXT", "COUPON_CODE", "OCCASION", "PRODUCT_TITLE", "PRODUCT_PRICE", "PRODUCT_URL", "STORE_NAME",
  "STORE_URL", "TAG_CLOUD",
];

/**
 * Every field in the promotion form is an unnamed, state-driven input (the payload is sent as JSON), so Enter
 * in any of them — promo lines, coupon, product or Postiz design search — must not save and start the promotion.
 */
function keepEnterFromSaving(event: KeyboardEvent<HTMLFormElement>) {
  const target = event.target;
  if (event.key === "Enter" && target instanceof HTMLInputElement && !target.name) event.preventDefault();
}

function hasCustomSchedule(schedule: CampaignSchedule | undefined): schedule is CampaignSchedule {
  return Boolean(schedule && (schedule.days || schedule.window_start || schedule.window_end));
}

function withTheme(config: Config, theme: PromotionTheme, occasion: OccasionDate | undefined, touched: Touched): Config {
  const next: Config = { ...config, theme_key: theme.key };
  if (!touched.texts) next.promo_texts = [...theme.promo_texts];
  if (!touched.types) next.content_types = [...theme.content_types];
  if (!touched.dates) {
    if (occasion) {
      next.start_date = occasion.suggested_start;
      next.duration_days = occasion.suggested_duration;
    } else {
      next.duration_days = Math.min(MAX_DURATION, Math.max(1, theme.duration_days));
    }
  }
  return next;
}

export function PromotionForm({
  promotion, prefill, themes, categories, calendar, defaults, today, budget, channels, collections, products,
}: {
  promotion: PromotionRow | null;
  prefill: PromotionPrefill | null;
  themes: Record<string, PromotionTheme[]>;
  categories: Record<string, string>;
  calendar: OccasionDate[];
  defaults: Partial<PromotionConfig>;
  today: string;
  budget: PostizBudget;
  channels: Channel[];
  collections: Collection[];
  products: Record<string, PickedProduct>;
}) {
  const { contentTypes, rules, settings } = useSocial();
  const { busy, isSubmitting } = useBusy();
  const allThemes = Object.values(themes).flat();
  const findTheme = (key: string) => allThemes.find((theme) => theme.key === key);
  const occasionFor = (key: string) => calendar.find((occasion) => occasion.theme_key === key);

  const [touched, setTouched] = useState<Touched>(() => ({ texts: Boolean(promotion), types: Boolean(promotion), dates: Boolean(promotion) }));
  const [draft, setDraft] = useState<Draft>(() => {
    if (promotion) {
      const { schedule, ...config } = promotion.config || {};
      return {
        id: promotion.id, name: promotion.name, approval_mode: promotion.approval_mode || "manual",
        channel_ids: promotion.channel_ids || [], product_filter: { in_stock_only: true, ...(promotion.product_filter || {}) },
        config: { ...FALLBACK_CONFIG, ...config } as Config,
        schedule: hasCustomSchedule(schedule) ? schedule : null,
      };
    }
    const { schedule: _ignored, ...base } = defaults;
    void _ignored;
    let config: Config = { ...FALLBACK_CONFIG, ...base, start_date: base.start_date || addDays(today, 1) } as Config;
    const theme = prefill ? findTheme(prefill.theme_key) : undefined;
    if (theme && prefill) {
      config = withTheme(config, theme, occasionFor(theme.key), { texts: false, types: false, dates: false });
      if (isDateKey(prefill.start_date)) config.start_date = prefill.start_date;
      if (prefill.duration_days) config.duration_days = Math.min(MAX_DURATION, Math.max(1, prefill.duration_days));
    } else {
      config.theme_key = "";
    }
    return {
      id: "", name: "", approval_mode: settings?.default_approval_mode || "manual", channel_ids: [],
      product_filter: { in_stock_only: true }, config, schedule: null,
    };
  });
  const [pickingTheme, setPickingTheme] = useState(!draft.config.theme_key);
  const [names, setNames] = useState<Record<string, PickedProduct>>(products);
  const suggest = useFetcher<ActionResult<{ texts: string[]; source: string }>>();
  const [hiddenSuggestion, setHiddenSuggestion] = useState<unknown>(null);

  const config = draft.config;
  const theme = findTheme(config.theme_key);
  const occasion = theme ? occasionFor(theme.key) : undefined;
  const setConfig = (patch: Partial<Config>) => setDraft((current) => ({ ...current, config: { ...current.config, ...patch } }));
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft((current) => ({ ...current, [key]: value }));
  const setTexts = (texts: string[]) => {
    setTouched((current) => ({ ...current, texts: true }));
    setConfig({ promo_texts: texts });
  };
  const setDates = (patch: Partial<Pick<Config, "start_date" | "duration_days">>) => {
    setTouched((current) => ({ ...current, dates: true }));
    setConfig(patch);
  };
  const pickTheme = (next: PromotionTheme) => {
    setDraft((current) => ({ ...current, config: withTheme(current.config, next, occasionFor(next.key), touched) }));
    setPickingTheme(false);
  };

  // ---- derived values ----------------------------------------------------------
  const allowedTypes = rules.promotion?.allowed_content_types?.length
    ? rules.promotion.allowed_content_types
    : Array.from(new Set(allThemes.flatMap((item) => item.content_types)));
  const themeTypes = theme?.content_types || [];
  const effectiveTypes = config.content_types.length ? config.content_types : themeTypes;
  const end = promotionEnd(config.start_date, config.duration_days);
  const storeSchedule = settings?.schedule;
  const days = draft.schedule ? draft.schedule.days ?? [0, 1, 2, 3, 4, 5, 6] : storeSchedule?.days ?? [0, 1, 2, 3, 4, 5, 6];
  const promoDays = isDateKey(config.start_date)
    ? Array.from({ length: Math.max(1, config.duration_days) }, (_, index) => addDays(config.start_date, index))
    : [];
  const postingDays = promoDays.filter((key) => key >= today && days.includes(weekdayOfKey(key)));
  const usable = channels.filter((channel) => channel.enabled && !channel.disabled);
  const channelCount = draft.channel_ids.length ? draft.channel_ids.length : usable.length;
  const totalPosts = postingDays.length * config.posts_per_day * channelCount;
  const perChannelCap = storeSchedule?.posts_per_channel_per_day;
  const imageCredits = creditsLeft(budget, "image");
  const videoCredits = creditsLeft(budget, "video");
  const storeAiBackground = Boolean(settings?.postiz?.ai_backgrounds_for_promotions);
  const texts = config.promo_texts;
  const suggestions = suggest.data && suggest.data !== hiddenSuggestion ? suggest.data : null;
  const title = config.theme.trim() || theme?.label || "Promotion";

  const problems: string[] = [];
  if (!theme) problems.push("Pick a theme.");
  if (!isDateKey(config.start_date)) problems.push("Pick a start date.");
  else if (end < today) problems.push("This promotion would already be over — pick a start date that isn't in the past.");
  if (texts.some((text) => text.trim().length > PROMO_TEXT_MAX)) problems.push(`Shorten promo lines to ${PROMO_TEXT_MAX} characters or fewer.`);
  if (config.scope === "collections" && !config.collection_ids.length) problems.push("Choose at least one collection.");
  if (config.scope === "products" && !config.product_ids.length) problems.push("Choose at least one product.");
  if (config.caption_mode === "custom" && !config.custom_caption.trim()) problems.push("Write your custom caption, or let Gemma write the captions.");
  if (draft.schedule && !days.length) problems.push("Pick at least one posting day.");

  const warnings: string[] = [];
  if (isDateKey(config.start_date) && end >= today && config.start_date < today) warnings.push("The start date is in the past, so it starts posting straight away.");
  if (promoDays.length && end >= today && !postingDays.length) warnings.push("None of the promotion's days fall on your posting days, so nothing would be posted. Add more posting days or move the dates.");
  if (!usable.length) warnings.push("No channels are switched on yet — connect and enable channels before the promotion starts.");
  if (config.hero_ai_video && videoCredits && videoCredits.left < 1) warnings.push("No Postiz video credits are left this month — the launch-day post will use a regular product video instead.");

  // ---- payload -------------------------------------------------------------------
  const schedulePayload = draft.schedule
    ? Object.fromEntries(Object.entries(draft.schedule).filter(([, value]) => value !== null && value !== undefined && value !== ""))
    : null;
  const payload = {
    id: draft.id,
    name: draft.name.trim(),
    approval_mode: draft.approval_mode,
    channel_ids: draft.channel_ids,
    product_filter: draft.product_filter,
    config: {
      ...config,
      theme: config.theme.trim(),
      coupon_code: config.coupon_code.trim(),
      discount_text: config.discount_text.trim(),
      promo_texts: texts.map((text) => text.trim()).filter(Boolean),
      collection_ids: config.scope === "collections" ? config.collection_ids : [],
      product_ids: config.scope === "products" ? config.product_ids : [],
      ...(schedulePayload && Object.keys(schedulePayload).length ? { schedule: schedulePayload } : {}),
    },
  };
  const wasStopped = promotion?.campaign_status === "paused";

  const requestSuggestions = () => {
    setHiddenSuggestion(null);
    suggest.submit({
      intent: "suggest",
      request: JSON.stringify({
        theme_key: config.theme_key, discount_text: config.discount_text.trim(), coupon_code: config.coupon_code.trim(),
        product_ids: config.scope === "products" ? config.product_ids : [],
      }),
    }, { method: "post", action: "/app/social/promotions" });
  };

  return (
    <Form method="post" className="sa-stack" preventScrollReset onKeyDown={keepEnterFromSaving}>
      <Hidden values={{ intent: "save", promotion: JSON.stringify(payload) }} />

      {/* ---- 1. theme ---- */}
      <fieldset className="sa-fieldset">
        <legend>1. Theme</legend>
        {theme && !pickingTheme ? (
          <div className="sa-spread">
            <div className="sa-row">
              <span className="sa-theme-emoji" aria-hidden="true">{theme.emoji || "📣"}</span>
              <div className="sa-stack-sm">
                <strong>{theme.label}</strong>
                <span className="sa-row sa-small">
                  <Palette colors={theme.colors} />
                  {theme.sticker ? <Badge tone="magic">{theme.sticker}</Badge> : null}
                  <span className="sa-muted">{categories[theme.category] || ""}</span>
                </span>
              </div>
            </div>
            <button type="button" className="sa-btn sa-btn-sm" onClick={() => setPickingTheme(true)}>Change theme</button>
          </div>
        ) : (
          <div className="sa-stack-sm">
            <span className="sa-help">Pick what you&apos;re promoting. Each theme comes with promo lines, colours, a sticker and a recommended length — you can change all of them.</span>
            <ThemePicker themes={themes} categories={categories} calendar={calendar} selected={config.theme_key} onPick={pickTheme} />
            {theme ? <div><button type="button" className="sa-btn sa-btn-sm" onClick={() => setPickingTheme(false)}>Keep {theme.label}</button></div> : null}
          </div>
        )}
      </fieldset>

      {theme ? (
        <>
          {/* ---- 2. basics ---- */}
          <fieldset className="sa-fieldset">
            <legend>2. The offer</legend>
            <div className="sa-stack">
              <div className="sa-grid-2">
                <Field label={theme.category === "custom" ? "Event name on your posts" : "Title on your posts"} help={`Used on the images and in captions. Empty = “${theme.label}”.`}>
                  <input type="text" maxLength={80} value={config.theme} placeholder={theme.label} onChange={(event) => setConfig({ theme: event.target.value })} />
                </Field>
                <Field label="Promotion name (only you see this)" help="Optional — it's how the promotion is listed here.">
                  <input type="text" maxLength={120} value={draft.name} placeholder={`${title} ${config.start_date.slice(0, 4) || today.slice(0, 4)}`} onChange={(event) => set("name", event.target.value)} />
                </Field>
                <Field label="Discount text" help="Shown on the images, e.g. “20% off” or “Buy 2 get 1 free”. Up to 40 characters.">
                  <input type="text" maxLength={40} value={config.discount_text} placeholder="20% off everything" onChange={(event) => setConfig({ discount_text: event.target.value })} />
                </Field>
                <Field label="Coupon code" help="Optional. Create the code in Shopify Discounts first — we only show it.">
                  <input type="text" maxLength={40} value={config.coupon_code} placeholder="BLACKFRIDAY20" onChange={(event) => setConfig({ coupon_code: event.target.value.toUpperCase().replace(/\s+/g, "") })} />
                </Field>
              </div>

              <div className="sa-stack-sm">
                <div className="sa-spread">
                  <span className="sa-field-label">Promo lines</span>
                  <span className="sa-small sa-muted">Each post picks one at random</span>
                </div>
                {texts.map((text, index) => {
                  const over = text.trim().length > PROMO_TEXT_MAX;
                  return (
                    <div key={index} className="sa-row" style={{ flexWrap: "nowrap" }}>
                      <input
                        type="text" value={text} aria-label={`Promo line ${index + 1}`} aria-invalid={over}
                        onChange={(event) => setTexts(texts.map((other, position) => (position === index ? event.target.value : other)))}
                      />
                      <span className={`sa-counter ${over ? "sa-counter-over" : ""}`} style={{ minWidth: 42 }}>{text.trim().length}/{PROMO_TEXT_MAX}</span>
                      <button type="button" className="sa-btn sa-btn-sm sa-btn-plain" aria-label={`Remove promo line ${index + 1}`} onClick={() => setTexts(texts.filter((_, position) => position !== index))}>✕</button>
                    </div>
                  );
                })}
                {!texts.length ? <span className="sa-small sa-muted">No lines yet — Gemma will write some when the promotion starts, or add your own.</span> : null}
                <div className="sa-row">
                  <button type="button" className="sa-btn sa-btn-sm" disabled={texts.length >= MAX_PROMO_LINES} onClick={() => setTexts([...texts, ""])}>Add a line</button>
                  <button type="button" className="sa-btn sa-btn-sm" disabled={suggest.state !== "idle"} onClick={requestSuggestions}>
                    {suggest.state !== "idle" ? "Gemma is writing…" : "✨ Suggest with Gemma"}
                  </button>
                  {texts.length && theme.promo_texts.length ? (
                    <button type="button" className="sa-btn sa-btn-sm sa-btn-plain" onClick={() => setTexts([...theme.promo_texts])}>Reset to the theme&apos;s lines</button>
                  ) : null}
                </div>
                {suggestions ? (
                  <div className="sa-card sa-card-muted sa-stack-sm">
                    {suggestions.ok ? (
                      <>
                        <div className="sa-spread">
                          <strong className="sa-small">{suggestions.message}</strong>
                          <div className="sa-row">
                            <button type="button" className="sa-btn sa-btn-sm sa-btn-primary" onClick={() => { setTexts((suggestions.data?.texts || []).slice(0, MAX_PROMO_LINES)); setHiddenSuggestion(suggestions); }}>Use these lines</button>
                            <button type="button" className="sa-btn sa-btn-sm sa-btn-plain" onClick={() => setHiddenSuggestion(suggestions)}>Dismiss</button>
                          </div>
                        </div>
                        {(suggestions.data?.texts || []).map((line) => (
                          <div key={line} className="sa-row" style={{ flexWrap: "nowrap" }}>
                            <span className="sa-grow">“{line}”</span>
                            <button
                              type="button" className="sa-btn sa-btn-sm" disabled={texts.includes(line) || texts.length >= MAX_PROMO_LINES}
                              onClick={() => setTexts([...texts.filter((text) => text.trim()), line])}
                            >
                              {texts.includes(line) ? "Added" : "Add"}
                            </button>
                          </div>
                        ))}
                      </>
                    ) : (
                      <div className="sa-spread">
                        <span className="sa-error-text">{suggestions.error}</span>
                        <button type="button" className="sa-btn sa-btn-sm sa-btn-plain" onClick={() => setHiddenSuggestion(suggestions)}>Dismiss</button>
                      </div>
                    )}
                  </div>
                ) : null}
              </div>
            </div>
          </fieldset>

          {/* ---- 3. products ---- */}
          <fieldset className="sa-fieldset">
            <legend>3. Which products</legend>
            <div className="sa-stack">
              <div className="sa-grid">
                {([
                  ["store", "Store-wide", "Any product in your catalogue."],
                  ["collections", "Collections", "Only products in the collections you pick."],
                  ["products", "Specific products", "Only the products you pick."],
                ] as const).map(([value, label, help]) => (
                  <Choice key={value} title={label} description={help}>
                    <input type="radio" name="scope_choice" checked={config.scope === value} onChange={() => setConfig({ scope: value })} />
                  </Choice>
                ))}
              </div>
              {config.scope === "collections" ? (
                <CollectionPicker value={config.collection_ids} onChange={(next) => setConfig({ collection_ids: next })} collections={collections} />
              ) : null}
              {config.scope === "products" ? (
                <ProductPicker
                  selected={config.product_ids.map((id) => names[id] || { product_id: id, title: "", image: "" })}
                  onChange={(next) => {
                    setNames((current) => ({ ...current, ...Object.fromEntries(next.map((item) => [item.product_id, item])) }));
                    setConfig({ product_ids: next.map((item) => item.product_id) });
                  }}
                  emptyText="No products picked yet."
                />
              ) : null}
              <Toggle
                checked={draft.product_filter.in_stock_only !== false}
                onChange={(value) => set("product_filter", { ...draft.product_filter, in_stock_only: value })}
                label="Only products that are in stock"
              />
            </div>
          </fieldset>

          {/* ---- 4. when ---- */}
          <fieldset className="sa-fieldset">
            <legend>4. When and how often</legend>
            <div className="sa-stack">
              {occasion ? (
                <div className="sa-row sa-small">
                  <span>{theme.emoji} {theme.label} is on <strong>{formatDay(occasion.date, true)}</strong> ({inDays(occasion.days_until)}). We suggest starting {formatDay(occasion.suggested_start)} and running {plural(occasion.suggested_duration, "day")}.</span>
                  {config.start_date !== occasion.suggested_start || config.duration_days !== occasion.suggested_duration ? (
                    <button type="button" className="sa-btn sa-btn-sm sa-btn-plain" onClick={() => setDates({ start_date: occasion.suggested_start, duration_days: occasion.suggested_duration })}>Use suggested dates</button>
                  ) : null}
                </div>
              ) : null}
              <div className="sa-grid">
                <Field label="Start date">
                  <input type="date" value={config.start_date} min={draft.id ? undefined : today} required onChange={(event) => setDates({ start_date: event.target.value })} />
                </Field>
                <Field label="Runs for" help={end ? `Last day: ${formatDay(end, true)}` : undefined}>
                  <select value={config.duration_days} onChange={(event) => setDates({ duration_days: Number(event.target.value) })}>
                    {Array.from({ length: MAX_DURATION }, (_, index) => index + 1).map((count) => <option key={count} value={count}>{plural(count, "day")}</option>)}
                  </select>
                </Field>
                <Field label="Posts per network per day" help="1–2 is best; more can feel pushy.">
                  <select value={config.posts_per_day} onChange={(event) => setConfig({ posts_per_day: Number(event.target.value) })}>
                    <option value={1}>1 (recommended)</option>
                    <option value={2}>2 (recommended)</option>
                    <option value={3}>3</option>
                  </select>
                </Field>
                <Field label="Countdown at the end" help="The last days switch to “2 days left” / “Last day” posts.">
                  <select value={config.countdown_days} onChange={(event) => setConfig({ countdown_days: Number(event.target.value) })}>
                    <option value={0}>No countdown</option>
                    {[1, 2, 3, 4, 5].map((count) => <option key={count} value={count}>Last {plural(count, "day")}</option>)}
                  </select>
                </Field>
              </div>

              <div className="sa-stack-sm">
                <Toggle
                  checked={draft.schedule === null}
                  onChange={(useStore) => set("schedule", useStore ? null : {
                    ...(promotion?.config.schedule || {}),
                    days: storeSchedule?.days ?? [0, 1, 2, 3, 4, 5, 6],
                    window_start: storeSchedule?.window_start || "09:00",
                    window_end: storeSchedule?.window_end || "21:00",
                  })}
                  label="Use my usual posting days and times"
                  help={storeSchedule ? `From Settings: ${WEEKDAYS.filter(([value]) => storeSchedule.days.includes(value)).map(([, label]) => label).join(", ") || "no days"}, ${storeSchedule.window_start}–${storeSchedule.window_end}.` : undefined}
                />
                {draft.schedule ? (
                  <div className="sa-stack-sm" style={{ paddingLeft: 24 }}>
                    <div className="sa-row">
                      {WEEKDAYS.map(([value, label]) => (
                        <label key={value} className="sa-check">
                          <input
                            type="checkbox" checked={days.includes(value)}
                            onChange={(event) => set("schedule", { ...draft.schedule, days: event.target.checked ? [...days, value].sort() : days.filter((day) => day !== value) })}
                          />
                          <span>{label}</span>
                        </label>
                      ))}
                    </div>
                    <div className="sa-row">
                      <Field label="Post between">
                        <input type="time" value={draft.schedule.window_start || ""} onChange={(event) => set("schedule", { ...draft.schedule, window_start: event.target.value || null })} />
                      </Field>
                      <Field label="and">
                        <input type="time" value={draft.schedule.window_end || ""} onChange={(event) => set("schedule", { ...draft.schedule, window_end: event.target.value || null })} />
                      </Field>
                    </div>
                  </div>
                ) : null}
              </div>

              {postingDays.length && channelCount ? (
                <span className="sa-small">
                  About <strong>{totalPosts}</strong> post{totalPosts === 1 ? "" : "s"} in total: {config.posts_per_day} a day on {plural(channelCount, "channel")} across {plural(postingDays.length, "posting day")}.
                </span>
              ) : null}
              <span className="sa-help">
                Promotions come first. On a day that&apos;s already full, a promotion post takes the slot of a SmartQ post that hasn&apos;t been sent to Postiz yet.
                {perChannelCap ? ` Each channel still posts at most ${plural(perChannelCap, "time")} a day (Settings → Schedule).` : ""}
              </span>
            </div>
          </fieldset>

          {/* ---- 5. content ---- */}
          <fieldset className="sa-fieldset">
            <legend>5. What the posts look like</legend>
            <div className="sa-stack">
              <div className="sa-stack-sm">
                <span className="sa-field-label">Content types</span>
                <div className="sa-grid">
                  {allowedTypes.map((type) => {
                    const checked = effectiveTypes.includes(type);
                    const info = contentTypeInfo(type);
                    return (
                      <label key={type} className="sa-check">
                        <input
                          type="checkbox" checked={checked} disabled={checked && effectiveTypes.length === 1}
                          onChange={(event) => {
                            setTouched((current) => ({ ...current, types: true }));
                            setConfig({ content_types: event.target.checked ? [...effectiveTypes, type] : effectiveTypes.filter((other) => other !== type) });
                          }}
                        />
                        <span>
                          {contentTypeLabel(type, contentTypes)}
                          {themeTypes.includes(type) ? <span className="sa-muted"> · theme pick</span> : null}
                          {info.description ? <span className="sa-help" style={{ display: "block" }}>{info.description}</span> : null}
                        </span>
                      </label>
                    );
                  })}
                </div>
                <span className="sa-help">Each post uses one of these at random. Countdown posts are added automatically for the last days.</span>
              </div>

              <div className="sa-stack-sm">
                <span className="sa-field-label">Captions</span>
                <div className="sa-grid">
                  <Choice title="Written by Gemma (AI)" description="A fresh caption for every post, using your promo line, discount and code in your usual caption style.">
                    <input type="radio" name="caption_choice" checked={config.caption_mode === "ai"} onChange={() => setConfig({ caption_mode: "ai" })} />
                  </Choice>
                  <Choice title="My own caption" description="The same caption on every post, with placeholders filled in for each product.">
                    <input type="radio" name="caption_choice" checked={config.caption_mode === "custom"} onChange={() => setConfig({ caption_mode: "custom" })} />
                  </Choice>
                </div>
                {config.caption_mode === "custom" ? (
                  <div className="sa-stack-sm">
                    <textarea
                      rows={5} maxLength={2000} value={config.custom_caption} aria-label="Custom caption"
                      placeholder={"PROMO_TEXT ✨\nPRODUCT_TITLE — DISCOUNT_TEXT\nUse code COUPON_CODE at checkout.\nPRODUCT_URL\nTAG_CLOUD"}
                      onChange={(event) => setConfig({ custom_caption: event.target.value })}
                    />
                    <div className="sa-row sa-small">
                      <span className="sa-muted">Insert:</span>
                      {CAPTION_PLACEHOLDERS.filter(([key]) => CUSTOM_CAPTION_KEYS.includes(key)).map(([key, help]) => (
                        <button
                          key={key} type="button" className="sa-btn sa-btn-sm" title={help}
                          onClick={() => setConfig({ custom_caption: `${config.custom_caption}${config.custom_caption && !/\s$/.test(config.custom_caption) ? " " : ""}${key}` })}
                        >
                          {key}
                        </button>
                      ))}
                    </div>
                    <span className="sa-help">Placeholders are replaced on every post — PROMO_TEXT is the rotating promo line and PRODUCT_URL follows your link settings. {config.custom_caption.length}/2000 characters.</span>
                  </div>
                ) : null}
              </div>

              <div className="sa-grid-2">
                <Toggle checked={config.show_logo} onChange={(value) => setConfig({ show_logo: value })} label="Show my logo on the images" />
                <Field label="Landing page" help="Where post links go, e.g. your sale collection. Empty = each product's own page.">
                  <input type="url" value={config.landing_url} placeholder="https://yourstore.com/collections/sale" onChange={(event) => setConfig({ landing_url: event.target.value.trim() })} />
                </Field>
              </div>
            </div>
          </fieldset>

          {/* ---- 6. Postiz extras ---- */}
          <fieldset className="sa-fieldset">
            <legend>6. Postiz extras</legend>
            <div className="sa-stack">
              <div className="sa-spread">
                <span className="sa-help">Optional touches made with Postiz&apos;s own creative tools. Credits are only spent by the live system.</span>
                <div style={{ minWidth: 220, flex: "0 1 340px" }}><PostizCredits budget={budget} compact /></div>
              </div>

              <Field
                label="AI background scene"
                help={`A seasonal Postiz AI scene behind your real product photo. Uses 1 Postiz AI image credit per image post${imageCredits ? ` — ${imageCredits.left} of ${imageCredits.total} left this month` : ""}.`}
              >
                <select
                  value={config.ai_background === null || config.ai_background === undefined ? "" : String(config.ai_background)}
                  onChange={(event) => setConfig({ ai_background: event.target.value === "" ? null : event.target.value === "true" })}
                >
                  <option value="">Use my setting (currently {storeAiBackground ? "on" : "off"})</option>
                  <option value="true">Yes — add an AI scene</option>
                  <option value="false">No — use my templates</option>
                </select>
              </Field>

              <div className="sa-stack-sm">
                <span className="sa-field-label">Use my Postiz designs</span>
                <PostizDesignPicker value={config.postiz_media_search} onChange={(search) => setConfig({ postiz_media_search: search })} />
              </div>

              <Toggle
                checked={config.hero_ai_video}
                onChange={(value) => setConfig({ hero_ai_video: value })}
                label="Hero AI video on launch day"
                help={`The first post on day one is a video made by Postiz AI. Uses 1 of your monthly Postiz video credits${videoCredits ? ` (${videoCredits.left} of ${videoCredits.total} left)` : ""}.`}
              />
            </div>
          </fieldset>

          {/* ---- 7. where & approval ---- */}
          <fieldset className="sa-fieldset">
            <legend>7. Channels and approval</legend>
            <div className="sa-stack">
              <div className="sa-stack-sm">
                <label className="sa-check">
                  <input type="checkbox" checked={!draft.channel_ids.length} onChange={(event) => set("channel_ids", event.target.checked ? [] : usable.map((channel) => channel.channel_id))} />
                  <span>Post to every enabled channel</span>
                </label>
                {draft.channel_ids.length ? (
                  <div className="sa-grid">
                    {channels.map((channel) => (
                      <label key={channel.channel_id} className="sa-check" style={channel.enabled && !channel.disabled ? undefined : { opacity: 0.55 }}>
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
              <div className="sa-grid">
                {APPROVAL_MODES.map((mode) => (
                  <Choice key={mode.value} title={mode.label} description={mode.help}>
                    <input type="radio" name="approval_choice" checked={draft.approval_mode === mode.value} onChange={() => set("approval_mode", mode.value)} />
                  </Choice>
                ))}
              </div>
            </div>
          </fieldset>

          {warnings.length ? <Banner tone="warning">{warnings.map((warning) => <div key={warning}>{warning}</div>)}</Banner> : null}
        </>
      ) : null}

      <div className="sa-form-actions">
        <button type="submit" name="status" value="active" className="sa-btn sa-btn-primary" disabled={busy || problems.length > 0}>
          {isSubmitting("save") ? "Saving…" : !draft.id ? "Create promotion" : wasStopped ? "Save and resume" : "Save changes"}
        </button>
        <button type="submit" name="status" value="paused" className="sa-btn" disabled={busy || problems.length > 0}>
          {!draft.id ? "Save, don't start yet" : wasStopped ? "Save (keep stopped)" : "Save and stop"}
        </button>
        <Link to="/app/social/promotions" className="sa-btn sa-btn-plain">Cancel</Link>
        {problems.length ? <span className="sa-error-text">{problems.join(" ")}</span> : <span className="sa-help">The backend checks everything when you save and shows any problem above.</span>}
      </div>
    </Form>
  );
}
