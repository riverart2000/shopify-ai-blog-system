import type { LoaderFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";

const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

export const loader = async ({ request, params }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  const filename = params.filename;
  
  if (!filename) {
    return new Response("Not found", { status: 404 });
  }

  const res = await fetch(`${BACKEND_URL}/api/landing-pages/images/${filename}`, {
    headers: {
      "x-api-key": BACKEND_KEY,
    },
  });

  if (!res.ok) {
    return new Response(`Backend Error: ${res.status}`, { status: res.status });
  }

  // Stream the response back to the client
  return new Response(res.body, {
    status: res.status,
    headers: {
      "Content-Type": res.headers.get("Content-Type") || "image/jpeg",
      "Cache-Control": "public, max-age=31536000",
    },
  });
};
