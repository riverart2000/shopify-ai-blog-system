import React, { useState, useEffect } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { useLoaderData, useNavigate, useParams, useSubmit, useActionData, useNavigation } from "react-router";
import { authenticate } from "../shopify.server";
import { loadShopifyStudioContext, requireShopifySession } from "../lib/blog-studio.server";
import {
  addLandingPageAssetPreviewUrls,
  addSocialImagePreviewUrls,
  removeLandingPageAssetPreviewUrls,
} from "../lib/landing-page-images.server";

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
      const body = await res.json() as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail && typeof body.detail === "object") {
        const value = body.detail as { message?: string; error?: string };
        detail = value.message || value.error || JSON.stringify(body.detail);
      }
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
  
  const [context, productDataResponse, creditResponse] = await Promise.all([
    loadShopifyStudioContext(session),
    BACKEND_KEY ? backendFetch(`/api/landing-pages/products/${handle}`).catch(() => null) : Promise.resolve(null),
    BACKEND_KEY
      ? backendFetch(`/api/landing-pages/credits?shop=${encodeURIComponent(session.shop)}`).catch((error) => ({
          credit: {
            status: "check_failed",
            can_start: false,
            available_display: "Unavailable",
            minimum_display: "Unavailable",
            message: `Credit check could not be loaded: ${error.message}`,
          },
        }))
      : Promise.resolve(null),
  ]);
  
  const storefrontDomain = context.storefrontDomain;
  const productUrl = `https://${storefrontDomain}/products/${handle}`;
  const rawProductData = productDataResponse?.data || null;

  return {
    handle,
    productUrl,
    initialData: addLandingPageAssetPreviewUrls(rawProductData),
    initialPublishResult: rawProductData?.landing_page_publication || null,
    initialCredit: creditResponse?.credit || null,
  };
};

export const action = async ({ request, params }: ActionFunctionArgs) => {
  const auth = await authenticate.admin(request);
  const session = requireShopifySession((auth as { session?: unknown }).session);
  const handle = params.handle as string;
  const formData = await request.formData();
  const intent = formData.get("intent") as string;
  const submittedConcept = String(formData.get("concept") || "");

  try {
    if (intent === "generate_prompts") {
      const productUrl = formData.get("productUrl") as string;
      const res = await backendFetch("/api/landing-pages/generate-prompts", {
        method: "POST",
        body: JSON.stringify({
          product_url: productUrl,
          shop: session.shop,
          fetcher: "shopify",
          generator: "grok",
        })
      });
      if (!res.accepted) {
        return {
          ok: false,
          intent,
          error: res.error || res.credit?.message || "Generation was not accepted.",
          credit: res.credit || null,
        };
      }
      return { ok: true, intent, job: res.job, credit: res.credit };
    }

    if (intent === "poll_generation") {
      const jobId = formData.get("jobId") as string;
      const res = await backendFetch(`/api/landing-pages/generate-prompts/jobs/${encodeURIComponent(jobId)}`);
      const job = res.job;
      if (job.status === "succeeded") {
        const productRes = await backendFetch(`/api/landing-pages/products/${handle}`);
        return {
          ok: true,
          intent,
          job,
          data: addLandingPageAssetPreviewUrls(productRes.data),
        };
      }
      return { ok: true, intent, job };
    }
    
    if (intent === "save_edits") {
      const payload = formData.get("payload") as string;
      const data = JSON.parse(payload);
      const persistedData = removeLandingPageAssetPreviewUrls(data);
      await backendFetch(`/api/landing-pages/products/${handle}`, {
        method: "PUT",
        body: JSON.stringify({ data: persistedData })
      });
      return { ok: true, intent, data: addLandingPageAssetPreviewUrls(persistedData) };
    }

    if (intent === "generate_social") {
      const payload = formData.get("payload") as string;
      if (payload) {
        const data = JSON.parse(payload);
        const persistedData = removeLandingPageAssetPreviewUrls(data);
        await backendFetch(`/api/landing-pages/products/${handle}`, {
          method: "PUT",
          body: JSON.stringify({ data: persistedData })
        });
      }
      await backendFetch("/api/landing-pages/generate-social", {
        method: "POST",
        body: JSON.stringify({ handle })
      });
      // Fetch the generated social items to display them
      const fetchRes = await backendFetch(`/api/landing-pages/social/${handle}`);
      return { ok: true, intent, socialItems: addSocialImagePreviewUrls(fetchRes.items) };
    }

    if (intent === "fetch_social") {
      const res = await backendFetch(`/api/landing-pages/social/${handle}`);
      return { ok: true, intent, socialItems: addSocialImagePreviewUrls(res.items) };
    }

    if (intent === "regenerate_social_item") {
      const concept = formData.get("concept") as string;
      await backendFetch("/api/landing-pages/generate-social", {
        method: "POST",
        body: JSON.stringify({ handle, concept_filter: [concept], overwrite: true })
      });
      const fetchRes = await backendFetch(`/api/landing-pages/social/${handle}`);
      return {
        ok: true,
        intent: "generate_social",
        socialItems: addSocialImagePreviewUrls(fetchRes.items),
      };
    }

    if (intent === "update_social_text") {
      const concept = formData.get("concept") as string;
      const text = formData.get("text") as string;
      await backendFetch(`/api/landing-pages/social/${handle}/${concept}`, {
        method: "PUT",
        body: JSON.stringify({ text })
      });
      return { ok: true, intent };
    }

    if (intent === "create_video_script") {
      const concept = formData.get("concept") as string;
      await backendFetch("/api/landing-pages/videos/script", {
        method: "POST",
        body: JSON.stringify({ handle, concept, shop: session.shop }),
      });
      const fetchRes = await backendFetch(`/api/landing-pages/social/${handle}`);
      return { ok: true, intent, socialItems: addSocialImagePreviewUrls(fetchRes.items) };
    }

    if (intent === "generate_video") {
      const concept = formData.get("concept") as string;
      const payload = JSON.parse(formData.get("payload") as string);
      await backendFetch(`/api/landing-pages/videos/${handle}/${concept}`, {
        method: "PUT",
        body: JSON.stringify({
          script: payload.script,
          posting_text: payload.posting_text,
        }),
      });
      await backendFetch("/api/landing-pages/videos/generate", {
        method: "POST",
        body: JSON.stringify({ handle, concept, shop: session.shop }),
      });
      const fetchRes = await backendFetch(`/api/landing-pages/social/${handle}`);
      return { ok: true, intent, socialItems: addSocialImagePreviewUrls(fetchRes.items) };
    }

    if (intent === "update_video") {
      const concept = formData.get("concept") as string;
      const payload = JSON.parse(formData.get("payload") as string);
      await backendFetch(`/api/landing-pages/videos/${handle}/${concept}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      return { ok: true, intent };
    }

    if (intent === "approve_video") {
      const concept = formData.get("concept") as string;
      const approved = formData.get("approved") === "true";
      await backendFetch(`/api/landing-pages/videos/${handle}/${concept}`, {
        method: "PUT",
        body: JSON.stringify({ approved }),
      });
      const fetchRes = await backendFetch(`/api/landing-pages/social/${handle}`);
      return { ok: true, intent, socialItems: addSocialImagePreviewUrls(fetchRes.items) };
    }

    if (intent === "publish") {
      const res = await backendFetch("/api/landing-pages/publish", {
        method: "POST",
        body: JSON.stringify({ handle, published: true })
      });
      return { ok: true, intent, publishResult: res.publication };
    }

    return { ok: false, error: "Unknown intent" };
  } catch (err: any) {
    if (intent === "create_video_script" && submittedConcept) {
      const fetchRes = await backendFetch(`/api/landing-pages/social/${handle}`).catch(() => null);
      return {
        ok: false,
        error: err.message,
        intent,
        concept: submittedConcept,
        socialItems: fetchRes?.items
          ? addSocialImagePreviewUrls(fetchRes.items)
          : null,
      };
    }
    return { ok: false, error: err.message, intent, concept: submittedConcept };
  }
};

export default function LandingPageWizard() {
  const { handle, productUrl, initialData, initialPublishResult, initialCredit } = useLoaderData<typeof loader>();
  const navigate = useNavigate();
  const submit = useSubmit();
  const actionData = useActionData<typeof action>();
  const navigation = useNavigation();
  
  const [data, setData] = useState<any>(initialData);
  const [step, setStep] = useState(initialPublishResult ? 4 : initialData ? 2 : 1);
  const [error, setError] = useState<string | null>(null);
  const [socialItems, setSocialItems] = useState<any[] | null>(null);
  const [publishResult, setPublishResult] = useState<any>(initialPublishResult);
  const [credit, setCredit] = useState<any>(initialCredit);
  const [generationJob, setGenerationJob] = useState<any>(null);

  const isSubmitting = navigation.state !== "idle";

  // If we're on step 3 but don't have social items loaded yet, fetch them
  useEffect(() => {
    if (step === 3 && !socialItems && !isSubmitting) {
      submit({ intent: "fetch_social" }, { method: "post" });
    }
  }, [step, socialItems, isSubmitting, submit]);

  useEffect(() => {
    if (actionData) {
      const result: any = actionData;
      if (result.credit) setCredit(result.credit);
      if (result.ok) {
        if (result.intent === "generate_prompts") {
          setGenerationJob(result.job);
        } else if (result.intent === "poll_generation") {
          setGenerationJob(result.job);
          if (result.job?.status === "succeeded" && result.data) {
            setData(result.data);
            setStep(2);
            setError(null);
          } else if (result.job?.status === "failed") {
            setError(result.job.message || result.job.error_message || "Generation failed.");
          }
        } else if (result.intent === "save_edits") {
          setData(result.data);
        } else if (["generate_social", "fetch_social", "create_video_script", "generate_video", "approve_video"].includes(result.intent || "")) {
          if (result.socialItems) {
            setSocialItems(result.socialItems);
          }
          setStep(3);
        } else if (result.intent === "publish") {
          setPublishResult(result.publishResult);
          setStep(4);
        }
      } else {
        setError(result.error);
        if (result.socialItems) {
          setSocialItems(result.socialItems);
          setStep(3);
        } else if (result.intent === "create_video_script" && result.concept) {
          setSocialItems((items) => items?.map((item) =>
            item.concept === result.concept
              ? {
                  ...item,
                  video: {
                    ...(item.video || {}),
                    status: "error",
                    approved: false,
                    last_error: result.error,
                  },
                }
              : item
          ) || null);
        }
      }
    }
  }, [actionData]);

  useEffect(() => {
    if (!generationJob || !["queued", "running"].includes(generationJob.status) || isSubmitting) {
      return;
    }
    const timer = window.setTimeout(() => {
      submit(
        { intent: "poll_generation", jobId: generationJob.id },
        { method: "post" },
      );
    }, 1800);
    return () => window.clearTimeout(timer);
  }, [generationJob, isSubmitting, submit]);

  const handleGeneratePrompts = () => {
    setError(null);
    setGenerationJob(null);
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

  const handleRegenerateSocialItem = (concept: string) => {
    setError(null);
    submit({ intent: "regenerate_social_item", concept }, { method: "post" });
  };

  const handleCreateVideoScript = (concept: string) => {
    setError(null);
    submit({ intent: "create_video_script", concept }, { method: "post" });
  };

  const updateLocalVideo = (concept: string, updater: (video: any) => any) => {
    setSocialItems((items) => items?.map((item) =>
      item.concept === concept ? { ...item, video: updater(item.video || {}) } : item
    ) || null);
  };

  const handleVideoScriptChange = (concept: string, field: string, value: any) => {
    updateLocalVideo(concept, (video) => ({
      ...video,
      script: { ...(video.script || {}), [field]: value },
      approved: false,
    }));
  };

  const handleVideoPostingTextChange = (concept: string, value: string) => {
    updateLocalVideo(concept, (video) => ({ ...video, posting_text: value }));
  };

  const handleSaveVideo = (item: any) => {
    if (!item.video) return;
    submit({
      intent: "update_video",
      concept: item.concept,
      payload: JSON.stringify({
        script: item.video.script,
        posting_text: item.video.posting_text,
      }),
    }, { method: "post", navigate: false });
  };

  const handleGenerateVideo = (item: any) => {
    if (!item.video?.script) return;
    setError(null);
    submit({
      intent: "generate_video",
      concept: item.concept,
      payload: JSON.stringify({
        script: item.video.script,
        posting_text: item.video.posting_text,
      }),
    }, { method: "post" });
  };

  const handleApproveVideo = (item: any) => {
    setError(null);
    submit({
      intent: "approve_video",
      concept: item.concept,
      approved: item.video?.approved ? "false" : "true",
    }, { method: "post" });
  };

  const handleUpdateSocialText = (concept: string, text: string) => {
    setError(null);
    // Update local state immediately for fast typing
    if (socialItems) {
      const updated = socialItems.map(item => item.concept === concept ? { ...item, text } : item);
      setSocialItems(updated);
    }
    // Save to backend
    submit({ intent: "update_social_text", concept, text }, { method: "post", navigate: false });
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

  const isGenerationActive = Boolean(
    generationJob && ["queued", "running"].includes(generationJob.status)
  );

  return (
    <s-page heading={`Landing Page Wizard: ${handle}`}>
      <div style={{ marginBottom: "12px" }}>
        <button onClick={() => navigate("/app/landing-pages")} style={{ border: 0, background: "transparent", color: "#005bd3", padding: 0, cursor: "pointer" }}>← Back to Landing Pages</button>
      </div>
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
        <div style={{
          margin: "14px 0",
          padding: "14px",
          borderRadius: "10px",
          border: `1px solid ${credit?.can_start ? "#86c79a" : "#e0a800"}`,
          background: credit?.can_start ? "#f0fff4" : "#fff8e6",
        }}>
          <div style={{ fontWeight: 700 }}>xAI credit preflight</div>
          <div style={{ marginTop: "6px" }}>
            Available: <strong>{credit?.available_display || "Unavailable"}</strong>
            {credit?.minimum_display && <> · Minimum required: <strong>{credit.minimum_display}</strong></>}
          </div>
          <div style={{ marginTop: "6px", color: "#4a4a4a" }}>
            {credit?.message || "Credit status has not been checked."}
          </div>
          <div style={{ marginTop: "5px", fontSize: "0.82rem", color: "#6d7175" }}>
            Generation is blocked unless this check succeeds. The check and generation do not retry.
          </div>
        </div>
        <button
          onClick={handleGeneratePrompts}
          disabled={isSubmitting || isGenerationActive || !credit?.can_start}
          style={{ padding: "8px 16px", background: "#202223", color: "#fff", borderRadius: "8px", border: "none", cursor: (isSubmitting || isGenerationActive || !credit?.can_start) ? "not-allowed" : "pointer", opacity: (isSubmitting || isGenerationActive || !credit?.can_start) ? 0.55 : 1 }}
        >
          {isGenerationActive ? "Generation running in background…" : isSubmitting && navigation.formData?.get("intent") === "generate_prompts" ? "Starting…" : "Run Generator"}
        </button>

        {generationJob && (
          <div style={{
            marginTop: "16px",
            padding: "16px",
            borderRadius: "10px",
            border: `1px solid ${generationJob.status === "failed" ? "#e57373" : generationJob.status === "succeeded" ? "#86c79a" : "#8c9196"}`,
            background: generationJob.status === "failed" ? "#fff1f0" : generationJob.status === "succeeded" ? "#f0fff4" : "#f6f6f7",
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: "12px", alignItems: "center" }}>
              <strong>
                {generationJob.status === "failed" ? "Generation failed" :
                  generationJob.status === "succeeded" ? "Generation complete" :
                  "Generation in progress"}
              </strong>
              <span>{generationJob.progress || 0}%</span>
            </div>
            <div style={{ height: "8px", background: "#d8d8d8", borderRadius: "999px", overflow: "hidden", marginTop: "10px" }}>
              <div style={{
                width: `${generationJob.progress || 0}%`,
                height: "100%",
                background: generationJob.status === "failed" ? "#b42318" : generationJob.status === "succeeded" ? "#107c41" : "#005bd3",
                transition: "width 250ms ease",
              }} />
            </div>
            <div style={{ marginTop: "10px" }}>{generationJob.message}</div>
            <div style={{ marginTop: "5px", color: "#6d7175", fontSize: "0.82rem" }}>
              Stage: {generationJob.stage} · Job: {generationJob.id}
            </div>

            {generationJob.status === "failed" && (
              <div style={{ marginTop: "14px", padding: "12px", background: "#fff", border: "1px solid #fecaca", borderRadius: "8px" }}>
                <div><strong>Error type:</strong> {generationJob.error_type || "unknown"}</div>
                <div style={{ marginTop: "6px", whiteSpace: "pre-wrap" }}>
                  <strong>Exact error:</strong> {generationJob.error_message}
                </div>
                <div style={{ marginTop: "6px", fontSize: "0.85rem" }}>
                  Attempts: <strong>1</strong> · Retries: <strong>0</strong> · Fallbacks: <strong>0</strong>
                </div>
              </div>
            )}

            {Array.isArray(generationJob.timeline) && generationJob.timeline.length > 0 && (
              <details style={{ marginTop: "12px" }}>
                <summary style={{ cursor: "pointer", fontWeight: 600 }}>Detailed progress log</summary>
                <div style={{ marginTop: "8px", display: "flex", flexDirection: "column", gap: "6px" }}>
                  {generationJob.timeline.map((entry: any, index: number) => (
                    <div key={`${entry.at}-${index}`} style={{ fontSize: "0.84rem" }}>
                      <strong>{entry.progress}% · {entry.stage}</strong> — {entry.message}
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
      </s-section>

      {/* STEP 2: Edit Text & Generate Social */}
      {step >= 2 && data && (
        <s-section heading="Step 2: Review Content & Generate Images">
          <s-paragraph>Review the generated text for each concept before rendering images.</s-paragraph>

          {data.generation_diagnostics?.status && data.generation_diagnostics.status !== "success" && (
            <div style={{ marginBottom: "16px", padding: "14px", borderRadius: "8px", background: data.generation_diagnostics.status === "error" ? "#fff1f0" : "#fff8e6", border: `1px solid ${data.generation_diagnostics.status === "error" ? "#e57373" : "#e0a800"}`, color: "#4a1f1f" }}>
              <div style={{ fontWeight: 700 }}>
                {data.generation_diagnostics.status === "error" ? "Grok generation problem" : "Persona corrected automatically"}
              </div>
              <div style={{ marginTop: "5px" }}>{data.generation_diagnostics.message}</div>
              <div style={{ marginTop: "5px", fontSize: "0.85rem" }}>
                Requested: {data.generation_diagnostics.requested_generator} ({data.generation_diagnostics.model || "model not configured"}) · Completed by: {data.generation_diagnostics.completed_by}
              </div>
            </div>
          )}

          {data.generation_diagnostics?.status === "success" && (
            <div style={{ marginBottom: "14px", color: "#4a4a4a", fontSize: "0.85rem" }}>
              Generated successfully by Grok ({data.generation_diagnostics.model}).
            </div>
          )}
          
          <div style={{ marginBottom: "20px" }}>
            <div>
              <strong>Ideal Client:</strong> {data.persona?.name}, {data.persona?.age} {data.persona?.sex}
              {data.persona?.race ? ` · ${data.persona.race}` : ""}
              {data.persona?.ethnicity && data.persona.ethnicity !== data.persona.race
                ? ` (${data.persona.ethnicity})`
                : ""}
            </div>
            {data.persona?.rationale && (
              <div style={{ marginTop: "6px", color: "#4a4a4a", lineHeight: 1.45 }}>
                <strong>Why this persona:</strong> {data.persona.rationale}
              </div>
            )}
          </div>

          <div style={{ display: "flex", gap: "20px", marginBottom: "20px", overflowX: "auto" }}>
            {data.assets?.map((asset: any, idx: number) => (
              <div key={idx} style={{ flex: "0 0 auto", width: "150px" }}>
                <img 
                  src={asset.preview_url}
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

      {/* STEP 3: Review Social & Publish Landing Page */}
      {step >= 3 && (
        <s-section heading="Step 3: Review Social & Publish Landing Page">
          <s-paragraph>
            Images and texts have been generated for your social posts. You can edit the final text or regenerate specific images below.
          </s-paragraph>

          {socialItems ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "30px", marginTop: "20px", marginBottom: "30px" }}>
              {socialItems.map((item, idx) => {
                const workingOnThis = isSubmitting && navigation.formData?.get("concept") === item.concept;
                const currentIntent = navigation.formData?.get("intent");
                const script = item.video?.script;
                return (
                  <div key={idx} style={{ padding: "20px", border: "1px solid #e1e3e5", borderRadius: "12px", background: "#fcfcfc" }}>
                    <div style={{ display: "flex", gap: "20px", flexWrap: "wrap" }}>
                      <div style={{ flex: "0 0 250px", display: "flex", flexDirection: "column", gap: "10px" }}>
                        <h4 style={{ margin: "0", fontSize: "1rem", color: "#202223" }}>{item.concept}</h4>
                        {item.image_file && (
                          <img
                            src={item.preview_url}
                            alt={item.concept}
                            style={{ width: "100%", height: "250px", objectFit: "cover", borderRadius: "8px", border: "1px solid #ccc" }}
                          />
                        )}
                        <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                          <button
                            onClick={() => handleRegenerateSocialItem(item.concept)}
                            disabled={isSubmitting}
                            style={{ flex: "1 1 105px", padding: "7px 10px", background: "#ffffff", color: "#202223", border: "1px solid #8c9196", borderRadius: "6px", cursor: isSubmitting ? "not-allowed" : "pointer", fontSize: "0.82rem" }}
                          >
                            {workingOnThis && currentIntent === "regenerate_social_item" ? "Regenerating..." : "Regenerate Image"}
                          </button>
                          <button
                            onClick={() => handleCreateVideoScript(item.concept)}
                            disabled={isSubmitting}
                            style={{ flex: "1 1 105px", padding: "7px 10px", background: "#202223", color: "#fff", border: "1px solid #202223", borderRadius: "6px", cursor: isSubmitting ? "not-allowed" : "pointer", fontSize: "0.82rem" }}
                          >
                            {workingOnThis && currentIntent === "create_video_script" ? "Writing Script..." : script ? "Rewrite Video Script" : "Create Video Script"}
                          </button>
                        </div>
                      </div>
                      <div style={{ flex: "1 1 320px" }}>
                        <label style={{ display: "block", marginBottom: "5px", fontWeight: "bold", fontSize: "0.85rem", color: "#202223" }}>Final Social Text</label>
                        <textarea
                          value={item.text}
                          onChange={(e) => handleUpdateSocialText(item.concept, e.target.value)}
                          style={{ width: "100%", height: "250px", padding: "12px", borderRadius: "8px", border: "1px solid #ccc", fontSize: "0.95rem", lineHeight: "1.4" }}
                        />
                      </div>
                    </div>

                    {item.video && (
                      <div style={{ marginTop: "20px", paddingTop: "20px", borderTop: "1px solid #d1d5db" }}>
                        <div style={{ display: "flex", justifyContent: "space-between", gap: "12px", alignItems: "center", flexWrap: "wrap", marginBottom: "14px" }}>
                          <div>
                            <strong>UGC lip-sync video</strong>
                            {script?.duration_seconds && <span style={{ marginLeft: "8px", color: "#4b5563" }}>{script.duration_seconds} seconds · Grok chose this length · 480p · vertical 9:16</span>}
                          </div>
                          <span style={{ padding: "4px 9px", borderRadius: "999px", background: item.video.approved ? "#dcfce7" : item.video.status === "error" ? "#fee2e2" : "#fef3c7", color: item.video.approved ? "#166534" : item.video.status === "error" ? "#991b1b" : "#92400e", fontSize: "0.78rem", fontWeight: 700 }}>
                            {item.video.approved ? "Approved for publishing" : item.video.status === "error" ? "Generation failed" : item.video.video_file ? "Ready for approval" : "Script ready"}
                          </span>
                        </div>

                        {item.video.last_error && (
                          <div style={{ marginBottom: "14px", padding: "10px", borderRadius: "7px", background: "#fff1f0", color: "#991b1b", border: "1px solid #fecaca" }}>
                            Exact Grok error: {item.video.last_error}
                          </div>
                        )}

                        {script?.speech_warning && (
                          <div style={{ marginBottom: "14px", padding: "10px", borderRadius: "7px", background: "#fff8e6", color: "#7a4b00", border: "1px solid #f1c56b" }}>
                            Speech timing warning: {script.speech_warning}
                          </div>
                        )}

                        {item.video.preview_url && (
                          <div style={{ maxWidth: "360px", marginBottom: "16px" }}>
                            <video controls playsInline preload="metadata" poster={item.preview_url} src={item.video.preview_url} style={{ display: "block", width: "100%", maxHeight: "640px", background: "#000", borderRadius: "10px" }} />
                            <a href={item.video.preview_url} download={item.video.video_file} style={{ display: "inline-block", marginTop: "8px", color: "#005bd3" }}>Download video</a>
                          </div>
                        )}

                        {script && (
                          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "14px" }}>
                            <div style={{ gridColumn: "1 / -1" }}>
                              <label style={{ display: "block", marginBottom: "5px", fontWeight: 700, fontSize: "0.85rem" }}>Exact Spoken Script for Lip-Sync</label>
                              <textarea
                                value={script.spoken_script || ""}
                                onChange={(e) => handleVideoScriptChange(item.concept, "spoken_script", e.target.value)}
                                onBlur={() => handleSaveVideo(item)}
                                style={{ width: "100%", minHeight: "95px", padding: "11px", borderRadius: "8px", border: "1px solid #ccc", lineHeight: 1.45 }}
                              />
                              <div style={{ marginTop: "5px", color: "#4b5563", fontSize: "0.78rem" }}>
                                These exact words are injected into the final Grok video request. Keep the dialogue natural and short enough for {script.duration_seconds} seconds.
                              </div>
                            </div>
                            <div>
                              <label style={{ display: "block", marginBottom: "5px", fontWeight: 700, fontSize: "0.85rem" }}>Detailed UGC Video Prompt</label>
                              <textarea
                                value={script.video_prompt || ""}
                                onChange={(e) => handleVideoScriptChange(item.concept, "video_prompt", e.target.value)}
                                onBlur={() => handleSaveVideo(item)}
                                style={{ width: "100%", minHeight: "230px", padding: "11px", borderRadius: "8px", border: "1px solid #ccc", lineHeight: 1.45 }}
                              />
                            </div>
                            <div>
                              <label style={{ display: "block", marginBottom: "5px", fontWeight: 700, fontSize: "0.85rem" }}>Shot-by-shot Script</label>
                              <div style={{ minHeight: "230px", padding: "11px", borderRadius: "8px", border: "1px solid #ccc", background: "#fff", fontSize: "0.85rem", lineHeight: 1.45 }}>
                                {(script.scenes || []).map((scene: any, sceneIndex: number) => (
                                  <div key={sceneIndex} style={{ marginBottom: "12px" }}>
                                    <strong>{scene.start_second}–{scene.end_second}s</strong>: {scene.visual_action}
                                    {scene.camera && <div><strong>Camera:</strong> {scene.camera}</div>}
                                    {scene.on_screen_text && <div><strong>On-screen:</strong> {scene.on_screen_text}</div>}
                                    {(scene.speech || scene.voiceover) && <div><strong>Speech:</strong> {scene.speech || scene.voiceover}</div>}
                                  </div>
                                ))}
                              </div>
                            </div>
                            <div style={{ gridColumn: "1 / -1" }}>
                              <label style={{ display: "block", marginBottom: "5px", fontWeight: 700, fontSize: "0.85rem" }}>Video Posting Text</label>
                              <textarea
                                value={item.video.posting_text || ""}
                                onChange={(e) => handleVideoPostingTextChange(item.concept, e.target.value)}
                                onBlur={() => handleSaveVideo(item)}
                                style={{ width: "100%", minHeight: "105px", padding: "11px", borderRadius: "8px", border: "1px solid #ccc", lineHeight: 1.45 }}
                              />
                            </div>
                          </div>
                        )}

                        <div style={{ display: "flex", gap: "10px", marginTop: "14px", flexWrap: "wrap" }}>
                          {script && (
                            <button
                              onClick={() => handleGenerateVideo(item)}
                              disabled={isSubmitting}
                              style={{ padding: "9px 16px", background: "#005bd3", color: "#fff", border: 0, borderRadius: "7px", cursor: isSubmitting ? "not-allowed" : "pointer", fontWeight: 700 }}
                            >
                              {workingOnThis && currentIntent === "generate_video" ? "Generating Video (may take a few minutes)..." : item.video.video_file ? "Regenerate Video" : "Generate Video"}
                            </button>
                          )}
                          {item.video.video_file && (
                            <button
                              onClick={() => handleApproveVideo(item)}
                              disabled={isSubmitting}
                              style={{ padding: "9px 16px", background: item.video.approved ? "#fff" : "#107c41", color: item.video.approved ? "#107c41" : "#fff", border: "1px solid #107c41", borderRadius: "7px", cursor: isSubmitting ? "not-allowed" : "pointer", fontWeight: 700 }}
                            >
                              {workingOnThis && currentIntent === "approve_video" ? "Saving..." : item.video.approved ? "Remove Approval" : "Approve Video"}
                            </button>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <div style={{ padding: "20px", textAlign: "center", color: "#6d7175" }}>
              Loading social items...
            </div>
          )}

          <div style={{ marginTop: "30px", paddingTop: "20px", borderTop: "1px solid #e1e3e5" }}>
            <s-paragraph>
              Once you are happy with the social posts, click below to assemble the Shopify Landing Page, upload assets, and prepare the RSS feed.
            </s-paragraph>
            <button
              onClick={handlePublish}
              disabled={isSubmitting}
              style={{ padding: "8px 16px", background: "#107c41", color: "#fff", borderRadius: "8px", border: "none", cursor: isSubmitting ? "not-allowed" : "pointer", fontSize: "1rem" }}
            >
              {isSubmitting && navigation.formData?.get("intent") === "publish" ? "Publishing..." : "Publish Page & RSS Feed"}
            </button>
          </div>
        </s-section>
      )}

      {step >= 4 && (
        <s-section heading="Published & Verified">
          <div style={{ color: "#107c41", background: "#dff6dd", padding: "14px", borderRadius: "12px", border: "1px solid #107c41" }}>
            The landing page and this product&apos;s RSS section were published successfully without creating duplicates.
          </div>
          {publishResult?.page && (
            <div style={{ marginTop: "18px", padding: "16px", border: "1px solid #d1d5db", borderRadius: "10px" }}>
              <h3 style={{ margin: "0 0 10px" }}>Shopify landing page</h3>
              <div style={{ marginBottom: "8px" }}>
                Status: <strong>{publishResult.page.action === "updated" ? "Existing page updated" : "New page created"}</strong>
              </div>
              <a href={publishResult.page.url} target="_blank" rel="noreferrer" style={{ color: "#005bd3", wordBreak: "break-all" }}>
                {publishResult.page.url}
              </a>
            </div>
          )}
          {publishResult?.rss && (
            <div style={{ marginTop: "18px", padding: "16px", border: "1px solid #d1d5db", borderRadius: "10px" }}>
              <h3 style={{ margin: "0 0 10px" }}>RSS section for this product</h3>
              <div style={{ marginBottom: "6px" }}>
                Status: <strong>{publishResult.rss.action === "updated" ? "Existing product entries replaced" : "New product entries created"}</strong>
              </div>
              <div style={{ marginBottom: "10px" }}>
                Entries: <strong>{publishResult.rss.entry_count}</strong>
                {publishResult.rss.replaced_count > 0 ? ` (${publishResult.rss.replaced_count} previous entries replaced)` : ""}
              </div>
              <a href={publishResult.rss.feed_url} target="_blank" rel="noreferrer" style={{ color: "#005bd3", wordBreak: "break-all" }}>
                {publishResult.rss.feed_url}
              </a>
              <div style={{ display: "flex", flexDirection: "column", gap: "12px", marginTop: "16px" }}>
                {publishResult.rss.entries?.map((entry: any) => (
                  <div key={entry.guid} style={{ display: "flex", gap: "14px", padding: "12px", background: "#f6f6f7", borderRadius: "8px" }}>
                    {entry.image_url && (
                      <img src={entry.image_url} alt={entry.concept} style={{ width: "90px", height: "90px", objectFit: "cover", borderRadius: "6px" }} />
                    )}
                    {entry.video_url && (
                      <video controls playsInline preload="metadata" src={entry.video_url} style={{ width: "90px", height: "140px", objectFit: "cover", borderRadius: "6px", background: "#000" }} />
                    )}
                    <div style={{ minWidth: 0 }}>
                      <strong>{entry.title}</strong>
                      <div style={{ marginTop: "5px", color: "#4b5563" }}>Concept: {entry.concept}</div>
                      <div style={{ marginTop: "5px", color: "#6b7280", fontFamily: "monospace", fontSize: "0.78rem", wordBreak: "break-all" }}>
                        GUID: {entry.guid}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </s-section>
      )}
    </s-page>
  );
}
