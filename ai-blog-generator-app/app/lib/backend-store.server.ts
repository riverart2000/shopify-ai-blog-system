import type { RequestInit } from "react-router";

import { authenticate } from "../shopify.server";
import { requireShopifySession } from "./blog-studio.server";

export const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
export const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

export type BackendStore = {
  id: string;
  name: string;
  myshopify_domain: string;
  default_blog_handle: string;
  default_author: string;
};

function normalizeShopDomain(value: string) {
  return value
    .trim()
    .replace(/^https?:\/\//i, "")
    .replace(/\/.*$/, "")
    .replace(/^www\./i, "")
    .toLowerCase();
}

function shopAlias(value: string) {
  return normalizeShopDomain(value).replace(/\.myshopify\.com$/, "");
}

export async function backendFetch(path: string, opts: RequestInit = {}) {
  const res = await fetch(`${BACKEND_URL}${path}`, {
    ...opts,
    headers: { "x-api-key": BACKEND_KEY, "content-type": "application/json", ...(opts.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = `Backend ${res.status}`;
    try {
      const body = await res.json() as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      const text = await res.text().catch(() => "");
      if (text) detail = text.slice(0, 200);
    }
    throw new Error(detail);
  }
  return res.json() as Promise<Record<string, unknown>>;
}

export function withStoreId(path: string, storeId: string) {
  if (!storeId) return path;
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}store_id=${encodeURIComponent(storeId)}`;
}

export function matchBackendStoreToShop(stores: BackendStore[], shopDomain: string) {
  const normalizedShop = normalizeShopDomain(shopDomain);
  const normalizedAlias = shopAlias(shopDomain);

  return stores.find((store) => {
    const storeDomain = normalizeShopDomain(store.myshopify_domain || "");
    return storeDomain === normalizedShop || shopAlias(storeDomain) === normalizedAlias;
  }) ?? null;
}

export async function loadBackendStoreContext(
  request: Request,
  options: { requestedStoreId?: string } = {},
) {
  const auth = await authenticate.admin(request);
  const session = requireShopifySession((auth as { session?: unknown }).session);

  if (!BACKEND_KEY) {
    return {
      auth,
      session,
      stores: [] as BackendStore[],
      matchedStore: null as BackendStore | null,
      selectedStore: null as BackendStore | null,
      storeId: options.requestedStoreId?.trim() || "",
    };
  }

  const storesData = await backendFetch("/api/stores");
  const stores = (storesData.stores ?? []) as BackendStore[];
  const requestedStoreId = options.requestedStoreId?.trim() || "";
  const requestedStore = requestedStoreId ? stores.find((store) => store.id === requestedStoreId) ?? null : null;
  const matchedStore = matchBackendStoreToShop(stores, session.shop);
  const selectedStore = requestedStore || matchedStore || stores[0] || null;

  return {
    auth,
    session,
    stores,
    matchedStore,
    selectedStore,
    storeId: selectedStore?.id || "",
  };
}