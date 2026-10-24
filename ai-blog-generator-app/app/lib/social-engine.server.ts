/**
 * Server-side client for the Social Engine API (Python backend, /api/social-engine/*).
 *
 * - `seFetch` calls the backend with the API key and turns its error envelope
 *   (`{detail, error:{type,code,message}}`, 400/404/429/502, or FastAPI 422 lists)
 *   into a `SocialEngineError` whose message is the exact `detail` text.
 * - `signedMediaUrl` / `signedTemplatePreviewUrl` build HMAC-signed URLs for the
 *   media proxy routes, because <img>/<video> requests inside the admin iframe do
 *   not carry the Shopify session token.
 * - `handlePostIntent` implements the post actions shared by the approval queue
 *   and the post detail page.
 */
import { createHmac, timingSafeEqual } from "node:crypto";

import type { ActionResult, PostSummary, Product } from "./social-engine.types";
import { zonedLocalToEpoch } from "./social-time";

export * from "./social-engine.types";

export const BACKEND_URL = (process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000").replace(/\/+$/, "");
export const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";
const API_PREFIX = "/api/social-engine";
const DEFAULT_TIMEOUT_MS = 30_000;

export class SocialEngineError extends Error {
  status: number;
  type: string;
  code: string;
  retryAt?: number;

  constructor(message: string, { status = 0, type = "", code = "", retryAt }: { status?: number; type?: string; code?: string; retryAt?: number } = {}) {
    super(message);
    this.name = "SocialEngineError";
    this.status = status;
    this.type = type;
    this.code = code;
    this.retryAt = retryAt;
  }
}

type QueryValue = string | number | boolean | null | undefined;

export type SeFetchOptions = {
  method?: "GET" | "POST";
  body?: unknown;
  query?: Record<string, QueryValue | QueryValue[]>;
  timeoutMs?: number;
};

function buildUrl(path: string, query?: SeFetchOptions["query"]): string {
  const fullPath = path.startsWith("/api/") ? path : `${API_PREFIX}${path.startsWith("/") ? path : `/${path}`}`;
  const url = new URL(`${BACKEND_URL}${fullPath}`);
  for (const [key, raw] of Object.entries(query || {})) {
    const values = Array.isArray(raw) ? raw : [raw];
    for (const value of values) {
      if (value === undefined || value === null || value === "") continue;
      url.searchParams.append(key, String(value));
    }
  }
  return url.toString();
}

/** Encode one URL path segment (ids come from the browser). */
export function seg(value: string): string {
  return encodeURIComponent(String(value || ""));
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

async function errorFromResponse(response: Response): Promise<SocialEngineError> {
  const text = await response.text().catch(() => "");
  let message = "";
  let type = "";
  let code = "";
  let retryAt: number | undefined;
  try {
    const body = JSON.parse(text) as { detail?: unknown; error?: { type?: string; code?: string; message?: string }; retry_at?: number };
    message = formatDetail(body.detail) || body.error?.message || "";
    type = body.error?.type || "";
    code = body.error?.code || "";
    retryAt = typeof body.retry_at === "number" ? body.retry_at : undefined;
  } catch {
    message = text.trim().slice(0, 500);
  }
  if (!message) message = `The Social Autopilot backend returned HTTP ${response.status}.`;
  return new SocialEngineError(message, { status: response.status, type, code, retryAt });
}

export function backendConfigured(): boolean {
  return Boolean(BACKEND_KEY);
}

/** Raw backend request (used by the media proxies and webhook relays). */
export async function seRequest(path: string, init: RequestInit & { query?: SeFetchOptions["query"]; timeoutMs?: number } = {}): Promise<Response> {
  const { query, timeoutMs, headers, ...rest } = init;
  return fetch(buildUrl(path, query), {
    ...rest,
    headers: { "x-api-key": BACKEND_KEY, ...(headers as Record<string, string> | undefined) },
    signal: AbortSignal.timeout(timeoutMs ?? DEFAULT_TIMEOUT_MS),
  });
}

export async function seFetch<T = Record<string, unknown>>(path: string, options: SeFetchOptions = {}): Promise<T> {
  if (!BACKEND_KEY) {
    throw new SocialEngineError(
      "The backend API key is not configured. Set AI_BLOG_BACKEND_API_KEY (or BLOG_GENERATOR_API_KEY) in the app's server .env and restart.",
      { type: "configuration" },
    );
  }
  const method = options.method || (options.body !== undefined ? "POST" : "GET");
  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers: {
        "x-api-key": BACKEND_KEY,
        accept: "application/json",
        ...(options.body !== undefined ? { "content-type": "application/json" } : {}),
      },
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: AbortSignal.timeout(options.timeoutMs ?? DEFAULT_TIMEOUT_MS),
    });
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    const timedOut = error instanceof Error && (error.name === "TimeoutError" || error.name === "AbortError");
    throw new SocialEngineError(
      timedOut
        ? `The Social Autopilot backend did not answer within ${Math.round((options.timeoutMs ?? DEFAULT_TIMEOUT_MS) / 1000)} seconds (${method} ${path}).`
        : `Could not reach the Social Autopilot backend at ${BACKEND_URL}: ${reason}`,
      { type: timedOut ? "timeout" : "network" },
    );
  }
  if (!response.ok) throw await errorFromResponse(response);
  if (response.status === 204) return {} as T;
  return (await response.json()) as T;
}

export function errorMessage(error: unknown, fallback = "Something went wrong."): string {
  if (error instanceof SocialEngineError) {
    if (error.retryAt) {
      const when = new Date(error.retryAt * 1000).toISOString().replace("T", " ").slice(0, 16);
      return `${error.message} (try again after ${when} UTC)`;
    }
    return error.message;
  }
  if (error instanceof Error) return error.message || fallback;
  return fallback;
}

/** Run an action intent and turn any error into `{ok:false, error}` with the backend's exact message. */
export async function runIntent<T = unknown>(
  intent: string,
  fn: () => Promise<{ message: string; data?: T } | ActionResult<T>>,
  target?: string,
): Promise<ActionResult<T>> {
  const tag = target ? { target } : {};
  try {
    const result = await fn();
    if ("ok" in result) return { ...tag, ...result };
    return { ok: true, intent, message: result.message, data: result.data, ...tag };
  } catch (error) {
    if (error instanceof Response) throw error;
    return { ok: false, intent, error: errorMessage(error, "The action failed."), ...tag };
  }
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

export function formJson<T>(form: FormData, key: string): T {
  const raw = formString(form, key);
  try {
    return JSON.parse(raw || "null") as T;
  } catch {
    throw new SocialEngineError(`The form field "${key}" is not valid JSON.`, { type: "validation" });
  }
}

export function formList(form: FormData, key: string): string[] {
  return form.getAll(key).map((value) => String(value).trim()).filter(Boolean);
}

/** Path ids from the browser: allow only a safe alphabet. */
export function safeId(value: string | null | undefined): string {
  const id = String(value || "").trim();
  if (!/^[A-Za-z0-9_.:-]{1,200}$/.test(id)) {
    throw new SocialEngineError("Invalid id.", { status: 400, type: "validation" });
  }
  return id;
}

// ------------------------------------------------------------------ signed media URLs

function signingSecret(): string {
  return process.env.SOCIAL_MEDIA_SIGNING_SECRET || BACKEND_KEY || process.env.SHOPIFY_API_SECRET || "";
}

const DAY = 86_400;

/** Expiry bucketed per day, so a page that polls keeps stable (cacheable) URLs. */
function expiry(): number {
  return (Math.floor(Date.now() / 1000 / DAY) + 2) * DAY;
}

function hmac(payload: string): string {
  const secret = signingSecret();
  if (!secret) return "";
  return createHmac("sha256", secret).update(payload).digest("hex");
}

const ASSET_ID_RE = /^[A-Za-z0-9_-]{1,128}$/;
const TEMPLATE_KEY_RE = /^[A-Za-z0-9_.:-]{1,128}$/;
const FORMAT_RE = /^[a-z0-9_-]{1,32}$/;

export function signedMediaUrl(assetId: string): string {
  if (!ASSET_ID_RE.test(assetId || "")) return "";
  const exp = expiry();
  const signature = hmac(`media:${assetId}:${exp}`);
  if (!signature) return "";
  return `/app/social/media/${encodeURIComponent(assetId)}?exp=${exp}&signature=${signature}`;
}

export function signedTemplatePreviewUrl(key: string, format: string): string {
  if (!TEMPLATE_KEY_RE.test(key || "") || !FORMAT_RE.test(format || "")) return "";
  const exp = expiry();
  const signature = hmac(`template:${key}:${format}:${exp}`);
  if (!signature) return "";
  return `/app/social/template-preview/${encodeURIComponent(key)}?format=${encodeURIComponent(format)}&exp=${exp}&signature=${signature}`;
}

function verify(payload: string, exp: string, signature: string): boolean {
  if (!/^[a-f0-9]{64}$/i.test(signature) || !/^\d{1,12}$/.test(exp)) return false;
  if (Number(exp) < Date.now() / 1000) return false;
  const expected = hmac(`${payload}:${exp}`);
  if (!expected) return false;
  return timingSafeEqual(Buffer.from(signature, "hex"), Buffer.from(expected, "hex"));
}

export function verifyMediaSignature(assetId: string, url: URL): boolean {
  return ASSET_ID_RE.test(assetId || "")
    && verify(`media:${assetId}`, url.searchParams.get("exp") || "", url.searchParams.get("signature") || "");
}

export function verifyTemplatePreviewSignature(key: string, format: string, url: URL): boolean {
  return TEMPLATE_KEY_RE.test(key || "") && FORMAT_RE.test(format || "")
    && verify(`template:${key}:${format}`, url.searchParams.get("exp") || "", url.searchParams.get("signature") || "");
}

/** Add signed proxy URLs to a post's preview and media. */
export function withSignedMedia<T extends PostSummary>(post: T): T {
  return {
    ...post,
    preview_url: post.preview_asset_id ? signedMediaUrl(post.preview_asset_id) : "",
    media: (post.media || []).map((media) => ({ ...media, url: signedMediaUrl(media.id) })),
  };
}

/**
 * Stream a backend media response back to the browser (keeps Range support for video).
 */
export async function proxyMedia(path: string, request: Request, query?: SeFetchOptions["query"]): Promise<Response> {
  const range = request.headers.get("range");
  let upstream: Response;
  try {
    upstream = await seRequest(path, { query, headers: range ? { range } : {}, timeoutMs: 120_000 });
  } catch (error) {
    return new Response(`Media backend unreachable: ${errorMessage(error)}`, { status: 502 });
  }
  if (!upstream.ok) {
    const failure = await errorFromResponse(upstream);
    return new Response(failure.message, { status: upstream.status, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  }
  const headers = new Headers({
    "Content-Type": upstream.headers.get("Content-Type") || "application/octet-stream",
    "Cache-Control": "private, max-age=3600",
    "Accept-Ranges": upstream.headers.get("Accept-Ranges") || "bytes",
    "X-Content-Type-Options": "nosniff",
  });
  for (const name of ["Content-Length", "Content-Range", "ETag", "Last-Modified"]) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers });
}

// ------------------------------------------------------------------ product lookup

/** Titles/images for product ids (posts only carry ids). One list call + a few detail calls. */
export async function lookupProducts(ids: string[]): Promise<Record<string, Pick<Product, "product_id" | "title" | "featured_image_url" | "url">>> {
  const wanted = Array.from(new Set(ids.filter(Boolean).map(String)));
  const found: Record<string, Pick<Product, "product_id" | "title" | "featured_image_url" | "url">> = {};
  if (!wanted.length) return found;
  try {
    const page = await seFetch<{ products: Product[] }>("/products", { query: { page_size: 200, order: "recently_posted" } });
    for (const product of page.products || []) {
      found[String(product.product_id)] = product;
    }
  } catch {
    // Labels are cosmetic; the posts still render with ids.
  }
  const missing = wanted.filter((id) => !found[id]).slice(0, 20);
  await Promise.all(missing.map(async (id) => {
    try {
      const detail = await seFetch<{ product: Product }>(`/products/${seg(id)}`);
      if (detail.product) found[id] = detail.product;
    } catch {
      // Not synced (or deleted) — shown as "Product #id".
    }
  }));
  return found;
}

// ------------------------------------------------------------------ shared post actions

export const POST_INTENTS = new Set([
  "approve", "reject", "regenerate", "publish_now", "retry", "cancel", "edit", "approve_bulk",
]);

function collectPrefixed(form: FormData, prefix: string): Record<string, string> | null {
  const out: Record<string, string> = {};
  let found = false;
  for (const [key, value] of form.entries()) {
    if (key.startsWith(prefix) && typeof value === "string") {
      out[key.slice(prefix.length)] = value;
      found = true;
    }
  }
  return found ? out : null;
}

/** Approve / reject / regenerate / publish now / retry / cancel / edit / bulk approve. */
export async function handlePostIntent(intent: string, form: FormData): Promise<ActionResult> {
  const target = intent === "approve_bulk" ? undefined : formString(form, "post_id") || undefined;
  return runIntent(intent, async () => {
    if (intent === "approve_bulk") {
      const postIds = formList(form, "post_ids");
      if (!postIds.length) throw new SocialEngineError("Select at least one post to approve.");
      const result = await seFetch<{ approved: string[]; errors: Record<string, string> }>("/posts/approve-bulk", {
        body: { post_ids: postIds },
      });
      const failures = Object.entries(result.errors || {});
      if (failures.length) {
        return {
          ok: result.approved.length > 0,
          intent,
          message: `Approved ${result.approved.length} post(s).`,
          error: `${failures.length} post(s) could not be approved: ${failures.map(([id, error]) => `${id.slice(0, 8)}… — ${error}`).join("; ")}`,
        };
      }
      return { message: `Approved ${result.approved.length} post(s). They will be sent to Postiz shortly.` };
    }

    const postId = safeId(formString(form, "post_id"));
    const base = `/posts/${seg(postId)}`;
    switch (intent) {
      case "approve":
        await seFetch(`${base}/approve`, { body: {} });
        return { message: "Approved. The post is being sent to Postiz for its scheduled time." };
      case "reject":
        await seFetch(`${base}/reject`, { body: { reason: formString(form, "reason") } });
        return { message: "Post rejected. It will not be published." };
      case "regenerate": {
        const brief = form.has("brief") ? formString(form, "brief") : null;
        const angle = form.has("angle") ? formString(form, "angle") : null;
        await seFetch(`${base}/regenerate`, {
          body: { ...(brief ? { brief } : {}), ...(angle ? { angle } : {}) },
        });
        return { message: "Regenerating the captions and images. This page updates when it is ready." };
      }
      case "publish_now":
        await seFetch(`${base}/publish-now`, { body: {} });
        return { message: "Publishing now. Check the channel versions below for the result." };
      case "retry":
        await seFetch(`${base}/retry`, { body: {} });
        return { message: "Retrying the failed channel versions." };
      case "cancel":
        await seFetch(`${base}/cancel`, { body: {} });
        return { message: "Post cancelled. Anything already scheduled in Postiz was deleted there too." };
      case "edit": {
        const captions = collectPrefixed(form, "caption:");
        const firstComments = collectPrefixed(form, "first_comment:");
        const titles = collectPrefixed(form, "title:");
        const removeChannelIds = formList(form, "remove_channel_ids");
        const local = formString(form, "scheduled_local");
        const original = formString(form, "scheduled_original");
        const tz = formString(form, "tz") || "UTC";
        let scheduledAt: number | undefined;
        if (local && local !== original) {
          const epoch = zonedLocalToEpoch(local, tz);
          if (!epoch) throw new SocialEngineError("The new time is not a valid date and time.");
          scheduledAt = epoch;
        }
        await seFetch(`${base}/edit`, {
          body: {
            ...(captions ? { captions } : {}),
            ...(firstComments ? { first_comments: firstComments } : {}),
            ...(titles ? { titles } : {}),
            ...(removeChannelIds.length ? { remove_channel_ids: removeChannelIds } : {}),
            ...(scheduledAt ? { scheduled_at: scheduledAt } : {}),
          },
        });
        return { message: "Changes saved." };
      }
      default:
        throw new SocialEngineError(`Unknown action "${intent}".`);
    }
  }, target);
}

