import React, { useState, useEffect } from "react";
import type { LoaderFunctionArgs } from "react-router";
import { useLoaderData, useNavigate } from "react-router";
import { authenticate } from "../shopify.server";
import { loadShopifyStudioContext, requireShopifySession } from "../lib/blog-studio.server";

const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

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
  const [context, landingPagesData] = await Promise.all([
    loadShopifyStudioContext(session),
    BACKEND_KEY
      ? backendFetch("/api/landing-pages/products", { cache: "no-store" }).catch(() => null)
      : Promise.resolve(null),
  ]);
  const storefrontDomain = context.storefrontDomain;
  const products = context.products || [];

  return {
    backendConfigured: !!BACKEND_KEY,
    products,
    storefrontDomain,
    generatedData: (landingPagesData?.products || []) as any[],
  };
};

export default function LandingPagesIndex() {
  const { backendConfigured, products, generatedData } = useLoaderData<typeof loader>();
  const navigate = useNavigate();

  // Merge shopify products with backend generated data
  const mergedProducts = products.map(p => {
    const generated = generatedData.find(g => g.handle === p.handle);
    // In a real scenario we'd check if the landing page actually exists in Shopify
    // For now we assume if it's generated and has concepts/images, it might be published or ready
    // We'll rely on the generated JSON status
    return {
      ...p,
      hasGeneratedAssets: !!generated,
      conceptsGenerated: generated?.concepts_generated || 0,
      landingPageUrl: generated?.landing_page?.url || "",
    };
  });

  return (
    <s-page heading="Marketing Landing Pages">
      {!backendConfigured ? (
        <s-section>
          <div style={{ color: "#991b1b", background: "rgba(239,68,68,0.08)", padding: "14px", borderRadius: "12px", border: "1px solid rgba(239,68,68,0.3)" }}>
            Backend API Key not configured.
          </div>
        </s-section>
      ) : null}

      <s-section heading="Product Registry">
        <s-paragraph>
          Select a product to generate marketing concepts, social posts, and a landing page.
        </s-paragraph>

        <div style={{ overflowX: "auto", marginTop: "16px" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
            <thead>
              <tr style={{ borderBottom: "2px solid #e1e3e5", textAlign: "left" }}>
                <th style={{ padding: "12px", fontWeight: 700 }}>Product</th>
                <th style={{ padding: "12px", fontWeight: 700 }}>Landing Page Status</th>
                <th style={{ padding: "12px", fontWeight: 700, textAlign: "right" }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {mergedProducts.map((p, pIndex) => (
                <tr key={p.id} style={{ borderBottom: "1px solid #f1f2f4", background: pIndex % 2 === 0 ? "transparent" : "#f6f6f7" }}>
                  <td style={{ padding: "12px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                      {p.imageUrl ? (
                        <img src={p.imageUrl} alt="" style={{ width: "38px", height: "38px", borderRadius: "6px", objectFit: "cover", border: "1px solid #e1e3e5" }} />
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
                    {p.landingPageUrl ? (
                       <a
                         href={p.landingPageUrl}
                         target="_blank"
                         rel="noreferrer"
                         title={p.landingPageUrl}
                         style={{ display: "inline-block", color: "#107c41", fontWeight: 600, background: "#dff6dd", padding: "4px 9px", borderRadius: "12px", fontSize: "0.75rem", textDecoration: "underline" }}
                       >
                         Published — View landing page ↗
                       </a>
                    ) : p.hasGeneratedAssets ? (
                       <span style={{ color: "#8a6116", fontWeight: 600, background: "#fff5d6", padding: "2px 8px", borderRadius: "12px", fontSize: "0.75rem" }}>
                         Ready to publish ({p.conceptsGenerated} concepts)
                       </span>
                    ) : (
                       <span style={{ color: "#616e75", background: "#f1f2f4", padding: "2px 8px", borderRadius: "12px", fontSize: "0.75rem" }}>
                         Not Started
                       </span>
                    )}
                  </td>
                  <td style={{ padding: "12px", textAlign: "right" }}>
                    <button
                      onClick={() => navigate(`/app/landing-pages-wizard/${p.handle}`)}
                      style={{
                        padding: "6px 12px",
                        fontSize: "0.75rem",
                        background: "#ffffff",
                        color: "#202223",
                        border: "1px solid #8c9196",
                        borderRadius: "6px",
                        fontWeight: 600,
                        cursor: "pointer",
                      }}
                    >
                      {p.hasGeneratedAssets ? "Edit / Publish" : "Generate Page"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </s-section>
    </s-page>
  );
}
