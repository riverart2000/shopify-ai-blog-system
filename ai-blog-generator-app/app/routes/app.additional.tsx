import type { LoaderFunctionArgs } from "react-router";
import { useLoaderData } from "react-router";

import {
  loadShopifyStudioContext,
  requireShopifySession,
} from "../lib/blog-studio.server";
import { authenticate } from "../shopify.server";

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const auth = await authenticate.admin(request);
  const session = requireShopifySession((auth as { session?: unknown }).session);
  const context = await loadShopifyStudioContext(session);

  return {
    ...context,
    envStatus: {
      shopifyAppUrl: Boolean(process.env.SHOPIFY_REACT_APP_URL),
      deepseek: Boolean(process.env.DEEPSEEK_API_KEY),
      grok: Boolean(process.env.GROK_API_KEY || process.env.XAI_API_KEY),
    },
  };
};

function StatusRow(props: { label: string; value: string; tone?: "ok" | "warn" }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        gap: "16px",
        padding: "12px 0",
        borderBottom: "1px solid #e5e7eb",
      }}
    >
      <strong>{props.label}</strong>
      <span style={{ color: props.tone === "warn" ? "#92400e" : "#065f46", textAlign: "right" }}>
        {props.value}
      </span>
    </div>
  );
}

export default function AdditionalPage() {
  const data = useLoaderData<typeof loader>();

  return (
    <s-page heading="Setup & Limits">
      <s-section heading="Current app readiness">
        <div style={{ display: "grid", gap: "12px" }}>
          <StatusRow label="Store" value={data.myshopifyDomain} />
          <StatusRow label="Storefront domain" value={data.storefrontDomain} />
          <StatusRow label="Available blog handles" value={data.blogs.map((entry) => entry.handle).join(", ") || "None returned"} />
          <StatusRow label="Installed scopes" value={data.scopes.join(", ") || "No scopes returned"} tone={data.missingScopes.length > 0 ? "warn" : "ok"} />
          <StatusRow label="Missing recommended scopes" value={data.missingScopes.join(", ") || "None"} tone={data.missingScopes.length > 0 ? "warn" : "ok"} />
          <StatusRow label="SHOPIFY_REACT_APP_URL" value={data.envStatus.shopifyAppUrl ? "Present" : "Missing"} tone={data.envStatus.shopifyAppUrl ? "ok" : "warn"} />
          <StatusRow label="DeepSeek key" value={data.envStatus.deepseek ? "Present" : "Missing"} tone={data.envStatus.deepseek ? "ok" : "warn"} />
          <StatusRow label="Grok key" value={data.envStatus.grok ? "Present" : "Missing"} tone={data.envStatus.grok ? "ok" : "warn"} />
        </div>
      </s-section>

      <s-section heading="Can this run solely on Shopify?">
        <s-paragraph>
          No. The embedded UI can live inside Shopify Admin, but the actual app
          still runs on your infrastructure. AI generation, secret management,
          server-side prompt assembly, and Shopify publishing calls happen on
          your app server, not on Shopify&apos;s servers.
        </s-paragraph>
        <s-paragraph>
          Shopify-hosted runtimes are limited to specific extension and function
          surfaces. They are not a good fit for long-running AI orchestration or
          multi-provider blog generation workflows.
        </s-paragraph>
      </s-section>

      <s-section heading="Deployment checklist">
        <s-unordered-list>
          <s-list-item>Set <code>application_url</code> in <code>shopify.app.toml</code> to the real external host for this app.</s-list-item>
          <s-list-item>Add the matching <code>/auth/callback</code> redirect URL in the Shopify app config.</s-list-item>
          <s-list-item>Provide at least one AI key so draft generation can run server-side.</s-list-item>
          <s-list-item>Reinstall or update the app if the content/file scopes are missing.</s-list-item>
        </s-unordered-list>
      </s-section>
    </s-page>
  );
}
