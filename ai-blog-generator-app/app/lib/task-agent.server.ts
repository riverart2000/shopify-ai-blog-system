/**
 * Server-side client for the Task Agent API (task-agent/, `task-agent worker` on this Mac).
 * Contract: task-agent/API.md. The browser never sees the key; only loaders/actions call this.
 */
import { seg } from "./task-agent.shared";
import type {
  AgentActionResult, EventTypeInput, EventTypeSaveResponse, EventTypeTestResponse, EventTypesResponse, JobActionResponse,
  MessageResponse,
} from "./task-agent.types";

export * from "./task-agent.types";
export { seg } from "./task-agent.shared";

export const TASK_AGENT_URL = (process.env.TASK_AGENT_URL || "http://127.0.0.1:4100").replace(/\/+$/, "");
const TASK_AGENT_KEY = process.env.TASK_AGENT_API_KEY || "";
const DEFAULT_TIMEOUT_MS = 20_000;

// ------------------------------------------------------------------ client

export class TaskAgentError extends Error {
  status: number;
  type: string;

  constructor(message: string, { status = 0, type = "" }: { status?: number; type?: string } = {}) {
    super(message);
    this.name = "TaskAgentError";
    this.status = status;
    this.type = type;
  }
}

type QueryValue = string | number | boolean | null | undefined;

export type TaFetchOptions = {
  method?: "GET" | "POST";
  body?: unknown;
  query?: Record<string, QueryValue | QueryValue[]>;
  timeoutMs?: number;
};

const OFFLINE_MESSAGE =
  "The Task Agent isn't running on the Mac, so this page can't reach it. Start it with " +
  "`launchctl kickstart -k gui/$(id -u)/com.joebains.task-agent` (or see task-agent/README.md), then reload.";

function buildUrl(path: string, query?: TaFetchOptions["query"]): string {
  const fullPath = path.startsWith("/api/") ? path : `/api${path.startsWith("/") ? path : `/${path}`}`;
  const url = new URL(`${TASK_AGENT_URL}${fullPath}`);
  for (const [key, raw] of Object.entries(query || {})) {
    const values = Array.isArray(raw) ? raw : [raw];
    for (const value of values) {
      if (value === undefined || value === null || value === "") continue;
      url.searchParams.append(key, String(value));
    }
  }
  return url.toString();
}

function formatDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI request validation (422): [{loc: ["body","name"], msg: "..."}]
    return detail
      .map((item) => {
        if (!item || typeof item !== "object") return String(item);
        const entry = item as { loc?: unknown[]; msg?: string };
        const loc = Array.isArray(entry.loc) ? entry.loc.filter((part) => part !== "body").join(".") : "";
        return loc ? `${loc}: ${entry.msg || "invalid"}` : entry.msg || "invalid";
      })
      .join("; ");
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return "";
}

async function errorFromResponse(response: Response): Promise<TaskAgentError> {
  const text = await response.text().catch(() => "");
  let message = "";
  try {
    message = formatDetail((JSON.parse(text) as { detail?: unknown }).detail);
  } catch {
    message = text.trim().slice(0, 500);
  }
  if (response.status === 401) {
    message = "The Task Agent rejected the app's key. TASK_AGENT_API_KEY must be the same in task-agent/.env and the app's .env.";
  } else if (response.status === 503 && !message) {
    message = "The Task Agent has no API key set. Add TASK_AGENT_API_KEY to task-agent/.env and restart it.";
  }
  if (!message) message = `The Task Agent returned HTTP ${response.status}.`;
  return new TaskAgentError(message, { status: response.status, type: "http" });
}

function isConnectionRefused(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  const cause = (error as Error & { cause?: { code?: string } }).cause;
  const code = cause?.code || "";
  return ["ECONNREFUSED", "ECONNRESET", "EHOSTUNREACH", "ENOTFOUND"].includes(code) || /ECONNREFUSED|fetch failed/i.test(error.message);
}

export function taskAgentConfigured(): boolean {
  return Boolean(TASK_AGENT_KEY);
}

export async function taFetch<T = Record<string, unknown>>(path: string, options: TaFetchOptions = {}): Promise<T> {
  if (!TASK_AGENT_KEY) {
    throw new TaskAgentError(
      "The Task Agent key isn't set up in the app. Add TASK_AGENT_API_KEY (the same value as in task-agent/.env) to the app's server .env and restart the app.",
      { type: "configuration" },
    );
  }
  const method = options.method || (options.body !== undefined ? "POST" : "GET");
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers: {
        "x-api-key": TASK_AGENT_KEY,
        accept: "application/json",
        ...(options.body !== undefined ? { "content-type": "application/json" } : {}),
      },
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    const timedOut = error instanceof Error && (error.name === "TimeoutError" || error.name === "AbortError");
    if (timedOut) {
      throw new TaskAgentError(`The Task Agent didn't answer within ${Math.round(timeoutMs / 1000)} seconds (${method} ${path}).`, { type: "timeout" });
    }
    if (isConnectionRefused(error)) throw new TaskAgentError(OFFLINE_MESSAGE, { type: "offline" });
    const reason = error instanceof Error ? error.message : String(error);
    throw new TaskAgentError(`Couldn't reach the Task Agent at ${TASK_AGENT_URL}: ${reason}`, { type: "network" });
  }
  if (!response.ok) throw await errorFromResponse(response);
  if (response.status === 204) return {} as T;
  return (await response.json()) as T;
}

export function errorMessage(error: unknown, fallback = "Something went wrong."): string {
  if (error instanceof Error) return error.message || fallback;
  return fallback;
}

export function isOffline(error: unknown): boolean {
  return error instanceof TaskAgentError && error.type === "offline";
}

/** Run an action intent and turn any error into `{ok:false, error}` with the Task Agent's exact message. */
export async function runAgentIntent(
  intent: string,
  fn: () => Promise<{ message: string } | AgentActionResult>,
  target?: string,
): Promise<AgentActionResult> {
  const tag = target ? { target } : {};
  try {
    const result = await fn();
    if ("ok" in result) return { ...tag, ...result };
    return { ok: true, intent, message: result.message, ...tag };
  } catch (error) {
    if (error instanceof Response) throw error;
    return { ok: false, intent, error: errorMessage(error, "That didn't work."), ...tag };
  }
}

// ------------------------------------------------------------------ event types

/** Every event type (with its logins) and the logins all types share (Facebook, LinkedIn). */
export function listEventTypes(): Promise<EventTypesResponse> {
  return taFetch<EventTypesResponse>("/event-types");
}

/** Create (no `key`) or update (with `key`) an event type. Leave `imap_password` out to keep the saved one. */
export function saveEventType(body: EventTypeInput): Promise<EventTypeSaveResponse> {
  return taFetch<EventTypeSaveResponse>("/event-types", { body });
}

/** Also removes that type's logins and their saved passwords. */
export function deleteEventType(key: string): Promise<MessageResponse> {
  return taFetch<MessageResponse>(`/event-types/${seg(key)}/delete`, { method: "POST" });
}

/** Logs in to the type's inbox (IMAP) and reads nothing. */
export function testEventTypeEmail(key: string): Promise<EventTypeTestResponse> {
  return taFetch<EventTypeTestResponse>(`/event-types/${seg(key)}/test-email`, { method: "POST", timeoutMs: 60_000 });
}

// ------------------------------------------------------------------ form helpers

export function formString(form: FormData, key: string): string {
  const value = form.get(key);
  return typeof value === "string" ? value.trim() : "";
}

export function formBool(form: FormData, key: string): boolean {
  const value = formString(form, key).toLowerCase();
  return value === "1" || value === "true" || value === "on" || value === "yes";
}

export function formInt(form: FormData, key: string, label: string): number {
  const raw = formString(form, key);
  const value = Number(raw);
  if (!raw || !Number.isFinite(value) || !Number.isInteger(value)) {
    throw new TaskAgentError(`${label} must be a whole number (got “${raw}”).`, { type: "validation" });
  }
  return value;
}

/** Cancel / retry one job: the shared action for the overview, task list and task pages. */
export async function handleJobIntent(intent: string, form: FormData): Promise<AgentActionResult> {
  const jobId = formString(form, "job_id");
  return runAgentIntent(intent, async () => {
    if (!jobId) throw new TaskAgentError("No task was given.", { type: "validation" });
    if (intent === "cancel" || intent === "retry") {
      const result = await taFetch<JobActionResponse>(`/jobs/${seg(jobId)}/${intent}`, { method: "POST" });
      return { message: result.message };
    }
    throw new TaskAgentError(`Unknown action “${intent}”.`, { type: "validation" });
  }, jobId);
}
