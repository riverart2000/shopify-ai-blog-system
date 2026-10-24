import { useMemo, useState, type KeyboardEvent } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useLoaderData } from "react-router";

import { ChannelChip } from "../components/social/ChannelChip";
import { LiveBadge, PostizCredits, PostizDesignPicker, creditsLeft, usePostizTools } from "../components/social/PostizParts";
import { ProductPicker, toPicked, type PickedProduct } from "../components/social/ProductPicker";
import { useSocial } from "../components/social/context";
import { ActionBanner, Banner, Card, Choice, EmptyState, Field, Hidden, LoadError, Toggle, useBusy } from "../components/social/ui";
import {
  APPROVAL_MODES, CAPTION_SOURCES, EMOJI_LEVELS, LANGUAGES, LINK_POLICIES, TEXT_FIELDS, TONES, contentTypeInfo,
} from "../components/social/vocab";
import {
  SocialEngineError, errorMessage, formBool, formList, formString, runIntent, seFetch, seg, signedTemplatePreviewUrl,
  type Channel, type ChannelsResponse, type PostizBudget, type Product, type PromotionTheme, type PromotionsResponse,
  type Template, type TemplatesResponse,
} from "../lib/social-engine.server";
import { timezoneLabel, zonedLocalToEpoch } from "../lib/social-time";
import { authenticate } from "../shopify.server";

export const handle = { title: "Create a post" };

type StudioTheme = Pick<PromotionTheme, "key" | "label" | "emoji" | "sticker" | "promo_texts" | "colors">;
type ThemeGroup = { key: string; label: string; themes: StudioTheme[] };

/** Promotion themes grouped like the Promotions tab ("Custom event" is the same as no theme here). */
function themeGroups(response: PromotionsResponse): ThemeGroup[] {
  return Object.entries(response.categories || {})
    .filter(([category]) => category !== "custom")
    .map(([category, label]) => ({
      key: category,
      label,
      themes: (response.themes?.[category] || []).map((theme) => ({
        key: theme.key, label: theme.label, emoji: theme.emoji, sticker: theme.sticker,
        promo_texts: theme.promo_texts || [], colors: theme.colors || {},
      })),
    }))
    .filter((group) => group.themes.length > 0);
}

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  const url = new URL(request.url);
  const requested = [
    ...url.searchParams.getAll("product_id"),
    ...(url.searchParams.get("product_ids") || "").split(","),
  ].map((id) => id.trim()).filter((id) => /^[A-Za-z0-9_-]{1,64}$/.test(id)).slice(0, 4);
  try {
    const [channels, templates, promotions, products] = await Promise.all([
      seFetch<ChannelsResponse>("/channels"),
      seFetch<TemplatesResponse>("/templates").catch((error) => ({ templates: [] as Template[], error: errorMessage(error) })),
      // Themes + Postiz credit counts (counted locally — this does not call Postiz).
      seFetch<PromotionsResponse>("/promotions").catch((error) => ({ error: errorMessage(error) })),
      Promise.all(requested.map((id) => seFetch<{ product: Product }>(`/products/${seg(id)}`).then((data) => data.product).catch(() => null))),
    ]);
    const promo = "categories" in promotions ? promotions : null;
    return {
      channels: channels.channels,
      templates: templates.templates.map((template) => ({
        ...template,
        preview_url: template.formats[0] ? signedTemplatePreviewUrl(template.key, template.formats[0]) : "",
      })),
      templatesError: "error" in templates ? String(templates.error) : "",
      themeGroups: promo ? themeGroups(promo) : [],
      themesError: promo ? "" : String((promotions as { error?: string }).error || ""),
      budget: promo?.postiz_budget ?? null,
      preselected: products.filter((product): product is Product => Boolean(product)).map(toPicked),
      initialType: url.searchParams.get("type") || "",
      error: "",
    };
  } catch (error) {
    return {
      channels: [] as Channel[], templates: [] as Array<Template & { preview_url: string }>, templatesError: "",
      themeGroups: [] as ThemeGroup[], themesError: "", budget: null as PostizBudget | null,
      preselected: [] as PickedProduct[], initialType: "", error: errorMessage(error),
    };
  }
};

const CAPTION_FIELDS = ["source", "tone", "language", "emoji_level", "hashtags_count", "include_price", "include_cta", "link_policy", "custom_url"];

type ChannelOption = { field: string; label: string; options: Array<[string, string]>; help?: string };

const INSTAGRAM_OPTIONS: ChannelOption[] = [
  { field: "post_type", label: "Post as", options: [["post", "Feed post (videos become Reels)"], ["story", "Story"]], help: "A story takes exactly one image or video." },
];
const TIKTOK_OPTIONS: ChannelOption[] = [
  // Values: TIKTOK_PRIVACY in services/social_engine/publishers/postiz_settings.py
  { field: "privacy_level", label: "Who can watch", options: [["PUBLIC_TO_EVERYONE", "Everyone"], ["MUTUAL_FOLLOW_FRIENDS", "Friends"], ["FOLLOWER_OF_CREATOR", "Followers"], ["SELF_ONLY", "Only me"]] },
];

/** Per-post network options (sent as `channel_settings`, merged over the channel's own defaults). */
const CHANNEL_OPTIONS: Record<string, ChannelOption[]> = {
  instagram: INSTAGRAM_OPTIONS,
  "instagram-standalone": INSTAGRAM_OPTIONS,
  facebook: [
    { field: "post_type", label: "Post as", options: [["post", "Page post"], ["story", "Story"]], help: "Stories need an image or video and don't show the caption link." },
  ],
  tiktok: TIKTOK_OPTIONS,
  "tiktok-business": TIKTOK_OPTIONS,
};

const ALLOWED_CHANNEL_OPTIONS = new Set(
  Object.values(CHANNEL_OPTIONS).flat().flatMap((option) => option.options.map(([value]) => `${option.field}=${value}`)),
);

/** Same rule as the backend's Postiz settings check. */
const LINK_RE = /^https?:\/\/[^\s/$.?#][^\s]*$/i;

export const action = async ({ request }: ActionFunctionArgs) => {
  const { redirect } = await authenticate.admin(request);
  const form = await request.formData();
  const result = await runIntent<{ post_id: string }>("create", async () => {
    const contentType = formString(form, "content_type");
    const timing = formString(form, "timing") || "slot";
    const tz = formString(form, "tz") || "UTC";
    const local = formString(form, "scheduled_local");
    let scheduledAt: number | undefined;
    if (timing === "pick" || (timing === "draft" && local)) {
      if (!local) throw new SocialEngineError("Pick a date and time, or choose “Next free slot”.");
      const epoch = zonedLocalToEpoch(local, tz);
      if (!epoch) throw new SocialEngineError("The date and time is not valid.");
      if (epoch < Date.now() / 1000 + 120) throw new SocialEngineError("Pick a time at least two minutes from now.");
      scheduledAt = epoch;
    }
    const linkUrl = formString(form, "link_url");
    if (linkUrl && (linkUrl.length > 1000 || !LINK_RE.test(linkUrl))) {
      throw new SocialEngineError("The link must be a full web address starting with https:// (up to 1,000 characters).");
    }
    const themeKey = formString(form, "theme_key");
    const channelIds = formList(form, "channel_ids");
    const texts: Record<string, string> = {};
    const captionOverrides: Record<string, unknown> = {};
    const channelSettings: Record<string, Record<string, string>> = {};
    for (const [key, raw] of form.entries()) {
      const value = typeof raw === "string" ? raw.trim() : "";
      if (!value) continue;
      if (key.startsWith("text:")) texts[key.slice(5)] = value;
      if (key.startsWith("cap:") && CAPTION_FIELDS.includes(key.slice(4))) {
        const field = key.slice(4);
        captionOverrides[field] = field === "hashtags_count" ? Number(value)
          : field === "include_price" || field === "include_cta" ? value === "true"
            : value;
      }
      if (key.startsWith("chset:")) {
        // chset:<channel_id>:<field> — only for chosen channels and known network values.
        const rest = key.slice(6);
        const cut = rest.lastIndexOf(":");
        const channelId = rest.slice(0, cut);
        const field = rest.slice(cut + 1);
        if (cut > 0 && channelIds.includes(channelId) && ALLOWED_CHANNEL_OPTIONS.has(`${field}=${value}`)) {
          channelSettings[channelId] = { ...channelSettings[channelId], [field]: value };
        }
      }
    }
    const created = await seFetch<{ post_id: string; scheduled_at: number }>("/posts", {
      body: {
        content_type: contentType,
        product_ids: formList(form, "product_ids"),
        channel_ids: channelIds,
        template_key: formString(form, "template_key"),
        scheduled_at: scheduledAt ?? null,
        brief: formString(form, "brief"),
        angle: formString(form, "angle"),
        texts,
        caption_overrides: captionOverrides,
        approval_mode: formString(form, "approval_mode") || "manual",
        publish_mode: timing === "now" ? "now" : timing === "draft" ? "draft" : "schedule",
        link_url: linkUrl,
        channel_settings: channelSettings,
        theme_key: /^[a-z0-9_]{1,64}$/.test(themeKey) ? themeKey : "",
        ai_background: formBool(form, "ai_background"),
        postiz_media_search: formString(form, "postiz_media_search").slice(0, 80),
      },
    });
    return { message: "Post created.", data: { post_id: created.post_id } };
  });
  if (result.ok && result.data?.post_id) {
    return redirect(`/app/social/posts/${encodeURIComponent(result.data.post_id)}`);
  }
  return result;
};

function usable(channel: Channel): boolean {
  return channel.enabled && !channel.disabled && !channel.missing_at;
}

/** "12 of 20 AI image credits left this month" — or why none can be used. */
function CreditNote({ budget, kind }: { budget?: PostizBudget; kind: "image" | "video" }) {
  const credit = creditsLeft(budget, kind);
  if (!credit) return null;
  const noun = kind === "image" ? "AI image" : "AI video";
  const fallback = kind === "image" ? "the template background" : "a template video";
  const style = { display: "block", marginTop: 2 };
  if (!credit.total) return <span className="sa-error-text" style={style}>Postiz {noun}s are off (monthly limit 0 in Settings → Postiz) — {fallback} is used instead.</span>;
  if (!credit.left) return <span className="sa-error-text" style={style}>No {noun} credits left this month — {fallback} is used instead.</span>;
  return <span style={style}>{credit.left} of {credit.total} {noun} credits left this month.</span>;
}

/** Enter in a picker's search box (an unnamed input, e.g. product or Postiz design search) must not create the post. */
function keepPickersFromSubmitting(event: KeyboardEvent<HTMLFormElement>) {
  const target = event.target;
  if (event.key === "Enter" && target instanceof HTMLInputElement && !target.name) event.preventDefault();
}

function ThemeSwatches({ colors }: { colors: StudioTheme["colors"] }) {
  const values = (["primary", "background", "cta", "accent", "text"] as const).map((key) => colors[key]).filter(Boolean);
  if (!values.length) return null;
  return (
    <span className="sa-swatches" aria-hidden="true">
      {values.map((color, index) => <span key={`${color}-${index}`} className="sa-swatch" style={{ background: color }} />)}
    </span>
  );
}

export default function StudioPage() {
  const data = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>();
  const { tz, settings, contentTypes } = useSocial();
  const { busy, isSubmitting } = useBusy();
  // Live flag + fresh credit counts (loads once, in the background); the loader's counts show meanwhile.
  const { tools } = usePostizTools();
  const budget = tools?.budget ?? data.budget ?? undefined;
  const live = tools?.live_publishing;

  const typeKeys = Object.keys(contentTypes);
  const [contentType, setContentType] = useState(
    typeKeys.includes(data.initialType) ? data.initialType : data.preselected.length > 1 ? "collage" : "single_product",
  );
  const info = contentTypeInfo(contentType);
  const [products, setProducts] = useState<PickedProduct[]>(data.preselected);
  const [templateKey, setTemplateKey] = useState("");
  const usableChannels = data.channels.filter(usable);
  const [channelIds, setChannelIds] = useState<string[]>(usableChannels.map((channel) => channel.channel_id));
  const [channelOptions, setChannelOptions] = useState<Record<string, string>>({});
  const [timing, setTiming] = useState<"slot" | "pick" | "now" | "draft">("slot");
  const [linkPolicy, setLinkPolicy] = useState("");
  const [themeKey, setThemeKey] = useState("");
  const [sticker, setSticker] = useState("");
  const [aiBackground, setAiBackground] = useState(false);
  const [designSearch, setDesignSearch] = useState("");

  const chosenProducts = products.slice(0, info.maxProducts);
  const templates = useMemo(
    () => data.templates.filter((template) => template.content_type === contentType),
    [data.templates, contentType],
  );
  const themes = useMemo(() => data.themeGroups.flatMap((group) => group.themes), [data.themeGroups]);
  const theme = themes.find((item) => item.key === themeKey) || null;
  const pickTheme = (key: string) => {
    setThemeKey(key);
    setSticker(themes.find((item) => item.key === key)?.sticker || "");
  };

  // What the backend actually uses (pipeline/generate.py): designs and AI scenes are for image posts;
  // an AI image ad always gets an AI scene; a Postiz design replaces the image (so no AI scene).
  const isVideo = Boolean(info.video);
  const designAllowed = !isVideo && contentType !== "ai_image_ad";
  const design = designAllowed ? designSearch : "";
  const backgroundAllowed = designAllowed && !design;
  const background = backgroundAllowed && aiBackground;
  const usesImageCredit = contentType === "ai_image_ad" || background;
  const usesVideoCredit = contentType === "ai_video";
  const creditText = usesImageCredit ? " Uses 1 Postiz AI image credit." : usesVideoCredit ? " Uses 1 Postiz AI video credit." : "";

  const optionChannels = usableChannels.filter((channel) => channelIds.includes(channel.channel_id) && CHANNEL_OPTIONS[channel.provider]);
  const productProblem = chosenProducts.length < info.minProducts
    ? `Choose at least ${info.minProducts} product${info.minProducts > 1 ? "s" : ""}.`
    : "";
  const canSubmit = !productProblem && channelIds.length > 0;

  if (data.error) return <LoadError error={data.error} />;

  return (
    <Form method="post" className="sa-stack" onKeyDown={keepPickersFromSubmitting}>
      <Hidden
        values={{
          intent: "create", content_type: contentType, template_key: templateKey, tz,
          theme_key: themeKey, ai_background: background ? "1" : "", postiz_media_search: design,
        }}
      />
      {chosenProducts.map((product) => <input key={product.product_id} type="hidden" name="product_ids" value={product.product_id} />)}
      <ActionBanner result={result && "intent" in result ? result : null} />

      <s-section heading="1. What kind of post?">
        <div className="sa-grid">
          {typeKeys.map((key) => {
            const typeInfo = contentTypeInfo(key);
            return (
              <Choice
                key={key} title={contentTypes[key]}
                description={typeInfo.credits ? <>{typeInfo.description}<CreditNote budget={budget} kind={typeInfo.credits} /></> : typeInfo.description}
              >
                <input
                  type="radio" name="content_type_choice" value={key} checked={contentType === key}
                  onChange={() => { setContentType(key); setTemplateKey(""); }}
                />
              </Choice>
            );
          })}
        </div>
      </s-section>

      {info.maxProducts > 0 ? (
        <s-section heading={`2. Product${info.maxProducts > 1 ? `s (up to ${info.maxProducts})` : ""}`}>
          <ProductPicker
            selected={chosenProducts}
            onChange={setProducts}
            max={info.maxProducts}
            emptyText={info.minProducts ? "Choose the product to feature." : "Optional — add a product to feature it."}
          />
          {products.length > info.maxProducts ? <p className="sa-help">Only the first {info.maxProducts} product(s) are used for this kind of post.</p> : null}
        </s-section>
      ) : null}

      <s-section heading="3. Template">
        {info.credits ? (
          <p className="sa-help">
            {contentType === "ai_video"
              ? "Postiz makes the video from your product photos. If it can't (no credit, or not the live system), the automatic template video is used."
              : "AI image ads use the automatic product (or sale) template, with the Postiz AI scene behind your product."}
          </p>
        ) : (
          <>
            {data.templatesError ? <Banner tone="warning" title="Templates couldn't be loaded">{data.templatesError}</Banner> : null}
            <div className="sa-grid">
              <Choice title="Automatic" description="Use the best active template for this kind of post.">
                <input type="radio" name="template_choice" checked={templateKey === ""} onChange={() => setTemplateKey("")} />
              </Choice>
              {templates.map((template) => (
                <Choice
                  key={template.key}
                  title={template.name}
                  description={(
                    <>
                      {template.preview_url ? <img src={template.preview_url} alt={`${template.name} preview`} loading="lazy" className="sa-media" style={{ maxHeight: 160, objectFit: "contain", margin: "4px 0" }} /> : null}
                      {template.formats.join(", ")}{template.is_active ? "" : " · inactive"}
                    </>
                  )}
                >
                  <input type="radio" name="template_choice" checked={templateKey === template.key} onChange={() => setTemplateKey(template.key)} />
                </Choice>
              ))}
            </div>
            {!templates.length && !data.templatesError ? <p className="sa-help" style={{ marginTop: 6 }}>No templates for this content type yet — the automatic choice will be used.</p> : null}
          </>
        )}
      </s-section>

      <s-section heading="4. Theme and Postiz extras (optional)">
        <div className="sa-stack">
          <div className="sa-grid-2">
            <div className="sa-stack-sm">
              <Field label="Theme" help="Seasonal colours, a sticker and the AI-scene style for this one post — e.g. Black Friday.">
                <select value={themeKey} onChange={(event) => pickTheme(event.target.value)} disabled={!themes.length}>
                  <option value="">No theme — your brand colours</option>
                  {data.themeGroups.map((group) => (
                    <optgroup key={group.key} label={group.label}>
                      {group.themes.map((item) => <option key={item.key} value={item.key}>{item.emoji ? `${item.emoji} ` : ""}{item.label}</option>)}
                    </optgroup>
                  ))}
                </select>
              </Field>
              {data.themesError ? <span className="sa-error-text">Themes couldn&apos;t be loaded: {data.themesError}</span> : null}
              {theme ? (
                <div className="sa-row sa-small">
                  <ThemeSwatches colors={theme.colors} />
                  <span className="sa-muted">
                    {Object.keys(theme.colors).length ? "These colours replace your brand colours on the image." : "Keeps your brand colours."}
                  </span>
                </div>
              ) : null}
            </div>
            {theme ? (
              <Field label="Sticker on the image" help="Shown on templates that have a sticker badge.">
                <input type="text" name="text:sticker" value={sticker} maxLength={40} onChange={(event) => setSticker(event.target.value)} />
              </Field>
            ) : null}
          </div>

          <div className="sa-spread">
            <h4>Postiz creative tools</h4>
            <div className="sa-row">
              <LiveBadge live={live} />
              <div style={{ minWidth: 260 }}><PostizCredits budget={budget} compact /></div>
            </div>
          </div>
          {live === false && (usesImageCredit || usesVideoCredit) ? (
            <span className="sa-help">
              This copy of the app is read-only for Postiz: AI scenes and videos are only made on the live system, so this post will use a template instead (the post page says why).
            </span>
          ) : null}

          {contentType === "ai_image_ad" ? (
            <div className="sa-small">
              <strong>AI background scene</strong> — always on for AI image ads{theme ? `, in a ${theme.label} style` : ""}. Uses 1 Postiz AI image credit.
              <CreditNote budget={budget} kind="image" />
            </div>
          ) : null}
          {contentType === "ai_video" ? (
            <div className="sa-small">
              <strong>AI video</strong> — Postiz makes a short vertical video from your product photos. Uses 1 Postiz AI video credit and can take up to 25 minutes.
              <CreditNote budget={budget} kind="video" />
            </div>
          ) : null}
          {isVideo ? (
            <span className="sa-help">Postiz designs and AI background scenes are for image posts, so they aren&apos;t offered for videos.</span>
          ) : null}

          {backgroundAllowed ? (
            <Toggle
              checked={aiBackground} onChange={setAiBackground} label="AI background scene"
              help={(
                <>
                  Postiz paints a scene{theme ? ` in a ${theme.label} style` : ""} behind your real product photo. Uses 1 Postiz AI image credit.
                  <CreditNote budget={budget} kind="image" />
                </>
              )}
            />
          ) : designAllowed && design ? (
            <span className="sa-small sa-muted">AI background scene: not used — your Postiz design replaces the image.</span>
          ) : null}

          {designAllowed ? (
            <div className="sa-stack-sm">
              <strong>Use a Postiz design</strong>
              <PostizDesignPicker value={designSearch} onChange={setDesignSearch} />
              {design ? (
                <span className="sa-help">A matching design replaces the template image on every image channel (theme colours don&apos;t apply to it). If nothing matches, the template is used.</span>
              ) : null}
            </div>
          ) : null}
        </div>
      </s-section>

      <s-section heading="5. Channels">
        {data.channels.length ? (
          <div className="sa-stack-sm">
            <div className="sa-grid">
              {data.channels.map((channel) => {
                const ok = usable(channel);
                const videoOnly = channel.network?.video_only && !info.video;
                const reason = !channel.enabled ? "Turned off in Channels" : channel.disabled ? "Disconnected — reconnect it in Postiz"
                  : channel.missing_at ? "No longer in Postiz" : channel.needs_setup ? "Choose a Pinterest board first" : videoOnly ? "Only accepts videos — will be skipped" : "";
                return (
                  <Choice
                    key={channel.channel_id} dimmed={!ok} description={reason || undefined}
                    title={<ChannelChip provider={channel.provider} name={channel.name} picture={channel.picture} />}
                  >
                    <input
                      type="checkbox" name="channel_ids" value={channel.channel_id} disabled={!ok}
                      checked={ok && channelIds.includes(channel.channel_id)}
                      onChange={(event) => setChannelIds((current) => event.target.checked ? [...current, channel.channel_id] : current.filter((id) => id !== channel.channel_id))}
                    />
                  </Choice>
                );
              })}
            </div>
            {!channelIds.length ? <span className="sa-error-text">Choose at least one channel.</span> : null}
            {optionChannels.length ? (
              <div className="sa-stack-sm" style={{ marginTop: 6 }}>
                <h4>Network options for this post</h4>
                <span className="sa-help">“Channel default” uses what&apos;s saved for the channel on the Channels page.</span>
                <div className="sa-grid">
                  {optionChannels.map((channel) => (
                    <Card key={channel.channel_id}>
                      <div className="sa-stack-sm">
                        <ChannelChip provider={channel.provider} name={channel.name} picture={channel.picture} />
                        {CHANNEL_OPTIONS[channel.provider].map((option) => {
                          const name = `chset:${channel.channel_id}:${option.field}`;
                          const saved = String(channel.default_settings?.[option.field] ?? "");
                          const savedLabel = option.options.find(([value]) => value.toLowerCase() === saved.toLowerCase())?.[1] || option.options[0][1];
                          const effective = (channelOptions[name] || saved || option.options[0][0]).toLowerCase();
                          const storyProblem = option.field === "post_type" && effective === "story"
                            && channel.provider.startsWith("instagram") && contentType === "carousel";
                          return (
                            <Field
                              key={name} label={option.label} help={option.help}
                              error={storyProblem ? "A carousel has several images, so Instagram will reject it as a story — choose Feed post." : undefined}
                            >
                              <select name={name} value={channelOptions[name] || ""} onChange={(event) => setChannelOptions((current) => ({ ...current, [name]: event.target.value }))}>
                                <option value="">Channel default ({savedLabel})</option>
                                {option.options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                              </select>
                            </Field>
                          );
                        })}
                      </div>
                    </Card>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        ) : (
          <EmptyState title="No channels yet" action={<Link to="/app/social/channels" className="sa-btn sa-btn-primary">Go to Channels</Link>}>
            Connect your social accounts in Postiz, then sync them on the Channels page.
          </EmptyState>
        )}
      </s-section>

      <s-section heading="6. What should it say?">
        <div className="sa-stack">
          {info.texts.length ? (
            <div className="sa-grid-2">
              {info.texts.map((key) => {
                const field = TEXT_FIELDS[key] || { label: key };
                const themeLines = key === "promo_text" && theme?.promo_texts.length ? theme.promo_texts : null;
                return (
                  <Field key={`${contentType}-${key}`} label={field.label} help={themeLines ? `Click for ${theme?.label} suggestions, or write your own.` : undefined}>
                    {field.options ? (
                      <select name={`text:${key}`} defaultValue="">
                        <option value="">Let Gemma choose</option>
                        {field.options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                    ) : field.multiline ? (
                      <textarea name={`text:${key}`} rows={2} placeholder={field.placeholder} />
                    ) : (
                      <input
                        type="text" name={`text:${key}`} placeholder={themeLines ? themeLines[0] : field.placeholder}
                        list={themeLines ? "sa-theme-promo-texts" : undefined}
                      />
                    )}
                  </Field>
                );
              })}
            </div>
          ) : null}
          {theme?.promo_texts.length ? (
            <datalist id="sa-theme-promo-texts">
              {theme.promo_texts.map((text) => <option key={text} value={text} />)}
            </datalist>
          ) : null}
          <div className="sa-grid-2">
            <Field label="Brief for Gemma (optional)" help="Anything Gemma should know: the occasion, who it's for, what to stress.">
              <textarea name="brief" rows={3} maxLength={2000} placeholder="e.g. Perfect as a Christmas gift for runners" />
            </Field>
            <Field label="Angle (optional)" help="One line, e.g. “morning routine” or “ingredient spotlight”.">
              <input type="text" name="angle" maxLength={500} />
            </Field>
            <Field
              label="Link (optional)"
              help={`Where the post sends people. Leave blank for ${chosenProducts.length === 1 ? "the product page" : "your store's home page"}.${settings?.utm?.enabled ? " Tracking (UTM) tags are added automatically." : ""}`}
            >
              <input type="url" name="link_url" maxLength={1000} placeholder="https://…" />
            </Field>
          </div>
          <details>
            <summary>Caption options for this post (defaults come from Settings)</summary>
            <div className="sa-grid" style={{ marginTop: 8 }}>
              <Field label="Captions">
                <select name="cap:source" defaultValue=""><option value="">Store default</option>{CAPTION_SOURCES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
              </Field>
              <Field label="Tone">
                <select name="cap:tone" defaultValue=""><option value="">Store default ({settings?.captions.tone || "—"})</option>{TONES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
              </Field>
              <Field label="Language">
                <select name="cap:language" defaultValue=""><option value="">Store default ({settings?.captions.language || "—"})</option>{LANGUAGES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
              </Field>
              <Field label="Emojis">
                <select name="cap:emoji_level" defaultValue=""><option value="">Store default</option>{EMOJI_LEVELS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
              </Field>
              <Field label="Hashtags">
                <select name="cap:hashtags_count" defaultValue=""><option value="">Store default ({settings?.captions.hashtags_count ?? "—"})</option>{Array.from({ length: 11 }, (_, count) => <option key={count} value={count}>{count}</option>)}</select>
              </Field>
              <Field label="Mention the price">
                <select name="cap:include_price" defaultValue=""><option value="">Store default</option><option value="true">Yes</option><option value="false">No</option></select>
              </Field>
              <Field label="Call to action">
                <select name="cap:include_cta" defaultValue=""><option value="">Store default</option><option value="true">Yes</option><option value="false">No</option></select>
              </Field>
              <Field label="Links">
                <select name="cap:link_policy" value={linkPolicy} onChange={(event) => setLinkPolicy(event.target.value)}>
                  <option value="">Store default</option>{LINK_POLICIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </Field>
              {linkPolicy === "custom" ? (
                <Field label="Custom URL"><input type="url" name="cap:custom_url" placeholder="https://…" required /></Field>
              ) : null}
            </div>
          </details>
        </div>
      </s-section>

      <s-section heading="7. When">
        <div className="sa-stack">
          <div className="sa-grid">
            {([
              ["slot", "Next free slot", "The first time inside your posting window that respects each network's limits."],
              ["pick", "Pick a date and time", `In the store time zone, ${timezoneLabel(tz)}.`],
              ["now", "Post now", "Published as soon as it's approved."],
              ["draft", "Send to Postiz as a draft", "Finish and publish it yourself in Postiz."],
            ] as const).map(([value, label, help]) => (
              <Choice key={value} title={label} description={help}>
                <input type="radio" name="timing" value={value} checked={timing === value} onChange={() => setTiming(value)} />
              </Choice>
            ))}
          </div>
          {timing === "pick" || timing === "draft" ? (
            <div style={{ maxWidth: 280 }}>
              <Field label={timing === "draft" ? "Draft date (optional)" : "Date and time"} help={timezoneLabel(tz)}>
                <input type="datetime-local" name="scheduled_local" required={timing === "pick"} />
              </Field>
            </div>
          ) : null}
        </div>
      </s-section>

      <s-section heading="8. Approval">
        <div className="sa-grid">
          {APPROVAL_MODES.map((mode) => (
            <Choice key={mode.value} title={mode.label} description={mode.help}>
              <input type="radio" name="approval_mode" value={mode.value} defaultChecked={(settings?.default_approval_mode || "manual") === mode.value} />
            </Choice>
          ))}
        </div>
      </s-section>

      <s-section>
        <div className="sa-spread">
          <span className="sa-small sa-muted">
            {productProblem || (channelIds.length ? `Gemma writes a caption for each of the ${channelIds.length} channel(s); images render in each network's format.${creditText}` : "Choose at least one channel.")}
          </span>
          <button type="submit" className="sa-btn sa-btn-primary" disabled={busy || !canSubmit}>
            {isSubmitting("create") ? "Creating…" : "Create post"}
          </button>
        </div>
      </s-section>
    </Form>
  );
}
