/**
 * Merchant-facing vocabulary for Social Autopilot. Mirrors the backend's
 * services/social_engine/models.py and settings.py, in plain English.
 * Content-type labels and rule schemas come from the API at runtime; these
 * are fallbacks plus things the API does not describe (help text, tones).
 */

export type Tone = "success" | "critical" | "warning" | "attention" | "info" | "neutral" | "magic";

type StatusInfo = { label: string; tone: Tone; help?: string };

export const POST_STATUS: Record<string, StatusInfo> = {
  planned: { label: "Planned", tone: "info", help: "Waiting to be created" },
  generating: { label: "Creating…", tone: "info", help: "Gemma is writing captions and images are rendering" },
  pending_approval: { label: "Needs approval", tone: "attention" },
  approved: { label: "Approved", tone: "info", help: "Being sent to Postiz" },
  publishing: { label: "Sending to Postiz…", tone: "info" },
  scheduled: { label: "Scheduled", tone: "success" },
  published: { label: "Published", tone: "success" },
  partially_published: { label: "Partly published", tone: "warning", help: "Some channels failed" },
  failed: { label: "Failed", tone: "critical" },
  rejected: { label: "Rejected", tone: "neutral" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

export const TARGET_STATUS: Record<string, StatusInfo> = {
  pending: { label: "Not sent yet", tone: "neutral" },
  publishing: { label: "Sending…", tone: "info" },
  uncertain: { label: "Confirming with Postiz…", tone: "warning", help: "Sent, but Postiz has not confirmed yet" },
  draft: { label: "Draft in Postiz", tone: "info" },
  scheduled: { label: "Scheduled in Postiz", tone: "success" },
  published: { label: "Published", tone: "success" },
  failed: { label: "Failed", tone: "critical" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

export const JOB_STATUS: Record<string, StatusInfo> = {
  queued: { label: "Queued", tone: "info" },
  running: { label: "Running", tone: "attention" },
  succeeded: { label: "Done", tone: "success" },
  failed: { label: "Failed", tone: "critical" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

export const CHECK_STATUS: Record<string, StatusInfo> = {
  ok: { label: "OK", tone: "success" },
  warning: { label: "Needs attention", tone: "warning" },
  error: { label: "Problem", tone: "critical" },
  skipped: { label: "Not needed", tone: "neutral" },
  unknown: { label: "Not checked", tone: "neutral" },
};

export const PROMOTION_STATUS: Record<string, StatusInfo> = {
  running: { label: "Running", tone: "success" },
  upcoming: { label: "Upcoming", tone: "info" },
  paused: { label: "Stopped", tone: "neutral" },
  ended: { label: "Ended", tone: "neutral" },
  archived: { label: "Removed", tone: "neutral" },
  invalid: { label: "Needs fixing", tone: "critical", help: "Its settings no longer validate — open it and save again" },
};

/** Postiz tools recorded in a post's generation (`postiz_tools`). */
export const POSTIZ_TOOLS: Record<string, string> = {
  postiz_ai_image: "Postiz AI background scene",
  postiz_design: "Your Postiz design (media library)",
  postiz_ai_video: "Postiz AI video",
  tiktok_music: "Trending TikTok music (Postiz)",
};

/** Doctor check names → plain English. */
export const CHECK_LABELS: Record<string, string> = {
  database: "Database", worker: "Background worker", postiz: "Postiz", gemma: "Gemma (local AI)", ffmpeg: "Video tools (ffmpeg)",
  media_storage: "Media storage", shopify: "Shopify catalogue", channels: "Channels", daily_fill: "Daily slots filled",
  webhooks: "Postiz webhook", recent_problems: "Recent problems",
};

export const CAMPAIGN_STATUS: Record<string, StatusInfo> = {
  active: { label: "On", tone: "success" },
  paused: { label: "Paused", tone: "neutral" },
  archived: { label: "Archived", tone: "neutral" },
};

export const ACTIVITY_LEVEL: Record<string, StatusInfo> = {
  debug: { label: "Debug", tone: "neutral" },
  info: { label: "Info", tone: "info" },
  warning: { label: "Warning", tone: "warning" },
  error: { label: "Error", tone: "critical" },
};

export function statusInfo(map: Record<string, StatusInfo>, status: string): StatusInfo {
  return map[status] || { label: humanize(status || "unknown"), tone: "neutral" };
}

export const APPROVAL_MODES: Array<{ value: "manual" | "ai_review" | "auto"; label: string; help: string }> = [
  { value: "manual", label: "I approve every post", help: "Nothing goes out until you approve it in the queue." },
  { value: "ai_review", label: "Gemma reviews, I approve anything flagged", help: "Posts Gemma is happy with go out automatically." },
  { value: "auto", label: "Publish automatically", help: "Content guards (health claims, banned words) still hold risky posts for you." },
];

export function approvalLabel(mode: string): string {
  return APPROVAL_MODES.find((item) => item.value === mode)?.label || humanize(mode);
}

export const PUBLISH_MODE_LABEL: Record<string, string> = {
  schedule: "Scheduled in Postiz",
  now: "Publish immediately",
  draft: "Postiz draft",
};

/** Fallback labels (the API's `content_types` wins). */
export const CONTENT_TYPE_FALLBACK: Record<string, string> = {
  single_product: "Product post",
  new_arrival: "New arrival",
  sale_promo: "Sale / promotion",
  back_in_stock: "Back in stock",
  product_of_the_day: "Product of the day",
  collage: "Collage",
  collection_roundup: "Collection round-up",
  countdown: "Countdown",
  carousel: "Carousel",
  holiday: "Holiday / occasion",
  quote: "Quote",
  product_video: "Product video (reel)",
  ai_image_ad: "AI image ad",
  ai_video: "AI video (Postiz credits)",
  blog_article: "Blog article share",
};

export type ContentTypeInfo = {
  description: string;
  minProducts: number;
  maxProducts: number;
  texts: string[];
  video?: boolean;
  /** Uses Postiz AI credits. */
  credits?: "image" | "video";
};

export const CONTENT_TYPE_INFO: Record<string, ContentTypeInfo> = {
  single_product: { description: "One product with its photos and price.", minProducts: 1, maxProducts: 1, texts: [] },
  new_arrival: { description: "Announce a new product.", minProducts: 1, maxProducts: 1, texts: ["promo_text"] },
  sale_promo: { description: "A product on offer, with your promo line, discount and coupon.", minProducts: 1, maxProducts: 1, texts: ["promo_text", "discount_text", "coupon_code"] },
  back_in_stock: { description: "Tell followers a sold-out product is available again.", minProducts: 1, maxProducts: 1, texts: [] },
  product_of_the_day: { description: "Feature one product for today.", minProducts: 1, maxProducts: 1, texts: [] },
  collage: { description: "Several photos in one image — one product or up to four.", minProducts: 1, maxProducts: 4, texts: ["headline"] },
  collection_roundup: { description: "A round-up of products from one collection.", minProducts: 1, maxProducts: 4, texts: ["collection_title"] },
  countdown: { description: "\"Ends soon\" post for a sale.", minProducts: 1, maxProducts: 1, texts: ["promo_text", "countdown_label", "ends_at", "discount_text", "coupon_code"] },
  carousel: { description: "Swipeable multi-image post.", minProducts: 1, maxProducts: 4, texts: ["headline"] },
  holiday: { description: "A seasonal or occasion post (Christmas, Mother's Day…).", minProducts: 0, maxProducts: 1, texts: ["occasion", "greeting", "promo_text"] },
  quote: { description: "Brand content: a quote, fact, poll or quiz — no product needed.", minProducts: 0, maxProducts: 0, texts: ["brand_kind", "quote", "topic_hint"] },
  product_video: { description: "A short vertical slideshow video (reel / short / story).", minProducts: 1, maxProducts: 4, texts: ["promo_text"], video: true },
  ai_image_ad: { description: "Postiz AI scene behind your real product photo. Uses 1 Postiz AI image credit.", minProducts: 1, maxProducts: 1, texts: ["headline"], credits: "image" },
  ai_video: { description: "A video made by Postiz AI. Uses 1 of your monthly Postiz video credits (live system only).", minProducts: 1, maxProducts: 1, texts: [], video: true, credits: "video" },
  blog_article: { description: "Share one of your blog articles.", minProducts: 0, maxProducts: 0, texts: ["blog_title", "blog_excerpt", "article_url"] },
};

export function contentTypeInfo(type: string): ContentTypeInfo {
  return CONTENT_TYPE_INFO[type] || { description: "", minProducts: 0, maxProducts: 4, texts: [] };
}

export type TextFieldInfo = { label: string; placeholder?: string; multiline?: boolean; options?: Array<[string, string]> };

export const TEXT_FIELDS: Record<string, TextFieldInfo> = {
  promo_text: { label: "Promo line", placeholder: "Summer sale — this week only" },
  discount_text: { label: "Discount text", placeholder: "20% off" },
  coupon_code: { label: "Coupon code", placeholder: "SUMMER20" },
  countdown_label: { label: "Countdown label", placeholder: "2 days left" },
  ends_at: { label: "Ends", placeholder: "Sunday at midnight" },
  occasion: { label: "Occasion", placeholder: "Mother's Day" },
  greeting: { label: "Greeting", placeholder: "Happy Mother's Day!" },
  headline: { label: "Headline on the image", placeholder: "Leave blank to let Gemma write it" },
  collection_title: { label: "Collection name", placeholder: "Best sellers" },
  quote: { label: "Quote, fact or question", placeholder: "Leave blank to let Gemma write one", multiline: true },
  topic_hint: { label: "Topic", placeholder: "Sleep and recovery" },
  brand_kind: {
    label: "Kind of brand post",
    options: [["quote", "Quote"], ["fact", "Fact"], ["poll", "Poll"], ["quiz", "Quiz"], ["brand_awareness", "Brand awareness"], ["brand_value", "Brand value"]],
  },
  blog_title: { label: "Article title" },
  blog_excerpt: { label: "Article summary", multiline: true },
  article_url: { label: "Article URL", placeholder: "https://…" },
};

export const TONES: Array<[string, string]> = [
  ["friendly", "Friendly"], ["professional", "Professional"], ["persuasive", "Persuasive"], ["casual", "Casual"],
  ["funny", "Funny"], ["luxury", "Luxury"], ["expert", "Expert"],
];

export const EMOJI_LEVELS: Array<[string, string]> = [["off", "No emojis"], ["light", "A few (2–4)"], ["heavy", "Lots (5–8)"]];

export const LINK_POLICIES: Array<[string, string]> = [
  ["all", "Add the product link on every network"],
  ["all_except_instagram", "Every network except Instagram (link in bio)"],
  ["none", "No links"],
  ["custom", "Always link to a custom URL"],
];

/** How each network carries the product link (ProviderProfile.link_policy). */
export const LINK_MODES: Record<string, string> = {
  inline: "link in the caption",
  bio: "“link in bio” (links aren't clickable)",
  settings_link: "link attached to the pin",
  first_comment: "link in the first comment",
  none: "no links",
};

export const CAPTION_SOURCES: Array<[string, string]> = [["ai", "Written by Gemma (AI)"], ["template", "Caption templates (no AI)"]];

export const LANGUAGES: Array<[string, string]> = [
  ["en-GB", "English (UK)"], ["en-US", "English (US)"], ["fr-FR", "French"], ["de-DE", "German"], ["es-ES", "Spanish"],
  ["it-IT", "Italian"], ["nl-NL", "Dutch"], ["pt-PT", "Portuguese"], ["pt-BR", "Portuguese (Brazil)"], ["sv-SE", "Swedish"],
  ["da-DK", "Danish"], ["nb-NO", "Norwegian"], ["fi-FI", "Finnish"], ["pl-PL", "Polish"], ["ja-JP", "Japanese"],
  ["ko-KR", "Korean"], ["zh-CN", "Chinese (Simplified)"],
];

export const WEEKDAYS: Array<[number, string]> = [[0, "Mon"], [1, "Tue"], [2, "Wed"], [3, "Thu"], [4, "Fri"], [5, "Sat"], [6, "Sun"]];
export const WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export const PRODUCT_ORDERS: Record<string, string> = {
  latest: "Latest first",
  latest_then_random: "Latest first, then random",
  random: "Random",
  least_posted: "Least posted first",
  on_sale_first: "On-sale products first",
  ai: "Let Gemma choose (AI)",
};

/** Network capabilities the API does not expose per channel (from models.py PROVIDER_PROFILES). */
export const PROVIDERS: Record<string, { label: string; maxLength: number; supportsComments: boolean; titleMax: number }> = {
  instagram: { label: "Instagram", maxLength: 2200, supportsComments: true, titleMax: 0 },
  "instagram-standalone": { label: "Instagram", maxLength: 2200, supportsComments: true, titleMax: 0 },
  facebook: { label: "Facebook", maxLength: 63206, supportsComments: true, titleMax: 0 },
  x: { label: "X", maxLength: 280, supportsComments: true, titleMax: 0 },
  linkedin: { label: "LinkedIn", maxLength: 3000, supportsComments: true, titleMax: 0 },
  "linkedin-page": { label: "LinkedIn Page", maxLength: 3000, supportsComments: true, titleMax: 0 },
  threads: { label: "Threads", maxLength: 500, supportsComments: true, titleMax: 0 },
  bluesky: { label: "Bluesky", maxLength: 300, supportsComments: true, titleMax: 0 },
  mastodon: { label: "Mastodon", maxLength: 500, supportsComments: true, titleMax: 0 },
  pinterest: { label: "Pinterest", maxLength: 500, supportsComments: false, titleMax: 100 },
  tiktok: { label: "TikTok", maxLength: 2200, supportsComments: false, titleMax: 90 },
  youtube: { label: "YouTube Shorts", maxLength: 5000, supportsComments: false, titleMax: 100 },
  gmb: { label: "Google Business", maxLength: 1500, supportsComments: false, titleMax: 0 },
  telegram: { label: "Telegram", maxLength: 4096, supportsComments: true, titleMax: 0 },
  reddit: { label: "Reddit", maxLength: 40000, supportsComments: true, titleMax: 300 },
};

export function providerLabel(provider: string): string {
  return PROVIDERS[provider]?.label || humanize(provider || "network");
}

export const PROVIDER_COLORS: Record<string, string> = {
  instagram: "#c13584", "instagram-standalone": "#c13584", facebook: "#1877f2", x: "#111111", linkedin: "#0a66c2",
  "linkedin-page": "#0a66c2", threads: "#333333", bluesky: "#1185fe", mastodon: "#6364ff", pinterest: "#e60023",
  tiktok: "#000000", youtube: "#ff0000", gmb: "#4285f4", telegram: "#26a5e4", reddit: "#ff4500",
};

export const CAPTION_PLACEHOLDERS: Array<[string, string]> = [
  ["PRODUCT_TITLE", "Product name(s)"], ["PRODUCT_DESC", "Product description (shortened)"], ["PRODUCT_PRICE", "Price"],
  ["PRODUCT_TAGS", "Product tags as hashtags"], ["PRODUCT_URL", "Placed by the link policy"], ["STORE_URL", "Your store address"],
  ["STORE_NAME", "Business name"], ["TAG_CLOUD", "Random tags from your hashtag clouds"], ["PROMO_TEXT", "Promo line"],
  ["DISCOUNT_TEXT", "Discount text"], ["COUPON_CODE", "Coupon code"], ["OCCASION", "Occasion"], ["ENDS_AT", "Sale end"],
  ["QUOTE_TEXT", "Quote"], ["BLOG_TITLE", "Article title"], ["BLOG_EXCERPT", "Article summary"],
];

export function humanize(value: string): string {
  const text = String(value || "").replace(/[_-]+/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
}

export function contentTypeLabel(type: string, labels?: Record<string, string>): string {
  return labels?.[type] || CONTENT_TYPE_FALLBACK[type] || humanize(type);
}

export function formatMoney(value: number | null | undefined, currency: string): string {
  if (value === null || value === undefined) return "—";
  try {
    return new Intl.NumberFormat("en-GB", { style: "currency", currency: currency || "GBP" }).format(value);
  } catch {
    return `${value.toFixed(2)} ${currency}`;
  }
}

export function formatNumber(value: number | null | undefined): string {
  return Number(value || 0).toLocaleString("en-GB");
}

export function splitList(value: string): string[] {
  return value.split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
}
