const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

export function reviewsBackendConfigured() {
  return Boolean(BACKEND_KEY);
}

export async function reviewsBackendFetch(path: string, options: RequestInit = {}) {
  const response = await fetch(`${BACKEND_URL}${path}`, {
    ...options,
    headers: {
      "x-api-key": BACKEND_KEY,
      "content-type": "application/json",
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    let detail = `Reviews backend ${response.status}`;
    try {
      const payload = await response.json() as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch {
      const text = await response.text().catch(() => "");
      if (text) detail = text.slice(0, 500);
    }
    throw new Error(detail);
  }
  return response;
}

export async function reviewsBackendJson(path: string, options: RequestInit = {}) {
  const response = await reviewsBackendFetch(path, options);
  return response.json() as Promise<Record<string, unknown>>;
}
