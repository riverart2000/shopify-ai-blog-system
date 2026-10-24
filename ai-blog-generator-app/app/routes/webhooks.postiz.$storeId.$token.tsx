import type { ActionFunctionArgs } from "react-router";

import { seRequest, seg } from "../lib/social-engine.server";

/**
 * Public relay for Postiz "post published" webhooks → Python backend.
 * Postiz doesn't sign webhooks; the per-store token in the path is the secret and is
 * verified by the backend. The backend only enqueues a reconcile job, so this answers fast.
 */
const MAX_BODY_BYTES = 2_000_000;
const SAFE_STORE_ID = /^[A-Za-z0-9_.:@-]{1,200}$/;
const SAFE_TOKEN = /^[A-Za-z0-9_-]{8,200}$/;

async function readLimited(request: Request, limit: number): Promise<Uint8Array<ArrayBuffer> | null> {
  if (!request.body) return new Uint8Array(new ArrayBuffer(0));
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) {
      await reader.cancel().catch(() => undefined);
      return null;
    }
    chunks.push(value);
  }
  const body = new Uint8Array(new ArrayBuffer(size));
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

const json = (status: number, body: Record<string, unknown>) => Response.json(body, { status });

export const loader = () => json(405, { ok: false, detail: "Use POST." });

export const action = async ({ request, params }: ActionFunctionArgs) => {
  const storeId = params.storeId || "";
  const token = params.token || "";
  if (!SAFE_STORE_ID.test(storeId) || !SAFE_TOKEN.test(token)) {
    return json(404, { ok: false, detail: "Not found." });
  }
  if (Number(request.headers.get("content-length") || 0) > MAX_BODY_BYTES) {
    return json(413, { ok: false, detail: "Payload too large." });
  }
  const body = await readLimited(request, MAX_BODY_BYTES);
  if (!body) return json(413, { ok: false, detail: "Payload too large." });

  try {
    const upstream = await seRequest(`/api/social-engine/webhooks/postiz/${seg(storeId)}/${seg(token)}`, {
      method: "POST",
      headers: { "content-type": request.headers.get("content-type") || "application/json" },
      body,
      timeoutMs: 10_000,
    });
    const text = await upstream.text();
    return new Response(text || "{}", {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") || "application/json" },
    });
  } catch (error) {
    console.error("Postiz webhook relay failed:", error);
    return json(502, { ok: false, detail: "Social Autopilot backend unavailable." });
  }
};
