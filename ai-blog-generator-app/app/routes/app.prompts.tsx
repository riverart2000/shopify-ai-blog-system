import React, { useState, useEffect } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, useActionData, useLoaderData, useNavigation } from "react-router";
import { BACKEND_KEY, type BackendStore, backendFetch, loadBackendStoreContext, withStoreId } from "../lib/backend-store.server";
import { authenticate } from "../shopify.server";

type Prompt = { id: string; store_id: string; name: string; text: string; sort_order: number };

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { selectedStore, storeId, stores } = await loadBackendStoreContext(request);
  if (!BACKEND_KEY) return { prompts: [] as Prompt[], stores: [] as Store[], storeId: "", error: "AI_BLOG_BACKEND_API_KEY is not configured." };
  try {
    const promptsData = storeId ? await backendFetch(withStoreId("/api/prompts", storeId)) : { prompts: [] };
    return { prompts: (promptsData.prompts ?? []) as Prompt[], stores: stores as BackendStore[], storeId, storeName: selectedStore?.name || "", error: null };
  } catch (e) {
    return { prompts: [] as Prompt[], stores: [] as BackendStore[], storeId: "", storeName: "", error: e instanceof Error ? e.message : "Failed to reach backend" };
  }
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  if (!BACKEND_KEY) return { ok: false, error: "AI_BLOG_BACKEND_API_KEY not configured" };
  try {
    if (intent === "save") {
      await backendFetch("/api/prompts/save", {
        method: "POST",
        body: JSON.stringify({
          store_id: form.get("store_id") || "",
          prompt_id: form.get("prompt_id") || "",
          name: form.get("name") || "",
          text: form.get("text") || "",
          sort_order: parseInt(String(form.get("sort_order") || "0"), 10) || 0,
        }),
      });
      return { ok: true, message: "Prompt saved." };
    }
    if (intent === "delete") {
      await backendFetch("/api/prompts/delete", { method: "POST", body: JSON.stringify({ prompt_id: form.get("prompt_id") }) });
      return { ok: true, message: "Prompt deleted." };
    }
    return { ok: false, error: "Unknown intent" };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Action failed" };
  }
};

const inp: React.CSSProperties = { borderRadius: "10px", border: "1px solid #d1d5db", padding: "9px 12px", font: "inherit", width: "100%", boxSizing: "border-box" };
const lbl: React.CSSProperties = { display: "grid", gap: "5px", fontSize: "0.875rem" };

function PromptForm({ prompt, storeId, onCancel }: { prompt?: Prompt; storeId: string; onCancel: () => void }) {
  const navigation = useNavigation();
  const saving = navigation.state !== "idle";
  return (
    <Form method="post">
      <input type="hidden" name="intent" value="save" />
      <input type="hidden" name="prompt_id" value={prompt?.id || ""} />
      <input type="hidden" name="store_id" value={prompt?.store_id || storeId} />
      <div style={{ display: "grid", gap: "14px" }}>
        <label style={lbl}>
          <span>Name <span style={{ color: "#dc2626" }}>*</span></span>
          <input name="name" defaultValue={prompt?.name || ""} required style={inp} placeholder="Weekly wellness blog" />
        </label>
        <label style={lbl}>
          <span>Prompt text <span style={{ color: "#dc2626" }}>*</span></span>
          <textarea
            name="text"
            defaultValue={prompt?.text || ""}
            required
            rows={8}
            style={{ ...inp, resize: "vertical", lineHeight: "1.5" }}
            placeholder={"Write a 600-word SEO-optimised blog post about {topic}. Use a friendly, professional tone...\n\nInclude:\n- An engaging introduction\n- 3-4 key sections with subheadings\n- A call to action at the end"}
          />
          <span style={{ color: "#6b7280", fontSize: "0.78rem" }}>
            You can use <code>{"{topic}"}</code>, <code>{"{product_title}"}</code>, <code>{"{product_description}"}</code> as variables.
          </span>
        </label>
        <label style={lbl}>
          <span>Sort order</span>
          <input name="sort_order" type="number" defaultValue={prompt?.sort_order ?? 0} style={{ ...inp, width: "120px" }} />
          <span style={{ color: "#6b7280", fontSize: "0.78rem" }}>Lower numbers appear first in dropdowns.</span>
        </label>
        <div style={{ display: "flex", gap: "10px", marginTop: "4px" }}>
          <button type="submit" disabled={saving} style={{ borderRadius: "999px", border: 0, background: "#111827", color: "white", padding: "10px 20px", fontWeight: 700, cursor: "pointer" }}>
            {saving ? "Saving…" : prompt ? "Update prompt" : "Create prompt"}
          </button>
          <button type="button" onClick={onCancel} style={{ borderRadius: "999px", border: "1px solid #d1d5db", background: "white", padding: "10px 20px", cursor: "pointer" }}>
            Cancel
          </button>
        </div>
      </div>
    </Form>
  );
}

export default function PromptsRoute() {
  const { prompts, storeId, storeName, error } = useLoaderData<typeof loader>();
  const actionData = useActionData<typeof action>() as { ok: boolean; message?: string | null; error?: string } | undefined;
  const [editing, setEditing] = useState<string | null>(null);

  useEffect(() => {
    if (actionData?.ok) setEditing(null);
  }, [actionData]);

  return (
    <s-page heading="Blog Prompts">
      {error || actionData?.error ? (
        <s-section>
          <div style={{ borderRadius: "12px", padding: "12px 16px", background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.3)", color: "#991b1b", fontSize: "0.875rem" }}>
            {error || actionData?.error}
          </div>
        </s-section>
      ) : null}
      {actionData?.ok && actionData.message ? (
        <s-section>
          <div style={{ borderRadius: "12px", padding: "12px 16px", background: "#ecfdf5", border: "1px solid #a7f3d0", color: "#065f46", fontSize: "0.875rem" }}>
            {actionData.message}
          </div>
        </s-section>
      ) : null}

      {editing === "new" ? (
        <s-section heading="New prompt">
          <PromptForm storeId={storeId} onCancel={() => setEditing(null)} />
        </s-section>
      ) : (
        <s-section>
          <button onClick={() => setEditing("new")} style={{ borderRadius: "999px", border: 0, background: "#111827", color: "white", padding: "10px 22px", fontWeight: 700, cursor: "pointer" }}>
            + New prompt
          </button>
        </s-section>
      )}

      <s-section heading={storeName ? `${storeName} prompts (${prompts.length})` : `Prompts (${prompts.length})`}>
        {prompts.length === 0 && !error ? (
          <s-paragraph>No prompts yet. Click &ldquo;+ New prompt&rdquo; above to create one.</s-paragraph>
        ) : (
          <div style={{ display: "grid", gap: "12px" }}>
            {prompts.map(prompt => (
              <div key={prompt.id} style={{ border: "1px solid #e5e7eb", borderRadius: "12px", padding: "16px", background: "white" }}>
                {editing === prompt.id ? (
                  <PromptForm prompt={prompt} storeId={storeId} onCancel={() => setEditing(null)} />
                ) : (
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "10px" }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 700, fontSize: "0.95rem" }}>{prompt.name}</div>
                      {prompt.sort_order !== 0 ? <div style={{ fontSize: "0.75rem", color: "#9ca3af", marginTop: 2 }}>Sort order: {prompt.sort_order}</div> : null}
                      <div style={{
                        marginTop: 8,
                        fontSize: "0.8rem",
                        color: "#4b5563",
                        whiteSpace: "pre-wrap",
                        background: "#f9fafb",
                        borderRadius: "8px",
                        padding: "10px 12px",
                        maxHeight: "120px",
                        overflow: "hidden",
                        position: "relative",
                      }}>
                        {prompt.text.length > 300 ? `${prompt.text.slice(0, 300)}…` : prompt.text}
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: "8px", flexShrink: 0 }}>
                      <button onClick={() => setEditing(prompt.id)} style={{ borderRadius: "999px", border: "1px solid #d1d5db", background: "white", padding: "6px 14px", fontSize: "0.8rem", cursor: "pointer" }}>
                        Edit
                      </button>
                      <Form method="post" style={{ display: "contents" }} onSubmit={e => { if (!confirm(`Delete "${prompt.name}"?`)) e.preventDefault(); }}>
                        <input type="hidden" name="intent" value="delete" />
                        <input type="hidden" name="prompt_id" value={prompt.id} />
                        <button type="submit" style={{ borderRadius: "999px", border: "1px solid #fca5a5", background: "#fff5f5", color: "#dc2626", padding: "6px 14px", fontSize: "0.8rem", cursor: "pointer" }}>
                          Delete
                        </button>
                      </Form>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </s-section>
    </s-page>
  );
}
