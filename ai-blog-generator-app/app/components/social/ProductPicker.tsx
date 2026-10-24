import { useEffect, useState } from "react";
import { useFetcher } from "react-router";

import type { Product } from "../../lib/social-engine.types";
import { formatMoney } from "./vocab";

export type PickedProduct = { product_id: string; title: string; image: string; available?: boolean };

export function toPicked(product: Pick<Product, "product_id" | "title" | "featured_image_url"> & { available?: boolean }): PickedProduct {
  return { product_id: String(product.product_id), title: product.title, image: product.featured_image_url, available: product.available };
}

type ProductsData = { products: Product[]; error?: string };

/**
 * Search the synced catalogue (via the Products page loader) and pick products.
 * Pass `name` to emit one hidden input per selected id.
 */
export function ProductPicker({ selected, onChange, max = 50, name, emptyText = "No products selected." }: {
  selected: PickedProduct[];
  onChange: (next: PickedProduct[]) => void;
  max?: number;
  name?: string;
  emptyText?: string;
}) {
  const fetcher = useFetcher<ProductsData>();
  const { load } = fetcher;
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [inStockOnly, setInStockOnly] = useState(true);

  useEffect(() => {
    if (!open) return undefined;
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({ page_size: "12", order: query ? "title" : "least_posted" });
      if (query) params.set("search", query);
      if (inStockOnly) params.set("available_only", "1");
      load(`/app/social/products?${params.toString()}`);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [open, query, inStockOnly, load]);

  const ids = new Set(selected.map((item) => item.product_id));
  const full = selected.length >= max;
  const results = fetcher.data?.products || [];

  return (
    <div className="sa-stack-sm">
      {name ? selected.map((item) => <input key={item.product_id} type="hidden" name={name} value={item.product_id} />) : null}
      {selected.length ? (
        <div className="sa-row">
          {selected.map((item) => (
            <span key={item.product_id} className="sa-chip">
              {item.image ? <img className="sa-avatar" src={item.image} alt="" /> : null}
              <span className="sa-chip-label">{item.title || `Product #${item.product_id}`}</span>
              <button
                type="button" className="sa-btn sa-btn-plain sa-btn-sm" aria-label={`Remove ${item.title}`}
                onClick={() => onChange(selected.filter((other) => other.product_id !== item.product_id))}
              >✕</button>
            </span>
          ))}
        </div>
      ) : <span className="sa-small sa-muted">{emptyText}</span>}
      {!open ? (
        <div>
          <button type="button" className="sa-btn sa-btn-sm" onClick={() => setOpen(true)} disabled={full}>
            {full ? `Maximum ${max} selected` : selected.length ? "Add another product" : "Choose products"}
          </button>
        </div>
      ) : (
        <div className="sa-card sa-card-muted sa-stack-sm">
          <div className="sa-row">
            <input
              type="search" value={query} onChange={(event) => setQuery(event.target.value)}
              placeholder="Search your synced products" aria-label="Search products" style={{ maxWidth: 280 }}
              // The search runs as you type; Enter must not submit the form this picker sits in.
              onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }}
            />
            <label className="sa-check sa-small">
              <input type="checkbox" checked={inStockOnly} onChange={(event) => setInStockOnly(event.target.checked)} />
              <span>In stock only</span>
            </label>
            <button type="button" className="sa-btn sa-btn-sm" onClick={() => setOpen(false)}>Done</button>
          </div>
          {fetcher.state !== "idle" && !results.length ? <span className="sa-small sa-muted">Searching…</span> : null}
          {fetcher.data?.error ? <span className="sa-error-text">{fetcher.data.error}</span> : null}
          {fetcher.state === "idle" && fetcher.data && !results.length && !fetcher.data.error ? (
            <span className="sa-small sa-muted">No synced products match. Try another word, or sync your catalogue on the Products page.</span>
          ) : null}
          <div className="sa-stack-sm" style={{ maxHeight: 320, overflowY: "auto" }}>
            {results.map((product) => {
              const picked = ids.has(String(product.product_id));
              return (
                <div key={product.product_id} className="sa-row" style={{ flexWrap: "nowrap" }}>
                  {product.featured_image_url ? <img className="sa-thumb sa-thumb-sm" src={product.featured_image_url} alt="" loading="lazy" /> : <span className="sa-thumb sa-thumb-sm" />}
                  <div className="sa-grow">
                    <div className="sa-ellipsis">{product.title}</div>
                    <div className="sa-small sa-muted">
                      {formatMoney(product.price_min, product.currency)}
                      {product.on_sale ? ` · on sale −${Math.round(product.discount_pct)}%` : ""}
                      {product.available ? "" : " · unavailable"}
                      {product.excluded ? " · excluded from automation" : ""}
                    </div>
                  </div>
                  <button
                    type="button" className="sa-btn sa-btn-sm" disabled={picked || full}
                    onClick={() => onChange([...selected, toPicked(product)].slice(0, max))}
                  >
                    {picked ? "Added" : "Add"}
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
