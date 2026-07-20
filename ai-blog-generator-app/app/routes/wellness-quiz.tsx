import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";
import { wellnessQuizBackendFetch, wellnessQuizBackendConfigured } from "../lib/wellness-quiz.server";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.public.appProxy(request);
  if (!wellnessQuizBackendConfigured()) {
    return jsonResponse({ ok: false, error: "Quiz service is not configured." }, 503);
  }
  const shop = new URL(request.url).searchParams.get("shop") || "";
  try {
    const result = await wellnessQuizBackendFetch(`/api/wellness-quiz/public?shop=${encodeURIComponent(shop)}`);
    return jsonResponse(result);
  } catch (error) {
    return jsonResponse({ ok: false, error: error instanceof Error ? error.message : "Quiz unavailable." }, 502);
  }
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.public.appProxy(request);
  if (!wellnessQuizBackendConfigured()) {
    return jsonResponse({ ok: false, error: "Quiz service is not configured." }, 503);
  }
  const shop = new URL(request.url).searchParams.get("shop") || "";
  try {
    const payload = await request.json() as Record<string, unknown>;
    const result = await wellnessQuizBackendFetch("/api/wellness-quiz/event", {
      method: "POST",
      body: JSON.stringify({ ...payload, shop }),
    });
    return jsonResponse(result);
  } catch (error) {
    return jsonResponse({ ok: false, error: error instanceof Error ? error.message : "Quiz event failed." }, 400);
  }
};

export default function WellnessQuizProxyRoute() {
  return null;
}
