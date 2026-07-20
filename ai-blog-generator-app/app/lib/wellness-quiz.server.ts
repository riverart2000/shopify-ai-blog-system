const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

export function wellnessQuizBackendConfigured() {
  return Boolean(BACKEND_KEY);
}

export async function wellnessQuizBackendFetch(path: string, options: RequestInit = {}) {
  const response = await fetch(`${BACKEND_URL}${path}`, {
    ...options,
    headers: {
      "x-api-key": BACKEND_KEY,
      "content-type": "application/json",
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    let detail = `Backend ${response.status}`;
    try {
      const payload = await response.json() as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch {
      const text = await response.text().catch(() => "");
      if (text) detail = text.slice(0, 300);
    }
    throw new Error(detail);
  }
  return response.json() as Promise<Record<string, unknown>>;
}
