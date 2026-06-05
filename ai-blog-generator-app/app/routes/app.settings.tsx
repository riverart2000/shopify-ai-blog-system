import React, { useState, useEffect } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, useActionData, useLoaderData, useNavigation } from "react-router";
import { authenticate } from "../shopify.server";

const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

type StoreSettings = {
  store_id: string;
  default_blog_handle: string;
  default_author: string;
  prompt_ending: string;
  keyword_niche: string;
  keyword_max_pool: number;
  title_gen_model_id: string;
  title_gen_prompt_id: string;
  default_prompt_id: string;
  social_x_handle: string;
  has_tavily_key: boolean;
  has_exa_key: boolean;
};

type BackendStore = {
  id: string;
  name: string;
  myshopify_domain: string;
  default_blog_handle: string;
  default_author: string;
};

type KeywordItem = { id: number; keyword: string; used: boolean };
type TitleItem = { id: number; title: string; used: boolean };
type Prompt = { id: string; name: string };
type Model = { id: string; name: string };

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

function withStoreId(path: string, storeId: string) {
  if (!storeId) return path;
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}store_id=${encodeURIComponent(storeId)}`;
}

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  if (!BACKEND_KEY) return { backendConfigured: false, settings: null, keywords: [], titles: [], prompts: [], models: [], storeId: "", stores: [] };
  const url = new URL(request.url);
  const requestedStoreId = url.searchParams.get("store_id")?.trim() || "";
  const storesData = await backendFetch("/api/stores");
  const stores = (storesData.stores ?? []) as BackendStore[];
  const resolvedStoreId = requestedStoreId || stores[0]?.id || "";
  const [settingsData, keywordsData, titlesData, modelsData, promptsData] = await Promise.allSettled([
    backendFetch(withStoreId("/api/settings", resolvedStoreId)),
    backendFetch(withStoreId("/api/keywords", resolvedStoreId)),
    backendFetch(withStoreId("/api/titles", resolvedStoreId)),
    backendFetch(withStoreId("/api/models", resolvedStoreId)),
    backendFetch(withStoreId("/api/init", resolvedStoreId)),
  ]);
  const settings = settingsData.status === "fulfilled" ? settingsData.value as unknown as StoreSettings : null;
  const keywords = keywordsData.status === "fulfilled" ? (keywordsData.value.keywords ?? []) as KeywordItem[] : [];
  const titles = titlesData.status === "fulfilled" ? (titlesData.value.titles ?? []) as TitleItem[] : [];
  const models = modelsData.status === "fulfilled" ? (modelsData.value.models ?? []) as Model[] : [];
  const prompts = promptsData.status === "fulfilled" ? (promptsData.value.prompts ?? []) as Prompt[] : [];
  const storeId = settings?.store_id || resolvedStoreId;
  return { backendConfigured: true, settings, keywords, titles, prompts, models, storeId, stores };
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");

  try {
    if (intent === "save-settings") {
      const payload: Record<string, unknown> = {
        store_id: form.get("store_id") || "",
        default_blog_handle: form.get("default_blog_handle") || "",
        default_author: form.get("default_author") || "",
        prompt_ending: form.get("prompt_ending") || "",
        keyword_niche: form.get("keyword_niche") || "",
        keyword_max_pool: parseInt(String(form.get("keyword_max_pool") || "100"), 10),
        title_gen_model_id: form.get("title_gen_model_id") || "",
        title_gen_prompt_id: form.get("title_gen_prompt_id") || "",
        default_prompt_id: form.get("default_prompt_id") || "",
        social_x_handle: form.get("social_x_handle") || "",
      };
      if (form.get("tavily_api_key")) payload.tavily_api_key = form.get("tavily_api_key");
      if (form.get("exa_api_key")) payload.exa_api_key = form.get("exa_api_key");
      await backendFetch("/api/settings/save", { method: "POST", body: JSON.stringify(payload) });
      return { ok: true, intent };
    }
    if (intent === "fetch-keywords") {
      const data = await backendFetch("/api/keywords/fetch", { method: "POST", body: JSON.stringify({ store_id: form.get("store_id") || "" }) });
      return { ok: true, intent, message: String(data.message || "Keywords fetched") };
    }
    if (intent === "delete-keyword") {
      await backendFetch("/api/keywords/delete", { method: "POST", body: JSON.stringify({ keyword_id: parseInt(String(form.get("keyword_id") || "0"), 10) }) });
      return { ok: true, intent };
    }
    if (intent === "clear-keywords") {
      const data = await backendFetch("/api/keywords/clear", { method: "POST", body: JSON.stringify({ store_id: form.get("store_id") || "" }) });
      return { ok: true, intent, message: String(data.message || `Cleared ${data.count ?? "all"} keywords`) };
    }
    if (intent === "generate-titles") {
      const data = await backendFetch("/api/titles/generate", { method: "POST", body: JSON.stringify({ store_id: form.get("store_id") || "" }) });
      return { ok: true, intent, message: String(data.message || "Titles generated") };
    }
    if (intent === "delete-title") {
      await backendFetch("/api/titles/delete", { method: "POST", body: JSON.stringify({ title_id: parseInt(String(form.get("title_id") || "0"), 10) }) });
      return { ok: true, intent };
    }
    if (intent === "clear-titles") {
      const data = await backendFetch("/api/titles/clear", { method: "POST", body: JSON.stringify({ store_id: form.get("store_id") || "" }) });
      return { ok: true, intent, message: String(data.message || `Cleared ${data.count ?? "all"} titles`) };
    }
    return { ok: false, error: "Unknown intent" };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Error" };
  }
};

const inp: React.CSSProperties = { borderRadius: "10px", border: "1px solid #d1d5db", padding: "9px 12px", font: "inherit", width: "100%", boxSizing: "border-box" };
const lbl: React.CSSProperties = { display: "grid", gap: "5px", fontSize: "0.875rem" };

function Alert({ message, tone }: { message: string; tone: "success" | "error" | "info" }) {
  const s = tone === "success" ? { bg: "#ecfdf5", border: "#a7f3d0", color: "#065f46" } : tone === "error" ? { bg: "rgba(239,68,68,0.08)", border: "rgba(239,68,68,0.3)", color: "#991b1b" } : { bg: "#eff6ff", border: "#bfdbfe", color: "#1e40af" };
  return <div style={{ borderRadius: "12px", padding: "12px 16px", background: s.bg, border: `1px solid ${s.border}`, color: s.color, fontSize: "0.875rem" }}>{message}</div>;
}

export default function SettingsRoute() {
  const { backendConfigured, settings, keywords, titles, prompts, models, storeId, stores } = useLoaderData<typeof loader>();
  const actionData = useActionData<typeof action>() as { ok: boolean; error?: string; intent?: string; message?: string } | undefined;
  const navigation = useNavigation();
  const submitting = navigation.state !== "idle";

  const [showTavilyKey, setShowTavilyKey] = useState(false);
  const [showExaKey, setShowExaKey] = useState(false);

  const lastSaved = actionData?.ok && actionData.intent === "save-settings";
  const lastMsg = actionData?.ok && actionData.message ? actionData.message : null;
  const lastErr = !actionData?.ok && actionData?.error ? actionData.error : null;

  return (
    <s-page heading="Store Settings">
      {!backendConfigured ? (
        <s-section>
          <Alert message="AI_BLOG_BACKEND_API_KEY is not configured. Add it to your .env file." tone="error" />
        </s-section>
      ) : null}

      {lastErr ? <s-section><Alert message={lastErr} tone="error" /></s-section> : null}
      {lastSaved ? <s-section><Alert message="Settings saved." tone="success" /></s-section> : null}
      {lastMsg ? <s-section><Alert message={lastMsg} tone="info" /></s-section> : null}

      {stores.length > 1 ? (
        <s-section heading="Backend store">
          <Form method="get">
            <div style={{ display: "grid", gridTemplateColumns: "minmax(280px, 420px)", gap: "12px", alignItems: "end" }}>
              <label style={lbl}>
                <span>Select backend store</span>
                <select name="store_id" defaultValue={storeId} style={inp}>
                  {stores.map((store) => (
                    <option key={store.id} value={store.id}>
                      {store.name} ({store.id})
                    </option>
                  ))}
                </select>
              </label>
              <div style={{ fontSize: "0.8rem", color: "#6b7280" }}>
                Switch which backend store record you are editing in this settings page.
              </div>
            </div>
            <div style={{ marginTop: "12px" }}>
              <button type="submit" style={{ borderRadius: "999px", border: 0, background: "#111827", color: "white", padding: "10px 22px", fontWeight: 700, cursor: "pointer" }}>
                Load store settings
              </button>
            </div>
          </Form>
        </s-section>
      ) : null}

      {/* --- Store defaults --- */}
      <s-section heading="Store defaults">
        <Form method="post">
          <input type="hidden" name="intent" value="save-settings" />
          <input type="hidden" name="store_id" value={storeId} />

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
            <label style={lbl}>
              <span>Default blog handle</span>
              <input name="default_blog_handle" defaultValue={settings?.default_blog_handle || "news"} style={inp} placeholder="news" />
            </label>
            <label style={lbl}>
              <span>Default author</span>
              <input name="default_author" defaultValue={settings?.default_author || ""} style={inp} placeholder="Store Team" />
            </label>
            <label style={{ ...lbl, gridColumn: "1 / -1" }}>
              <span>Prompt ending (appended to every prompt)</span>
              <textarea name="prompt_ending" defaultValue={settings?.prompt_ending || ""} rows={3} style={{ ...inp, resize: "vertical" }} placeholder="Always include a call-to-action at the end. Write in British English." />
            </label>
            <label style={lbl}>
              <span>Default prompt</span>
              <select name="default_prompt_id" defaultValue={settings?.default_prompt_id || ""} style={inp}>
                <option value="">None</option>
                {prompts.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>
            <label style={lbl}>
              <span>Social X (Twitter) handle</span>
              <input name="social_x_handle" defaultValue={settings?.social_x_handle || ""} style={inp} placeholder="@yourbrand" />
            </label>
          </div>

          <div style={{ marginTop: "16px" }}>
            <button type="submit" disabled={submitting} style={{ borderRadius: "999px", border: 0, background: "#111827", color: "white", padding: "10px 22px", fontWeight: 700, cursor: "pointer", opacity: submitting ? 0.6 : 1 }}>
              {submitting ? "Saving…" : "Save settings"}
            </button>
          </div>
        </Form>
      </s-section>

      {/* --- Keyword pool --- */}
      <s-section heading="Keyword pool">
        <Form method="post">
          <input type="hidden" name="intent" value="save-settings" />
          <input type="hidden" name="store_id" value={storeId} />
          {/* carry other hidden values so partial save doesn't blank them */}
          <input type="hidden" name="default_blog_handle" value={settings?.default_blog_handle || ""} />
          <input type="hidden" name="default_author" value={settings?.default_author || ""} />
          <input type="hidden" name="prompt_ending" value={settings?.prompt_ending || ""} />
          <input type="hidden" name="default_prompt_id" value={settings?.default_prompt_id || ""} />
          <input type="hidden" name="social_x_handle" value={settings?.social_x_handle || ""} />
          <input type="hidden" name="title_gen_model_id" value={settings?.title_gen_model_id || ""} />
          <input type="hidden" name="title_gen_prompt_id" value={settings?.title_gen_prompt_id || ""} />

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
            <label style={lbl}>
              <span>Keyword niche</span>
              <input name="keyword_niche" defaultValue={settings?.keyword_niche || ""} style={inp} placeholder="wellness supplements, sleep health" />
            </label>
            <label style={lbl}>
              <span>Max pool size</span>
              <input name="keyword_max_pool" type="number" defaultValue={settings?.keyword_max_pool || 100} min={10} max={1000} style={inp} />
            </label>
            <label style={lbl}>
              <span>Tavily API key {settings?.has_tavily_key ? <span style={{ color: "#059669" }}>✓ set</span> : <span style={{ color: "#dc2626" }}>not set</span>}</span>
              <div style={{ display: "flex", gap: "6px" }}>
                <input name="tavily_api_key" type={showTavilyKey ? "text" : "password"} placeholder={settings?.has_tavily_key ? "Leave blank to keep existing" : "tvly-…"} style={{ ...inp, flex: 1 }} autoComplete="off" />
                <button type="button" onClick={() => setShowTavilyKey(!showTavilyKey)} style={{ borderRadius: "8px", border: "1px solid #d1d5db", background: "white", padding: "8px 12px", cursor: "pointer", fontSize: "0.78rem", whiteSpace: "nowrap" }}>
                  {showTavilyKey ? "Hide" : "Show"}
                </button>
              </div>
            </label>
            <label style={lbl}>
              <span>Exa API key {settings?.has_exa_key ? <span style={{ color: "#059669" }}>✓ set</span> : <span style={{ color: "#dc2626" }}>not set</span>}</span>
              <div style={{ display: "flex", gap: "6px" }}>
                <input name="exa_api_key" type={showExaKey ? "text" : "password"} placeholder={settings?.has_exa_key ? "Leave blank to keep existing" : "exa-…"} style={{ ...inp, flex: 1 }} autoComplete="off" />
                <button type="button" onClick={() => setShowExaKey(!showExaKey)} style={{ borderRadius: "8px", border: "1px solid #d1d5db", background: "white", padding: "8px 12px", cursor: "pointer", fontSize: "0.78rem", whiteSpace: "nowrap" }}>
                  {showExaKey ? "Hide" : "Show"}
                </button>
              </div>
            </label>
          </div>
          <div style={{ marginTop: "12px" }}>
            <button type="submit" disabled={submitting} style={{ borderRadius: "999px", border: 0, background: "#111827", color: "white", padding: "10px 22px", fontWeight: 700, cursor: "pointer", opacity: submitting ? 0.6 : 1 }}>
              Save
            </button>
          </div>
        </Form>

        <div style={{ marginTop: "14px", display: "flex", gap: "10px", flexWrap: "wrap" }}>
          <Form method="post" style={{ display: "inline" }}>
            <input type="hidden" name="intent" value="fetch-keywords" />
            <input type="hidden" name="store_id" value={storeId} />
            <button type="submit" disabled={submitting} style={{ borderRadius: "999px", border: "1px solid #2563eb", color: "#2563eb", background: "white", padding: "8px 18px", cursor: "pointer", fontSize: "0.875rem", opacity: submitting ? 0.6 : 1 }}>
              Fetch keywords now
            </button>
          </Form>
          {keywords.length > 0 && (
            <Form method="post" style={{ display: "inline" }} onSubmit={e => { if (!confirm("Clear all keywords?")) e.preventDefault(); }}>
              <input type="hidden" name="intent" value="clear-keywords" />
              <input type="hidden" name="store_id" value={storeId} />
              <button type="submit" style={{ borderRadius: "999px", border: "1px solid rgba(239,68,68,0.4)", background: "rgba(239,68,68,0.05)", color: "#dc2626", padding: "8px 18px", cursor: "pointer", fontSize: "0.875rem" }}>
                Clear all keywords ({keywords.length})
              </button>
            </Form>
          )}
        </div>

        {keywords.length > 0 && (
          <div style={{ marginTop: "14px" }}>
            <div style={{ fontSize: "0.8rem", color: "#6b7280", marginBottom: "6px" }}>{keywords.length} keyword{keywords.length !== 1 ? "s" : ""} in pool</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
              {keywords.map(k => (
                <span key={k.id} style={{ display: "inline-flex", alignItems: "center", gap: "4px", borderRadius: "999px", border: "1px solid #e5e7eb", padding: "3px 8px 3px 10px", fontSize: "0.8rem", background: k.used ? "#f3f4f6" : "white", color: k.used ? "#9ca3af" : "#111827" }}>
                  {k.keyword}
                  <Form method="post" style={{ display: "inline", margin: 0 }}>
                    <input type="hidden" name="intent" value="delete-keyword" />
                    <input type="hidden" name="keyword_id" value={k.id} />
                    <button type="submit" style={{ border: 0, background: "none", cursor: "pointer", color: "#9ca3af", fontSize: "0.9rem", padding: "0 2px", lineHeight: 1 }}>×</button>
                  </Form>
                </span>
              ))}
            </div>
          </div>
        )}
      </s-section>

      {/* --- Title pool --- */}
      <s-section heading="Title pool">
        <Form method="post">
          <input type="hidden" name="intent" value="save-settings" />
          <input type="hidden" name="store_id" value={storeId} />
          <input type="hidden" name="default_blog_handle" value={settings?.default_blog_handle || ""} />
          <input type="hidden" name="default_author" value={settings?.default_author || ""} />
          <input type="hidden" name="prompt_ending" value={settings?.prompt_ending || ""} />
          <input type="hidden" name="default_prompt_id" value={settings?.default_prompt_id || ""} />
          <input type="hidden" name="social_x_handle" value={settings?.social_x_handle || ""} />
          <input type="hidden" name="keyword_niche" value={settings?.keyword_niche || ""} />
          <input type="hidden" name="keyword_max_pool" value={String(settings?.keyword_max_pool || 100)} />

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
            <label style={lbl}>
              <span>Title generation model</span>
              <select name="title_gen_model_id" defaultValue={settings?.title_gen_model_id || ""} style={inp}>
                <option value="">Default (highest priority active text model)</option>
                {models.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
            </label>
            <label style={lbl}>
              <span>Title generation prompt</span>
              <select name="title_gen_prompt_id" defaultValue={settings?.title_gen_prompt_id || ""} style={inp}>
                <option value="">Default title prompt</option>
                {prompts.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>
          </div>
          <div style={{ marginTop: "12px" }}>
            <button type="submit" disabled={submitting} style={{ borderRadius: "999px", border: 0, background: "#111827", color: "white", padding: "10px 22px", fontWeight: 700, cursor: "pointer", opacity: submitting ? 0.6 : 1 }}>
              Save
            </button>
          </div>
        </Form>

        <div style={{ marginTop: "14px", display: "flex", gap: "10px", flexWrap: "wrap" }}>
          <Form method="post" style={{ display: "inline" }}>
            <input type="hidden" name="intent" value="generate-titles" />
            <input type="hidden" name="store_id" value={storeId} />
            <button type="submit" disabled={submitting} style={{ borderRadius: "999px", border: "1px solid #2563eb", color: "#2563eb", background: "white", padding: "8px 18px", cursor: "pointer", fontSize: "0.875rem", opacity: submitting ? 0.6 : 1 }}>
              Generate titles now
            </button>
          </Form>
          {titles.length > 0 && (
            <Form method="post" style={{ display: "inline" }} onSubmit={e => { if (!confirm("Clear all titles?")) e.preventDefault(); }}>
              <input type="hidden" name="intent" value="clear-titles" />
              <input type="hidden" name="store_id" value={storeId} />
              <button type="submit" style={{ borderRadius: "999px", border: "1px solid rgba(239,68,68,0.4)", background: "rgba(239,68,68,0.05)", color: "#dc2626", padding: "8px 18px", cursor: "pointer", fontSize: "0.875rem" }}>
                Clear all titles ({titles.length})
              </button>
            </Form>
          )}
        </div>

        {titles.length > 0 && (
          <div style={{ marginTop: "14px" }}>
            <div style={{ fontSize: "0.8rem", color: "#6b7280", marginBottom: "8px" }}>{titles.length} title{titles.length !== 1 ? "s" : ""} in pool</div>
            <div style={{ display: "grid", gap: "6px" }}>
              {titles.map(t => (
                <div key={t.id} style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "0.875rem", padding: "6px 10px", borderRadius: "8px", background: t.used ? "#f3f4f6" : "white", border: "1px solid #e5e7eb" }}>
                  <span style={{ flex: 1, color: t.used ? "#9ca3af" : "#111827" }}>
                    {t.title}
                    {t.used && <span style={{ marginLeft: "8px", fontSize: "0.75rem", color: "#9ca3af" }}>used</span>}
                  </span>
                  <Form method="post" style={{ display: "inline", margin: 0 }}>
                    <input type="hidden" name="intent" value="delete-title" />
                    <input type="hidden" name="title_id" value={t.id} />
                    <button type="submit" style={{ border: 0, background: "none", cursor: "pointer", color: "#9ca3af", fontSize: "1rem", padding: "0 4px", lineHeight: 1 }}>×</button>
                  </Form>
                </div>
              ))}
            </div>
          </div>
        )}
      </s-section>
    </s-page>
  );
}
