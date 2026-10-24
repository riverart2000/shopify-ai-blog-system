import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useLoaderData, useSearchParams } from "react-router";

import { useSocial } from "../components/social/context";
import { ActionBanner, Badge, EmptyState, Hidden, Kpi, LoadError, TimeText, useBusy } from "../components/social/ui";
import { formatMoney, formatNumber } from "../components/social/vocab";
import {
  errorMessage, formBool, formString, runIntent, safeId, seFetch,
  type Collection, type Product, type ProductCounts, type ProductsResponse,
} from "../lib/social-engine.server";
import { authenticate } from "../shopify.server";

export const handle = { title: "Products" };

const ORDERS: Array<[string, string]> = [
  ["title", "Name (A–Z)"], ["newest", "Newest first"], ["least_posted", "Least posted first"],
  ["recently_posted", "Recently posted"], ["discount", "Biggest discount"], ["updated", "Recently updated"],
];

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  const url = new URL(request.url);
  const query = {
    search: url.searchParams.get("search") || "",
    collection_id: url.searchParams.get("collection_id") || "",
    order: url.searchParams.get("order") || "title",
    available_only: url.searchParams.get("available_only") === "1",
    on_sale_only: url.searchParams.get("on_sale_only") === "1",
    page: Math.max(1, Number(url.searchParams.get("page") || 1) || 1),
    page_size: Math.min(100, Math.max(1, Number(url.searchParams.get("page_size") || 50) || 50)),
  };
  try {
    const data = await seFetch<ProductsResponse>("/products", { query });
    return { ...data, query, error: "" };
  } catch (error) {
    return {
      products: [] as Product[], page: query.page, page_size: query.page_size, counts: {} as ProductCounts,
      collections: [] as Collection[], query, error: errorMessage(error),
    };
  }
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  return runIntent(intent, async () => {
    if (intent === "exclude") {
      const excluded = formBool(form, "excluded");
      await seFetch("/products/exclude", { body: { product_id: safeId(formString(form, "product_id")), excluded } });
      return { message: excluded ? "Excluded from automation. Campaigns will skip this product." : "Included in automation again." };
    }
    if (intent === "sync" || intent === "sync_full") {
      const result = await seFetch<{ already_queued: boolean }>("/products/sync", { body: { full: intent === "sync_full" } });
      return {
        message: result.already_queued
          ? "A sync is already queued — it will run shortly."
          : intent === "sync_full" ? "Full catalogue sync started (re-reads every product)." : "Catalogue sync started (changes since the last sync).",
      };
    }
    throw new Error(`Unknown action "${intent}".`);
  });
};

export default function ProductsPage() {
  const data = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>();
  const { tz } = useSocial();
  const { busy, isSubmitting } = useBusy();
  const [params] = useSearchParams();
  const pageLink = (page: number) => {
    const next = new URLSearchParams(params);
    next.set("page", String(page));
    return `?${next.toString()}`;
  };

  return (
    <>
      <ActionBanner result={result} />
      <LoadError error={data.error} />
      <s-section>
        <div className="sa-stack">
          <div className="sa-grid-sm">
            <Kpi label="Products" value={formatNumber(data.counts.total)} />
            <Kpi label="In stock" value={formatNumber(data.counts.available)} />
            <Kpi label="On sale" value={formatNumber(data.counts.on_sale)} />
            <Kpi label="Never posted" value={formatNumber(data.counts.never_posted)} />
          </div>
          <div className="sa-spread">
            <span className="sa-small sa-muted">
              Last synced {data.counts.last_synced_at ? <TimeText epoch={data.counts.last_synced_at} tz={tz} relative /> : "never"}. Product changes arrive instantly through Shopify webhooks; a sync runs every 15 minutes as a safety net.
            </span>
            <div className="sa-row">
              <Form method="post"><Hidden values={{ intent: "sync" }} /><button type="submit" className="sa-btn" disabled={busy}>{isSubmitting("sync") ? "Starting…" : "Sync now"}</button></Form>
              <Form method="post"><Hidden values={{ intent: "sync_full" }} /><button type="submit" className="sa-btn" disabled={busy}>{isSubmitting("sync_full") ? "Starting…" : "Full sync"}</button></Form>
            </div>
          </div>
          <Form method="get" className="sa-row" preventScrollReset>
            <input type="search" name="search" defaultValue={data.query.search} placeholder="Search name, handle or tag" aria-label="Search products" style={{ width: 220 }} />
            <select name="collection_id" defaultValue={data.query.collection_id} aria-label="Collection" style={{ width: "auto", maxWidth: 220 }}>
              <option value="">All collections</option>
              {data.collections.map((collection) => <option key={collection.id} value={collection.id}>{collection.title} ({collection.products_count})</option>)}
            </select>
            <select name="order" defaultValue={data.query.order} aria-label="Sort by" style={{ width: "auto" }}>
              {ORDERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
            <label className="sa-check"><input type="checkbox" name="available_only" value="1" defaultChecked={data.query.available_only} /><span>In stock</span></label>
            <label className="sa-check"><input type="checkbox" name="on_sale_only" value="1" defaultChecked={data.query.on_sale_only} /><span>On sale</span></label>
            <button type="submit" className="sa-btn">Apply</button>
          </Form>
        </div>
      </s-section>

      <s-section>
        {data.products.length ? (
          <div className="sa-stack">
            <div className="sa-table-wrap">
              <table className="sa-table">
                <thead>
                  <tr>
                    <th>Product</th><th className="num">Price</th><th>Stock</th><th>Collections</th><th>Last posted</th>
                    <th className="num">Posts</th><th>Automation</th><th />
                  </tr>
                </thead>
                <tbody>
                  {data.products.map((product) => (
                    <tr key={product.product_id}>
                      <td>
                        <div className="sa-row" style={{ flexWrap: "nowrap" }}>
                          {product.featured_image_url ? <img className="sa-thumb" src={product.featured_image_url} alt="" loading="lazy" /> : <span className="sa-thumb sa-placeholder">—</span>}
                          <div className="sa-stack-sm" style={{ minWidth: 0 }}>
                            {product.url ? <a href={product.url} target="_blank" rel="noreferrer" className="sa-strong">{product.title}</a> : <span className="sa-strong">{product.title}</span>}
                            <span className="sa-small sa-muted">{product.handle}{product.tags.length ? ` · ${product.tags.slice(0, 4).join(", ")}` : ""}</span>
                          </div>
                        </div>
                      </td>
                      <td className="num sa-nowrap">
                        {formatMoney(product.price_min, product.currency)}
                        {product.on_sale && product.compare_at_max ? (
                          <div className="sa-small">
                            <s className="sa-muted">{formatMoney(product.compare_at_max, product.currency)}</s>{" "}
                            <Badge tone="success">−{Math.round(product.discount_pct)}%</Badge>
                          </div>
                        ) : null}
                      </td>
                      <td className="sa-nowrap">
                        {product.available ? <Badge tone="success">In stock</Badge> : <Badge tone="neutral">Unavailable</Badge>}
                        {product.total_inventory !== null && product.total_inventory !== undefined ? <div className="sa-small sa-muted">{formatNumber(product.total_inventory)} units</div> : null}
                      </td>
                      <td className="sa-small">{product.collections.map((collection) => collection.title).join(", ") || "—"}</td>
                      <td className="sa-nowrap sa-small"><TimeText epoch={product.last_posted_at} tz={tz} relative /></td>
                      <td className="num">{formatNumber(product.post_count)}</td>
                      <td>
                        <Form method="post" preventScrollReset>
                          <Hidden values={{ intent: "exclude", product_id: product.product_id, excluded: product.excluded ? "0" : "1" }} />
                          <button type="submit" className={`sa-btn sa-btn-sm ${product.excluded ? "sa-btn-critical" : ""}`} disabled={busy} title={product.excluded ? "Campaigns skip this product. Click to include it again." : "Click to stop campaigns from posting this product."}>
                            {product.excluded ? "Excluded" : "Included"}
                          </button>
                        </Form>
                      </td>
                      <td><Link to={`/app/social/studio?product_id=${encodeURIComponent(product.product_id)}`} className="sa-btn sa-btn-sm">Create post</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="sa-spread">
              {data.page > 1 ? <Link to={pageLink(data.page - 1)} className="sa-btn">← Previous</Link> : <span />}
              <span className="sa-small sa-muted">Page {data.page}</span>
              {data.products.length >= data.page_size ? <Link to={pageLink(data.page + 1)} className="sa-btn">Next →</Link> : <span />}
            </div>
          </div>
        ) : !data.error ? (
          <EmptyState
            title={data.counts.total ? "No products match these filters" : "No products synced yet"}
            action={!data.counts.total ? <Form method="post"><Hidden values={{ intent: "sync_full" }} /><button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>Sync my catalogue</button></Form> : undefined}
          >
            {data.counts.total ? "Clear the search or filters to see everything." : "Social Autopilot posts from a synced copy of your Shopify catalogue. Run the first sync to get started."}
          </EmptyState>
        ) : null}
      </s-section>
    </>
  );
}
