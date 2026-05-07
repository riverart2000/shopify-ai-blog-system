import React, { useState, useEffect } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, useActionData, useLoaderData, useNavigation } from "react-router";
import { authenticate } from "../shopify.server";

const BACKEND_URL = process.env.AI_BLOG_BACKEND_URL || "http://127.0.0.1:4000";
const BACKEND_KEY = process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY || "";

type AiModel = {
  id: string;
  store_id: string;
  name: string;
  provider: string;
  model_type: string;
  model_name: string;
  endpoint: string;
  extra_json: string;
  priority: number;
  is_active: boolean;
};

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
  await authenticate.admin(request);
  if (!BACKEND_KEY) return { models: [], storeId: "" };
  try {
    const data = await backendFetch("/api/models");
    return { models: (data.models ?? []) as AiModel[], storeId: (data.store_id ?? "") as string };
  } catch {
    return { models: [], storeId: "" };
  }
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");

  try {
    if (intent === "save") {
      await backendFetch("/api/models/save", {
        method: "POST",
        body: JSON.stringify({
          store_id: form.get("store_id") || "",
          model_id: form.get("model_id") || "",
          name: form.get("name") || "",
          provider: form.get("provider") || "",
          model_type: form.get("model_type") || "text",
          model_name: form.get("model_name") || "",
          api_key: form.get("api_key") || "",
          endpoint: form.get("endpoint") || "",
          extra_json: form.get("extra_json") || "",
          priority: parseInt(String(form.get("priority") || "50"), 10),
          is_active: form.get("is_active") === "true",
        }),
      });
      return { ok: true };
    }
    if (intent === "delete") {
      await backendFetch("/api/models/delete", { method: "POST", body: JSON.stringify({ model_id: form.get("model_id") }) });
      return { ok: true };
    }
    if (intent === "toggle") {
      await backendFetch("/api/models/toggle", { method: "POST", body: JSON.stringify({ model_id: form.get("model_id"), is_active: form.get("is_active") === "true" }) });
      return { ok: true };
    }
    return { ok: false, error: "Unknown intent" };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Error" };
  }
};

const inp: React.CSSProperties = { borderRadius: "10px", border: "1px solid #d1d5db", padding: "9px 12px", font: "inherit", width: "100%", boxSizing: "border-box" };
const lbl: React.CSSProperties = { display: "grid", gap: "5px", fontSize: "0.875rem" };

const PROVIDERS = ["openai", "anthropic", "deepseek", "replicate", "xai", "google", "mistral", "custom"];
const MODEL_TYPES = ["text", "image"];

type ModelFormProps = {
  storeId: string;
  model?: AiModel;
  onClose: () => void;
};

function ModelForm({ storeId, model, onClose }: ModelFormProps) {
  const navigation = useNavigation();
  const submitting = navigation.state !== "idle";

  return (
    <div style={{ background: "#f9fafb", border: "1px solid #e5e7eb", borderRadius: "14px", padding: "20px", marginTop: "12px" }}>
      <h3 style={{ margin: "0 0 16px", fontSize: "1rem" }}>{model ? "Edit model" : "Add new model"}</h3>
      <Form method="post">
        <input type="hidden" name="intent" value="save" />
        <input type="hidden" name="store_id" value={storeId} />
        <input type="hidden" name="model_id" value={model?.id || ""} />

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
          <label style={lbl}>
            <span>Name *</span>
            <input name="name" required defaultValue={model?.name} placeholder="GPT-4o Text" style={inp} />
          </label>
          <label style={lbl}>
            <span>Provider</span>
            <select name="provider" defaultValue={model?.provider || "openai"} style={inp}>
              {PROVIDERS.map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label style={lbl}>
            <span>Model type</span>
            <select name="model_type" defaultValue={model?.model_type || "text"} style={inp}>
              {MODEL_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label style={lbl}>
            <span>Model name (API identifier) *</span>
            <input name="model_name" required defaultValue={model?.model_name} placeholder="gpt-4o" style={inp} />
          </label>
          <label style={lbl}>
            <span>API key</span>
            <input name="api_key" type="password" placeholder={model ? "Leave blank to keep unchanged" : "sk-…"} style={inp} autoComplete="off" />
          </label>
          <label style={lbl}>
            <span>Endpoint URL (optional)</span>
            <input name="endpoint" defaultValue={model?.endpoint} placeholder="https://api.openai.com/v1" style={inp} />
          </label>
          <label style={lbl}>
            <span>Priority (higher = preferred)</span>
            <input name="priority" type="number" defaultValue={model?.priority ?? 50} min={0} max={100} style={inp} />
          </label>
          <label style={lbl}>
            <span>Active</span>
            <select name="is_active" defaultValue={String(model?.is_active ?? true)} style={inp}>
              <option value="true">Active</option>
              <option value="false">Inactive</option>
            </select>
          </label>
          <label style={{ ...lbl, gridColumn: "1 / -1" }}>
            <span>Extra config (JSON, optional)</span>
            <textarea name="extra_json" defaultValue={model?.extra_json} rows={2} placeholder='{"temperature": 0.8}' style={{ ...inp, resize: "vertical", fontFamily: "monospace", fontSize: "0.82rem" }} />
          </label>
        </div>

        <div style={{ display: "flex", gap: "10px", marginTop: "16px" }}>
          <button type="submit" disabled={submitting} style={{ borderRadius: "999px", border: 0, background: "#111827", color: "white", padding: "10px 22px", fontWeight: 700, cursor: "pointer", opacity: submitting ? 0.6 : 1 }}>
            {submitting ? "Saving…" : "Save model"}
          </button>
          <button type="button" onClick={onClose} style={{ borderRadius: "999px", border: "1px solid #d1d5db", background: "white", padding: "10px 18px", cursor: "pointer" }}>
            Cancel
          </button>
        </div>
      </Form>
    </div>
  );
}

export default function ModelsRoute() {
  const { models, storeId } = useLoaderData<typeof loader>();
  const actionData = useActionData<typeof action>() as { ok: boolean; error?: string } | undefined;
  const [editing, setEditing] = useState<string | null>(null); // model.id or "new"

  useEffect(() => {
    if (actionData?.ok) setEditing(null);
  }, [actionData]);

  return (
    <s-page heading="AI Models">
      {actionData && !actionData.ok && actionData.error ? (
        <s-section>
          <div style={{ borderRadius: "12px", padding: "12px 16px", background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.3)", color: "#991b1b", fontSize: "0.875rem" }}>
            {actionData.error}
          </div>
        </s-section>
      ) : null}

      <s-section heading="Configured models">
        {models.length === 0 ? (
          <p style={{ color: "#6b7280", fontSize: "0.9rem" }}>No AI models configured yet. Add one below.</p>
        ) : (
          <div style={{ display: "grid", gap: "10px" }}>
            {models.map(m => (
              <div key={m.id} style={{ border: "1px solid #e5e7eb", borderRadius: "12px", padding: "14px 16px", background: "white" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600 }}>{m.name}</div>
                    <div style={{ fontSize: "0.8rem", color: "#6b7280" }}>
                      {m.provider} · {m.model_type} · <code style={{ fontSize: "0.78rem" }}>{m.model_name}</code> · priority {m.priority}
                    </div>
                  </div>
                  <span style={{ borderRadius: "999px", padding: "3px 10px", fontSize: "0.75rem", fontWeight: 600, background: m.is_active ? "#ecfdf5" : "#f3f4f6", color: m.is_active ? "#065f46" : "#6b7280", border: `1px solid ${m.is_active ? "#a7f3d0" : "#e5e7eb"}` }}>
                    {m.is_active ? "Active" : "Inactive"}
                  </span>
                  <Form method="post" style={{ display: "inline" }}>
                    <input type="hidden" name="intent" value="toggle" />
                    <input type="hidden" name="model_id" value={m.id} />
                    <input type="hidden" name="is_active" value={String(!m.is_active)} />
                    <button type="submit" style={{ borderRadius: "999px", border: "1px solid #d1d5db", background: "white", padding: "5px 12px", cursor: "pointer", fontSize: "0.78rem" }}>
                      {m.is_active ? "Deactivate" : "Activate"}
                    </button>
                  </Form>
                  <button type="button" onClick={() => setEditing(editing === m.id ? null : m.id)} style={{ borderRadius: "999px", border: "1px solid #d1d5db", background: "white", padding: "5px 12px", cursor: "pointer", fontSize: "0.78rem" }}>
                    {editing === m.id ? "Cancel" : "Edit"}
                  </button>
                  <Form method="post" style={{ display: "inline" }} onSubmit={e => { if (!confirm(`Delete model "${m.name}"?`)) e.preventDefault(); }}>
                    <input type="hidden" name="intent" value="delete" />
                    <input type="hidden" name="model_id" value={m.id} />
                    <button type="submit" style={{ borderRadius: "999px", border: "1px solid rgba(239,68,68,0.4)", background: "rgba(239,68,68,0.05)", color: "#dc2626", padding: "5px 12px", cursor: "pointer", fontSize: "0.78rem" }}>
                      Delete
                    </button>
                  </Form>
                </div>
                {editing === m.id && <ModelForm storeId={storeId} model={m} onClose={() => setEditing(null)} />}
              </div>
            ))}
          </div>
        )}
      </s-section>

      <s-section heading="Add new model">
        {editing === "new" ? (
          <ModelForm storeId={storeId} onClose={() => setEditing(null)} />
        ) : (
          <button onClick={() => setEditing("new")} style={{ borderRadius: "999px", border: 0, background: "#111827", color: "white", padding: "10px 22px", fontWeight: 700, cursor: "pointer" }}>
            + Add model
          </button>
        )}
      </s-section>

      <s-section heading="About AI models">
        <p style={{ color: "#6b7280", fontSize: "0.875rem", margin: 0 }}>
          Add your API keys for OpenAI, Anthropic, DeepSeek, Replicate, xAI, Google, Mistral or any custom endpoint.
          Models with higher priority are preferred for generation. Only active models are used.
          Text models are used for content; image models are used for infographic generation.
        </p>
      </s-section>
    </s-page>
  );
}
