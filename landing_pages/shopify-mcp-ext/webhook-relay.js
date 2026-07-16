#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import dotenv from "dotenv";

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT_DIR = path.resolve(__dirname, "..");
const HOST = process.env.COMMERCE_WEBHOOK_HOST || "127.0.0.1";
const PORT = Number.parseInt(process.env.COMMERCE_WEBHOOK_PORT || "8787", 10);
const EVENTS_FILE =
  process.env.COMMERCE_WEBHOOK_EVENTS_FILE ||
  path.join(ROOT_DIR, ".ironclaw-commerce", "webhook-events.jsonl");
const SHOPIFY_CLIENT_SECRET =
  process.env.SHOPIFY_CLIENT_SECRET ||
  process.env.SHOPIFY_SECRET ||
  process.env.SHOPIFY_API_SECRET ||
  "";
const CJ_WEBHOOK_SECRET = process.env.CJ_WEBHOOK_SECRET || "";
const AUTO_RUN = /^(1|true|yes)$/i.test(
  process.env.IRONCLAW_WEBHOOK_AUTO_RUN || "",
);
const IRONCLAW_BIN =
  process.env.IRONCLAW_BIN ||
  path.join(ROOT_DIR, "nearai-ironclaw", "target", "release", "ironclaw");

function json(res, statusCode, body) {
  res.writeHead(statusCode, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

async function readRawBody(req) {
  const chunks = [];
  let total = 0;

  for await (const chunk of req) {
    total += chunk.length;
    if (total > 5 * 1024 * 1024) {
      throw new Error("Request body too large.");
    }
    chunks.push(chunk);
  }

  return Buffer.concat(chunks);
}

function safeEquals(left, right) {
  const leftBuffer = Buffer.from(left || "", "utf8");
  const rightBuffer = Buffer.from(right || "", "utf8");

  if (leftBuffer.length !== rightBuffer.length) {
    return false;
  }

  return crypto.timingSafeEqual(leftBuffer, rightBuffer);
}

function verifyShopifyHmac(rawBody, receivedHmac) {
  if (!SHOPIFY_CLIENT_SECRET) {
    throw new Error(
      "SHOPIFY_CLIENT_SECRET, SHOPIFY_SECRET, or SHOPIFY_API_SECRET must be set for Shopify webhook verification.",
    );
  }

  const digest = crypto
    .createHmac("sha256", SHOPIFY_CLIENT_SECRET)
    .update(rawBody)
    .digest("base64");

  return safeEquals(digest, receivedHmac);
}

async function appendEvent(event) {
  await fs.mkdir(path.dirname(EVENTS_FILE), { recursive: true });
  await fs.appendFile(EVENTS_FILE, `${JSON.stringify(event)}\n`, "utf8");
}

function summarizeEvent(event) {
  return [
    `${event.source} webhook received.`,
    `topic=${event.topic || "unknown"}`,
    `path=${event.path}`,
    `events_file=${EVENTS_FILE}`,
  ].join(" ");
}

function maybeRunIronclaw(event) {
  if (!AUTO_RUN) {
    return;
  }

  const child = spawn(
    IRONCLAW_BIN,
    [
      "--no-onboard",
      "--auto-approve",
      "-m",
      summarizeEvent(event),
    ],
    {
      cwd: ROOT_DIR,
      env: process.env,
      stdio: "ignore",
      detached: true,
    },
  );

  child.unref();
}

function buildEvent({
  source,
  path: requestPath,
  headers,
  payload,
  topic,
}) {
  return {
    id: crypto.randomUUID(),
    source,
    topic,
    path: requestPath,
    receivedAt: new Date().toISOString(),
    headers,
    payload,
  };
}

function selectHeaders(headers, names) {
  const selected = {};
  names.forEach((name) => {
    const value = headers[name];
    if (value !== undefined) {
      selected[name] = value;
    }
  });
  return selected;
}

async function handleShopifyWebhook(req, res, requestPath) {
  const rawBody = await readRawBody(req);
  const receivedHmac = req.headers["x-shopify-hmac-sha256"];
  if (!verifyShopifyHmac(rawBody, receivedHmac)) {
    return json(res, 401, { ok: false, error: "Invalid Shopify webhook HMAC." });
  }

  let payload = {};
  if (rawBody.length > 0) {
    payload = JSON.parse(rawBody.toString("utf8"));
  }

  const event = buildEvent({
    source: "shopify",
    path: requestPath,
    headers: selectHeaders(req.headers, [
      "x-shopify-topic",
      "x-shopify-shop-domain",
      "x-shopify-webhook-id",
      "x-shopify-api-version",
      "x-shopify-triggered-at",
    ]),
    payload,
    topic: req.headers["x-shopify-topic"] || "unknown",
  });

  await appendEvent(event);
  json(res, 200, { ok: true, id: event.id });
  maybeRunIronclaw(event);
}

async function handleCjWebhook(req, res, requestPath) {
  const rawBody = await readRawBody(req);
  const incomingSecret = req.headers["x-cj-webhook-secret"];
  if (CJ_WEBHOOK_SECRET && !safeEquals(CJ_WEBHOOK_SECRET, incomingSecret)) {
    return json(res, 401, { ok: false, error: "Invalid CJ webhook secret." });
  }

  let payload = {};
  if (rawBody.length > 0) {
    payload = JSON.parse(rawBody.toString("utf8"));
  }

  const topic =
    req.headers["x-cj-topic"] ||
    payload?.topic ||
    payload?.messageType ||
    payload?.type ||
    "unknown";

  const event = buildEvent({
    source: "cj",
    path: requestPath,
    headers: selectHeaders(req.headers, [
      "x-cj-topic",
      "x-cj-webhook-secret",
      "user-agent",
    ]),
    payload,
    topic,
  });

  await appendEvent(event);
  json(res, 200, { ok: true, id: event.id });
  maybeRunIronclaw(event);
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url || "/", `http://${req.headers.host || HOST}`);
    const requestPath = url.pathname;

    if (req.method === "GET" && requestPath === "/health") {
      return json(res, 200, {
        ok: true,
        host: HOST,
        port: PORT,
        eventsFile: EVENTS_FILE,
      });
    }

    if (req.method === "POST" && requestPath.startsWith("/webhooks/shopify")) {
      return await handleShopifyWebhook(req, res, requestPath);
    }

    if (req.method === "POST" && requestPath.startsWith("/webhooks/cj")) {
      return await handleCjWebhook(req, res, requestPath);
    }

    return json(res, 404, { ok: false, error: "Not found." });
  } catch (error) {
    return json(res, 500, {
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    });
  }
});

server.listen(PORT, HOST, () => {
  console.log(
    JSON.stringify(
      {
        ok: true,
        message: "Commerce webhook relay listening",
        host: HOST,
        port: PORT,
        healthUrl: `http://${HOST}:${PORT}/health`,
        shopifyWebhookUrl: `http://${HOST}:${PORT}/webhooks/shopify`,
        cjWebhookUrl: `http://${HOST}:${PORT}/webhooks/cj`,
        eventsFile: EVENTS_FILE,
      },
      null,
      2,
    ),
  );
});
