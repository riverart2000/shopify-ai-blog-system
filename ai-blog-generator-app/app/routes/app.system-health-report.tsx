import type { ActionFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";

const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  if (!BACKEND_KEY) return Response.json({ ok: false }, { status: 503 });
  const payload = await request.json().catch(() => ({}));
  const response = await fetch(`${BACKEND_URL}/api/system-health/report`, {
    method: "POST",
    headers: { "x-api-key": BACKEND_KEY, "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  return Response.json({ ok: response.ok }, { status: response.ok ? 200 : response.status });
};
