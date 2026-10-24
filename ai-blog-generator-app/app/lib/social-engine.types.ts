/**
 * Response shapes of the Social Engine API (ai-blog-generator-python-server/routes/social_engine/*.py).
 *
 * Client-safe: only types live here, so both loaders and components can import them.
 * Epoch values are seconds unless a field name says otherwise.
 */

export type JsonRecord = Record<string, unknown>;

export type PostStatus =
  | "planned" | "generating" | "pending_approval" | "approved" | "publishing" | "scheduled"
  | "published" | "partially_published" | "failed" | "rejected" | "cancelled";

export type TargetStatus =
  | "pending" | "publishing" | "uncertain" | "draft" | "scheduled" | "published" | "failed" | "cancelled";

export type ApprovalMode = "manual" | "ai_review" | "auto";
export type PublishMode = "schedule" | "now" | "draft";
export type CheckStatus = "ok" | "warning" | "error";

// ------------------------------------------------------------------ settings

export type BrandColors = {
  primary: string; text: string; background: string; cta: string; accent: string; secondary: string;
};

export type BrandKit = {
  business_name: string; description: string; what_you_sell: string; website: string; slogan: string;
  cta_text: string; colors: BrandColors; heading_font: string; body_font: string; use_store_logo: boolean;
  logo_position: "top_left" | "top_right" | "bottom_left" | "bottom_right" | "none";
  logo_opacity: number; currency_symbol: string; include_taxes_pct: number; show_price: boolean; music_path: string;
};

export type HashtagCloud = { name: string; tags: string[]; collection_ids: string[]; is_default: boolean };

export type CaptionSettings = {
  source: "ai" | "template";
  language: string;
  tone: "friendly" | "professional" | "persuasive" | "casual" | "funny" | "luxury" | "expert";
  include_price: boolean;
  hashtags_count: number;
  use_product_tags: boolean;
  emoji_level: "off" | "light" | "heavy";
  include_cta: boolean;
  link_policy: "all" | "all_except_instagram" | "none" | "custom";
  custom_url: string;
  link_in_first_comment: boolean;
  custom_instructions: string;
  product_category_hint: string;
  banned_terms: string[];
  hashtag_clouds: HashtagCloud[];
  templates: Record<string, string>;
};

export type ScheduleSettings = {
  timezone: string; days: number[]; window_start: string; window_end: string; randomize_times: boolean;
  use_best_times: boolean; min_gap_minutes: number; plan_days_ahead: number; daily_caps: Record<string, number>;
  posts_per_channel_per_day: number; always_fill_daily_slots: boolean;
};

export type PostizSettings = {
  base_url: string; api_key_env: string; create_post_hourly_budget: number; short_links: boolean;
  tag_posts: boolean; upload_adjust_video: boolean; customer_id: string; ai_video_monthly_budget: number;
  ai_image_monthly_budget: number; mcp_url: string; video_voice_id: string; ai_backgrounds_for_promotions: boolean;
  trending_tiktok_music: boolean; webhook_token_set: boolean; api_key_configured: boolean;
};

export type AISettings = {
  base_url: string; model: string; api_key_env: string; timeout_seconds: number; temperature: number;
  decision_temperature: number; max_tokens: number; fallback_model_id: string; ai_decisions: boolean;
  ai_review: boolean; vision_model: string; api_key_configured: boolean;
};

export type GuardSettings = { hold_health_claims: boolean; hold_low_review_score: number; max_caption_retries: number };
export type UtmSettings = { enabled: boolean; medium: string; source_overrides: Record<string, string> };
export type ContentSettings = {
  multi_image: boolean; rotate_images: boolean; allow_reposting: boolean; repost_after_days: number;
  excluded_product_ids: string[]; excluded_tags: string[];
};

export type EngineSettings = {
  enabled: boolean;
  publisher: "postiz" | "dry_run";
  default_approval_mode: ApprovalMode;
  brand: BrandKit;
  captions: CaptionSettings;
  schedule: ScheduleSettings;
  content: ContentSettings;
  postiz: PostizSettings;
  ai: AISettings;
  guards: GuardSettings;
  utm: UtmSettings;
};

/** A pydantic `model_json_schema()` (subset we render). */
export type JsonSchema = {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  const?: unknown;
  format?: string;
  pattern?: string;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  minLength?: number;
  maxLength?: number;
  minItems?: number;
  maxItems?: number;
  items?: JsonSchema;
  properties?: Record<string, JsonSchema>;
  additionalProperties?: JsonSchema | boolean;
  propertyNames?: JsonSchema;
  required?: string[];
  anyOf?: JsonSchema[];
  oneOf?: JsonSchema[];
  allOf?: JsonSchema[];
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
};

export type RuleInfo = {
  label: string;
  description: string;
  schema: JsonSchema;
  /** Every field's default (including list/dict defaults the JSON schema omits). */
  defaults: Record<string, unknown>;
  /** Content types the rule accepts (null = any). */
  allowed_content_types: string[] | null;
  /** Slot priority: higher outranks lower on a full day (promotions 10, SmartQ 0). */
  priority: number;
};

export type FallbackModel = { id: string; name: string; provider: string; model_name: string };

export type SettingsResponse = {
  store_id: string;
  settings: EngineSettings;
  content_types: Record<string, string>;
  formats: Record<string, [number, number]>;
  providers: Record<string, { label: string; max_length: number }>;
  rules: Record<string, RuleInfo>;
  fallback_models: FallbackModel[];
  has_logo: boolean;
};

// ------------------------------------------------------------------ health

export type HealthCheckName =
  | "database" | "worker" | "postiz" | "gemma" | "ffmpeg" | "media_storage" | "shopify" | "channels"
  | "daily_fill" | "webhooks" | "recent_problems";

export type HealthCheck = { name: HealthCheckName | string; status: CheckStatus | "skipped"; message: string; details: JsonRecord };

export type DoctorReport = {
  store_id: string; status: CheckStatus; checked_at: number; live: boolean; engine_enabled: boolean;
  publisher: string; checks: HealthCheck[];
};

export type WorkerHealth = {
  alive: boolean;
  workers: Array<JsonRecord & { last_seen: number; age_seconds: number }>;
};

// ------------------------------------------------------------------ channels

export type ChannelProfile = {
  label: string; max_length: number; media: string; video_only: boolean; link_policy: string;
  image_formats: string[]; video_format: string; has_post_analytics: boolean; title_max: number;
};

export type Channel = {
  store_id: string;
  channel_id: string;
  provider: string;
  name: string;
  picture: string;
  disabled: boolean;
  customer_id: string;
  customer_name: string;
  enabled: boolean;
  max_length: number | null;
  rules: string;
  default_settings: JsonRecord;
  cadence: { max_per_day?: number; min_gap_minutes?: number };
  missing_at: number | null;
  last_synced_at: number;
  /** The account handle from Postiz. */
  profile: string;
  /** What the network accepts (models.py PROVIDER_PROFILES). */
  network: ChannelProfile;
  needs_setup: boolean;
};

export type ChannelSync = { count?: number; errors?: string[]; synced_at?: number | null };

export type ChannelsResponse = { channels: Channel[]; last_sync: ChannelSync | null };

export type ChannelTool = { methodName?: string; description?: string; dataSchema?: JsonRecord[] };

// ------------------------------------------------------------------ products

export type ProductImage = { url: string; alt?: string; width?: number; height?: number };
export type ProductCollectionRef = { id: string; handle: string; title: string };

export type Product = {
  product_id: string;
  handle: string;
  title: string;
  product_type: string;
  vendor: string;
  tags: string[];
  status: string;
  url: string;
  featured_image_url: string;
  images: ProductImage[];
  variants: number | unknown[];
  collections: ProductCollectionRef[];
  price_min: number;
  price_max: number;
  compare_at_max: number | null;
  currency: string;
  on_sale: boolean;
  discount_pct: number;
  total_inventory: number | null;
  available: boolean;
  last_posted_at: number | null;
  post_count: number;
  excluded?: boolean;
  description?: string;
  synced_at?: number;
  deleted_at?: number | null;
};

export type ProductCounts = {
  total?: number; available?: number; on_sale?: number; never_posted?: number; last_synced_at?: number;
};

export type Collection = { id: string; title: string; products_count: number };

export type ProductsResponse = {
  products: Product[]; page: number; page_size: number; counts: ProductCounts; collections: Collection[];
};

export type ProductEvent = {
  id: number; product_id: string; event_type: string; details: JsonRecord; source: string; detected_at: number;
};

export type ProductDetailResponse = { product: Product; posts: PostSummary[]; events: ProductEvent[] };

// ------------------------------------------------------------------ posts

export type MediaRef = {
  id: string; kind: string; format: string; role: string; width: number; height: number; duration_ms: number;
  /** Added by the embedded app: signed proxy URL. */
  url?: string;
};

export type PostTarget = {
  id: string;
  post_id: string;
  store_id: string;
  channel_id: string;
  provider: string;
  caption: string;
  first_comment: string;
  title: string;
  settings: JsonRecord;
  media_ids: string[];
  format: string;
  link_url: string;
  scheduled_at: number | null;
  status: TargetStatus;
  external_id: string;
  external_group: string;
  release_url: string;
  error_type: string;
  error_message: string;
  published_at: number | null;
  last_synced_at: number | null;
  created_at: number;
  updated_at: number;
  channel_name: string;
  channel_picture: string;
};

export type PostProduct = { product_id: string; title: string; image: string; url: string; available: boolean };

export type GuardIssue = { code: string; message: string; severity: "info" | "warning" | "block" | string; channel_id: string };

export type PostReview = {
  guard?: { hold: boolean; issues: GuardIssue[] };
  ai_review?: {
    score?: number; approve?: boolean; issues?: string[]; suggestions?: string[]; model?: string; error?: string;
  };
};

export type PostSummary = {
  id: string;
  store_id: string;
  campaign_id: string;
  rule_type: string;
  content_type: string;
  template_key: string;
  product_ids: string[];
  title: string;
  angle: string;
  brief: string;
  base_caption: string;
  hashtags: string[];
  alt_text: string;
  link_url: string;
  options: { channel_ids?: string[]; texts?: Record<string, string>; decided_by?: string; decision_reason?: string };
  status: PostStatus;
  approval_mode: ApprovalMode;
  publish_mode: PublishMode;
  scheduled_at: number | null;
  dedupe_key: string;
  external_group: string;
  error_type: string;
  error_message: string;
  review: PostReview;
  approved_at: number | null;
  approved_by: string;
  rejected_reason: string;
  published_at: number | null;
  created_at: number;
  updated_at: number;
  warnings: string[];
  model: string;
  preview_asset_id: string;
  media: MediaRef[];
  targets: PostTarget[];
  products: PostProduct[];
  /** Postiz tools used to make this post, e.g. postiz_ai_image, postiz_design, postiz_ai_video, tiktok_music. */
  postiz_tools: string[];
  /** Added by the embedded app: signed proxy URL of `preview_asset_id`. */
  preview_url?: string;
};

export type PostsResponse = { posts: PostSummary[]; counts: Record<string, number>; page: number; page_size: number };

export type ActivityItem = {
  id: number; created_at: number; store_id: string; entity_type: string; entity_id: string;
  level: "debug" | "info" | "warning" | "error" | string; stage: string; message: string; data: JsonRecord;
  correlation_id: string;
};

export type Job = {
  id: string; store_id: string; type: string; payload: JsonRecord; status: string; priority: number;
  run_at: number; attempts: number; max_attempts: number; lease_owner: string; lease_expires_at: number | null;
  last_error_type: string; last_error: string; result: JsonRecord; dedupe_key: string; correlation_id: string;
  created_at: number; started_at: number | null; finished_at: number | null; updated_at: number;
};

export type ApiCall = {
  id: number; created_at: number; store_id: string; service: string; operation: string; method: string;
  url_path: string; status_code: number | null; ok: number | boolean; duration_ms: number; attempt: number;
  error: string; correlation_id: string; request_excerpt: string; response_excerpt: string;
};

export type ApiCallStat = {
  service: string; operation: string; calls: number; ok_calls: number; failed_calls: number; avg_ms: number;
  last_at: number;
};

export type Decision = {
  id: number; created_at: number; store_id: string; decision_type: string; subject_id: string; model: string;
  valid: number | boolean; used_fallback: number | boolean; duration_ms: number; input: unknown; output: unknown;
  error: string;
};

export type MetricsRow = {
  id: number; target_id: string; post_id: string; channel_id: string; provider: string; external_id: string;
  period_days: number; impressions: number; reach: number; views: number; likes: number; comments: number;
  shares: number; saves: number; clicks: number; engagement: number; fetched_at: number;
};

export type PostGeneration = {
  caption_source?: string; model?: string; used_fallback?: boolean; warnings?: string[]; template_key?: string;
  postiz_tools?: string[];
  /** Content type actually rendered (an AI image ad renders as a product/sale post; an AI video may fall back to a product video). */
  rendered_as?: string;
  formats?: string[]; media?: Array<{ format: string; kind: string; role: string; source: string }>;
  hook?: string; duration_ms?: number; generated_at?: number;
};

export type PostDetailResponse = {
  post: PostSummary;
  generation: PostGeneration;
  review: PostReview;
  products: Array<{ product_id: string; title: string; url: string; price_min: number; featured_image_url: string }>;
  timeline: ActivityItem[];
  jobs: Job[];
  api_calls: ApiCall[];
  decisions: Decision[];
  metrics: MetricsRow[];
};

/** Rows from `list_targets` (overview "upcoming"). */
export type UpcomingTarget = PostTarget & {
  content_type: string; post_title: string; post_status: PostStatus; campaign_id: string; product_ids: string[];
  template_key: string;
};

export type CalendarItem = {
  target_id: string; post_id: string; channel_id: string; channel_name: string; provider: string;
  status: TargetStatus; post_status: PostStatus; scheduled_at: number | null; content_type: string; title: string;
  release_url: string; error: string;
};

export type CalendarResponse = { start: number; end: number; items: CalendarItem[] };

// ------------------------------------------------------------------ campaigns / templates

export type ProductFilter = {
  collection_ids?: string[]; product_ids?: string[]; include_tags?: string[]; exclude_tags?: string[];
  exclude_product_ids?: string[]; in_stock_only?: boolean; on_sale_only?: boolean;
};

export type CampaignSchedule = {
  days?: number[] | null; window_start?: string | null; window_end?: string | null; randomize?: boolean | null;
  use_best_times?: boolean | null;
};

export type Campaign = {
  id: string;
  store_id: string;
  name: string;
  rule_type: string;
  status: "active" | "paused" | "archived";
  config: JsonRecord & { schedule?: CampaignSchedule };
  product_filter: ProductFilter;
  channel_ids: string[];
  content: JsonRecord;
  caption: Partial<CaptionSettings>;
  approval_mode: ApprovalMode;
  last_evaluated_at: number | null;
  next_evaluation_at: number | null;
  last_error: string;
  created_at: number;
  updated_at: number;
  state: JsonRecord;
  post_counts: Record<string, number>;
};

export type CampaignsResponse = { campaigns: Campaign[]; rules: Record<string, RuleInfo> };

export type PlanItem = {
  dedupe_key: string; content_type: string; product_ids: string[]; channel_ids: string[];
  channel_times: Record<string, number>; template_key: string; title: string; angle: string; brief: string;
  texts: Record<string, string>; options: JsonRecord; scheduled_at: number | null;
};

export type CampaignPreview = {
  campaign_id: string; planned: number; items?: PlanItem[]; note?: string; duration_ms?: number; error?: string;
};

export type Template = {
  id: string; store_id: string; key: string; name: string; content_type: string; kind: string; formats: string[];
  caption_template: string; is_builtin: boolean; is_active: boolean; created_at: number; updated_at: number;
};

export type TemplatesResponse = { templates: Template[] };

// ------------------------------------------------------------------ overview

export type CampaignSummary = Pick<
  Campaign, "id" | "name" | "rule_type" | "status" | "last_evaluated_at" | "next_evaluation_at" | "last_error"
>;

export type DailyFillChannel = { channel_id: string; name: string; provider: string; planned: number; target: number; full: boolean };
export type DailyFillDay = { date: string; window_open: boolean; filled: number; slots: number; channels: DailyFillChannel[] };

export type OverviewResponse = {
  store_id: string;
  /** Jobs running or due now (excludes jobs scheduled for later). */
  active_jobs: number;
  /** Today + the next two days. */
  daily_fill: DailyFillDay[];
  settings: EngineSettings;
  health: DoctorReport;
  worker: WorkerHealth;
  posts: Record<string, number>;
  products: ProductCounts;
  channels: Channel[];
  campaigns: CampaignSummary[];
  jobs: Record<string, number>;
  upcoming: UpcomingTarget[];
  activity: ActivityItem[];
};

// ------------------------------------------------------------------ analytics

export type RollupRow = {
  dimension: string | number | null; targets: number; impressions: number; reach: number; views: number;
  likes: number; comments: number; shares: number; saves: number; clicks: number; engagement: number;
  engagement_rate: number; label?: string; handle?: string;
};

export type TopPost = {
  post_id: string; title: string; content_type: string; template_key: string; campaign_id: string;
  product_ids: string[]; published_at: number | null; impressions: number; reach: number; views: number;
  likes: number; comments: number; shares: number; saves: number; clicks: number; engagement: number;
  engagement_rate: number;
};

export type PerformanceReport = {
  days: number; generated_at: number; product: RollupRow[]; template: RollupRow[]; channel: RollupRow[];
  content_type: RollupRow[]; campaign: RollupRow[]; hour: RollupRow[]; top_posts: TopPost[];
  totals: Record<string, number>;
};

export type Insights = {
  summary?: string; recommendations?: string[]; content_type_weights?: Record<string, number>;
  best_hours?: number[]; generated_at?: number;
};

export type AnalyticsResponse = {
  report: PerformanceReport;
  learned: { content_type_rates: Record<string, number>; best_hours: number[] };
  insights: Insights | null;
};

// ------------------------------------------------------------------ debugging

export type JobsResponse = {
  jobs: Job[];
  counts: Record<string, number>;
  types: Record<string, { lane: string; max_attempts: number; description: string }>;
  periodic: Array<{ task: string; store_id: string; last_enqueued_at: unknown }>;
};

export type JobDetailResponse = { job: Job; timeline: ActivityItem[]; api_calls: ApiCall[] };

export type WebhookEvent = {
  id: number; store_id: string; source: string; topic: string; dedupe_key: string; status: string; error: string;
  received_at: number; processed_at: number | null;
};

/** What every embedded-app action returns. */
export type ActionResult<T = unknown> = {
  ok: boolean;
  intent: string;
  message?: string;
  error?: string;
  data?: T;
  /** Id of the post / campaign / channel the action was about (lets a page show the result on that card). */
  target?: string;
};

// ------------------------------------------------------------------ promotions (Outfy PromoQ)

export type PromotionCategory = "occasion" | "offer" | "new_arrivals" | "custom";

export type PromotionTheme = {
  key: string; label: string; category: PromotionCategory | string; occasion_key: string; emoji: string;
  promo_texts: string[]; palette: string; colors: Partial<BrandColors>; sticker: string; lead_days: number;
  duration_days: number; content_types: string[]; background_prompt: string; description: string;
};

export type OccasionDate = {
  theme_key: string; label: string; emoji: string; date: string; days_until: number; suggested_start: string;
  suggested_duration: number; sticker: string;
};

export type PromotionConfig = {
  theme_key: string; theme: string; scope: "store" | "collections" | "products"; collection_ids: string[];
  product_ids: string[]; coupon_code: string; discount_text: string; promo_texts: string[]; start_date: string;
  duration_days: number; posts_per_day: number; content_types: string[]; countdown_days: number; landing_url: string;
  caption_mode: "ai" | "custom"; custom_caption: string; show_logo: boolean; ai_background: boolean | null;
  postiz_media_search: string; hero_ai_video: boolean;
  schedule?: CampaignSchedule;
};

export type PromotionRow = {
  id: string; name: string; status: "running" | "upcoming" | "paused" | "ended" | "archived" | "invalid" | string;
  campaign_status: string; theme_key: string; theme_label: string; emoji: string; category: string;
  start_date: string; end_date: string; coupon_code: string; posts_per_day: number; channel_ids: string[];
  approval_mode: ApprovalMode; post_counts: Record<string, number>; config: Partial<PromotionConfig>;
  product_filter: ProductFilter; last_error: string;
};

export type PostizBudget = { images_used?: number; images_budget?: number; videos_used?: number; videos_budget?: number; error?: string };

export type PromotionsResponse = {
  today: string;
  categories: Record<string, string>;
  themes: Record<string, PromotionTheme[]>;
  calendar: OccasionDate[];
  promotions: PromotionRow[];
  defaults: Partial<PromotionConfig>;
  postiz_budget: PostizBudget;
};

// ------------------------------------------------------------------ Postiz creative tools

export type PostizVoice = { id: string; name: string; preview_url?: string | null };

export type PostizToolsResponse = {
  live_publishing: boolean;
  budget: PostizBudget;
  video_generators: JsonRecord[];
  voices: PostizVoice[];
  settings: Partial<PostizSettings>;
  ui_only_features: Array<{ name: string; how: string }>;
};

export type PostizMediaItem = { id?: string; name?: string; originalName?: string; path?: string; thumbnail?: string; createdAt?: string };

export type PostizMediaResponse = { pages: number; items: PostizMediaItem[] };
