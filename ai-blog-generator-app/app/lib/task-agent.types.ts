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
};

export type Step = {
  n: number;
  tool: string;
  args: string;
  result_summary: string;
  tokens: number | null;
  duration_ms: number | null;
  created_at: string;
};

export type TemplateField = { name: string; label: string; required: boolean; default: string | null };

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
};

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

export type AccountsResponse = { accounts: Account[]; email: EmailAccount };
export type AccountSaveResponse = { account: Account; message: string };
export type EmailSaveResponse = { email: EmailAccount; message: string };
export type EmailTestResponse = { ok: boolean; message: string; email: EmailAccount };
export type MessageResponse = { message: string };

/** Result of a page action, shown with ActionBanner. */
export type AgentActionResult = { ok: boolean; intent: string; message?: string; error?: string; target?: string };
