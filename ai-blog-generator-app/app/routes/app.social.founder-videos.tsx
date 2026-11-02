import { useEffect, useState } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useLoaderData, useNavigation, useRevalidator } from "react-router";

import { useSocial } from "../components/social/context";
import { providerLabel } from "../components/social/vocab";
import { ActionBanner, Badge, Banner, Card, EmptyState, Field, Hidden, LoadError, SectionHeader, TimeText, useBusy } from "../components/social/ui";
import {
  SocialEngineError, errorMessage, formString, runIntent, safeId, seFetch, seRequest, seg, type Product,
} from "../lib/social-engine.server";
import { authenticate } from "../shopify.server";

export const handle = { title: "Founder videos" };

type FounderPost = { id: string; status: string; title: string; content_type: string; content_type_label: string; scheduled_at: number | null };
type FounderVideo = {
  id: string; source: string; title: string; note: string; original_name: string; youtube_url: string;
  product_ids: string[]; status: string; status_label: string; error: string; duration_ms: number;
  clips: Array<{ id?: string; title?: string; content?: string; status?: string }>;
  clip_post_ids: string[]; full_post_id: string; posts: FounderPost[]; created_at: number; updated_at: number;
};
type FounderResponse = {
  videos: FounderVideo[]; drop_folder: string; processed_folder: string; waiting_files: string[];
  settings: { clips_per_video: number; clip_providers: string[]; full_video_providers: string[]; approval_mode: string; max_source_minutes: number };
  extensions: string[];
};

/** Through Cloudflare the embedded app can only accept uploads under ~100 MB; bigger files go in the drop folder. */
const UPLOAD_LIMIT_MB = 95;

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  try {
    const [data, products] = await Promise.all([
      seFetch<FounderResponse>("/founder-videos"),
      seFetch<{ products: Product[] }>("/products", { query: { available_only: true, page_size: 200 } }).catch(() => ({ products: [] as Product[] })),
    ]);
    return { ...data, products: products.products, error: "" };
  } catch (error) {
    return {
      videos: [] as FounderVideo[], drop_folder: "", processed_folder: "", waiting_files: [] as string[],
      settings: null as FounderResponse["settings"] | null, extensions: [] as string[], products: [] as Product[],
      error: errorMessage(error),
    };
  }
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  return runIntent(intent, async () => {
    switch (intent) {
      case "add_url": {
        const url = formString(form, "url");
        if (!url) throw new SocialEngineError("Paste a YouTube link first.");
        const product = formString(form, "product_id");
        await seFetch("/founder-videos/url", { body: { url, note: formString(form, "note"), product_ids: product ? [product] : [] } });
        return { message: "YouTube video added. Postiz starts cutting clips within a few minutes; they appear in the approval queue." };
      }
      case "upload": {
        const file = form.get("file");
        if (!(file instanceof File) || !file.size) throw new SocialEngineError("Choose a video file first.");
        if (file.size > UPLOAD_LIMIT_MB * 1024 * 1024) {
          throw new SocialEngineError(`${file.name} is ${(file.size / 1048576).toFixed(0)} MB. Files over ${UPLOAD_LIMIT_MB} MB go in the drop folder on the Mac instead.`);
        }
        const upstream = new FormData();
        upstream.set("file", file, file.name);
        upstream.set("note", formString(form, "note"));
        upstream.set("product_ids", formString(form, "product_id"));
        const response = await seRequest("/api/social-engine/founder-videos/upload", { method: "POST", body: upstream, timeoutMs: 600_000 });
        if (!response.ok) {
          const body = await response.json().catch(() => ({})) as { detail?: string; error?: { message?: string } };
          throw new SocialEngineError(body.error?.message || body.detail || `Upload failed (HTTP ${response.status}).`);
        }
        return { message: `${file.name} uploaded. The full video is queued for YouTube and shows in the approval queue shortly.` };
      }
      case "scan": {
        const result = await seFetch<{ already_queued: boolean }>("/founder-videos/scan", { method: "POST" });
        return { message: result.already_queued ? "A check is already running." : "Checking the drop folder and Postiz now. Refresh in a minute." };
      }
      case "retry": {
        await seFetch(`/founder-videos/${seg(safeId(formString(form, "video_id")))}/retry`, { method: "POST" });
        return { message: "Retrying." };
      }
      case "delete": {
        await seFetch(`/founder-videos/${seg(safeId(formString(form, "video_id")))}/delete`, { method: "POST" });
        return { message: "Removed." };
      }
      default:
        throw new SocialEngineError(`Unknown action "${intent}".`);
    }
  }, formString(form, "video_id") || undefined);
};

const STATUS_TONE: Record<string, "neutral" | "info" | "success" | "warning" | "critical"> = {
  new: "neutral", awaiting_youtube: "info", clipping: "info", done: "success", failed: "critical",
};

function minutes(ms: number): string {
  if (!ms) return "";
  const total = Math.round(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")} min`;
}

export default function FounderVideos() {
  const data = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>();
  const { tz } = useSocial();
  const { busy } = useBusy();
  const navigation = useNavigation();
  const revalidator = useRevalidator();
  const [fileName, setFileName] = useState("");
  const inProgress = data.videos.some((video) => video.status === "awaiting_youtube" || video.status === "clipping");

  useEffect(() => {
    if (!inProgress) return;
    const timer = setInterval(() => { if (navigation.state === "idle") revalidator.revalidate(); }, 30_000);
    return () => clearInterval(timer);
  }, [inProgress, navigation.state, revalidator]);

  if (data.error) return <LoadError error={data.error} />;
  const uploading = navigation.state === "submitting" && navigation.formData?.get("intent") === "upload";
  const providers = (data.settings?.clip_providers || []).map(providerLabel);

  return (
    <div className="sa-stack">
      <ActionBanner result={result} />

      <Card>
        <SectionHeader
          title="Record once, post everywhere"
          description={
            <>
              Film a long, natural video on your phone: you on the acupressure mat, the massage gun after a run, why you wear the wide-toe shoes.
              The full video is posted to YouTube, then Postiz cuts it into {data.settings?.clips_per_video || 5} short clips that go to{" "}
              {providers.join(", ") || "the video channels"}, one a day. Every post waits in the{" "}
              <Link to="/app/social/queue">approval queue</Link> first.
            </>
          }
        />
        <div className="sa-grid sa-grid-2">
          <Card muted>
            <h3 style={{ margin: "0 0 8px" }}>1. Easiest: drop the file in the folder on the Mac</h3>
            <p>AirDrop the video to the Mac, then move it into:</p>
            <pre className="sa-break" style={{ whiteSpace: "pre-wrap", userSelect: "all" }}>{data.drop_folder}</pre>
            <p>
              Name the file after what it shows, e.g. <em>massage gun after a run.mov</em>, so the right product link is attached.
              It is picked up within five minutes (there is a READ ME in the folder).
              {data.waiting_files.length ? <> Waiting to be picked up: <strong>{data.waiting_files.join(", ")}</strong>.</> : null}
            </p>
            <Form method="post"><Hidden values={{ intent: "scan" }} /><button type="submit" className="sa-btn" disabled={busy}>Check now</button></Form>
          </Card>
          <Card muted>
            <h3 style={{ margin: "0 0 8px" }}>2. Already on YouTube? Paste the link</h3>
            <Form method="post" className="sa-stack">
              <Hidden values={{ intent: "add_url" }} />
              <Field label="YouTube link"><input name="url" type="url" required placeholder="https://youtu.be/…" className="sa-input" /></Field>
              <Field label="What it's about (optional)" help="A few words; the product is matched from them.">
                <input name="note" type="text" maxLength={200} className="sa-input" placeholder="massage gun after a run" />
              </Field>
              <ProductSelect products={data.products} />
              <button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>Cut clips from this video</button>
            </Form>
          </Card>
        </div>
        <Card muted>
          <h3 style={{ margin: "0 0 8px" }}>3. Or upload a small file here (under {UPLOAD_LIMIT_MB} MB)</h3>
          <Form method="post" encType="multipart/form-data" className="sa-stack">
            <Hidden values={{ intent: "upload" }} />
            <Field label="Video file" help={`Accepted: ${data.extensions.join(", ")}. Bigger files: use the drop folder.`}>
              <input name="file" type="file" accept="video/*" required className="sa-input" onChange={(event) => setFileName(event.target.files?.[0]?.name || "")} />
            </Field>
            <Field label="What it's about (optional)"><input name="note" type="text" maxLength={200} className="sa-input" /></Field>
            <ProductSelect products={data.products} />
            <button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>{uploading ? `Uploading ${fileName}…` : "Upload and post"}</button>
          </Form>
        </Card>
      </Card>

      {data.videos.length === 0 ? (
        <EmptyState title="No founder videos yet">Drop your first recording in the folder above, or paste a YouTube link.</EmptyState>
      ) : (
        <div className="sa-stack">
          {data.videos.map((video) => (
            <Card key={video.id} accent={STATUS_TONE[video.status] === "critical" ? "critical" : video.status === "done" ? "success" : "neutral"}>
              <div className="sa-row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
                <div>
                  <strong>{video.title}</strong>
                  <div className="sa-muted">
                    {video.source === "url" ? "YouTube link" : video.source === "folder" ? "Drop folder" : "Upload"}
                    {video.original_name ? ` · ${video.original_name}` : ""}{video.duration_ms ? ` · ${minutes(video.duration_ms)}` : ""}
                    {" · added "}<TimeText epoch={video.created_at} tz={tz} relative />
                  </div>
                </div>
                <Badge tone={STATUS_TONE[video.status] || "neutral"}>{video.status_label}</Badge>
              </div>
              {video.error ? <Banner tone={video.status === "failed" ? "critical" : "warning"}>{video.error}</Banner> : null}
              {video.youtube_url ? <p>On YouTube: <a href={video.youtube_url} target="_blank" rel="noreferrer">{video.youtube_url}</a></p> : null}
              {video.posts.length ? (
                <ul>
                  {video.posts.map((post) => (
                    <li key={post.id}>
                      <Link to={`/app/social/posts/${post.id}`}>{post.content_type_label}: {post.title || "Untitled"}</Link>
                      {" "}<Badge tone={post.status === "published" ? "success" : post.status === "failed" || post.status === "rejected" ? "critical" : "neutral"}>{post.status.replace(/_/g, " ")}</Badge>
                      {post.scheduled_at ? <span className="sa-muted"> · <TimeText epoch={post.scheduled_at} tz={tz} /></span> : null}
                    </li>
                  ))}
                </ul>
              ) : null}
              <div className="sa-row">
                {video.status === "failed" || video.status === "clipping" || video.status === "awaiting_youtube" ? (
                  <Form method="post"><Hidden values={{ intent: "retry", video_id: video.id }} /><button type="submit" className="sa-btn" disabled={busy}>{video.status === "failed" ? "Retry" : "Check again"}</button></Form>
                ) : null}
                {video.status === "failed" || video.status === "done" || video.status === "new" ? (
                  <Form method="post"><Hidden values={{ intent: "delete", video_id: video.id }} /><button type="submit" className="sa-btn sa-btn-plain" disabled={busy}>Remove</button></Form>
                ) : null}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function ProductSelect({ products }: { products: Product[] }) {
  return (
    <Field label="Product to link (optional)" help="Leave on automatic to match from the words above.">
      <select name="product_id" className="sa-input" defaultValue="">
        <option value="">Automatic</option>
        {products.map((product) => <option key={product.product_id} value={String(product.product_id)}>{product.title}</option>)}
      </select>
    </Field>
  );
}
