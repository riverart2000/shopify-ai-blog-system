import type { LoaderFunctionArgs } from "react-router";
import { verifyLandingPageImageSignature } from "../lib/landing-page-images.server";

const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

export const loader = async ({ request, params }: LoaderFunctionArgs) => {
  const filename = params.filename;
  if (!filename) return new Response("Not found", { status: 404 });

  const signature = new URL(request.url).searchParams.get("signature") || "";
  if (!verifyLandingPageImageSignature("video", filename, signature)) {
    return new Response("Forbidden", { status: 403 });
  }

  const range = request.headers.get("range");
  const res = await fetch(`${BACKEND_URL}/api/landing-pages/social/videos/${filename}`, {
    headers: {
      "x-api-key": BACKEND_KEY,
      ...(range ? { range } : {}),
    },
  });
  if (!res.ok) {
    return new Response(`Backend Error: ${res.status}`, { status: res.status });
  }

  const headers = new Headers({
    "Content-Type": res.headers.get("Content-Type") || "video/mp4",
    "Cache-Control": "private, no-store",
    "Accept-Ranges": res.headers.get("Accept-Ranges") || "bytes",
  });
  for (const name of ["Content-Length", "Content-Range", "Content-Disposition"]) {
    const value = res.headers.get(name);
    if (value) headers.set(name, value);
  }
  return new Response(res.body, { status: res.status, headers });
};
