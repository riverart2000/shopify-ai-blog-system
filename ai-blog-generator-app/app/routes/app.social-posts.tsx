import React, { useEffect, useMemo, useState } from "react";
import type { LoaderFunctionArgs } from "react-router";
import { useLoaderData } from "react-router";
import { useAppBridge } from "@shopify/app-bridge-react";
import { authenticate } from "../shopify.server";
import { loadShopifyStudioContext, requireShopifySession } from "../lib/blog-studio.server";

const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

const SOCIAL_PROVIDERS = ["instagram", "facebook", "x", "linkedin", "pinterest"] as const;
type SocialProvider = (typeof SOCIAL_PROVIDERS)[number];

type Product = {
  id: string;
  title: string;
  handle: string;
  url: string;
};

type Workspace = {
  id: string;
  name: string;
  role: string;
  picture: string;
};

type Account = {
  id: string;
  name: string;
  provider: string;
  type: string;
  picture: string;
  status: string;
};

type SocialDefaults = {
  workspaceId: string;
  accountIds: string[];
  providers: SocialProvider[];
  mode: "draft" | "scheduled";
};

type SocialHistoryRow = {
  id: number;
  campaign_name: string;
  product_title: string;
  mode: string;
  account_ids: string[];
  publer_status: string;
  publer_job_id: string;
  scheduled_at: string | null;
  created_at: number;
};

const SOCIAL_OFFER_TYPES = [
  "direct_offer",
  "ingredient_spotlight",
  "science_post",
  "problem_solution",
  "benefits_post",
  "lifestyle_post",
  "myth_busting",
  "educational_carousel",
  "blog_promotion",
  "motivational",
] as const;
type SocialOfferType = (typeof SOCIAL_OFFER_TYPES)[number];

async function backendFetch(path: string, opts: RequestInit = {}) {
  const res = await fetch(`${BACKEND_URL}${path}`, {
    ...opts,
    headers: {
      "x-api-key": BACKEND_KEY,
      "content-type": "application/json",
      ...(opts.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = `Backend ${res.status}`;
    try {
      const body = await res.json() as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      const text = await res.text().catch(() => "");
      if (text) detail = text.slice(0, 300);
    }
    throw new Error(detail);
  }
  return res.json() as Promise<Record<string, unknown>>;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item || "").trim()).filter(Boolean);
}

function toSocialProvider(value: string): SocialProvider | null {
  const normalizedRaw = value.trim().toLowerCase();
  const normalized = normalizedRaw === "twitter" ? "x" : normalizedRaw;
  return SOCIAL_PROVIDERS.includes(normalized as SocialProvider)
    ? (normalized as SocialProvider)
    : null;
}

function toSocialOfferType(value: string): SocialOfferType {
  const normalized = value.trim().toLowerCase();
  return SOCIAL_OFFER_TYPES.includes(normalized as SocialOfferType)
    ? (normalized as SocialOfferType)
    : "direct_offer";
}

function formatDate(epochSeconds: number): string {
  if (!epochSeconds) return "";
  return new Date(epochSeconds * 1000).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const auth = await authenticate.admin(request);
  const session = requireShopifySession((auth as { session?: unknown }).session);
  const context = await loadShopifyStudioContext(session);

  const storefrontDomain = context.storefrontDomain;
  const products: Product[] = (context.products || []).map((p: { id: string; handle: string; title?: string }) => ({
    id: p.id,
    handle: p.handle,
    title: p.title || p.handle,
    url: `https://${storefrontDomain}/products/${p.handle}`,
  }));

  const emptyDefaults: SocialDefaults = {
    workspaceId: "",
    accountIds: [],
    providers: ["instagram", "facebook", "x"],
    mode: "draft",
  };

  if (!BACKEND_KEY) {
    return {
      backendConfigured: false,
      backendError: "AI_BLOG_BACKEND_API_KEY is not configured.",
      publerConfigured: false,
      publerDocsUrl: "",
      products,
      storeId: "",
      storeName: context.shopName,
      workspaces: [] as Workspace[],
      initialAccounts: [] as Account[],
      defaults: emptyDefaults,
      historyRows: [] as SocialHistoryRow[],
    };
  }

  let backendError = "";
  let storeId = "";
  let storeName = context.shopName;
  let workspaces: Workspace[] = [];
  let initialAccounts: Account[] = [];
  let defaults = emptyDefaults;
  let historyRows: SocialHistoryRow[] = [];
  let publerConfigured = false;
  let publerDocsUrl = "";

  try {
    const initData = await backendFetch("/api/init").catch(() => null);
    if (initData) {
      storeId = asString(initData.store_id);
      const storeObj = (initData.store as Record<string, unknown> | undefined) || {};
      storeName = asString(storeObj.name) || storeName;
    }

    if (!storeId) {
      const storesData = await backendFetch("/api/stores").catch(() => null);
      const stores = ((storesData?.stores as unknown[]) || [])
        .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null);
      if (stores.length > 0) {
        storeId = asString(stores[0].id);
        storeName = asString(stores[0].name) || storeName;
      }
    }

    if (storeId) {
      const [defaultsData, historyData, workspacesData] = await Promise.all([
        backendFetch(`/api/social/defaults?store_id=${encodeURIComponent(storeId)}`).catch(() => null),
        backendFetch(`/api/social/history?store_id=${encodeURIComponent(storeId)}&limit=30`).catch(() => null),
        backendFetch("/api/social/workspaces").catch(() => null),
      ]);

      const defaultsObj = ((defaultsData?.defaults as Record<string, unknown> | undefined) || {});
      const defaultProviders = asStringArray(defaultsObj.providers)
        .map((entry) => toSocialProvider(entry))
        .filter((entry): entry is SocialProvider => entry !== null);
      const modeRaw = asString(defaultsObj.mode);
      defaults = {
        workspaceId: asString(defaultsObj.workspace_id),
        accountIds: asStringArray(defaultsObj.account_ids),
        providers: defaultProviders.length > 0 ? defaultProviders : emptyDefaults.providers,
        mode: modeRaw === "scheduled" ? "scheduled" : "draft",
      };

      const rowsRaw = ((historyData?.rows as unknown[]) || [])
        .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null);
      historyRows = rowsRaw.map((row) => ({
        id: Number(row.id || 0),
        campaign_name: asString(row.campaign_name),
        product_title: asString(row.product_title),
        mode: asString(row.mode),
        account_ids: asStringArray(row.account_ids),
        publer_status: asString(row.publer_status),
        publer_job_id: asString(row.publer_job_id),
        scheduled_at: asString(row.scheduled_at) || null,
        created_at: Number(row.created_at || 0),
      }));

      publerConfigured = Boolean(workspacesData?.configured);
      publerDocsUrl = asString(workspacesData?.docs_url);
      const wsRaw = ((workspacesData?.workspaces as unknown[]) || [])
        .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null);
      workspaces = wsRaw.map((ws) => ({
        id: asString(ws.id),
        name: asString(ws.name) || "Workspace",
        role: asString(ws.role),
        picture: asString(ws.picture),
      })).filter((ws) => ws.id);

      const initialWorkspaceId = defaults.workspaceId || (workspaces[0]?.id || "");
      if (initialWorkspaceId) {
        const accountData = await backendFetch(
          `/api/social/accounts?workspace_id=${encodeURIComponent(initialWorkspaceId)}`,
        ).catch(() => null);
        const accountsRaw = ((accountData?.accounts as unknown[]) || [])
          .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null);
        initialAccounts = accountsRaw.map((account) => ({
          id: asString(account.id),
          name: asString(account.name),
          provider: asString(account.provider),
          type: asString(account.type),
          picture: asString(account.picture),
          status: asString(account.status),
        })).filter((account) => account.id && toSocialProvider(account.provider) !== null);
      }
    }
  } catch (error) {
    backendError = error instanceof Error ? error.message : "Failed to load social post data.";
  }

  return {
    backendConfigured: true,
    backendError,
    publerConfigured,
    publerDocsUrl,
    products,
    storeId,
    storeName,
    workspaces,
    initialAccounts,
    defaults,
    historyRows,
  };
};

const inputStyle: React.CSSProperties = {
  borderRadius: "10px",
  border: "1px solid #d1d5db",
  padding: "9px 12px",
  font: "inherit",
  width: "100%",
  boxSizing: "border-box",
};

const labelStyle: React.CSSProperties = {
  display: "grid",
  gap: "6px",
  fontSize: "0.875rem",
};

const providerLabel: Record<SocialProvider, string> = {
  instagram: "Instagram",
  facebook: "Facebook",
  x: "X",
  linkedin: "LinkedIn",
  pinterest: "Pinterest",
};

const offerTypeLabel: Record<SocialOfferType, string> = {
  direct_offer: "Direct Offers",
  ingredient_spotlight: "Ingredient Spotlights",
  science_post: "Science Posts",
  problem_solution: "Problem → Solution",
  benefits_post: "Benefits Posts",
  lifestyle_post: "Lifestyle Posts",
  myth_busting: "Myth Busting",
  educational_carousel: "Educational Carousels",
  blog_promotion: "Blog Promotion",
  motivational: "Motivational",
};

const offerTypeDescription: Record<SocialOfferType, string> = {
  direct_offer: "Strong launch discount-led copy with immediate CTA.",
  ingredient_spotlight: "Focus on key ingredients and what they do.",
  science_post: "Evidence/mechanism-led science angle with conversion CTA.",
  problem_solution: "Open with pain point, position product as the solution.",
  benefits_post: "Benefit stack angle focused on outcomes.",
  lifestyle_post: "Routine and lifestyle framing for daily use.",
  myth_busting: "Myth vs fact style credibility-led copy.",
  educational_carousel: "Carousel-style educational breakdown tone.",
  blog_promotion: "Promote read-more content then push the offer.",
  motivational: "Inspirational tone tied to consistent wellness goals.",
};

export default function SocialPostsRoute() {
  const data = useLoaderData<typeof loader>();
  const shopify = useAppBridge();

  const [workspaceId, setWorkspaceId] = useState<string>(data.defaults.workspaceId || data.workspaces[0]?.id || "");
  const [accounts, setAccounts] = useState<Account[]>(data.initialAccounts || []);
  const [selectedAccountIds, setSelectedAccountIds] = useState<string[]>(data.defaults.accountIds || []);
  const [selectedProviders, setSelectedProviders] = useState<SocialProvider[]>(
    data.defaults.providers.length > 0 ? data.defaults.providers : ["instagram", "facebook", "x"],
  );
  const [mode, setMode] = useState<"draft" | "scheduled">(data.defaults.mode || "draft");

  const [selectedProductHandle, setSelectedProductHandle] = useState<string>(data.products[0]?.handle || "");
  const [briefText, setBriefText] = useState<string>("");
  const [offerType, setOfferType] = useState<SocialOfferType>("direct_offer");
  const [campaignName, setCampaignName] = useState<string>("");
  const [baseText, setBaseText] = useState<string>("");
  const [providerTexts, setProviderTexts] = useState<Record<string, string>>({});
  const [discountUrl, setDiscountUrl] = useState<string>("");
  const [generatedImageUrls, setGeneratedImageUrls] = useState<string[]>([]);
  const [selectedImageUrls, setSelectedImageUrls] = useState<string[]>([]);
  const [imageRatio, setImageRatio] = useState<string>("9:16");
  const [hashtags, setHashtags] = useState<string[]>([]);
  const [keywords, setKeywords] = useState<string[]>([]);

  const [historyRows, setHistoryRows] = useState<SocialHistoryRow[]>(data.historyRows || []);
  const [jobId, setJobId] = useState<string>("");
  const [jobStatus, setJobStatus] = useState<string>("");

  const [loadingAccounts, setLoadingAccounts] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [savingDefaults, setSavingDefaults] = useState(false);
  const [refreshingHistory, setRefreshingHistory] = useState(false);

  const [error, setError] = useState<string>(data.backendError || "");
  const [message, setMessage] = useState<string>("");

  const selectedProduct = useMemo(
    () => data.products.find((product) => product.handle === selectedProductHandle) || null,
    [data.products, selectedProductHandle],
  );

  async function callSocialApi(fields: Record<string, string>) {
    const formData = new FormData();
    for (const [key, value] of Object.entries(fields)) {
      formData.append(key, value);
    }

    const token = await shopify.idToken();

    const response = await fetch("/app/social-posts-api", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
      },
      body: formData,
    });

    const payload = await response.json() as Record<string, unknown>;
    return payload;
  }

  async function refreshAccounts(nextWorkspaceId: string) {
    if (!nextWorkspaceId) {
      setAccounts([]);
      return;
    }

    setLoadingAccounts(true);
    const payload = await callSocialApi({ intent: "accounts", workspaceId: nextWorkspaceId });
    setLoadingAccounts(false);

    if (!payload.ok) {
      setError(asString(payload.error) || "Failed to load Publer accounts.");
      return;
    }

    const rows = (Array.isArray(payload.accounts) ? payload.accounts : [])
      .filter((entry): entry is Record<string, unknown> => typeof entry === "object" && entry !== null)
      .map((entry) => ({
        id: asString(entry.id),
        name: asString(entry.name),
        provider: asString(entry.provider),
        type: asString(entry.type),
        picture: asString(entry.picture),
        status: asString(entry.status),
      }))
      .filter((entry) => entry.id && toSocialProvider(entry.provider) !== null);

    setAccounts(rows);
    const available = new Set(rows.map((row) => row.id));
    setSelectedAccountIds((prev) => prev.filter((accountId) => available.has(accountId)));
  }

  async function refreshHistory() {
    if (!data.storeId) return;
    setRefreshingHistory(true);
    const payload = await callSocialApi({ intent: "history", storeId: data.storeId });
    setRefreshingHistory(false);

    if (!payload.ok) {
      setError(asString(payload.error) || "Failed to refresh history.");
      return;
    }

    const rows = (Array.isArray(payload.rows) ? payload.rows : [])
      .filter((entry): entry is Record<string, unknown> => typeof entry === "object" && entry !== null)
      .map((row) => ({
        id: Number(row.id || 0),
        campaign_name: asString(row.campaign_name),
        product_title: asString(row.product_title),
        mode: asString(row.mode),
        account_ids: asStringArray(row.account_ids),
        publer_status: asString(row.publer_status),
        publer_job_id: asString(row.publer_job_id),
        scheduled_at: asString(row.scheduled_at) || null,
        created_at: Number(row.created_at || 0),
      }));

    setHistoryRows(rows);
  }

  useEffect(() => {
    if (!workspaceId) return;
    if (accounts.length > 0) return;
    void refreshAccounts(workspaceId);
  }, [workspaceId]);

  function toggleAccount(accountId: string) {
    setSelectedAccountIds((prev) => (
      prev.includes(accountId)
        ? prev.filter((id) => id !== accountId)
        : [...prev, accountId]
    ));
  }

  function toggleProvider(provider: SocialProvider) {
    setSelectedProviders((prev) => (
      prev.includes(provider)
        ? prev.filter((entry) => entry !== provider)
        : [...prev, provider]
    ));
  }

  function updateProviderText(provider: SocialProvider, text: string) {
    setProviderTexts((prev) => ({ ...prev, [provider]: text }));
  }

  function toggleImageUrl(imageUrl: string) {
    setSelectedImageUrls((prev) => (
      prev.includes(imageUrl)
        ? prev.filter((url) => url !== imageUrl)
        : [...prev, imageUrl]
    ));
  }

  async function generateSocialDraft() {
    setError("");
    setMessage("");

    if (!data.storeId) {
      setError("Store is not available from backend init.");
      return;
    }
    if (!selectedProduct) {
      setError("Choose a product first.");
      return;
    }

    setGenerating(true);
    const payload = await callSocialApi({
      intent: "generate",
      storeId: data.storeId,
      productTitle: selectedProduct.title,
      productHandle: selectedProduct.handle,
      productUrl: selectedProduct.url,
      briefText,
      offerType,
      modelId: "",
    });
    setGenerating(false);

    if (!payload.ok) {
      setError(asString(payload.error) || "Failed to generate social draft.");
      return;
    }

    const providerMap = (payload.provider_texts && typeof payload.provider_texts === "object")
      ? (payload.provider_texts as Record<string, unknown>)
      : {};
    const normalizedOfferType = toSocialOfferType(asString(payload.offer_type));

    const nextTexts: Record<string, string> = {};
    for (const provider of SOCIAL_PROVIDERS) {
      const value = providerMap[provider];
      if (typeof value === "string" && value.trim()) {
        nextTexts[provider] = value.trim();
      }
    }

    setCampaignName(asString(payload.campaign_name));
    setBaseText(asString(payload.summary));
    setOfferType(normalizedOfferType);
    setDiscountUrl(asString(payload.discount_url));
    const nextImageUrls = asStringArray(payload.image_urls);
    setGeneratedImageUrls(nextImageUrls);
    setSelectedImageUrls(nextImageUrls);
    setImageRatio(asString(payload.image_ratio) || "9:16");
    setProviderTexts(nextTexts);
    setHashtags(asStringArray(payload.hashtags));
    setKeywords(asStringArray(payload.keywords));
    setMessage(
      nextImageUrls.length > 0
        ? `${offerTypeLabel[normalizedOfferType]} draft and 9:16 marketing images generated. Review before sending to Publer.`
        : `${offerTypeLabel[normalizedOfferType]} draft generated. No images were returned by active image models.`,
    );

    if (selectedProviders.length === 0) {
      setSelectedProviders(["instagram", "facebook", "x"]);
    }
  }

  async function saveDefaults() {
    setError("");
    setMessage("");

    if (!data.storeId) {
      setError("Store is not available from backend init.");
      return;
    }

    setSavingDefaults(true);
    const payload = await callSocialApi({
      intent: "save-defaults",
      storeId: data.storeId,
      workspaceId,
      accountIdsJson: JSON.stringify(selectedAccountIds),
      providersJson: JSON.stringify(selectedProviders),
      mode,
    });
    setSavingDefaults(false);

    if (!payload.ok) {
      setError(asString(payload.error) || "Failed to save defaults.");
      return;
    }

    setMessage("Defaults saved for this store.");
  }

  async function publishToPubler() {
    setError("");
    setMessage("");

    if (!data.storeId) {
      setError("Store is not available from backend init.");
      return;
    }
    if (!selectedProduct) {
      setError("Choose a product first.");
      return;
    }
    if (!workspaceId) {
      setError("Choose a Publer workspace.");
      return;
    }
    if (selectedAccountIds.length === 0) {
      setError("Select at least one account.");
      return;
    }
    if (selectedProviders.length === 0) {
      setError("Select at least one provider.");
      return;
    }

    const filteredProviderTexts: Record<string, string> = {};
    for (const provider of selectedProviders) {
      const text = (providerTexts[provider] || "").trim();
      if (text) filteredProviderTexts[provider] = text;
    }
    if (Object.keys(filteredProviderTexts).length === 0) {
      setError("Generate or enter post text for at least one selected provider.");
      return;
    }

    if (generatedImageUrls.length > 0 && selectedImageUrls.length === 0) {
      setError("Select at least one generated image or regenerate before sending to Publer.");
      return;
    }

    setPublishing(true);
    const payload = await callSocialApi({
      intent: "publish",
      storeId: data.storeId,
      workspaceId,
      campaignName,
      productHandle: selectedProduct.handle,
      productTitle: selectedProduct.title,
      productUrl: selectedProduct.url,
      briefText,
      baseText,
      providerTextsJson: JSON.stringify(filteredProviderTexts),
      imageUrlsJson: JSON.stringify(selectedImageUrls),
      accountIdsJson: JSON.stringify(selectedAccountIds),
      mode,
    });
    setPublishing(false);

    if (!payload.ok) {
      setError(asString(payload.error) || "Failed to send post to Publer.");
      return;
    }

    const nextJobId = asString(payload.job_id);
    setJobId(nextJobId);
    setJobStatus(asString(payload.status) || "queued");
    setMessage(nextJobId ? `Queued in Publer. Job ID: ${nextJobId}` : "Sent to Publer.");
    void refreshHistory();
  }

  async function checkJobStatus() {
    setError("");
    setMessage("");

    if (!workspaceId || !jobId) {
      setError("Workspace and job ID are required.");
      return;
    }

    const payload = await callSocialApi({
      intent: "job-status",
      workspaceId,
      jobId,
    });

    if (!payload.ok) {
      setError(asString(payload.error) || "Failed to load Publer job status.");
      return;
    }

    const status = asString(payload.status) || "unknown";
    setJobStatus(status);
    setMessage(`Publer job status: ${status}`);
    void refreshHistory();
  }

  return (
    <s-page heading="Social Posts Generator">
      {!data.backendConfigured ? (
        <s-section>
          <div style={{ borderRadius: "12px", padding: "14px 16px", background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.3)", color: "#991b1b", fontSize: "0.9rem" }}>
            {data.backendError || "AI_BLOG_BACKEND_API_KEY is not configured."}
          </div>
        </s-section>
      ) : null}

      {data.backendConfigured && !data.publerConfigured ? (
        <s-section>
          <div style={{ borderRadius: "12px", padding: "14px 16px", background: "#fff7ed", border: "1px solid #fed7aa", color: "#9a3412", fontSize: "0.9rem" }}>
            Publer is not configured on the backend. Add PUBLER_API_KEY in backend environment.
            {data.publerDocsUrl ? (
              <>
                {" "}
                <a href={data.publerDocsUrl} target="_blank" rel="noreferrer">Publer API docs</a>
              </>
            ) : null}
          </div>
        </s-section>
      ) : null}

      {error ? (
        <s-section>
          <div style={{ borderRadius: "12px", padding: "14px 16px", background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.3)", color: "#991b1b", fontSize: "0.9rem" }}>
            {error}
          </div>
        </s-section>
      ) : null}

      <s-section>
        <s-paragraph>
          This page is for text and image-compatible social channels only.
          TikTok and YouTube will be handled in a separate video workflow.
        </s-paragraph>
      </s-section>

      {message ? (
        <s-section>
          <div style={{ borderRadius: "12px", padding: "14px 16px", background: "#ecfdf5", border: "1px solid #a7f3d0", color: "#065f46", fontSize: "0.9rem" }}>
            {message}
          </div>
        </s-section>
      ) : null}

      <s-section heading="1. Generate Social Draft">
        <div style={{ display: "grid", gap: "14px" }}>
          <label style={labelStyle}>
            <span>Product</span>
            <select
              style={inputStyle}
              value={selectedProductHandle}
              onChange={(event) => setSelectedProductHandle(event.target.value)}
            >
              {data.products.map((product) => (
                <option key={product.id} value={product.handle}>{product.title}</option>
              ))}
            </select>
          </label>

          <label style={labelStyle}>
            <span>Optional brief</span>
            <textarea
              style={{ ...inputStyle, resize: "vertical" }}
              rows={4}
              value={briefText}
              onChange={(event) => setBriefText(event.target.value)}
              placeholder="Example: highlight anti-ageing benefits, premium quality, and invite people to discover the full range at bioluxelab.com"
            />
          </label>

          <label style={labelStyle}>
            <span>Offer Style</span>
            <select
              style={inputStyle}
              value={offerType}
              onChange={(event) => setOfferType(toSocialOfferType(event.target.value))}
            >
              {SOCIAL_OFFER_TYPES.map((value) => (
                <option key={value} value={value}>{offerTypeLabel[value]}</option>
              ))}
            </select>
            <span style={{ fontSize: "0.8rem", color: "#6b7280" }}>{offerTypeDescription[offerType]}</span>
          </label>

          <div>
            <button
              type="button"
              onClick={generateSocialDraft}
              disabled={!data.backendConfigured || generating || !selectedProduct}
              style={{ borderRadius: "999px", border: 0, background: "#111827", color: "white", padding: "10px 20px", fontWeight: 700, cursor: "pointer", opacity: generating ? 0.7 : 1 }}
            >
              {generating ? "Generating..." : "Generate social copy"}
            </button>
          </div>
        </div>
      </s-section>

      <s-section heading="2. Edit Copy Per Provider">
        <div style={{ display: "grid", gap: "14px" }}>
          <label style={labelStyle}>
            <span>Campaign name</span>
            <input
              style={inputStyle}
              value={campaignName}
              onChange={(event) => setCampaignName(event.target.value)}
              placeholder="Campaign name"
            />
          </label>

          <label style={labelStyle}>
            <span>Base message</span>
            <textarea
              style={{ ...inputStyle, resize: "vertical" }}
              rows={3}
              value={baseText}
              onChange={(event) => setBaseText(event.target.value)}
              placeholder="High-level message for this campaign"
            />
          </label>

          <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
            {SOCIAL_PROVIDERS.map((provider) => (
              <label key={provider} style={{ display: "inline-flex", alignItems: "center", gap: "6px", padding: "6px 10px", border: "1px solid #e5e7eb", borderRadius: "999px", fontSize: "0.8rem" }}>
                <input
                  type="checkbox"
                  checked={selectedProviders.includes(provider)}
                  onChange={() => toggleProvider(provider)}
                />
                {providerLabel[provider]}
              </label>
            ))}
          </div>

          <div style={{ display: "grid", gap: "12px" }}>
            {selectedProviders.map((provider) => (
              <label key={provider} style={labelStyle}>
                <span>{providerLabel[provider]}</span>
                <textarea
                  style={{ ...inputStyle, resize: "vertical" }}
                  rows={3}
                  value={providerTexts[provider] || ""}
                  onChange={(event) => updateProviderText(provider, event.target.value)}
                  placeholder={`Write ${providerLabel[provider]} post copy`}
                />
              </label>
            ))}
          </div>

          {discountUrl ? (
            <label style={labelStyle}>
              <span>Discount CTA URL (20% offer)</span>
              <input
                style={inputStyle}
                value={discountUrl}
                readOnly
                placeholder="https://bioluxelab.com/discount/LAUNCH20?redirect=/products/..."
              />
            </label>
          ) : null}

          <div style={{ display: "grid", gap: "10px" }}>
            <div style={{ fontSize: "0.86rem", fontWeight: 600 }}>Generated Marketing Images ({imageRatio || "9:16"})</div>
            {generatedImageUrls.length === 0 ? (
              <div style={{ fontSize: "0.84rem", color: "#6b7280" }}>
                Generate social copy to create marketing images.
              </div>
            ) : (
              <div style={{ display: "grid", gap: "10px", gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))" }}>
                {generatedImageUrls.map((imageUrl, index) => {
                  const checked = selectedImageUrls.includes(imageUrl);
                  return (
                    <label
                      key={`${index}-${imageUrl}`}
                      style={{
                        border: checked ? "2px solid #111827" : "1px solid #e5e7eb",
                        borderRadius: "12px",
                        padding: "8px",
                        display: "grid",
                        gap: "8px",
                        cursor: "pointer",
                        background: checked ? "#f9fafb" : "white",
                      }}
                    >
                      <div
                        style={{
                          width: "100%",
                          aspectRatio: "9 / 16",
                          borderRadius: "10px",
                          overflow: "hidden",
                          background: "#f3f4f6",
                        }}
                      >
                        <img
                          src={imageUrl}
                          alt={`Generated marketing visual ${index + 1}`}
                          style={{ width: "100%", height: "100%", objectFit: "cover" }}
                          loading="lazy"
                        />
                      </div>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "0.82rem" }}>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleImageUrl(imageUrl)}
                        />
                        Attach image {index + 1}
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
          </div>

          {(hashtags.length > 0 || keywords.length > 0) ? (
            <div style={{ fontSize: "0.82rem", color: "#4b5563", display: "grid", gap: "6px" }}>
              {keywords.length > 0 ? <div><strong>Keywords:</strong> {keywords.join(", ")}</div> : null}
              {hashtags.length > 0 ? <div><strong>Hashtags:</strong> {hashtags.join(" ")}</div> : null}
            </div>
          ) : null}
        </div>
      </s-section>

      <s-section heading="3. Publer Destination">
        <div style={{ display: "grid", gap: "14px" }}>
          <label style={labelStyle}>
            <span>Workspace</span>
            <select
              style={inputStyle}
              value={workspaceId}
              onChange={(event) => {
                const next = event.target.value;
                setWorkspaceId(next);
                void refreshAccounts(next);
              }}
            >
              <option value="">Select workspace</option>
              {data.workspaces.map((workspace) => (
                <option key={workspace.id} value={workspace.id}>{workspace.name}</option>
              ))}
            </select>
          </label>

          <div>
            <div style={{ fontSize: "0.85rem", fontWeight: 600, marginBottom: "8px" }}>Accounts</div>
            {loadingAccounts ? (
              <div style={{ fontSize: "0.85rem", color: "#6b7280" }}>Loading accounts...</div>
            ) : accounts.length === 0 ? (
              <div style={{ fontSize: "0.85rem", color: "#6b7280" }}>No accounts loaded for this workspace.</div>
            ) : (
              <div style={{ display: "grid", gap: "8px" }}>
                {accounts.map((account) => (
                  <label key={account.id} style={{ display: "flex", alignItems: "center", gap: "8px", border: "1px solid #e5e7eb", borderRadius: "10px", padding: "8px 10px" }}>
                    <input
                      type="checkbox"
                      checked={selectedAccountIds.includes(account.id)}
                      onChange={() => toggleAccount(account.id)}
                    />
                    <span style={{ fontSize: "0.86rem" }}>
                      {account.name || account.id} ({account.provider || "unknown"})
                    </span>
                  </label>
                ))}
              </div>
            )}
          </div>

          <div>
            <button
              type="button"
              onClick={saveDefaults}
              disabled={savingDefaults || !data.storeId}
              style={{ borderRadius: "999px", border: "1px solid #d1d5db", background: "white", color: "#111827", padding: "9px 16px", fontWeight: 600, cursor: "pointer", opacity: savingDefaults ? 0.7 : 1 }}
            >
              {savingDefaults ? "Saving..." : "Save as defaults"}
            </button>
          </div>
        </div>
      </s-section>

      <s-section heading="4. Send to Publer">
        <div style={{ display: "grid", gap: "12px" }}>
          <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
            <label style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
              <input type="radio" checked={mode === "draft"} onChange={() => setMode("draft")} /> Draft
            </label>
            <label style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
              <input type="radio" checked={mode === "scheduled"} onChange={() => setMode("scheduled")} /> Send to Publer Schedule
            </label>
          </div>

          <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
            <button
              type="button"
              onClick={publishToPubler}
              disabled={publishing || !data.backendConfigured}
              style={{ borderRadius: "999px", border: 0, background: "#111827", color: "white", padding: "10px 20px", fontWeight: 700, cursor: "pointer", opacity: publishing ? 0.7 : 1 }}
            >
              {publishing ? "Sending..." : "Send to Publer"}
            </button>

            <button
              type="button"
              onClick={checkJobStatus}
              disabled={!jobId || !workspaceId}
              style={{ borderRadius: "999px", border: "1px solid #d1d5db", background: "white", color: "#111827", padding: "10px 20px", fontWeight: 600, cursor: "pointer" }}
            >
              Check job status
            </button>
          </div>

          {(jobId || jobStatus) ? (
            <div style={{ fontSize: "0.85rem", color: "#374151" }}>
              Job ID: {jobId || "-"} | Status: {jobStatus || "queued"}
            </div>
          ) : null}
        </div>
      </s-section>

      <s-section heading="Recent Social Post History">
        <div style={{ marginBottom: "12px" }}>
          <button
            type="button"
            onClick={refreshHistory}
            disabled={refreshingHistory || !data.storeId}
            style={{ borderRadius: "999px", border: "1px solid #d1d5db", background: "white", color: "#111827", padding: "8px 14px", fontWeight: 600, cursor: "pointer", opacity: refreshingHistory ? 0.7 : 1 }}
          >
            {refreshingHistory ? "Refreshing..." : "Refresh history"}
          </button>
        </div>

        {historyRows.length === 0 ? (
          <s-paragraph>No social post entries yet.</s-paragraph>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
              <thead>
                <tr style={{ borderBottom: "2px solid #e5e7eb", textAlign: "left" }}>
                  <th style={{ padding: "8px 10px" }}>Time</th>
                  <th style={{ padding: "8px 10px" }}>Campaign</th>
                  <th style={{ padding: "8px 10px" }}>Product</th>
                  <th style={{ padding: "8px 10px" }}>Mode</th>
                  <th style={{ padding: "8px 10px" }}>Status</th>
                  <th style={{ padding: "8px 10px" }}>Job</th>
                </tr>
              </thead>
              <tbody>
                {historyRows.map((row, index) => (
                  <tr key={`${row.id}-${index}`} style={{ borderBottom: "1px solid #f3f4f6", background: index % 2 === 0 ? "transparent" : "#f9fafb" }}>
                    <td style={{ padding: "8px 10px", whiteSpace: "nowrap", color: "#6b7280" }}>{formatDate(row.created_at)}</td>
                    <td style={{ padding: "8px 10px" }}>{row.campaign_name || "(untitled)"}</td>
                    <td style={{ padding: "8px 10px" }}>{row.product_title || "-"}</td>
                    <td style={{ padding: "8px 10px" }}>{row.mode || "-"}</td>
                    <td style={{ padding: "8px 10px" }}>{row.publer_status || "queued"}</td>
                    <td style={{ padding: "8px 10px", color: "#6b7280" }}>{row.publer_job_id || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </s-section>
    </s-page>
  );
}
