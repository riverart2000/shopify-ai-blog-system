/** Types for the Task Agent API (task-agent/API.md). Safe to import in browser code. */

export type JobStatus = "queued" | "running" | "waiting_approval" | "done" | "failed" | "cancelled";

export type JobSummary = {
  id: number;
  type: string;
  title: string;
  status: JobStatus;
  priority: number;
  attempts: number;
  run_at: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  template_key: string | null;
  created_by: string;
  cancel_requested: boolean;
  step_count: number;
  /** A promotion's per-place jobs point at the promotion's job (the list hides them). Older backends omit it. */
  parent_id?: number | null;
};

export type ResearchSource = { url: string; title: string; used_for: string };

export type ResearchResult = {
  answer: string;
  key_points: string[];
  sources: ResearchSource[];
  confidence: "high" | "medium" | "low";
  gaps: string[];
};

export type JobDetail = JobSummary & {
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  tools_allowed: string[];
  /** What the task was created with (for Clone). Null for tasks made before cloning existed. */
  template_values?: Record<string, unknown> | null;
  /** Only for `promote_event` jobs. */
  promotion?: Promotion | null;
  /** The event type a promotion was started with (its email, logins and Pages). */
  event_type?: { key: string; name: string } | null;
};

/** The event a promotion is for, read from its pages. Times are UK local, e.g. "2026-11-15T19:00". */
export type EventDetails = {
  title?: string | null;
  starts_at?: string | null;
  ends_at?: string | null;
  venue?: string | null;
  address?: string | null;
  town?: string | null;
  online?: boolean | null;
  online_url?: string | null;
  price?: string | null;
  ticket_url?: string | null;
  organiser?: string | null;
  description?: string | null;
  category?: string | null;
  image_url?: string | null;
};

export type PromotionTargetKind = "listing" | "group" | "forum" | "own_social";

export type PromotionTargetStatus =
  | "planned" | "queued" | "working" | "posted" | "already_listed" | "needs_you" | "skipped" | "failed";

/** What a site needs Joe to do (the page is open in the shared browser on the Mac). */
export type PromotionTargetNeed = { site?: string | null; url?: string | null; reason?: string | null };

/** One place to promote an event on. */
export type PromotionTarget = {
  id: number;
  kind: PromotionTargetKind | string;
  name: string;
  url?: string | null;
  why?: string | null;
  account?: string | null;
  source?: "found" | "reused" | string | null;
  status: PromotionTargetStatus | string;
  job_id?: number | null;
  scheduled_at?: string | null;
  result_url?: string | null;
  note?: string | null;
  need?: PromotionTargetNeed | null;
  updated_at?: string | null;
};

export type Promotion = {
  event?: EventDetails | null;
  /** The promotion this one was cloned from (its places were reused). */
  reused_from?: number | null;
  counts?: Partial<Record<PromotionTargetStatus, number>> | null;
  targets?: PromotionTarget[] | null;
};

export type TargetActionResponse = { target: PromotionTarget; message: string };

/**
 * The browser every agent shares. In "chrome" mode that's Joe's everyday Chrome, reached through the
 * OpenClaw extension (then `profile_saved` and `visible` are always true).
 */
export type BrowserStatus = {
  running: boolean;
  visible: boolean;
  open_tabs: number;
  login_tabs: string[];
  profile_saved: boolean;
  busy_job_id: number | null;
  mode?: "chrome";
  /** Why Chrome can't be used right now (null when it's fine). */
  offline?: string | null;
};

export type BrowserResponse = { browser: BrowserStatus; message?: string };

export type Step = {
  n: number;
  tool: string;
  args: string;
  result_summary: string;
  tokens: number | null;
  duration_ms: number | null;
  created_at: string;
};

export type TemplateField = {
  name: string;
  label: string;
  required: boolean;
  default: string | null;
  /** Show a textarea (lists of URLs, accounts, notes). Older backends omit it. */
  multiline?: boolean;
  /** Show a dropdown of these choices (e.g. the enabled event types). */
  options?: TemplateFieldOption[];
};

export type TemplateFieldOption = { value: string; label: string };

export type Template = {
  key: string;
  name: string;
  description: string;
  task_type: string;
  prompt: string;
  defaults: Record<string, unknown>;
  builtin: boolean;
  enabled: boolean;
  fields: TemplateField[];
  updated_at: string | null;
};

export type TaskType = { type: string; summary: string; prompt_field: string };

export type Account = {
  site: string;
  label: string;
  login_url: string;
  username: string;
  has_password: boolean;
  notes: string;
  has_profile: boolean;
  last_login_ok_at: string | null;
  login_window_open: boolean;
  /** The event type this login belongs to; "" = shared by every type (e.g. Facebook, LinkedIn). Older backends omit it. */
  event_type?: string;
  /** That type's name ("" when shared). */
  event_type_name?: string;
};

/** One kind of event Joe runs (Comedy, Biohacking, …), with its own email, logins and Pages. */
export type EventType = {
  key: string;
  name: string;
  email: string;
  imap_host: string;
  imap_port: number;
  imap_username: string;
  has_imap_password: boolean;
  facebook_page: string;
  linkedin_page: string;
  tag_handles: string;
  audience: string;
  enabled: boolean;
  /** Logins saved for this type (Eventbrite, Meetup, …). */
  logins: Account[];
  last_test_at: string | null;
  last_test_ok: boolean | null;
  last_test_message: string;
  updated_at: string | null;
};

/** Body of POST /api/event-types: no key = create, with key = update. */
export type EventTypeInput = {
  key?: string;
  name: string;
  email?: string;
  imap_host?: string;
  imap_port?: number;
  imap_username?: string;
  /** "" (or left out) keeps the saved one. */
  imap_password?: string;
  facebook_page?: string;
  linkedin_page?: string;
  tag_handles?: string;
  audience?: string;
  enabled?: boolean;
};

/** Telegram bot: token in the Keychain; only `owner_username` may use it (locked to their id on first /start). */
export type TelegramStatus = {
  configured: boolean;
  bot_username: string | null;
  owner_username: string;
  linked: boolean;
  linked_username: string | null;
  linked_at: string | null;
  summary_hour: number;
  social_connected: boolean;
  last_error: string | null;
};

export type TelegramResponse = { telegram: TelegramStatus; message?: string };

export type EmailAccount = {
  username: string;
  from_name: string;
  imap_host: string;
  imap_port: number;
  smtp_host: string;
  smtp_port: number;
  has_password: boolean;
  last_test_at: string | null;
  last_test_ok: boolean | null;
  last_test_message: string;
};

export type Approval = {
  id: number;
  job_id: number;
  job_title: string;
  action: string;
  preview: string;
  status: "pending" | "approved" | "rejected";
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;
};

export type WorkerState = "running" | "idle" | "paused" | "offline";

export type StatusResponse = {
  worker: { state: WorkerState; last_seen_at: string | null; current_job: { id: number; title: string } | null };
  model: string;
  thinking: boolean;
  counts: Partial<Record<JobStatus, number>>;
  pending_approvals: number;
  recent_jobs: JobSummary[];
};

export type JobsResponse = {
  jobs: JobSummary[];
  total: number;
  page: number;
  page_size: number;
  counts: Partial<Record<JobStatus, number>>;
};

export type JobResponse = { job: JobDetail; steps: Step[] };
export type JobActionResponse = { job: JobSummary; message: string };
export type TemplatesResponse = { templates: Template[]; task_types: TaskType[] };
export type PreviewResponse = { title: string; prompt: string; missing: string[] };
export type TemplateSaveResponse = { template: Template; message: string };
export type ApprovalsResponse = { approvals: Approval[]; counts: Record<string, number> };
export type ApprovalDecideResponse = { approval: Approval; message: string };

export type AgentSettings = {
  llm_model: string;
  llm_thinking: boolean;
  max_steps: number;
  max_minutes: number;
  max_attempts: number;
  paused: boolean;
};

export type SettingsResponse = {
  settings: AgentSettings;
  limits: { max_steps: [number, number]; max_minutes: [number, number]; max_attempts: [number, number] };
  models: string[];
  recommended_model: string;
};

export type AccountsResponse = { accounts: Account[]; email: EmailAccount; browser?: BrowserStatus | null };
export type EventTypesResponse = { event_types: EventType[]; shared_logins: Account[] };
export type EventTypeSaveResponse = { event_type: EventType; message: string };
export type EventTypeTestResponse = { ok: boolean; message: string; event_type: EventType };
export type AccountSaveResponse = { account: Account; message: string };
export type EmailSaveResponse = { email: EmailAccount; message: string };
export type EmailTestResponse = { ok: boolean; message: string; email: EmailAccount };
export type MessageResponse = { message: string };

/** Result of a page action, shown with ActionBanner. */
export type AgentActionResult = { ok: boolean; intent: string; message?: string; error?: string; target?: string };
