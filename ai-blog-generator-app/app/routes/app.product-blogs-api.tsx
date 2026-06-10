import type { ActionFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";

const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

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
      if (text) detail = text.slice(0, 200);
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
  const intent = String(form.get("intent") || "");

  if (intent === "generate") {
    try {
      const storeId = String(form.get("storeId") || "");
      const productTitle = String(form.get("productTitle") || "");
      const productHandle = String(form.get("productHandle") || "");
      const productUrl = String(form.get("productUrl") || "");

      const result = await backendFetch("/api/products/generate-blog", {
        method: "POST",
        body: JSON.stringify({
          store_id: storeId,
          product_title: productTitle,
          product_handle: productHandle,
          product_url: productUrl,
          blog_handle: "inside-the-products",
        }),
      });

      return Response.json({
        ok: true,
        productId: String(form.get("productId") || ""),
        status: result.status,
        articleUrl: result.article_url,
        articleId: result.article_id,
        title: result.title,
        message: result.message
      });
    } catch (e) {
      return Response.json({
        ok: false,
        productId: String(form.get("productId") || ""),
        error: e instanceof Error ? e.message : "Generation failed"
      });
    }
  }

  if (intent === "status") {
    try {
      const storeId = String(form.get("storeId") || "");
      const productHandle = String(form.get("productHandle") || "");

      const result = await backendFetch(`/api/products/generate-blog/status?store_id=${storeId}&product_handle=${productHandle}`);

      return Response.json({
        ok: true,
        status: result.status,
        articleUrl: result.article_url,
        articleId: result.article_id,
        title: result.title,
        error: result.error
      });
    } catch (e) {
      return Response.json({
        ok: false,
        error: e instanceof Error ? e.message : "Status check failed"
      });
    }
  }

  if (intent === "ensure-description") {
    try {
      const storeId = String(form.get("storeId") || "");
      const productTitle = String(form.get("productTitle") || "");
      const productHandle = String(form.get("productHandle") || "");
      const productUrl = String(form.get("productUrl") || "");
      const guideTitle = String(form.get("guideTitle") || "");
      const guideUrl = String(form.get("guideUrl") || "");

      const result = await backendFetch("/api/products/ensure-description", {
        method: "POST",
        body: JSON.stringify({
          store_id: storeId,
          product_title: productTitle,
          product_handle: productHandle,
          product_url: productUrl,
          guide_title: guideTitle,
          guide_url: guideUrl,
        }),
      });

      return Response.json({
        ok: true,
        productId: String(form.get("productId") || ""),
        message: result.message
      });
    } catch (e) {
      return Response.json({
        ok: false,
        productId: String(form.get("productId") || ""),
        error: e instanceof Error ? e.message : "Checking description failed"
      });
    }
  }

  return Response.json({ ok: false, error: "Unknown intent" });
};
