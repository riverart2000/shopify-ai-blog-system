import type { LoaderFunctionArgs } from "react-router";
import { useLoaderData } from "react-router";
import { BACKEND_KEY, BACKEND_URL, loadBackendStoreContext, withStoreId } from "../lib/backend-store.server";
import { authenticate } from "../shopify.server";

type Generation = {
  id: number;
  store_id: string;
  store_name: string;
  blog_handle: string;
  title: string;
  summary: string;
  status: string;
  article_url: string | null;
  created_at: number;
  keywords: string[];
};

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { selectedStore, storeId } = await loadBackendStoreContext(request);

  let generations: Generation[] = [];
  let error: string | null = null;

  if (!BACKEND_KEY) {
    error = "AI_BLOG_BACKEND_API_KEY is not configured.";
  } else {
    try {
      const res = await fetch(`${BACKEND_URL}${withStoreId("/api/history?limit=50", storeId)}`, {
        headers: { "x-api-key": BACKEND_KEY },
      });
      if (!res.ok) {
        error = `Backend returned ${res.status}: ${await res.text()}`;
      } else {
        const data = await res.json() as { generations: Generation[] };
        generations = data.generations ?? [];
      }
    } catch (e) {
      error = e instanceof Error ? e.message : "Failed to reach backend";
    }
  }

  return { generations, error, storeName: selectedStore?.name || "" };
};

function formatDate(epochSeconds: number) {
  return new Date(epochSeconds * 1000).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function HistoryRoute() {
  const { generations, error, storeName } = useLoaderData<typeof loader>();

  return (
    <s-page heading="Blog Generation History">
      {error ? (
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
            {error}
          </div>
        </s-section>
      ) : null}

      <s-section heading={storeName ? `${storeName} recent generations (${generations.length})` : `${generations.length} recent generations`}>
        {generations.length === 0 && !error ? (
          <s-paragraph>No blog generations recorded yet.</s-paragraph>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: "0.875rem",
              }}
            >
              <thead>
                <tr style={{ borderBottom: "2px solid #e5e7eb", textAlign: "left" }}>
                  <th style={{ padding: "10px 12px", fontWeight: 700 }}>#</th>
                  <th style={{ padding: "10px 12px", fontWeight: 700 }}>Title</th>
                  <th style={{ padding: "10px 12px", fontWeight: 700 }}>Store</th>
                  <th style={{ padding: "10px 12px", fontWeight: 700 }}>Blog</th>
                  <th style={{ padding: "10px 12px", fontWeight: 700 }}>Status</th>
                  <th style={{ padding: "10px 12px", fontWeight: 700 }}>Generated</th>
                </tr>
              </thead>
              <tbody>
                {generations.map((g, i) => (
                  <tr
                    key={g.id}
                    style={{
                      borderBottom: "1px solid #f3f4f6",
                      background: i % 2 === 0 ? "transparent" : "#f9fafb",
                    }}
                  >
                    <td style={{ padding: "10px 12px", color: "#6b7280" }}>{g.id}</td>
                    <td style={{ padding: "10px 12px", maxWidth: "320px" }}>
                      {g.article_url ? (
                        <a
                          href={g.article_url}
                          target="_blank"
                          rel="noreferrer"
                          style={{ color: "#2563eb", textDecoration: "none", fontWeight: 500 }}
                        >
                          {g.title}
                        </a>
                      ) : (
                        <span style={{ fontWeight: 500 }}>{g.title}</span>
                      )}
                      {g.summary ? (
                        <div style={{ fontSize: "0.78rem", color: "#6b7280", marginTop: 3, lineHeight: 1.4 }}>
                          {g.summary.slice(0, 120)}{g.summary.length > 120 ? "…" : ""}
                        </div>
                      ) : null}
                    </td>
                    <td style={{ padding: "10px 12px", color: "#374151" }}>{g.store_name}</td>
                    <td style={{ padding: "10px 12px", color: "#374151" }}>{g.blog_handle}</td>
                    <td style={{ padding: "10px 12px" }}>
                      <span
                        style={{
                          display: "inline-block",
                          borderRadius: "999px",
                          padding: "2px 10px",
                          fontSize: "0.75rem",
                          fontWeight: 600,
                          background: g.status === "published" ? "#dcfce7" : "#fef9c3",
                          color: g.status === "published" ? "#166534" : "#92400e",
                        }}
                      >
                        {g.status}
                      </span>
                    </td>
                    <td style={{ padding: "10px 12px", color: "#6b7280", whiteSpace: "nowrap" }}>
                      {formatDate(g.created_at)}
                    </td>
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
