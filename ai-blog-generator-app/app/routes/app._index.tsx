import React, { useState, useEffect } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, useActionData, useLoaderData, useNavigation } from "react-router";
import { authenticate } from "../shopify.server";
import { loadShopifyStudioContext, requireShopifySession } from "../lib/blog-studio.server";

const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

type Prompt = { id: string; name: string };
type Model = { id: string; name: string; provider: string };
type BlogHandle = { handle: string; title?: string };
type Product = { id: string; handle: string; title: string };

type QualityCheck = { name: string; status: "pass" | "warn" | "fail"; message: string };
type QualityReport = {
  score: number;
  verdict: "ready" | "review" | "blocked";
  checks: QualityCheck[];
  publish_blocked: boolean;
};

type DraftData = {
  store_id: string;
  prompt_id: string;
  prompt_text: string;
  blog_handle: string;
  author: string;
  title: string;
  summary: string;
  content: string;
  keywords: string[];
  hashtags: string[];
  image_urls: string[];
  image_types: string[];
  generated_by: string;
  product_url: string;
  product_title: string;
  quality_report: QualityReport;
  title_pool_id: number;
};

type ActionData =
  | { step: "preview"; ok: true; draft: DraftData }
  | { step: "result"; ok: true; article_url: string; message: string }
  | { step: "error"; ok: false; error: string };

async function backendFetch(path: string, opts: RequestInit = {}) {
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

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const auth = await authenticate.admin(request);
  const session = requireShopifySession((auth as { session?: unknown }).session);
  const [context, initData] = await Promise.all([
    loadShopifyStudioContext(session),
    BACKEND_KEY ? backendFetch("/api/init").catch(() => null) : Promise.resolve(null),
  ]);
  const storefrontDomain = context.storefrontDomain;
  const products: Product[] = (context.products || []).map((p: { id: string; handle: string; title?: string }) => ({
    id: p.id,
    handle: p.handle,
    title: p.title || p.handle,
  }));
  return {
    backendConfigured: !!BACKEND_KEY,
    storefrontDomain,
    products,
    prompts: (initData?.prompts ?? []) as Prompt[],
    models: (initData?.models ?? []) as Model[],
    blogs: (initData?.blogs ?? []) as BlogHandle[],
    defaultPromptId: (initData?.default_prompt_id as string) || "",
    storeName: (initData?.store as { name?: string })?.name || context.shopName,
    defaultBlogHandle: (initData?.store as { default_blog_handle?: string })?.default_blog_handle || "news",
    defaultAuthor: (initData?.store as { default_author?: string })?.default_author || "",
  };
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  if (!BACKEND_KEY) return { step: "error", ok: false, error: "AI_BLOG_BACKEND_API_KEY is not configured." } satisfies ActionData;

  const form = await request.formData();
  const intent = String(form.get("intent") || "");

  if (intent === "generate") {
    try {
      const data = await backendFetch("/api/generate/draft", {
        method: "POST",
        body: JSON.stringify({
          store_id: form.get("store_id") || "",
          prompt_id: form.get("prompt_id") || "",
          custom_prompt: form.get("custom_prompt") || "",
          blog_handle: form.get("blog_handle") || "",
          author: form.get("author") || "",
          model_id: form.get("model_id") || "",
          product_url: form.get("product_url") || "",
        }),
      });
      return { step: "preview", ok: true, draft: data as unknown as DraftData } satisfies ActionData;
    } catch (e) {
      return { step: "error", ok: false, error: e instanceof Error ? e.message : "Generation failed" } satisfies ActionData;
    }
  }

  if (intent === "publish") {
    try {
      const data = await backendFetch("/api/publish/article", {
        method: "POST",
        body: JSON.stringify({
          store_id: form.get("store_id") || "",
          prompt_id: form.get("prompt_id") || "",
          prompt_text: form.get("prompt_text") || "",
          blog_handle: form.get("blog_handle") || "news",
          author: form.get("author") || "",
          title: form.get("title") || "",
          summary: form.get("summary") || "",
          content: form.get("content") || "",
          keywords: JSON.parse(String(form.get("keywords_json") || "[]")),
          hashtags: JSON.parse(String(form.get("hashtags_json") || "[]")),
          image_urls: JSON.parse(String(form.get("image_urls_json") || "[]")),
          image_types: JSON.parse(String(form.get("image_types_json") || "[]")),
          selected_image_index: parseInt(String(form.get("selected_image_index") || "0"), 10),
          product_url: form.get("product_url") || "",
          product_title: form.get("product_title") || "",
          title_pool_id: parseInt(String(form.get("title_pool_id") || "0"), 10),
        }),
      });
      return { step: "result", ok: true, article_url: data.article_url as string, message: data.message as string } satisfies ActionData;
    } catch (e) {
      return { step: "error", ok: false, error: e instanceof Error ? e.message : "Publish failed" } satisfies ActionData;
    }
  }

  return { step: "error", ok: false, error: "Unknown intent" } satisfies ActionData;
};

// --- Shared styles ---
const inp: React.CSSProperties = { borderRadius: "10px", border: "1px solid #d1d5db", padding: "9px 12px", font: "inherit", width: "100%", boxSizing: "border-box" };
const lbl: React.CSSProperties = { display: "grid", gap: "5px", fontSize: "0.875rem" };

function Alert({ message, tone }: { message: string; tone: "success" | "error" | "warn" }) {
  const bg = tone === "success" ? { bg: "#ecfdf5", border: "#a7f3d0", color: "#065f46" } : tone === "error" ? { bg: "rgba(239,68,68,0.08)", border: "rgba(239,68,68,0.3)", color: "#991b1b" } : { bg: "#fef3c7", border: "#fde68a", color: "#92400e" };
  return (
    <div style={{ borderRadius: "12px", padding: "12px 16px", background: bg.bg, border: `1px solid ${bg.border}`, color: bg.color, fontSize: "0.875rem" }}>
      {message}
    </div>
  );
}

// --- Step 1: Generate form ---
function GenerateForm({ data, prevError }: { data: ReturnType<typeof useLoaderData<typeof loader>>; prevError?: string }) {
  const navigation = useNavigation();
  const submitting = navigation.state !== "idle";
  const [showCustom, setShowCustom] = useState(false);

  return (
    <s-page heading="Blog Generator">
      {!data.backendConfigured ? (
        <s-section>
          <Alert message="AI_BLOG_BACKEND_API_KEY is not configured. Add it to your .env file." tone="error" />
        </s-section>
      ) : null}
      {prevError ? (
        <s-section>
          <Alert message={prevError} tone="error" />
        </s-section>
      ) : null}

      <s-section heading="Generate a new blog post">
        <Form method="post">
          <input type="hidden" name="intent" value="generate" />
          <div style={{ display: "grid", gap: "16px" }}>

            <label style={lbl}>
              <span>Prompt <span style={{ color: "#dc2626" }}>*</span></span>
              {data.prompts.length > 0 ? (
                <select name="prompt_id" defaultValue={data.defaultPromptId} style={inp} onChange={e => setShowCustom(e.target.value === "custom")}>
                  {data.prompts.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                  <option value="custom">Custom prompt…</option>
                </select>
              ) : (
                <>
                  <input type="hidden" name="prompt_id" value="custom" />
                  {!showCustom && <span style={{ color: "#6b7280", fontSize: "0.8rem" }}>No prompts configured — enter a custom prompt below, or add prompts on the <a href="/app/prompts">Prompts page</a>.</span>}
                </>
              )}
            </label>

            {(showCustom || data.prompts.length === 0) && (
              <label style={lbl}>
                <span>{data.prompts.length > 0 ? "Custom prompt text (replaces selected prompt)" : "Prompt text"} <span style={{ color: "#dc2626" }}>*</span></span>
                <textarea name="custom_prompt" rows={6} required={data.prompts.length === 0} style={{ ...inp, resize: "vertical" }} placeholder="Write a 700-word wellness blog about the benefits of magnesium for sleep…" />
              </label>
            )}
            {!showCustom && data.prompts.length > 0 && (
              <label style={lbl}>
                <span>Additional instructions (optional)</span>
                <textarea name="custom_prompt" rows={3} style={{ ...inp, resize: "vertical" }} placeholder="Focus on sleep tracking devices, mention our spring sale…" />
              </label>
            )}

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
              <label style={lbl}>
                <span>Blog handle</span>
                <select name="blog_handle" defaultValue={data.defaultBlogHandle} style={inp}>
                  {data.blogs.length > 0
                    ? data.blogs.map((b: BlogHandle) => <option key={b.handle} value={b.handle}>{b.handle}{b.title ? ` (${b.title})` : ""}</option>)
                    : <option value={data.defaultBlogHandle}>{data.defaultBlogHandle}</option>
                  }
                </select>
              </label>
              <label style={lbl}>
                <span>Author</span>
                <input name="author" defaultValue={data.defaultAuthor} style={inp} placeholder="Store Team" />
              </label>
            </div>

            {data.models.length > 0 && (
              <label style={lbl}>
                <span>AI model</span>
                <select name="model_id" style={inp}>
                  <option value="">Auto (highest priority active model)</option>
                  {data.models.map(m => <option key={m.id} value={m.id}>{m.name} ({m.provider})</option>)}
                </select>
              </label>
            )}

            {data.products.length > 0 && (
              <label style={lbl}>
                <span>Related product (optional)</span>
                <select name="product_url" style={inp}>
                  <option value="">No product — write a general blog post</option>
                  {data.products.map((p: Product) => (
                    <option key={p.id} value={`https://${data.storefrontDomain}/products/${p.handle}`}>
                      {p.title}
                    </option>
                  ))}
                </select>
                <span style={{ color: "#6b7280", fontSize: "0.78rem" }}>When selected, the blog post will be written specifically about this product.</span>
              </label>
            )}

            <button type="submit" disabled={submitting || !data.backendConfigured} style={{ borderRadius: "999px", border: 0, background: "#111827", color: "white", padding: "12px 24px", fontWeight: 700, cursor: "pointer", justifySelf: "start", opacity: (submitting || !data.backendConfigured) ? 0.6 : 1 }}>
              {submitting ? "Generating… this takes 30–90 seconds" : "Generate blog post"}
            </button>
          </div>
        </Form>
      </s-section>
    </s-page>
  );
}

// --- Step 2: Preview ---
function verdictColor(v: string) {
  if (v === "ready") return { bg: "#ecfdf5", color: "#065f46", border: "#a7f3d0" };
  if (v === "review") return { bg: "#fef3c7", color: "#92400e", border: "#fde68a" };
  return { bg: "rgba(239,68,68,0.08)", color: "#991b1b", border: "rgba(239,68,68,0.3)" };
}
function checkIcon(s: string) { return s === "pass" ? "✓" : s === "warn" ? "⚠" : "✗"; }
function checkColor(s: string) { return s === "pass" ? "#059669" : s === "warn" ? "#d97706" : "#dc2626"; }

function PreviewStep({ draft, onBack }: { draft: DraftData; onBack: () => void }) {
  const navigation = useNavigation();
  const submitting = navigation.state !== "idle";
  const [title, setTitle] = useState(draft.title);
  const [summary, setSummary] = useState(draft.summary);
  const [content, setContent] = useState(draft.content);
  const [selectedImage, setSelectedImage] = useState(0);
  const qr = draft.quality_report;
  const dc = verdictColor(qr.verdict);

  return (
    <s-page heading="Preview & Edit">
      <s-section>
        <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
          <button type="button" onClick={onBack} style={{ borderRadius: "999px", border: "1px solid #d1d5db", background: "white", padding: "8px 18px", cursor: "pointer" }}>
            ← Start over
          </button>
          <div style={{ ...dc, borderRadius: "999px", border: `1px solid ${dc.border}`, padding: "8px 16px", fontSize: "0.85rem", fontWeight: 600 }}>
            Quality score: {qr.score}/100 — {qr.verdict.toUpperCase()}
          </div>
          {draft.generated_by ? <span style={{ padding: "8px 0", fontSize: "0.8rem", color: "#6b7280" }}>Generated by {draft.generated_by}</span> : null}
        </div>
      </s-section>

      {qr.checks.filter(c => c.status !== "pass").length > 0 && (
        <s-section heading="Quality checks">
          <div style={{ display: "grid", gap: "6px" }}>
            {qr.checks.filter(c => c.status !== "pass").map((c, i) => (
              <div key={i} style={{ fontSize: "0.82rem", display: "flex", gap: "6px", alignItems: "baseline" }}>
                <span style={{ color: checkColor(c.status), fontWeight: 700 }}>{checkIcon(c.status)}</span>
                <span><strong>{c.name}:</strong> {c.message}</span>
              </div>
            ))}
          </div>
        </s-section>
      )}

      {draft.image_urls.length > 0 && (
        <s-section heading={`Images (${draft.image_urls.length})`}>
          <div style={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
            {draft.image_urls.map((url, i) => (
              <label key={i} style={{ cursor: "pointer", border: `2px solid ${selectedImage === i ? "#111827" : "#e5e7eb"}`, borderRadius: "12px", overflow: "hidden", display: "block" }}>
                <input type="radio" name="_img_preview" value={i} checked={selectedImage === i} onChange={() => setSelectedImage(i)} style={{ display: "none" }} />
                <img src={url} alt={`Generated image ${i + 1}`} style={{ width: "240px", height: "160px", objectFit: "cover", display: "block" }} />
                <div style={{ padding: "4px 8px", fontSize: "0.75rem", textAlign: "center", background: selectedImage === i ? "#111827" : "#f9fafb", color: selectedImage === i ? "white" : "#374151" }}>
                  {selectedImage === i ? "Selected ✓" : `Image ${i + 1} — ${draft.image_types[i] || ""}`}
                </div>
              </label>
            ))}
          </div>
        </s-section>
      )}

      <s-section heading="Edit content">
        <Form method="post">
          <input type="hidden" name="intent" value="publish" />
          <input type="hidden" name="store_id" value={draft.store_id} />
          <input type="hidden" name="prompt_id" value={draft.prompt_id} />
          <input type="hidden" name="prompt_text" value={draft.prompt_text} />
          <input type="hidden" name="blog_handle" value={draft.blog_handle} />
          <input type="hidden" name="author" value={draft.author} />
          <input type="hidden" name="keywords_json" value={JSON.stringify(draft.keywords)} />
          <input type="hidden" name="hashtags_json" value={JSON.stringify(draft.hashtags)} />
          <input type="hidden" name="image_urls_json" value={JSON.stringify(draft.image_urls)} />
          <input type="hidden" name="image_types_json" value={JSON.stringify(draft.image_types)} />
          <input type="hidden" name="selected_image_index" value={selectedImage} />
          <input type="hidden" name="product_url" value={draft.product_url} />
          <input type="hidden" name="product_title" value={draft.product_title} />
          <input type="hidden" name="title_pool_id" value={draft.title_pool_id} />

          <div style={{ display: "grid", gap: "16px" }}>
            <label style={lbl}>
              <span style={{ fontWeight: 600 }}>Title</span>
              <input name="title" value={title} onChange={e => setTitle(e.target.value)} required style={inp} />
            </label>
            <label style={lbl}>
              <span style={{ fontWeight: 600 }}>Summary / excerpt</span>
              <textarea name="summary" value={summary} onChange={e => setSummary(e.target.value)} rows={3} style={{ ...inp, resize: "vertical" }} />
            </label>
            <label style={lbl}>
              <span style={{ fontWeight: 600 }}>Content (Markdown)</span>
              <textarea name="content" value={content} onChange={e => setContent(e.target.value)} rows={20} required style={{ ...inp, resize: "vertical", fontFamily: "monospace", fontSize: "0.85rem" }} />
            </label>

            {draft.keywords.length > 0 && (
              <div style={{ fontSize: "0.8rem", color: "#6b7280" }}>
                Keywords: {draft.keywords.join(", ")}
              </div>
            )}

            {qr.publish_blocked ? (
              <Alert message="Quality checks are blocking publish. Fix the issues listed above, then try again." tone="error" />
            ) : null}

            <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
              <button type="submit" disabled={submitting || qr.publish_blocked} style={{ borderRadius: "999px", border: 0, background: "#111827", color: "white", padding: "12px 24px", fontWeight: 700, cursor: "pointer", opacity: (submitting || qr.publish_blocked) ? 0.6 : 1 }}>
                {submitting ? "Publishing…" : "Publish to Shopify"}
              </button>
              <button type="button" onClick={onBack} style={{ borderRadius: "999px", border: "1px solid #d1d5db", background: "white", padding: "12px 18px", cursor: "pointer" }}>
                Discard &amp; start over
              </button>
            </div>
          </div>
        </Form>
      </s-section>
    </s-page>
  );
}

// --- Step 3: Result ---
function ResultStep({ articleUrl, message, onNew }: { articleUrl: string; message: string; onNew: () => void }) {
  return (
    <s-page heading="Blog Published!">
      <s-section>
        <Alert message={message} tone="success" />
      </s-section>
      {articleUrl ? (
        <s-section heading="Your new article">
          <div style={{ display: "grid", gap: "10px" }}>
            <div>
              <strong>Storefront URL</strong>
              <div style={{ marginTop: 4 }}>
                <a href={articleUrl} target="_blank" rel="noreferrer" style={{ color: "#2563eb" }}>{articleUrl}</a>
              </div>
            </div>
          </div>
        </s-section>
      ) : null}
      <s-section>
        <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
          <button onClick={onNew} style={{ borderRadius: "999px", border: 0, background: "#111827", color: "white", padding: "12px 24px", fontWeight: 700, cursor: "pointer" }}>
            Generate another blog post
          </button>
          <a href="/app/history" style={{ borderRadius: "999px", border: "1px solid #d1d5db", background: "white", padding: "12px 18px", cursor: "pointer", textDecoration: "none", color: "#111827", display: "inline-block" }}>
            View history
          </a>
        </div>
      </s-section>
    </s-page>
  );
}

// --- Main route component ---
export default function AppIndexRoute() {
  const data = useLoaderData<typeof loader>();
  const actionData = useActionData<typeof action>() as ActionData | undefined;
  const [step, setStep] = useState<"form" | "preview" | "result">("form");

  useEffect(() => {
    if (!actionData) return;
    if (actionData.step === "preview" && actionData.ok) setStep("preview");
    else if (actionData.step === "result" && actionData.ok) setStep("result");
    else if (actionData.step === "error") setStep("form");
  }, [actionData]);

  if (step === "preview" && actionData?.step === "preview" && actionData.ok) {
    return <PreviewStep draft={actionData.draft} onBack={() => setStep("form")} />;
  }
  if (step === "result" && actionData?.step === "result" && actionData.ok) {
    return <ResultStep articleUrl={actionData.article_url} message={actionData.message} onNew={() => setStep("form")} />;
  }
  return (
    <GenerateForm
      data={data}
      prevError={actionData?.step === "error" ? (actionData as { ok: false; error: string }).error : undefined}
    />
  );
}

