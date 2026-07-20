import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { wellnessQuizBackendFetch, wellnessQuizBackendConfigured } from "../lib/wellness-quiz.server";

const PUBLIC_SHOP = (process.env.WELLNESS_QUIZ_PUBLIC_SHOP || "xfxs8g-hn.myshopify.com").toLowerCase();
const ALLOWED_ORIGINS = new Set([
  "https://bioluxelab.com",
  "https://www.bioluxelab.com",
  `https://${PUBLIC_SHOP}`,
]);

function allowedOrigin(request: Request) {
  const origin = request.headers.get("origin");
  return origin && ALLOWED_ORIGINS.has(origin.toLowerCase()) ? origin : "";
}

function responseHeaders(request: Request) {
  const origin = allowedOrigin(request);
  return {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "access-control-allow-origin": origin,
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "accept, content-type",
    vary: "Origin",
  };
}

function jsonResponse(request: Request, body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: responseHeaders(request),
  });
}

function validateStorefrontRequest(request: Request) {
  const origin = request.headers.get("origin");
  const shop = (new URL(request.url).searchParams.get("shop") || "").toLowerCase();
  if (shop !== PUBLIC_SHOP) return "Unknown shop.";
  if (origin && !allowedOrigin(request)) return "Storefront origin is not allowed.";
  return "";
}

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const invalid = validateStorefrontRequest(request);
  if (invalid) return jsonResponse(request, { ok: false, error: invalid }, 403);
  if (!wellnessQuizBackendConfigured()) {
    return jsonResponse(request, { ok: false, error: "Quiz service is not configured." }, 503);
  }
  try {
    const result = await wellnessQuizBackendFetch(`/api/wellness-quiz/public?shop=${encodeURIComponent(PUBLIC_SHOP)}`);
    return jsonResponse(request, result);
  } catch (error) {
    return jsonResponse(request, { ok: false, error: error instanceof Error ? error.message : "Quiz unavailable." }, 502);
  }
};

export const action = async ({ request }: ActionFunctionArgs) => {
  if (request.method === "OPTIONS") {
    if (!allowedOrigin(request)) return jsonResponse(request, { ok: false }, 403);
    return new Response(null, { status: 204, headers: responseHeaders(request) });
  }
  const invalid = validateStorefrontRequest(request);
  if (invalid) return jsonResponse(request, { ok: false, error: invalid }, 403);
  if (!wellnessQuizBackendConfigured()) {
    return jsonResponse(request, { ok: false, error: "Quiz service is not configured." }, 503);
  }
  try {
    const payload = await request.json() as Record<string, unknown>;
    const result = await wellnessQuizBackendFetch("/api/wellness-quiz/event", {
      method: "POST",
      body: JSON.stringify({ ...payload, shop: PUBLIC_SHOP }),
    });
    return jsonResponse(request, result);
  } catch (error) {
    return jsonResponse(request, { ok: false, error: error instanceof Error ? error.message : "Quiz event failed." }, 400);
  }
};
