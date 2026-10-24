"""Synced Shopify catalogue: products, collections and detected change events."""
from __future__ import annotations

from typing import Any, Callable, Iterable

from .connection import connect, dumps, loads, now, row_to_dict, rows_to_dicts

_PRODUCT_JSON = {"tags": [], "images": [], "variants": [], "collections": []}

_PRODUCT_COLUMNS = (
    "gid", "handle", "title", "description", "product_type", "vendor", "status", "url",
    "featured_image_url", "price_min", "price_max", "compare_at_max", "currency", "on_sale",
    "discount_pct", "total_inventory", "available", "shopify_created_at", "shopify_updated_at",
    "published_at", "content_hash",
)


def _product_row(row: Any) -> dict | None:
    item = row_to_dict(row, _PRODUCT_JSON)
    if item is not None:
        item["available"] = bool(item.get("available"))
        item["on_sale"] = bool(item.get("on_sale"))
    return item


async def get_product(store_id: str, product_id: str) -> dict | None:
    async with connect() as conn:
        async with conn.execute(
            "SELECT * FROM social_products WHERE store_id=? AND product_id=?", (store_id, str(product_id)),
        ) as cur:
            return _product_row(await cur.fetchone())


async def get_products(store_id: str, product_ids: Iterable[str]) -> list[dict]:
    ids = [str(pid) for pid in product_ids]
    if not ids:
        return []
    async with connect() as conn:
        async with conn.execute(
            f"SELECT * FROM social_products WHERE store_id=? AND product_id IN ({','.join('?' * len(ids))})",
            (store_id, *ids),
        ) as cur:
            rows = [_product_row(row) for row in await cur.fetchall()]
    by_id = {row["product_id"]: row for row in rows if row}
    return [by_id[pid] for pid in ids if pid in by_id]


async def get_product_by_handle(store_id: str, handle: str) -> dict | None:
    async with connect() as conn:
        async with conn.execute(
            "SELECT * FROM social_products WHERE store_id=? AND handle=? AND deleted_at IS NULL",
            (store_id, handle),
        ) as cur:
            return _product_row(await cur.fetchone())


async def _write_product(conn: Any, store_id: str, product: dict) -> None:
    """Upsert one product on an open connection (the caller commits)."""
    timestamp = now()
    values = {column: product.get(column) for column in _PRODUCT_COLUMNS}
    values["on_sale"] = 1 if product.get("on_sale") else 0
    values["available"] = 1 if product.get("available") else 0
    for key in ("gid", "handle", "title", "description", "product_type", "vendor", "url",
                "featured_image_url", "currency", "shopify_created_at", "shopify_updated_at",
                "published_at", "content_hash"):
        values[key] = values[key] or ""
    values["status"] = values["status"] or "ACTIVE"
    values["price_min"] = float(values["price_min"] or 0)
    values["price_max"] = float(values["price_max"] or 0)
    values["discount_pct"] = float(values["discount_pct"] or 0)
    columns = list(values)
    await conn.execute(
        f"""INSERT INTO social_products
            (store_id, product_id, {', '.join(columns)}, tags_json, images_json, variants_json,
             collections_json, first_seen_at, synced_at, deleted_at)
            VALUES (?, ?, {', '.join('?' * len(columns))}, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(store_id, product_id) DO UPDATE SET
            {', '.join(f'{c}=excluded.{c}' for c in columns)},
            tags_json=excluded.tags_json, images_json=excluded.images_json,
            variants_json=excluded.variants_json, collections_json=excluded.collections_json,
            synced_at=excluded.synced_at, deleted_at=NULL""",
        (
            store_id, str(product["product_id"]), *[values[c] for c in columns],
            dumps(product.get("tags") or []), dumps(product.get("images") or []),
            dumps(product.get("variants") or []), dumps(product.get("collections") or []),
            timestamp, timestamp,
        ),
    )


async def upsert_product(store_id: str, product: dict) -> None:
    """Insert or update a product. `product` uses decoded field names."""
    async with connect() as conn:
        await _write_product(conn, store_id, product)
        await conn.commit()


async def mark_deleted(store_id: str, product_ids: Iterable[str]) -> int:
    ids = [str(pid) for pid in product_ids]
    if not ids:
        return 0
    async with connect() as conn:
        cur = await conn.execute(
            f"UPDATE social_products SET deleted_at=? WHERE store_id=? AND deleted_at IS NULL "
            f"AND product_id IN ({','.join('?' * len(ids))})",
            (now(), store_id, *ids),
        )
        await conn.commit()
        return cur.rowcount


async def list_product_ids(store_id: str, *, include_deleted: bool = False) -> set[str]:
    clause = "" if include_deleted else " AND deleted_at IS NULL"
    async with connect() as conn:
        async with conn.execute(
            f"SELECT product_id FROM social_products WHERE store_id=?{clause}", (store_id,),
        ) as cur:
            return {str(row["product_id"]) for row in await cur.fetchall()}


async def list_products(
    store_id: str,
    *,
    search: str = "",
    available_only: bool = False,
    on_sale_only: bool = False,
    collection_id: str = "",
    statuses: Iterable[str] = ("ACTIVE",),
    order: str = "title",
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    clauses = ["store_id=?", "deleted_at IS NULL"]
    params: list[Any] = [store_id]
    status_list = [s for s in statuses if s]
    if status_list:
        clauses.append(f"status IN ({','.join('?' * len(status_list))})")
        params.extend(status_list)
    if search:
        clauses.append("(title LIKE ? OR handle LIKE ? OR tags_json LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    if available_only:
        clauses.append("available=1")
    if on_sale_only:
        clauses.append("on_sale=1")
    if collection_id:
        clauses.append("collections_json LIKE ?")
        params.append(f'%"id":"{collection_id}"%')
    order_sql = {
        "title": "title COLLATE NOCASE ASC",
        "newest": "shopify_created_at DESC",
        "least_posted": "COALESCE(last_posted_at, 0) ASC, post_count ASC, title",
        "recently_posted": "COALESCE(last_posted_at, 0) DESC",
        "discount": "discount_pct DESC",
        "updated": "shopify_updated_at DESC",
    }.get(order, "title COLLATE NOCASE ASC")
    async with connect() as conn:
        async with conn.execute(
            f"SELECT * FROM social_products WHERE {' AND '.join(clauses)} ORDER BY {order_sql} LIMIT ? OFFSET ?",
            (*params, max(1, min(limit, 1000)), max(0, offset)),
        ) as cur:
            return [_product_row(row) for row in await cur.fetchall()]  # type: ignore[misc]


async def count_products(store_id: str) -> dict[str, int]:
    async with connect() as conn:
        async with conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN available=1 THEN 1 ELSE 0 END) AS available,
                      SUM(CASE WHEN on_sale=1 THEN 1 ELSE 0 END) AS on_sale,
                      SUM(CASE WHEN last_posted_at IS NULL THEN 1 ELSE 0 END) AS never_posted,
                      MAX(synced_at) AS last_synced_at
               FROM social_products WHERE store_id=? AND deleted_at IS NULL AND status='ACTIVE'""",
            (store_id,),
        ) as cur:
            row = await cur.fetchone()
    return {key: int(row[key] or 0) for key in ("total", "available", "on_sale", "never_posted", "last_synced_at")} if row else {}


async def mark_posted(store_id: str, product_ids: Iterable[str], posted_at: int | None = None) -> None:
    ids = [str(pid) for pid in product_ids]
    if not ids:
        return
    async with connect() as conn:
        await conn.execute(
            f"UPDATE social_products SET last_posted_at=?, post_count=post_count+1 "
            f"WHERE store_id=? AND product_id IN ({','.join('?' * len(ids))})",
            (posted_at or now(), store_id, *ids),
        )
        await conn.commit()


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------

async def upsert_collection(store_id: str, collection: dict) -> None:
    async with connect() as conn:
        await conn.execute(
            """INSERT INTO social_collections
               (store_id, collection_id, handle, title, image_url, products_count, product_ids_json, synced_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(store_id, collection_id) DO UPDATE SET
                 handle=excluded.handle, title=excluded.title, image_url=excluded.image_url,
                 products_count=excluded.products_count, product_ids_json=excluded.product_ids_json,
                 synced_at=excluded.synced_at""",
            (
                store_id, str(collection["collection_id"]), collection.get("handle", ""),
                collection.get("title", ""), collection.get("image_url", ""),
                int(collection.get("products_count") or 0),
                dumps([str(pid) for pid in collection.get("product_ids") or []]), now(),
            ),
        )
        await conn.commit()


async def delete_collections_except(store_id: str, keep_ids: Iterable[str]) -> int:
    keep = [str(cid) for cid in keep_ids]
    async with connect() as conn:
        if keep:
            cur = await conn.execute(
                f"DELETE FROM social_collections WHERE store_id=? AND collection_id NOT IN ({','.join('?' * len(keep))})",
                (store_id, *keep),
            )
        else:
            cur = await conn.execute("DELETE FROM social_collections WHERE store_id=?", (store_id,))
        await conn.commit()
        return cur.rowcount


async def list_collections(store_id: str) -> list[dict]:
    async with connect() as conn:
        async with conn.execute(
            "SELECT * FROM social_collections WHERE store_id=? ORDER BY title COLLATE NOCASE", (store_id,),
        ) as cur:
            return rows_to_dicts(await cur.fetchall(), {"product_ids": []})


async def get_collection(store_id: str, collection_id: str) -> dict | None:
    async with connect() as conn:
        async with conn.execute(
            "SELECT * FROM social_collections WHERE store_id=? AND collection_id=?", (store_id, collection_id),
        ) as cur:
            return row_to_dict(await cur.fetchone(), {"product_ids": []})


# ---------------------------------------------------------------------------
# Change events (new product, price drop, back in stock, ...)
# ---------------------------------------------------------------------------

async def _insert_event(
    conn: Any, store_id: str, product_id: str, event_type: str, details: dict | None, source: str,
) -> int:
    cur = await conn.execute(
        """INSERT INTO social_product_events (store_id, product_id, event_type, details_json, source, detected_at)
           VALUES (?,?,?,?,?,?)""",
        (store_id, str(product_id), event_type, dumps(details or {}), source, now()),
    )
    return int(cur.lastrowid or 0)


async def add_event(
    store_id: str, product_id: str, event_type: str, details: dict | None = None, *, source: str = "sync",
) -> int:
    async with connect() as conn:
        event_id = await _insert_event(conn, store_id, product_id, event_type, details, source)
        await conn.commit()
        return event_id


async def list_events(
    store_id: str,
    *,
    event_types: Iterable[str] = (),
    since: int | None = None,
    after_id: int | None = None,
    product_id: str = "",
    limit: int = 200,
) -> list[dict]:
    clauses = ["store_id=?"]
    params: list[Any] = [store_id]
    types = list(event_types)
    if types:
        clauses.append(f"event_type IN ({','.join('?' * len(types))})")
        params.extend(types)
    if since is not None:
        clauses.append("detected_at>=?")
        params.append(since)
    if after_id is not None:
        clauses.append("id>?")
        params.append(after_id)
    if product_id:
        clauses.append("product_id=?")
        params.append(str(product_id))
    async with connect() as conn:
        async with conn.execute(
            f"SELECT * FROM social_product_events WHERE {' AND '.join(clauses)} ORDER BY id ASC LIMIT ?",
            (*params, max(1, min(limit, 2000))),
        ) as cur:
            rows = rows_to_dicts(await cur.fetchall())
    for row in rows:
        row["details"] = loads(row.pop("details_json", "{}"), {})
    return rows


# ---------------------------------------------------------------------------
# Atomic catalogue sync helpers (safe when syncs and webhooks overlap)
# ---------------------------------------------------------------------------

ProductEvent = tuple[str, dict]


async def save_product_with_events(
    store_id: str,
    product: dict,
    decide: Callable[[dict | None], list[ProductEvent] | None],
    *,
    source: str = "sync",
) -> tuple[dict | None, list[ProductEvent] | None]:
    """Read the stored row, upsert `product` and store the events `decide(previous)` returns,
    all in one write transaction. `decide` returns None to skip the write (stale data).
    Returns (previous_row_or_None, events_or_None)."""
    product_id = str(product["product_id"])
    async with connect() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            async with conn.execute(
                "SELECT * FROM social_products WHERE store_id=? AND product_id=?", (store_id, product_id),
            ) as cur:
                previous = _product_row(await cur.fetchone())
            events = decide(previous)
            if events is None:
                await conn.rollback()
                return previous, None
            await _write_product(conn, store_id, product)
            for event_type, details in events:
                await _insert_event(conn, store_id, product_id, event_type, details, source)
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
    return previous, list(events)


async def mark_products_deleted(
    store_id: str,
    product_ids: Iterable[str],
    *,
    events_for: Callable[[dict], list[ProductEvent]],
    source: str = "sync",
) -> list[tuple[dict, list[ProductEvent]]]:
    """Mark products deleted and store `events_for(previous_row)` for each, in one transaction.

    Products that are unknown or already deleted are skipped, so repeats are harmless.
    Returns (previous_row, events) for every product that was marked."""
    ids = sorted({str(pid) for pid in product_ids if str(pid)})
    if not ids:
        return []
    marked: list[tuple[dict, list[ProductEvent]]] = []
    async with connect() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            async with conn.execute(
                f"SELECT * FROM social_products WHERE store_id=? AND deleted_at IS NULL "
                f"AND product_id IN ({','.join('?' * len(ids))})",
                (store_id, *ids),
            ) as cur:
                rows = [_product_row(row) for row in await cur.fetchall()]
            timestamp = now()
            for row in rows:
                if row is None:
                    continue
                await conn.execute(
                    "UPDATE social_products SET deleted_at=? WHERE store_id=? AND product_id=?",
                    (timestamp, store_id, row["product_id"]),
                )
                events = list(events_for(row))
                for event_type, details in events:
                    await _insert_event(conn, store_id, row["product_id"], event_type, details, source)
                marked.append((row, events))
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
    return marked


# ---------------------------------------------------------------------------
# Auto-pilot eligibility queries
# ---------------------------------------------------------------------------

async def list_publishable_products(
    store_id: str, *, in_stock_only: bool = True, on_sale_only: bool = False,
) -> list[dict]:
    """ACTIVE, published (has a storefront URL) and not deleted; newest first."""
    clauses = ["store_id=?", "deleted_at IS NULL", "status='ACTIVE'", "url<>''"]
    if in_stock_only:
        clauses.append("available=1")
    if on_sale_only:
        clauses.append("on_sale=1")
    async with connect() as conn:
        async with conn.execute(
            f"SELECT * FROM social_products WHERE {' AND '.join(clauses)} "
            "ORDER BY shopify_created_at DESC, product_id DESC",
            (store_id,),
        ) as cur:
            return [_product_row(row) for row in await cur.fetchall()]  # type: ignore[misc]


async def collection_member_ids(store_id: str, collection_ids: Iterable[str]) -> set[str]:
    """Product ids that belong to any of the given collections (from the collections sync)."""
    ids = [str(cid) for cid in collection_ids if str(cid)]
    if not ids:
        return set()
    async with connect() as conn:
        async with conn.execute(
            f"SELECT product_ids_json FROM social_collections WHERE store_id=? "
            f"AND collection_id IN ({','.join('?' * len(ids))})",
            (store_id, *ids),
        ) as cur:
            rows = await cur.fetchall()
    members: set[str] = set()
    for row in rows:
        members.update(str(pid) for pid in loads(row["product_ids_json"], []) or [])
    return members
