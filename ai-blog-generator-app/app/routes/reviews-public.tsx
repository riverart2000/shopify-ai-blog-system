import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { reviewsBackendConfigured, reviewsBackendJson } from "../lib/reviews.server";

const PUBLIC_SHOP = (process.env.REVIEWS_PUBLIC_SHOP || process.env.WELLNESS_QUIZ_PUBLIC_SHOP || "xfxs8g-hn.myshopify.com").toLowerCase();
const ALLOWED_ORIGINS = new Set([
  "https://bioluxelab.com",
  "https://www.bioluxelab.com",
  `https://${PUBLIC_SHOP}`,
]);

function allowedOrigin(request: Request) {
  const origin = request.headers.get("origin");
  return origin && ALLOWED_ORIGINS.has(origin.toLowerCase()) ? origin : "";
}

function headers(request: Request) {
  const isRead = request.method === "GET" || request.method === "HEAD";
  return {
    "content-type": "application/json; charset=utf-8",
    "cache-control": isRead ? "public, max-age=60, s-maxage=300, stale-while-revalidate=86400" : "no-store",
    "access-control-allow-origin": allowedOrigin(request),
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "accept, content-type",
    vary: "Origin",
  };
}

function json(request: Request, body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: headers(request) });
}

function validate(request: Request) {
  const url = new URL(request.url);
  const shop = (url.searchParams.get("shop") || "").toLowerCase();
  const origin = request.headers.get("origin");
  if (shop !== PUBLIC_SHOP) return "Unknown shop.";
  if (origin && !allowedOrigin(request)) return "Storefront origin is not allowed.";
  return "";
}

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const invalid = validate(request);
  if (invalid) return json(request, { ok: false, error: invalid }, 403);
  if (!reviewsBackendConfigured()) return json(request, { ok: false, error: "Reviews service is not configured." }, 503);
  const input = new URL(request.url).searchParams;
  const query = new URLSearchParams({
    shop: PUBLIC_SHOP,
    review_type: input.get("review_type") || "product",
    product_handle: input.get("product_handle") || "",
    rating: input.get("rating") || "0",
    sort: input.get("sort") || "newest",
    page: input.get("page") || "1",
  });
  try {
    const result = await reviewsBackendJson(`/api/reviews/public?${query.toString()}`);
    return json(request, result);
  } catch (error) {
    return json(request, { ok: false, error: error instanceof Error ? error.message : "Reviews unavailable." }, 502);
  }
};

export const action = async ({ request }: ActionFunctionArgs) => {
  if (request.method === "OPTIONS") {
    if (!allowedOrigin(request)) return json(request, { ok: false }, 403);
    return new Response(null, { status: 204, headers: headers(request) });
  }
  const invalid = validate(request);
  if (invalid) return json(request, { ok: false, error: invalid }, 403);
  if (!reviewsBackendConfigured()) return json(request, { ok: false, error: "Reviews service is not configured." }, 503);
  try {
    const payload = await request.json() as Record<string, unknown>;
    const forwarded = request.headers.get("cf-connecting-ip") || request.headers.get("x-forwarded-for")?.split(",")[0] || "";
    const result = await reviewsBackendJson("/api/reviews/submit", {
      method: "POST",
      body: JSON.stringify({ ...payload, shop: PUBLIC_SHOP, client_ip: forwarded.trim() }),
    });
    return json(request, result);
  } catch (error) {
    return json(request, { ok: false, error: error instanceof Error ? error.message : "Review submission failed." }, 400);
  }
};
