import type { ActionFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";

const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

function safeJsonParse<T>(raw: string, fallback: T): T {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

async function backendFetch(path: string, opts: RequestInit = {}) {
  const res = await fetch(`${BACKEND_URL}${path}`, {
    ...opts,
    headers: {
      "x-api-key": BACKEND_KEY,
      "content-type": "application/json",
      ...(opts.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = `Backend ${res.status}`;
    try {
      const body = await res.json() as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      const text = await res.text().catch(() => "");
      if (text) detail = text.slice(0, 300);
    }
    throw new Error(detail);
  }
  return res.json() as Promise<Record<string, unknown>>;
}

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  if (!BACKEND_KEY) {
    return Response.json({ ok: false, error: "AI_BLOG_BACKEND_API_KEY is not configured." });
  }

  const form = await request.formData();
  const intent = String(form.get("intent") || "").trim();
  const storeId = String(form.get("storeId") || "").trim();

  if (intent === "workspaces") {
    try {
      const result = await backendFetch("/api/social/workspaces");
      return Response.json({ ok: true, ...result });
    } catch (e) {
      return Response.json({ ok: false, error: e instanceof Error ? e.message : "Failed to load workspaces" });
    }
  }

  if (intent === "accounts") {
    try {
      const workspaceId = String(form.get("workspaceId") || "").trim();
      const result = await backendFetch(`/api/social/accounts?workspace_id=${encodeURIComponent(workspaceId)}`);
      return Response.json({ ok: true, ...result });
    } catch (e) {
      return Response.json({ ok: false, error: e instanceof Error ? e.message : "Failed to load accounts" });
    }
  }

  if (intent === "generate") {
    try {
      const result = await backendFetch("/api/social/generate", {
        method: "POST",
        body: JSON.stringify({
          store_id: storeId,
          product_title: String(form.get("productTitle") || ""),
          product_handle: String(form.get("productHandle") || ""),
          product_url: String(form.get("productUrl") || ""),
          brief_text: String(form.get("briefText") || ""),
          offer_type: String(form.get("offerType") || "direct_offer"),
          model_id: String(form.get("modelId") || ""),
        }),
      });
      return Response.json({ ok: true, ...result });
    } catch (e) {
      return Response.json({ ok: false, error: e instanceof Error ? e.message : "Failed to generate social draft" });
    }
  }

  if (intent === "save-defaults") {
    try {
      const result = await backendFetch("/api/social/defaults/save", {
        method: "POST",
        body: JSON.stringify({
          store_id: storeId,
          default_workspace_id: String(form.get("workspaceId") || ""),
          default_account_ids: safeJsonParse<string[]>(String(form.get("accountIdsJson") || "[]"), []),
          default_providers: safeJsonParse<string[]>(String(form.get("providersJson") || "[]"), []),
          default_mode: String(form.get("mode") || "draft"),
        }),
      });
      return Response.json({ ok: true, ...result });
    } catch (e) {
      return Response.json({ ok: false, error: e instanceof Error ? e.message : "Failed to save defaults" });
    }
  }

  if (intent === "publish") {
    try {
      const result = await backendFetch("/api/social/publish", {
        method: "POST",
        body: JSON.stringify({
          store_id: storeId,
          workspace_id: String(form.get("workspaceId") || ""),
          campaign_name: String(form.get("campaignName") || ""),
          product_handle: String(form.get("productHandle") || ""),
          product_title: String(form.get("productTitle") || ""),
          product_url: String(form.get("productUrl") || ""),
          brief_text: String(form.get("briefText") || ""),
          base_text: String(form.get("baseText") || ""),
          provider_texts: safeJsonParse<Record<string, string>>(String(form.get("providerTextsJson") || "{}"), {}),
          image_urls: safeJsonParse<string[]>(String(form.get("imageUrlsJson") || "[]"), []),
          account_ids: safeJsonParse<string[]>(String(form.get("accountIdsJson") || "[]"), []),
          mode: String(form.get("mode") || "draft"),
          scheduled_at: String(form.get("scheduledAt") || ""),
        }),
      });
      return Response.json({ ok: true, ...result });
    } catch (e) {
      return Response.json({ ok: false, error: e instanceof Error ? e.message : "Failed to send to Publer" });
    }
  }

  if (intent === "job-status") {
    try {
      const workspaceId = String(form.get("workspaceId") || "").trim();
      const jobId = String(form.get("jobId") || "").trim();
      const result = await backendFetch(
        `/api/social/job-status?workspace_id=${encodeURIComponent(workspaceId)}&job_id=${encodeURIComponent(jobId)}`,
      );
      return Response.json({ ok: true, ...result });
    } catch (e) {
      return Response.json({ ok: false, error: e instanceof Error ? e.message : "Failed to fetch job status" });
    }
  }

  if (intent === "history") {
    try {
      const result = await backendFetch(`/api/social/history?store_id=${encodeURIComponent(storeId)}&limit=30`);
      return Response.json({ ok: true, ...result });
    } catch (e) {
      return Response.json({ ok: false, error: e instanceof Error ? e.message : "Failed to fetch history" });
    }
  }

  return Response.json({ ok: false, error: "Unknown intent" });
};
