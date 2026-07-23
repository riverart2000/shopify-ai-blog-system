import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, useActionData, useLoaderData, useNavigation } from "react-router";
import { authenticate } from "../shopify.server";
import { reviewsBackendConfigured, reviewsBackendJson } from "../lib/reviews.server";

type Review = {
  id: string; review_type: "product" | "store"; product_handle: string; product_title: string;
  rating: number; review_title: string; review_body: string; reviewer_name: string;
  reviewer_email: string; status: string; merchant_reply: string; moderation_flags: string[];
  moderation_note: string; photo_data: string; photo_url: string; verified_purchase: boolean;
  source: string; source_path: string; created_at: number; published_at?: number;
};
type ReviewData = {
  store_id: string; shop: string; total: number; reviews: Review[];
  summary: { total: number; pending: number; published: number; not_published: number; awaiting_reply: number; facebook: number; average: number };
};

const EMPTY: ReviewData = {
  store_id: "", shop: "", total: 0, reviews: [],
  summary: { total: 0, pending: 0, published: 0, not_published: 0, awaiting_reply: 0, facebook: 0, average: 0 },
};

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const context = await authenticate.admin(request);
  const url = new URL(request.url);
  const status = url.searchParams.get("status") ?? "pending";
  const reviewType = url.searchParams.get("review_type") ?? "";
  if (!reviewsBackendConfigured()) return { data: EMPTY, error: "Reviews backend is not configured.", status, reviewType, apiKey: "", shop: context.session.shop };
  try {
    const query = new URLSearchParams({ shop: context.session.shop, status, review_type: reviewType, limit: "200" });
    const data = await reviewsBackendJson(`/api/reviews?${query.toString()}`) as unknown as ReviewData;
    return { data, error: "", status, reviewType, apiKey: process.env.SHOPIFY_API_KEY || "", shop: context.session.shop };
  } catch (error) {
    return { data: EMPTY, error: error instanceof Error ? error.message : "Reviews could not be loaded.", status, reviewType, apiKey: process.env.SHOPIFY_API_KEY || "", shop: context.session.shop };
  }
};

export const action = async ({ request }: ActionFunctionArgs) => {
  const context = await authenticate.admin(request);
  if (!reviewsBackendConfigured()) return { ok: false, error: "Reviews backend is not configured." };
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  const reviewId = String(form.get("review_id") || "");
  try {
    if (intent === "delete") {
      const result = await reviewsBackendJson("/api/reviews/delete", {
        method: "POST", body: JSON.stringify({ shop: context.session.shop, review_id: reviewId }),
      });
      return { ok: true, message: String(result.message || "Review deleted.") };
    }
    if (intent === "sync-cache") {
      const result = await reviewsBackendJson("/api/reviews/sync-cache", {
        method: "POST", body: JSON.stringify({ shop: context.session.shop }),
      });
      return { ok: true, message: String(result.message || "Shopify review cache refreshed.") };
    }
    if (intent === "import-facebook") {
      const result = await reviewsBackendJson("/api/reviews/import-external", {
        method: "POST",
        body: JSON.stringify({
          shop: context.session.shop,
          source: "facebook",
          reviewer_name: String(form.get("reviewer_name") || ""),
          review_body: String(form.get("review_body") || ""),
          recommendation: String(form.get("recommendation") || "recommends"),
          source_url: String(form.get("source_url") || ""),
          review_date: String(form.get("review_date") || ""),
          confirmed_complete: form.get("confirmed_complete") === "1",
        }),
      });
      return { ok: true, message: String(result.message || "Facebook recommendation imported.") };
    }
    if (intent === "moderate") {
      const result = await reviewsBackendJson("/api/reviews/moderate", {
        method: "POST",
        body: JSON.stringify({
          shop: context.session.shop, review_id: reviewId,
          status: String(form.get("status") || "pending"),
          merchant_reply: String(form.get("merchant_reply") || ""),
          moderation_note: String(form.get("moderation_note") || ""),
        }),
      });
      return { ok: true, message: String(result.message || "Review updated.") };
    }
    return { ok: false, error: "Unknown review action." };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "Review action failed." };
  }
};

function formatDate(value: number) {
  return new Date(value * 1000).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

function Alert({ tone, children }: { tone: "success" | "error" | "info"; children: React.ReactNode }) {
  const colours = tone === "success" ? ["#ecfdf5", "#a7f3d0", "#065f46"] : tone === "error" ? ["#fef2f2", "#fecaca", "#991b1b"] : ["#eff6ff", "#bfdbfe", "#1e40af"];
  return <div style={{ background: colours[0], border: `1px solid ${colours[1]}`, color: colours[2], borderRadius: 12, padding: "12px 16px", fontSize: ".875rem" }}>{children}</div>;
}

function Metric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return <div style={{ border: "1px solid #e5e7eb", borderRadius: 14, padding: 16, background: "white" }}>
    <div style={{ color: "#6b7280", fontSize: ".72rem", textTransform: "uppercase", letterSpacing: ".05em", fontWeight: 700 }}>{label}</div>
    <div style={{ fontSize: "1.55rem", fontWeight: 750, marginTop: 5 }}>{value}</div>
    {detail ? <div style={{ color: "#6b7280", fontSize: ".76rem", marginTop: 2 }}>{detail}</div> : null}
  </div>;
}

const button: React.CSSProperties = { border: 0, borderRadius: 8, padding: "8px 13px", fontWeight: 700, cursor: "pointer", fontSize: ".78rem" };
const input: React.CSSProperties = { width: "100%", boxSizing: "border-box", border: "1px solid #c9cccf", borderRadius: 8, padding: "8px 10px", font: "inherit" };

export default function ReviewsAdmin() {
  const { data, error, status, reviewType, apiKey, shop } = useLoaderData<typeof loader>();
  const actionData = useActionData<typeof action>() as { ok?: boolean; message?: string; error?: string } | undefined;
  const navigation = useNavigation();
  const busy = navigation.state !== "idle";
  const productBlockUrl = `https://${shop}/admin/themes/current/editor?template=product&addAppBlockId=${encodeURIComponent(apiKey)}/product-reviews&target=newAppsSection`;
  const starsBlockUrl = `https://${shop}/admin/themes/current/editor?template=product&addAppBlockId=${encodeURIComponent(apiKey)}/review-stars&target=mainSection`;
  const storeBlockUrl = `https://${shop}/admin/themes/current/editor?template=index&addAppBlockId=${encodeURIComponent(apiKey)}/store-reviews&target=newAppsSection`;

  return <s-page heading="Customer Reviews">
    {error ? <s-section><Alert tone="error">{error}</Alert></s-section> : null}
    {actionData?.message ? <s-section><Alert tone="success">{actionData.message}</Alert></s-section> : null}
    {actionData?.error ? <s-section><Alert tone="error">{actionData.error}</Alert></s-section> : null}

    <s-section heading="Genuine product and store feedback">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 18, flexWrap: "wrap", alignItems: "flex-start" }}>
        <div style={{ maxWidth: 720, color: "#4b5563", lineHeight: 1.55 }}>
          Every customer submission is held here before publication. Approve relevant feedback regardless of rating; reject only spam, abuse, personal information or content unrelated to the selected product. Customer emails remain private.
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <a href={starsBlockUrl} target="_top" style={{ ...button, background: "white", color: "#202223", border: "1px solid #c9cccf", textDecoration: "none" }}>Add rating stars</a>
          <a href={productBlockUrl} target="_top" style={{ ...button, background: "#111827", color: "white", textDecoration: "none" }}>Add product reviews</a>
          <a href={storeBlockUrl} target="_top" style={{ ...button, background: "white", color: "#202223", border: "1px solid #c9cccf", textDecoration: "none" }}>Add store reviews</a>
          <a href="/app/reviews-export" style={{ ...button, background: "white", color: "#202223", border: "1px solid #c9cccf", textDecoration: "none" }}>Export CSV</a>
          <Form method="post"><input type="hidden" name="intent" value="sync-cache" /><button type="submit" disabled={busy} style={{ ...button, background: "white", color: "#202223", border: "1px solid #c9cccf" }}>Refresh Shopify cache</button></Form>
        </div>
      </div>
    </s-section>

    <s-section heading="Facebook recommendations">
      <div style={{ display: "grid", gap: 14 }}>
        <Alert tone="info">
          Import genuine public recommendations from the BioLuxeLab Facebook Reviews page. They are labelled as Facebook recommendations, link to the original source and remain separate from the store&apos;s first-party star average.
        </Alert>
        <details style={{ border: "1px solid #d8dee4", borderRadius: 12, background: "white", padding: "12px 14px" }}>
          <summary style={{ cursor: "pointer", fontWeight: 750 }}>Import a Facebook review</summary>
          <Form method="post" style={{ display: "grid", gap: 11, marginTop: 14, maxWidth: 760 }}>
            <input type="hidden" name="intent" value="import-facebook" />
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 11 }}>
              <label style={{ display: "grid", gap: 4, fontSize: ".8rem" }}><span>Reviewer&apos;s public name</span><input name="reviewer_name" required minLength={2} maxLength={80} style={input} /></label>
              <label style={{ display: "grid", gap: 4, fontSize: ".8rem" }}><span>Facebook recommendation</span><select name="recommendation" style={input}><option value="recommends">Recommends BioLuxeLab</option><option value="does_not_recommend">Does not recommend BioLuxeLab</option></select></label>
              <label style={{ display: "grid", gap: 4, fontSize: ".8rem" }}><span>Original review date</span><input type="date" name="review_date" style={input} /></label>
            </div>
            <label style={{ display: "grid", gap: 4, fontSize: ".8rem" }}><span>Original Facebook review link</span><input type="url" name="source_url" defaultValue="https://www.facebook.com/bioluxelab/reviews" required style={input} /></label>
            <label style={{ display: "grid", gap: 4, fontSize: ".8rem" }}><span>Review text exactly as the customer wrote it</span><textarea name="review_body" required minLength={2} maxLength={3000} rows={4} style={{ ...input, resize: "vertical" }} /></label>
            <label style={{ display: "flex", gap: 8, alignItems: "flex-start", color: "#4b5563", fontSize: ".8rem", lineHeight: 1.45 }}><input type="checkbox" name="confirmed_complete" value="1" required style={{ marginTop: 2 }} /><span>I confirm this is genuine public feedback and that I will import Facebook feedback consistently, including negative recommendations.</span></label>
            <div><button type="submit" disabled={busy} style={{ ...button, background: "#1877f2", color: "white" }}>Import and publish Facebook review</button></div>
          </Form>
        </details>
      </div>
    </s-section>

    <s-section heading="Review totals">
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(145px, 1fr))", gap: 10 }}>
        <Metric label="All reviews" value={String(data.summary.total)} />
        <Metric label="Pending" value={String(data.summary.pending)} detail="Needs moderation" />
        <Metric label="Published" value={String(data.summary.published)} />
        <Metric label="Site average" value={data.summary.published - data.summary.facebook > 0 ? `${data.summary.average.toFixed(1)} ★` : "—"} detail="Facebook kept separate" />
        <Metric label="Facebook" value={String(data.summary.facebook)} detail="Imported recommendations" />
        <Metric label="Needs reply" value={String(data.summary.awaiting_reply)} />
      </div>
    </s-section>

    <s-section heading={`Moderation queue (${data.total})`}>
      <Form method="get" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end", marginBottom: 16 }}>
        <label style={{ display: "grid", gap: 4, fontSize: ".8rem" }}><span>Status</span><select name="status" defaultValue={status} style={{ ...input, minWidth: 150 }}><option value="">All statuses</option><option value="pending">Pending</option><option value="published">Published</option><option value="hidden">Hidden</option><option value="rejected">Rejected</option><option value="spam">Spam</option></select></label>
        <label style={{ display: "grid", gap: 4, fontSize: ".8rem" }}><span>Review type</span><select name="review_type" defaultValue={reviewType} style={{ ...input, minWidth: 150 }}><option value="">Product and store</option><option value="product">Product</option><option value="store">Store</option></select></label>
        <button type="submit" style={{ ...button, background: "#202223", color: "white" }}>Apply filters</button>
      </Form>

      <div style={{ display: "grid", gap: 14 }}>
        {data.reviews.map(review => <article key={review.id} style={{ border: "1px solid #e5e7eb", borderLeft: `4px solid ${review.rating >= 4 ? "#16a34a" : review.rating === 3 ? "#d97706" : "#dc2626"}`, borderRadius: 12, padding: 16, background: "white" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
            <div>
              {review.source === "facebook"
                ? <div style={{ color: "#1877f2", fontWeight: 800 }}>Facebook · {review.rating >= 4 ? "Recommends BioLuxeLab" : "Does not recommend BioLuxeLab"}</div>
                : <div style={{ color: "#d97706", letterSpacing: 1, fontWeight: 800 }}>{"★".repeat(review.rating)}{"☆".repeat(5 - review.rating)}</div>}
              <h3 style={{ margin: "5px 0 2px", fontSize: "1rem" }}>{review.review_title}</h3>
              <div style={{ color: "#6b7280", fontSize: ".75rem" }}>{review.review_type === "product" ? review.product_title || review.product_handle : "BioLuxeLab store review"} · {formatDate(review.created_at)}</div>
            </div>
            <div style={{ display: "flex", gap: 6, alignItems: "flex-start", flexWrap: "wrap" }}>
              <span style={{ borderRadius: 999, padding: "4px 9px", background: "#f3f4f6", fontSize: ".7rem", fontWeight: 750, textTransform: "uppercase" }}>{review.status}</span>
              {review.source === "facebook" ? <span style={{ borderRadius: 999, padding: "4px 9px", background: "#e7f0ff", color: "#145dbf", fontSize: ".7rem", fontWeight: 750 }}>Facebook source</span> : null}
              {review.verified_purchase ? <span style={{ borderRadius: 999, padding: "4px 9px", background: "#dcfce7", color: "#166534", fontSize: ".7rem", fontWeight: 750 }}>Verified purchase</span> : null}
              {review.moderation_flags.map(flag => <span key={flag} style={{ borderRadius: 999, padding: "4px 9px", background: "#fff7ed", color: "#9a3412", fontSize: ".7rem" }}>{flag.replaceAll("_", " ")}</span>)}
            </div>
          </div>
          <p style={{ whiteSpace: "pre-wrap", lineHeight: 1.55, color: "#374151" }}>{review.review_body}</p>
          <div style={{ color: "#4b5563", fontSize: ".8rem" }}><strong>{review.reviewer_name}</strong>{review.reviewer_email ? <> · <span title="Private customer email">{review.reviewer_email}</span></> : null}{review.source === "facebook" && review.source_path ? <> · <a href={review.source_path} target="_blank" rel="noreferrer">View original on Facebook</a></> : null}</div>
          {review.photo_data || review.photo_url ? <img src={review.photo_url || review.photo_data} alt="Customer-submitted review" style={{ marginTop: 12, width: 150, maxHeight: 150, objectFit: "cover", borderRadius: 10, border: "1px solid #e5e7eb" }} /> : null}

          <Form method="post" style={{ marginTop: 14, display: "grid", gap: 9 }}>
            <input type="hidden" name="intent" value="moderate" /><input type="hidden" name="review_id" value={review.id} />
            <label style={{ display: "grid", gap: 4, fontSize: ".8rem" }}><span>Public BioLuxeLab response</span><textarea name="merchant_reply" defaultValue={review.merchant_reply} rows={2} style={{ ...input, resize: "vertical" }} placeholder="Optional response shown beneath the customer review" /></label>
            <label style={{ display: "grid", gap: 4, fontSize: ".8rem" }}><span>Private moderation note</span><input name="moderation_note" defaultValue={review.moderation_note} style={input} placeholder="Why it was rejected, hidden or flagged" /></label>
            <div style={{ display: "flex", gap: 7, flexWrap: "wrap" }}>
              <button type="submit" name="status" value="published" disabled={busy} style={{ ...button, background: "#166534", color: "white" }}>{review.status === "published" ? "Save response" : "Approve & publish"}</button>
              <button type="submit" name="status" value="hidden" disabled={busy} style={{ ...button, background: "#f3f4f6", color: "#374151" }}>Hide</button>
              <button type="submit" name="status" value="rejected" disabled={busy} style={{ ...button, background: "#fff7ed", color: "#9a3412" }}>Reject</button>
              <button type="submit" name="status" value="spam" disabled={busy} style={{ ...button, background: "#fef2f2", color: "#991b1b" }}>Mark spam</button>
            </div>
          </Form>
          <Form method="post" onSubmit={event => { if (!window.confirm("Permanently delete this review and its audit history?")) event.preventDefault(); }} style={{ marginTop: 8 }}>
            <input type="hidden" name="intent" value="delete" /><input type="hidden" name="review_id" value={review.id} />
            <button type="submit" disabled={busy} style={{ border: 0, background: "transparent", padding: 0, color: "#b42318", fontSize: ".75rem", cursor: "pointer" }}>Delete permanently</button>
          </Form>
        </article>)}
        {!data.reviews.length && !error ? <Alert tone="info">No reviews match these filters.</Alert> : null}
      </div>
    </s-section>

    <s-section heading="Google review integrity">
      <p style={{ color: "#4b5563", lineHeight: 1.55 }}>Only genuine, visible, published product reviews contribute to the product rating metafields and structured data. The system retains low-star reviews, assigns stable review IDs, separates store-service feedback from product feedback, and never asks Grok to fabricate or rewrite a customer&apos;s opinion.</p>
    </s-section>
  </s-page>;
}
