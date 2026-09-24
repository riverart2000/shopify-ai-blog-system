import React, { useState, useEffect, useMemo } from "react";
import type { LoaderFunctionArgs } from "react-router";
import { useLoaderData } from "react-router";
import { useAppBridge } from "@shopify/app-bridge-react";
import { authenticate } from "../shopify.server";
import { loadShopifyStudioContext, requireShopifySession } from "../lib/blog-studio.server";

const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

type SyncProductFinding = {
  product_id: string;
  product_title: string;
  product_handle: string;
  product_url: string;
  status: "matched" | "repaired" | "missing" | "mismatch" | "duplicate" | "error";
  article_title: string;
  article_url: string;
  match_source: string;
  issues: string[];
  unresolved_issues: string[];
  repairs: string[];
  errors: string[];
};

type SyncResult = {
  message: string;
  counts: {
    products: number;
    articles: number;
    matched: number;
    repaired: number;
    missing: number;
    mismatch: number;
    duplicate: number;
    error: number;
    orphans: number;
  };
  products: SyncProductFinding[];
  orphans: Array<{ article_title: string; article_url: string; reason: string }>;
};

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
      if (text) detail = text.slice(0, 200);
    }
    throw new Error(detail);
  }
  return res.json() as Promise<Record<string, unknown>>;
}

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const auth = await authenticate.admin(request);
  const session = requireShopifySession((auth as { session?: unknown }).session);
  const [context, initData] = await Promise.all([
    loadShopifyStudioContext(session),
    BACKEND_KEY ? backendFetch("/api/init").catch(() => null) : Promise.resolve(null),
  ]);
  const storefrontDomain = context.storefrontDomain;
  const products = context.products || [];

  // Find store id
  const storesData = await backendFetch("/api/stores").catch(() => null) as { stores?: Array<{ id?: string }> } | null;
  const storeId = (storesData?.stores ?? [])[0]?.id || "";

  return {
    backendConfigured: !!BACKEND_KEY,
    products,
    storefrontDomain,
    storeId,
    storeName: (initData?.store as { name?: string })?.name || context.shopName,
  };
};

export default function ProductBlogsPage() {
  const { backendConfigured, products, storefrontDomain, storeId } = useLoaderData<typeof loader>();
  const shopify = useAppBridge();

  const [productStatuses, setProductStatuses] = useState<Record<string, {
    status: "idle" | "generating" | "success" | "failed";
    guideTitle?: string | null;
    guideUrl?: string | null;
    imageCount?: number;
    currentStage?: string;
    warnings?: string[];
    error?: string;
  }>>({});

  useEffect(() => {
    const initial: typeof productStatuses = {};
    for (const p of products) {
      initial[p.id] = {
        status: p.guideUrl ? "success" : "idle",
        guideTitle: p.guideTitle,
        guideUrl: p.guideUrl,
      };
    }
    setProductStatuses(initial);
  }, [products]);

  const [isBulkRunning, setIsBulkRunning] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [isSyncRunning, setIsSyncRunning] = useState(false);
  const [syncProgress, setSyncProgress] = useState("");
  const [syncResult, setSyncResult] = useState<SyncResult | null>(null);
  const [syncError, setSyncError] = useState("");

  const syncFindingsByHandle = useMemo(() => {
    const findings: Record<string, SyncProductFinding> = {};
    for (const finding of syncResult?.products ?? []) {
      findings[finding.product_handle] = finding;
    }
    return findings;
  }, [syncResult]);

  const postProductBlogAction = async (formData: FormData) => {
    const token = await shopify.idToken();
    const response = await fetch("/app/product-blogs-api", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: formData,
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return response.json() as Promise<Record<string, unknown>>;
  };

  const handleProductBlogSync = async () => {
    setIsSyncRunning(true);
    setSyncResult(null);
    setSyncError("");
    setSyncProgress("Queueing live Shopify scan...");
    setLogs(prev => [...prev, "[Re-sync] Starting a fresh Shopify product/blog sanity check. No AI models will be called."]);

    try {
      const startForm = new FormData();
      startForm.append("intent", "sync-start");
      startForm.append("storeId", storeId);
      const started = await postProductBlogAction(startForm) as {
        ok?: boolean;
        job_id?: string;
        message?: string;
        error?: string;
      };
      if (!started.ok || !started.job_id) {
        throw new Error(started.error || "The product-blog sanity check did not return a job id.");
      }

      setSyncProgress(started.message || "Sanity check queued...");
      for (let pollNumber = 0; pollNumber < 300; pollNumber += 1) {
        await new Promise(resolve => setTimeout(resolve, 2000));
        const statusForm = new FormData();
        statusForm.append("intent", "sync-status");
        statusForm.append("storeId", storeId);
        statusForm.append("jobId", started.job_id);
        const status = await postProductBlogAction(statusForm) as {
          ok?: boolean;
          status?: string;
          message?: string;
          current?: number;
          total?: number;
          result?: SyncResult;
          error?: string;
        };
        if (!status.ok) {
          throw new Error(status.error || "Sanity-check progress could not be read.");
        }

        const progressSuffix = status.total
          ? ` (${status.current || 0} of ${status.total})`
          : "";
        setSyncProgress(`${status.message || status.status || "Working"}${progressSuffix}`);

        if (status.status === "failed") {
          throw new Error(`${status.message || "Sanity check failed"} ${status.error || ""}`.trim());
        }
        if (status.status === "success") {
          if (!status.result) {
            throw new Error("Sanity check completed but returned no reconciliation report.");
          }
          const completedResult = status.result;
          setSyncResult(completedResult);
          setProductStatuses(prev => {
            const next = { ...prev };
            for (const finding of completedResult.products) {
              const product = products.find(item => item.handle === finding.product_handle);
              if (!product) continue;
              if (finding.status === "matched" || finding.status === "repaired") {
                next[product.id] = {
                  ...next[product.id],
                  status: "success",
                  guideTitle: finding.article_title,
                  guideUrl: finding.article_url,
                  error: undefined,
                };
              } else if (finding.status === "missing") {
                next[product.id] = {
                  ...next[product.id],
                  status: "idle",
                  guideTitle: null,
                  guideUrl: null,
                };
              }
            }
            return next;
          });
          const notable = completedResult.products.filter(item => item.status !== "matched");
          setLogs(prev => [
            ...prev,
            `[Re-sync complete] ${completedResult.message}`,
            ...notable.map(item =>
              `[${item.status.toUpperCase()}] ${item.product_title}: ${[...item.repairs, ...item.issues, ...item.unresolved_issues, ...item.errors].join(" | ") || "See report"}`,
            ),
            ...completedResult.orphans.map(item => `[ORPHAN ARTICLE] ${item.article_title}: ${item.reason}`),
          ]);
          setSyncProgress(completedResult.message);
          return;
        }
      }
      throw new Error("Sanity check did not finish within 10 minutes. The job was not restarted.");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown product-blog sanity-check error";
      setSyncError(message);
      setLogs(prev => [...prev, `[Re-sync failed] ${message}`]);
    } finally {
      setIsSyncRunning(false);
    }
  };

  const checkStatus = async (p: typeof products[0]) => {
    const formData = new FormData();
    formData.append("intent", "status");
    formData.append("storeId", storeId);
    formData.append("productHandle", p.handle);

    const token = await shopify.idToken();
    const response = await fetch("/app/product-blogs-api", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
      },
      body: formData,
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status} checking status`);
    }
    const resData = await response.json() as {
      ok: boolean;
      status: string;
      articleUrl?: string;
      title?: string;
      imageCount?: number;
      currentStage?: string;
      warnings?: string[];
      error?: string;
    };
    if (!resData.ok) {
      throw new Error(resData.error || "Status check failed");
    }
    return resData;
  };

  const generateProductBlog = async (p: typeof products[0]) => {
    setProductStatuses(prev => ({
      ...prev,
      [p.id]: { ...prev[p.id], status: "generating", error: undefined }
    }));
    setLogs(prev => [...prev, `[Started] Generating blog for "${p.title}"...`]);

    const formData = new FormData();
    formData.append("intent", "generate");
    formData.append("storeId", storeId);
    formData.append("productId", p.id);
    formData.append("productTitle", p.title);
    formData.append("productHandle", p.handle);
    formData.append("productUrl", `https://${storefrontDomain}/products/${p.handle}`);

    try {
      const token = await shopify.idToken();
      const response = await fetch("/app/product-blogs-api", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
        },
        body: formData,
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const resData = await response.json() as {
        ok: boolean;
        status?: string;
        articleUrl?: string;
        title?: string;
        imageCount?: number;
        warnings?: string[];
        error?: string;
        message?: string;
      };

      if (!resData.ok) {
        throw new Error(resData.error || "Failed to initiate generation");
      }

      if (resData.status === "success") {
        setProductStatuses(prev => ({
          ...prev,
          [p.id]: {
            status: "success",
            guideTitle: resData.title || p.title,
            guideUrl: resData.articleUrl,
            imageCount: resData.imageCount,
            warnings: resData.warnings,
          }
        }));
        setLogs(prev => [...prev, `[Success] Published blog for "${p.title}" with ${resData.imageCount ?? "all"} images: ${resData.articleUrl}`]);
        return true;
      }

      // Start polling status
      let attempts = 0;
      const maxAttempts = 60; // 5 min maximum
      while (attempts < maxAttempts) {
        await new Promise(resolve => setTimeout(resolve, 5000));
        attempts++;
        try {
          const poll = await checkStatus(p);
          if (poll.status === "success") {
            setProductStatuses(prev => ({
              ...prev,
              [p.id]: {
                status: "success",
                guideTitle: poll.title || p.title,
                guideUrl: poll.articleUrl,
                imageCount: poll.imageCount,
                currentStage: "complete",
                warnings: poll.warnings,
              }
            }));
            setLogs(prev => [...prev, `[Success] Published blog for "${p.title}" with ${poll.imageCount ?? "all"} images: ${poll.articleUrl}`]);
            return true;
          }
          if (poll.status === "failed") {
            throw new Error(poll.error || "Generation task failed");
          }
          setProductStatuses(prev => ({
            ...prev,
            [p.id]: {
              ...prev[p.id],
              status: "generating",
              currentStage: poll.currentStage || "processing",
              warnings: poll.warnings,
            },
          }));
        } catch (pollErr: any) {
          // Keep trying if temporary status error, unless failed status reported
          const msg = pollErr.message || pollErr;
          if (typeof msg === "string" && msg.includes("failed")) {
            throw pollErr;
          }
        }
      }

      throw new Error("Generation timed out on the backend (took > 5 minutes)");
    } catch (err: any) {
      const errMsg = err.message || "Unknown error";
      setProductStatuses(prev => ({
        ...prev,
        [p.id]: { ...prev[p.id], status: "failed", error: errMsg }
      }));
      setLogs(prev => [...prev, `[Failed] "${p.title}": ${errMsg}`]);
      return false;
    }
  };

  const ensureProductDescription = async (p: typeof products[0]) => {
    const local = productStatuses[p.id];
    if (!local || !local.guideUrl) return;

    setLogs(prev => [...prev, `[Started] Verifying description link for "${p.title}"...`]);

    const formData = new FormData();
    formData.append("intent", "ensure-description");
    formData.append("storeId", storeId);
    formData.append("productId", p.id);
    formData.append("productTitle", p.title);
    formData.append("productHandle", p.handle);
    formData.append("productUrl", `https://${storefrontDomain}/products/${p.handle}`);
    formData.append("guideTitle", local.guideTitle || p.title);
    formData.append("guideUrl", local.guideUrl);

    try {
      const token = await shopify.idToken();
      const response = await fetch("/app/product-blogs-api", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
        },
        body: formData,
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const resData = await response.json() as { ok: boolean; message?: string; error?: string };
      if (resData.ok) {
        setLogs(prev => [...prev, `[Success] Checked description for "${p.title}": ${resData.message || "already set or updated successfully"}`]);
        return true;
      } else {
        throw new Error(resData.error || "Failed to check description");
      }
    } catch (err: any) {
      const errMsg = err.message || "Unknown error";
      setLogs(prev => [...prev, `[Failed] Description check for "${p.title}": ${errMsg}`]);
      return false;
    }
  };

  const handleBulkGenerate = async () => {
    const targetProducts = products.filter(p => {
      const local = productStatuses[p.id];
      return !local?.guideUrl;
    });

    const backfillProducts = products.filter(p => {
      const local = productStatuses[p.id];
      return !!local?.guideUrl;
    });

    setIsBulkRunning(true);
    setLogs(prev => [
      ...prev,
      `Starting batch execution: generating ${targetProducts.length} missing blogs, checking ${backfillProducts.length} existing product descriptions...`
    ]);

    // 1. Generate missing blogs
    for (const p of targetProducts) {
      await generateProductBlog(p);
    }

    // 2. Ensure existing descriptions are updated
    for (const p of backfillProducts) {
      await ensureProductDescription(p);
    }

    setIsBulkRunning(false);
    setLogs(prev => [...prev, "Batch execution finished."]);
  };

  const totalProducts = products.length;
  const attachedCount = products.filter(p => productStatuses[p.id]?.guideUrl).length;
  const missingCount = totalProducts - attachedCount;

  return (
    <s-page heading="Product Blogs Setup">
      {!backendConfigured ? (
        <s-section>
          <div
            style={{
              borderRadius: "12px",
              padding: "14px 16px",
              background: "rgba(239,68,68,0.08)",
              border: "1px solid rgba(239,68,68,0.3)",
              color: "#991b1b",
              fontSize: "0.9rem",
            }}
          >
            AI_BLOG_BACKEND_API_KEY is not configured. Product blogs cannot be generated.
          </div>
        </s-section>
      ) : null}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "16px", marginBottom: "16px" }}>
        <div style={{ background: "#ffffff", padding: "16px", borderRadius: "12px", border: "1px solid #e1e3e5", boxShadow: "0 1px 3px rgba(0,0,0,0.05)" }}>
          <p style={{ margin: 0, fontSize: "0.875rem", color: "#616e75" }}>Total Products</p>
          <p style={{ margin: "4px 0 0 0", fontSize: "1.75rem", fontWeight: "700" }}>{totalProducts}</p>
        </div>
        <div style={{ background: "#ffffff", padding: "16px", borderRadius: "12px", border: "1px solid #e1e3e5", boxShadow: "0 1px 3px rgba(0,0,0,0.05)" }}>
          <p style={{ margin: 0, fontSize: "0.875rem", color: "#616e75" }}>Blogs Attached</p>
          <p style={{ margin: "4px 0 0 0", fontSize: "1.75rem", fontWeight: "700", color: "#107c41" }}>{attachedCount}</p>
        </div>
        <div style={{ background: "#ffffff", padding: "16px", borderRadius: "12px", border: "1px solid #e1e3e5", boxShadow: "0 1px 3px rgba(0,0,0,0.05)" }}>
          <p style={{ margin: 0, fontSize: "0.875rem", color: "#616e75" }}>Missing Blogs</p>
          <p style={{ margin: "4px 0 0 0", fontSize: "1.75rem", fontWeight: "700", color: "#a42e2b" }}>{missingCount}</p>
        </div>
      </div>

      <s-section heading="Actions">
        <s-paragraph>
          This dashboard lets you automatically compile blogs/guides for all products under the blog handle <code>inside-the-products</code>. Click <strong>Generate Missing Blogs</strong> to run the product-linked generator for products that do not yet have an attached publication.
        </s-paragraph>
        <div style={{ display: "flex", gap: "12px", marginTop: "12px", flexWrap: "wrap" }}>
          <button
            onClick={handleProductBlogSync}
            disabled={isSyncRunning || isBulkRunning || !backendConfigured}
            style={{
              padding: "10px 18px",
              background: isSyncRunning ? "#8c9196" : "#005bd3",
              color: "#ffffff",
              border: "none",
              borderRadius: "8px",
              fontWeight: 600,
              cursor: isSyncRunning || isBulkRunning ? "not-allowed" : "pointer",
            }}
          >
            {isSyncRunning ? "Re-syncing Products & Blogs..." : "Re-sync & Sanity Check"}
          </button>
          <button
            onClick={handleBulkGenerate}
            disabled={isBulkRunning || isSyncRunning || !backendConfigured}
            style={{
              padding: "10px 18px",
              background: isBulkRunning ? "#8c9196" : "#202223",
              color: "#ffffff",
              border: "none",
              borderRadius: "8px",
              fontWeight: 600,
              cursor: isBulkRunning ? "not-allowed" : "pointer",
            }}
          >
            {isBulkRunning ? "Running Batch Process..." : `Generate Missing Blogs & Check Link Meta (${missingCount} missing)`}
          </button>
        </div>
        <p style={{ margin: "10px 0 0", color: "#616e75", fontSize: "0.82rem" }}>
          Re-sync reads Shopify afresh, verifies both directions of every product/blog link, repairs only exact matches, and reports anything ambiguous. It does not call Grok, DeepSeek, or any image model.
        </p>
        {syncProgress ? (
          <p style={{ margin: "8px 0 0", color: syncError ? "#a42e2b" : "#005ea5", fontSize: "0.82rem", fontWeight: 600 }}>
            {syncError || syncProgress}
          </p>
        ) : null}
      </s-section>

      {syncResult ? (
        <s-section heading="Latest Re-sync Report">
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: "10px" }}>
            {[
              ["Checked", syncResult.counts.products, "#202223"],
              ["Matched", syncResult.counts.matched, "#107c41"],
              ["Repaired", syncResult.counts.repaired, "#005ea5"],
              ["Missing", syncResult.counts.missing, "#a42e2b"],
              ["Mismatched", syncResult.counts.mismatch, "#704700"],
              ["Duplicates", syncResult.counts.duplicate, "#a42e2b"],
              ["Errors", syncResult.counts.error, "#a42e2b"],
              ["Orphan articles", syncResult.counts.orphans, "#704700"],
            ].map(([label, value, colour]) => (
              <div key={String(label)} style={{ border: "1px solid #e1e3e5", borderRadius: "8px", padding: "10px", background: "#fff" }}>
                <div style={{ fontSize: "0.72rem", color: "#616e75" }}>{label}</div>
                <div style={{ fontSize: "1.35rem", fontWeight: 700, color: String(colour) }}>{value}</div>
              </div>
            ))}
          </div>
          {syncResult.orphans.length > 0 ? (
            <div style={{ marginTop: "12px", padding: "10px 12px", borderRadius: "8px", background: "#fff8e5", border: "1px solid #f2c94c" }}>
              <strong>Articles needing manual review</strong>
              <ul style={{ margin: "6px 0 0", paddingLeft: "20px" }}>
                {syncResult.orphans.map(orphan => (
                  <li key={orphan.article_url} style={{ marginBottom: "4px" }}>
                    <a href={orphan.article_url} target="_blank" rel="noreferrer">{orphan.article_title || orphan.article_url}</a>
                    {`: ${orphan.reason}`}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </s-section>
      ) : null}

      {logs.length > 0 && (
        <s-section heading="Processing Logs">
          <div
            style={{
              background: "#1e1e1e",
              color: "#00ff00",
              padding: "12px",
              borderRadius: "8px",
              fontFamily: "monospace",
              maxHeight: "180px",
              overflowY: "auto",
              fontSize: "0.85rem",
              lineHeight: 1.4,
            }}
          >
            {logs.map((log, listIndex) => (
              <div key={listIndex}>{log}</div>
            ))}
          </div>
        </s-section>
      )}

      <s-section heading="Product Registry">
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
            <thead>
              <tr style={{ borderBottom: "2px solid #e1e3e5", textAlign: "left" }}>
                <th style={{ padding: "12px", fontWeight: 700 }}>Product</th>
                <th style={{ padding: "12px", fontWeight: 700 }}>Associated Blog / Guide</th>
                <th style={{ padding: "12px", fontWeight: 700 }}>Status</th>
                <th style={{ padding: "12px", fontWeight: 700, textAlign: "right" }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {products.map((p, pIndex) => {
                const state = productStatuses[p.id] || { status: "idle" };
                const finding = syncFindingsByHandle[p.handle];
                const findingColour = finding?.status === "matched" || finding?.status === "repaired"
                  ? "#107c41"
                  : finding?.status === "missing" || finding?.status === "duplicate" || finding?.status === "error"
                    ? "#a42e2b"
                    : "#704700";
                const generationBlockedByFinding = !!finding && finding.status !== "missing";
                return (
                  <tr
                    key={p.id}
                    style={{
                      borderBottom: "1px solid #f1f2f4",
                      background: pIndex % 2 === 0 ? "transparent" : "#f6f6f7",
                    }}
                  >
                    <td style={{ padding: "12px" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                        {p.imageUrl ? (
                          <img
                            src={p.imageUrl}
                            alt=""
                            style={{ width: "38px", height: "38px", borderRadius: "6px", objectFit: "cover", border: "1px solid #e1e3e5" }}
                          />
                        ) : (
                          <div style={{ width: "38px", height: "38px", borderRadius: "6px", background: "#eaebeb", border: "1px solid #e1e3e5" }} />
                        )}
                        <div>
                          <p style={{ margin: 0, fontWeight: "600" }}>{p.title}</p>
                          <p style={{ margin: "2px 0 0 0", fontSize: "0.75rem", color: "#616e75" }}>{p.handle}</p>
                        </div>
                      </div>
                    </td>
                    <td style={{ padding: "12px" }}>
                      {state.guideUrl ? (
                        <a
                          href={state.guideUrl}
                          target="_blank"
                          rel="noreferrer"
                          style={{ color: "#005ea5", textDecoration: "none", fontWeight: "500" }}
                        >
                          {state.guideTitle || "View Article"}
                        </a>
                      ) : (
                        <span style={{ color: "#616e75", fontStyle: "italic" }}>No blog found</span>
                      )}
                    </td>
                    <td style={{ padding: "12px" }}>
                      <div style={{ display: "grid", gap: "5px" }}>
                        {state.status === "generating" ? (
                          <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", color: "#005ea5", fontWeight: 600 }}>
                            <span className="spinner" style={{ display: "inline-block", width: "12px", height: "12px", border: "2px solid #5c6ac4", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 1s linear infinite" }} />
                            Generating...
                            {state.currentStage ? <span style={{ fontSize: "0.7rem", color: "#616e75", fontWeight: 500 }}>({state.currentStage})</span> : null}
                          </span>
                        ) : state.status === "success" ? (
                          <div style={{ display: "grid", gap: 4 }}>
                            <div>
                              <span style={{ color: "#107c41", fontWeight: 600, background: "#dff6dd", padding: "2px 8px", borderRadius: "12px", fontSize: "0.75rem" }}>
                                Attached
                              </span>
                              {state.imageCount != null ? <span style={{ marginLeft: 6, fontSize: "0.72rem", color: "#616e75" }}>{state.imageCount} images verified</span> : null}
                            </div>
                            {state.warnings?.map((warning, warningIndex) => (
                              <span key={warningIndex} style={{ fontSize: "0.7rem", color: "#704700" }}>Warning: {warning}</span>
                            ))}
                          </div>
                        ) : state.status === "failed" ? (
                          <div style={{ display: "grid", gap: "4px" }}>
                            <span style={{ color: "#a42e2b", fontWeight: 600, background: "#fde7e9", padding: "2px 8px", borderRadius: "12px", fontSize: "0.75rem", width: "max-content" }}>
                              Failed
                            </span>
                            {state.error && <span style={{ fontSize: "0.7rem", color: "#a42e2b" }}>{state.error}</span>}
                          </div>
                        ) : (
                          <span style={{ color: "#616e75", background: "#f1f2f4", padding: "2px 8px", borderRadius: "12px", fontSize: "0.75rem", width: "max-content" }}>
                            Missing
                          </span>
                        )}
                        {finding ? (
                          <div style={{ fontSize: "0.7rem", color: findingColour }}>
                            <strong>Re-sync: {finding.status}</strong>
                            {finding.repairs.map((repair, repairIndex) => <div key={`repair-${repairIndex}`}>Fixed: {repair}</div>)}
                            {finding.issues.map((issue, issueIndex) => <div key={`issue-${issueIndex}`}>{issue}</div>)}
                            {finding.unresolved_issues.map((issue, issueIndex) => <div key={`unresolved-${issueIndex}`}>Manual review: {issue}</div>)}
                            {finding.errors.map((error, errorIndex) => <div key={`error-${errorIndex}`}>Error: {error}</div>)}
                          </div>
                        ) : null}
                      </div>
                    </td>
                    <td style={{ padding: "12px", textAlign: "right" }}>
                      <button
                        onClick={() => generateProductBlog(p)}
                        disabled={state.status === "generating" || state.status === "success" || isBulkRunning || isSyncRunning || generationBlockedByFinding || !backendConfigured}
                        style={{
                          padding: "6px 12px",
                          fontSize: "0.75rem",
                          background: state.status === "generating" || state.status === "success" || isBulkRunning || isSyncRunning || generationBlockedByFinding ? "#eaebeb" : "#ffffff",
                          color: state.status === "generating" || state.status === "success" || isBulkRunning || isSyncRunning || generationBlockedByFinding ? "#8c9196" : "#202223",
                          border: "1px solid #8c9196",
                          borderRadius: "6px",
                          fontWeight: 600,
                          cursor: state.status === "generating" || state.status === "success" || isBulkRunning || isSyncRunning || generationBlockedByFinding ? "not-allowed" : "pointer",
                        }}
                      >
                        Generate Blog
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <style dangerouslySetInnerHTML={{ __html: `
          @keyframes spin {
            to { transform: rotate(360deg); }
          }
        `}} />
      </s-section>
    </s-page>
  );
}
