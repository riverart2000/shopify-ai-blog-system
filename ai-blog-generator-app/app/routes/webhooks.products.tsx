import type { ActionFunctionArgs } from "react-router";

import { BACKEND_KEY, seRequest } from "../lib/social-engine.server";
import { authenticate } from "../shopify.server";

/**
 * Shopify product / collection webhooks → Social Autopilot catalogue sync.
 *
 * `authenticate.webhook` verifies the HMAC. We forward to the backend with a short
 * timeout and always answer 200 once the HMAC is valid, so a slow backend never makes
 * Shopify retry-storm; the backend's 15-minute incremental sync is the safety net.
 * Relay failures are reported to System Health.
 */
const RELAY_TIMEOUT_MS = 4_000;

async function reportFailure(message: string, details: string, correlationId: string) {
  if (!BACKEND_KEY) return;
  try {
    await seRequest("/api/system-health/report", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        level: "WARNING",
        component: "social_autopilot_webhooks",
        operation: "shopify_product_webhook_relay",
        message,
        correlation_id: correlationId,
        details: details.slice(0, 2000),
      }),
      timeoutMs: 3_000,
    });
  } catch {
    // Reporting is best effort.
  }
}

export const action = async ({ request }: ActionFunctionArgs) => {
  // Shopify's header carries the canonical "products/update" form; the library returns "PRODUCTS_UPDATE".
  const headerTopic = (request.headers.get("x-shopify-topic") || "").trim().toLowerCase();
  const { shop, topic, payload, webhookId } = await authenticate.webhook(request);
  const normalizedTopic = headerTopic || String(topic).toLowerCase().replace(/_(?=[^_]*$)/, "/");

  if (!BACKEND_KEY) {
    console.error(`Social Autopilot: ${normalizedTopic} webhook for ${shop} dropped — backend API key not configured.`);
    return new Response();
  }
  try {
    const response = await seRequest("/api/social-engine/internal/shopify-webhook", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ shop, topic: normalizedTopic, webhook_id: webhookId, payload }),
      timeoutMs: RELAY_TIMEOUT_MS,
    });
    if (!response.ok) {
      const detail = (await response.text().catch(() => "")).slice(0, 1000);
      console.error(`Social Autopilot: ${normalizedTopic} webhook relay returned ${response.status}: ${detail}`);
      await reportFailure(`Shopify ${normalizedTopic} webhook relay failed (HTTP ${response.status})`, detail, webhookId);
    }
  } catch (error) {
    const detail = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
    console.error(`Social Autopilot: ${normalizedTopic} webhook relay error: ${detail}`);
    await reportFailure(`Shopify ${normalizedTopic} webhook relay failed`, detail, webhookId);
  }
  return new Response();
};
