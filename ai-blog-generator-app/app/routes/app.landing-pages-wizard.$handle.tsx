import React, { useState, useEffect } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { useLoaderData, useNavigate, useParams, useSubmit, useActionData, useNavigation } from "react-router";
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
  return res.json();
}

export const loader = async ({ request, params }: LoaderFunctionArgs) => {
  const auth = await authenticate.admin(request);
  const session = requireShopifySession((auth as { session?: unknown }).session);
  const handle = params.handle as string;
  
  const [context, productDataResponse] = await Promise.all([
    loadShopifyStudioContext(session),
    BACKEND_KEY ? backendFetch(`/api/landing-pages/products/${handle}`).catch(() => null) : Promise.resolve(null),
  ]);
  
  const storefrontDomain = context.storefrontDomain;
  const productUrl = `https://${storefrontDomain}/products/${handle}`;

  return {
    handle,
    productUrl,
    initialData: productDataResponse?.data || null,
  };
};

export const action = async ({ request, params }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const handle = params.handle as string;
  const formData = await request.formData();
  const intent = formData.get("intent") as string;

  try {
    if (intent === "generate_prompts") {
      const productUrl = formData.get("productUrl") as string;
      const res = await backendFetch("/api/landing-pages/generate-prompts", {
        method: "POST",
        body: JSON.stringify({ product_url: productUrl })
      });
      return { ok: true, intent, data: res.data };
    }
    
    if (intent === "save_edits") {
      const payload = formData.get("payload") as string;
      const data = JSON.parse(payload);
      await backendFetch(`/api/landing-pages/products/${handle}`, {
        method: "PUT",
        body: JSON.stringify({ data })
      });
      return { ok: true, intent, data };
    }

    if (intent === "generate_social") {
      const payload = formData.get("payload") as string;
      if (payload) {
        const data = JSON.parse(payload);
        await backendFetch(`/api/landing-pages/products/${handle}`, {
          method: "PUT",
          body: JSON.stringify({ data })
        });
      }
      const res = await backendFetch("/api/landing-pages/generate-social", {
        method: "POST",
        body: JSON.stringify({ handle })
      });
      return { ok: true, intent, produced: res.produced };
    }

    if (intent === "publish") {
      await backendFetch("/api/landing-pages/publish", {
        method: "POST",
        body: JSON.stringify({ handle, published: true })
      });
      return { ok: true, intent };
    }

    return { ok: false, error: "Unknown intent" };
  } catch (err: any) {
    return { ok: false, error: err.message, intent };
  }
};

export default function LandingPageWizard() {
  const { handle, productUrl, initialData } = useLoaderData<typeof loader>();
  const navigate = useNavigate();
  const submit = useSubmit();
  const actionData = useActionData<typeof action>();
  const navigation = useNavigation();
  
  const [data, setData] = useState<any>(initialData);
  const [step, setStep] = useState(initialData ? 2 : 1);
  const [error, setError] = useState<string | null>(null);

  const isSubmitting = navigation.state !== "idle";

  useEffect(() => {
    if (actionData) {
      if (actionData.ok) {
        if (actionData.intent === "generate_prompts") {
          // Use a function to ensure we capture the new data directly into state
          setData((prevData: any) => actionData.data);
          setStep(2);
        } else if (actionData.intent === "save_edits") {
          setData(actionData.data);
        } else if (actionData.intent === "generate_social") {
          setStep(3);
        } else if (actionData.intent === "publish") {
          setStep(4);
        }
      } else {
        setError(actionData.error);
      }
    }
  }, [actionData]);

  const handleGeneratePrompts = () => {
    setError(null);
    submit({ intent: "generate_prompts", productUrl }, { method: "post" });
  };

  const handleSaveEdits = () => {
    setError(null);
    submit({ intent: "save_edits", payload: JSON.stringify(data) }, { method: "post" });
  };

  const handleGenerateSocial = () => {
    setError(null);
    submit({ intent: "generate_social", payload: JSON.stringify(data) }, { method: "post" });
  };

  const handlePublish = () => {
    setError(null);
    submit({ intent: "publish" }, { method: "post" });
  };

  const handleConceptTextChange = (idx: number, field: string, value: string) => {
    const newData = { ...data };
    newData.concepts[idx][field] = value;
    setData(newData);
  };

  return (
    <s-page heading={`Landing Page Wizard: ${handle}`} backAction={{ content: "Back", onAction: () => navigate("/app/landing-pages") }}>
      {error && (
        <s-section>
          <div style={{ color: "#991b1b", background: "rgba(239,68,68,0.08)", padding: "14px", borderRadius: "12px", border: "1px solid rgba(239,68,68,0.3)" }}>
            Error: {error}
          </div>
        </s-section>
      )}

      {/* STEP 1: Generate Prompts */}
      <s-section heading="Step 1: Generate Prompts & Persona">
        <s-paragraph>
          Extracts product details and generates marketing angles, persona, and image prompts.
        </s-paragraph>
        <button
          onClick={handleGeneratePrompts}
          disabled={isSubmitting}
          style={{ padding: "8px 16px", background: "#202223", color: "#fff", borderRadius: "8px", border: "none", cursor: isSubmitting ? "not-allowed" : "pointer" }}
        >
          {isSubmitting && navigation.formData?.get("intent") === "generate_prompts" ? "Generating..." : "Run Generator"}
        </button>
      </s-section>

      {/* STEP 2: Edit Text & Generate Social */}
      {step >= 2 && data && (
        <s-section heading="Step 2: Review Content & Generate Images">
          <s-paragraph>Review the generated text for each concept before rendering images.</s-paragraph>
          
          <div style={{ marginBottom: "20px" }}>
             <strong>Ideal Client:</strong> {data.persona?.name}, {data.persona?.age} {data.persona?.sex}
          </div>

          <div style={{ display: "flex", gap: "20px", marginBottom: "20px", overflowX: "auto" }}>
            {data.assets?.map((asset: any, idx: number) => (
              <div key={idx} style={{ flex: "0 0 auto", width: "150px" }}>
                <img 
                  src={`/app/landing-pages/images/${asset.local_path.split('/').pop()}`} 
                  alt="Product Asset" 
                  style={{ width: "100%", height: "150px", objectFit: "cover", borderRadius: "8px", border: "1px solid #ccc" }} 
                />
              </div>
            ))}
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
            {data.concepts?.map((c: any, idx: number) => (
              <div key={idx} style={{ padding: "16px", border: "1px solid #e1e3e5", borderRadius: "8px", background: "#fcfcfc" }}>
                <h3 style={{ margin: "0 0 10px 0", fontSize: "1.1rem" }}>{c.concept}</h3>
                
                <label style={{ display: "block", marginBottom: "5px", fontWeight: "bold", fontSize: "0.85rem" }}>Image Prompt (for AI generation)</label>
                <textarea 
                  value={c.image_prompt || ""} 
                  onChange={(e) => handleConceptTextChange(idx, "image_prompt", e.target.value)}
                  style={{ width: "100%", height: "80px", marginBottom: "15px", padding: "8px", borderRadius: "4px", border: "1px solid #ccc" }}
                />

                <label style={{ display: "block", marginBottom: "5px", fontWeight: "bold", fontSize: "0.85rem" }}>Social Post Text</label>
                <textarea 
                  value={c.social_text || ""} 
                  onChange={(e) => handleConceptTextChange(idx, "social_text", e.target.value)}
                  style={{ width: "100%", height: "80px", marginBottom: "15px", padding: "8px", borderRadius: "4px", border: "1px solid #ccc" }}
                />
              </div>
            ))}
          </div>

          <div style={{ marginTop: "20px" }}>
            <label style={{ display: "block", marginBottom: "5px", fontWeight: "bold", fontSize: "0.85rem" }}>Raw JSON (Advanced)</label>
            <textarea 
              value={JSON.stringify(data, null, 2)} 
              onChange={(e) => {
                try {
                  const parsed = JSON.parse(e.target.value);
                  setData(parsed);
                } catch (err) {
                  // Ignore parse errors while typing
                }
              }}
              style={{ width: "100%", height: "300px", padding: "8px", borderRadius: "4px", border: "1px solid #ccc", fontFamily: "monospace", fontSize: "0.85rem" }}
            />
          </div>

          <div style={{ display: "flex", gap: "10px", marginTop: "20px" }}>
            <button
              onClick={handleSaveEdits}
              disabled={isSubmitting}
              style={{ padding: "8px 16px", background: "#ffffff", color: "#202223", border: "1px solid #8c9196", borderRadius: "8px", cursor: isSubmitting ? "not-allowed" : "pointer" }}
            >
              {isSubmitting && navigation.formData?.get("intent") === "save_edits" ? "Saving..." : "Save Edits"}
            </button>

            <button
              onClick={handleGenerateSocial}
              disabled={isSubmitting}
              style={{ padding: "8px 16px", background: "#0071e3", color: "#fff", border: "none", borderRadius: "8px", cursor: isSubmitting ? "not-allowed" : "pointer" }}
            >
              {isSubmitting && navigation.formData?.get("intent") === "generate_social" ? "Generating Images..." : "Generate Images & Socials"}
            </button>
          </div>
        </s-section>
      )}

      {/* STEP 3: Publish Landing Page */}
      {step >= 3 && (
        <s-section heading="Step 3: Publish Landing Page">
          <s-paragraph>
            Images have been generated! Click below to assemble the Shopify Landing Page, upload assets, and prepare the RSS feed.
          </s-paragraph>
          <button
            onClick={handlePublish}
            disabled={isSubmitting}
            style={{ padding: "8px 16px", background: "#107c41", color: "#fff", borderRadius: "8px", border: "none", cursor: isSubmitting ? "not-allowed" : "pointer" }}
          >
            {isSubmitting && navigation.formData?.get("intent") === "publish" ? "Publishing..." : "Publish Page & RSS Feed"}
          </button>
        </s-section>
      )}

      {step >= 4 && (
        <s-section heading="Success!">
          <div style={{ color: "#107c41", background: "#dff6dd", padding: "14px", borderRadius: "12px", border: "1px solid #107c41" }}>
            The landing page was published successfully. Social posts are now queued in the RSS feed for Publer.
          </div>
        </s-section>
      )}
    </s-page>
  );
}