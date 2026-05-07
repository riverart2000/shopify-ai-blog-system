import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, useActionData, useLoaderData, useNavigation } from "react-router";

import {
  generateAiBlogDraft,
  isBlogBackendConfigured,
  loadShopifyStudioContext,
  publishDraftToShopify,
  requireShopifySession,
  type GeneratedBlogDraft,
} from "../lib/blog-studio.server";
import { loadStudioSettings } from "../lib/studio-settings.server";
import { authenticate } from "../shopify.server";

type FormInputs = {
  prompt: string;
  relatedProductId: string;
};

type ActionData = {
  ok: boolean;
  message?: string;
  error?: string;
  inputs?: FormInputs;
  draft?: GeneratedBlogDraft;
  publishResult?: Awaited<ReturnType<typeof publishDraftToShopify>>;
  defaultBlogHandle?: string;
};

function buildStatusBox(message: string, tone: "success" | "error") {
  return (
    <div
      style={{
        borderRadius: "16px",
        padding: "16px 18px",
        background: tone === "success" ? "#ecfdf5" : "#fef2f2",
        color: tone === "success" ? "#065f46" : "#991b1b",
        border: `1px solid ${tone === "success" ? "#a7f3d0" : "#fecaca"}`,
      }}
    >
      {message}
    </div>
  );
}

function buildDefaults() {
  return {
    prompt: "",
    relatedProductId: "",
  } satisfies FormInputs;
}

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const auth = await authenticate.admin(request);
  const session = requireShopifySession((auth as { session?: unknown }).session);
  const context = await loadShopifyStudioContext(session);
  const settings = await loadStudioSettings(session.shop, {
    shopName: context.shopName,
    defaultBlogHandle: context.blogs[0]?.handle || "news",
  });

  return {
    ...context,
    defaults: buildDefaults(),
    backendConfigured: isBlogBackendConfigured(),
    defaultBlogHandle: settings.defaultBlogHandle,
    defaultAuthorName: settings.defaultAuthorName,
    promptSuffix: settings.promptEndingConfig.selectedPreset.text,
    businessName: settings.businessName || context.shopName,
    brandContext: settings.brandContext,
    targetAudience: settings.targetAudience,
    ctaGuidance: settings.ctaGuidance,
    heroImagePromptStyle: settings.heroImagePromptStyle,
  };
};

export const action = async ({ request }: ActionFunctionArgs) => {
  const auth = await authenticate.admin(request);
  const session = requireShopifySession((auth as { session?: unknown }).session);
  const context = await loadShopifyStudioContext(session);
  const settings = await loadStudioSettings(session.shop, {
    shopName: context.shopName,
    defaultBlogHandle: context.blogs[0]?.handle || "news",
  });
  const formData = await request.formData();
  const inputs = {
    prompt: String(formData.get("prompt") || ""),
    relatedProductId: String(formData.get("relatedProductId") || ""),
  } satisfies FormInputs;

  let draft: GeneratedBlogDraft;
  let generatedAssets;
  try {
    const generated = await generateAiBlogDraft({
      topic: "",
      category: "Blog",
      tone: "Expert, useful, and ready to publish",
      authorName: settings.defaultAuthorName,
      brandContext: settings.brandContext || `${context.shopName} publishes practical Shopify blog content for customers and search visitors.`,
      targetAudience: settings.targetAudience || `${context.shopName} customers and people searching for useful advice before they buy.`,
      ctaGuidance: settings.ctaGuidance || `End with a soft, relevant next step that points back to ${settings.businessName || context.shopName}.`,
      promptEnding: [inputs.prompt.trim(), settings.promptEndingConfig.selectedPreset.text.trim()].filter(Boolean).join("\n\n"),
      shopName: settings.businessName || context.shopName,
    });
    draft = generated.draft;
    generatedAssets = generated.generatedAssets;
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "AI blog generation failed.",
      inputs,
      defaultBlogHandle: settings.defaultBlogHandle,
    } satisfies ActionData;
  }

  try {
    const publishResult = await publishDraftToShopify(session, {
      blogHandle: settings.defaultBlogHandle,
      publishMode: "publish",
      draft,
      baseUrl: new URL(request.url).origin,
      businessName: settings.businessName || context.shopName,
      brandContext: settings.brandContext,
      heroImagePromptStyle: settings.heroImagePromptStyle,
      generatedAssets,
      relatedProductId: inputs.relatedProductId || undefined,
    });

    return {
      ok: true,
      message: "The Python backend generated the blog and the app posted it to Shopify.",
      inputs,
      draft,
      publishResult,
      defaultBlogHandle: settings.defaultBlogHandle,
    } satisfies ActionData;
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "The blog was generated but could not be posted to Shopify.",
      inputs,
      draft,
      defaultBlogHandle: settings.defaultBlogHandle,
    } satisfies ActionData;
  }
};

export default function AppIndexRoute() {
  const data = useLoaderData<typeof loader>();
  const actionData = useActionData<typeof action>() as ActionData | undefined;
  const navigation = useNavigation();
  const promptValue = actionData?.inputs?.prompt ?? data.defaults.prompt;
  const relatedProductIdValue = actionData?.inputs?.relatedProductId ?? data.defaults.relatedProductId;
  const isSubmitting = navigation.state !== "idle";
  const publishResult = actionData?.publishResult;
  const draft = actionData?.draft;
  const missingProductScopes = data.missingScopes.filter((scope) => ["read_products", "write_products"].includes(scope));

  return (
    <s-page heading="AI Blog Publisher">
      <s-section heading="Simple flow">
        <s-paragraph>
          Enter one prompt and submit. The app sends it to the configured Python generation backend and posts the generated article to your default Shopify blog.
        </s-paragraph>
        <div style={{ display: "grid", gap: "12px", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
          <div><strong>Store</strong><div>{data.myshopifyDomain}</div></div>
          <div><strong>Default blog handle</strong><div>{data.defaultBlogHandle}</div></div>
          <div><strong>Python backend</strong><div>{data.backendConfigured ? "Configured" : "Missing backend API key"}</div></div>
        </div>
      </s-section>

      {actionData?.message ? buildStatusBox(actionData.message, "success") : null}
      {actionData?.error ? buildStatusBox(actionData.error, "error") : null}

      <s-section heading="Prompt">
        <Form method="post">
          <div style={{ display: "grid", gap: "16px" }}>
            <label style={{ display: "grid", gap: "8px" }}>
              <span style={{ fontWeight: 600 }}>Prompt</span>
              <textarea
                name="prompt"
                defaultValue={promptValue}
                required
                rows={10}
                placeholder="Write a Shopify blog post about how meditation corners can improve sleep quality for wellness shoppers, and end with a soft CTA to explore our products."
                style={{
                  borderRadius: "14px",
                  border: "1px solid #d1d5db",
                  padding: "14px 16px",
                  font: "inherit",
                  resize: "vertical",
                }}
              />
            </label>

            <label style={{ display: "grid", gap: "8px" }}>
              <span style={{ fontWeight: 600 }}>Related product page</span>
              <select
                name="relatedProductId"
                defaultValue={relatedProductIdValue}
                disabled={data.products.length === 0}
                style={{
                  borderRadius: "14px",
                  border: "1px solid #d1d5db",
                  padding: "14px 16px",
                  font: "inherit",
                  background: data.products.length === 0 ? "#f9fafb" : "white",
                }}
              >
                <option value="">
                  {data.products.length > 0 ? "Don’t link this post to a product page" : "No products available for linking"}
                </option>
                {data.products.map((product) => (
                  <option key={product.id} value={product.id}>
                    {product.title} ({product.handle})
                  </option>
                ))}
              </select>
              <span style={{ color: "#4b5563", fontSize: "0.92rem" }}>
                When selected, the app saves this blog back onto the product through product metafields so the storefront block can render it.
              </span>
              {missingProductScopes.length > 0 ? (
                <span style={{ color: "#991b1b", fontSize: "0.92rem" }}>
                  Product linking needs updated app scopes: {missingProductScopes.join(", ")}. Reinstall the app or approve the scope update before using this field.
                </span>
              ) : null}
            </label>

            <button
              type="submit"
              style={{
                borderRadius: "999px",
                border: 0,
                background: "#111827",
                color: "white",
                padding: "12px 18px",
                fontWeight: 700,
                cursor: "pointer",
                justifySelf: "start",
              }}
            >
              {isSubmitting ? "Generating and posting..." : "Submit"}
            </button>
          </div>
        </Form>
      </s-section>

      {draft ? (
        <s-section heading="Latest generated blog">
          <div style={{ display: "grid", gap: "12px" }}>
            <div><strong>Title</strong><div>{draft.title}</div></div>
            <div><strong>Slug</strong><div>{draft.slug}</div></div>
            <div><strong>Excerpt</strong><div>{draft.excerpt}</div></div>
            <div><strong>Meta description</strong><div>{draft.metaDescription}</div></div>
          </div>
        </s-section>
      ) : null}

      {publishResult ? (
        <s-section heading="Shopify result">
          <div style={{ display: "grid", gap: "10px" }}>
            <div><strong>Status</strong><div>Published live</div></div>
            <div><strong>Blog handle</strong><div>{publishResult.blogHandle}</div></div>
            <div><strong>Article handle</strong><div>{publishResult.articleHandle}</div></div>
            {publishResult.articleUrl ? (
              <div>
                <strong>Storefront URL</strong>
                <div><a href={publishResult.articleUrl} target="_blank" rel="noreferrer">{publishResult.articleUrl}</a></div>
              </div>
            ) : null}
            {publishResult.relatedProductLink ? (
              <div>
                <strong>Product page link</strong>
                <div>
                  {publishResult.relatedProductLink.status === "linked"
                    ? `Linked to ${publishResult.relatedProductLink.productTitle || publishResult.relatedProductLink.productHandle}.`
                    : publishResult.relatedProductLink.error || "The product page link could not be saved."}
                </div>
              </div>
            ) : null}
            {publishResult.productGuideBlockEditorUrl ? (
              <div>
                <strong>Theme editor</strong>
                <div>
                  <a href={publishResult.productGuideBlockEditorUrl} target="_blank" rel="noreferrer">
                    Add the related guide block to the product template
                  </a>
                </div>
              </div>
            ) : null}
            {publishResult.shopifyFilesError ? (
              <div style={{ color: "#92400e" }}>{publishResult.shopifyFilesError}</div>
            ) : null}
          </div>
        </s-section>
      ) : null}
    </s-page>
  );
}