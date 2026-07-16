import asyncio
import re
from datetime import datetime, timezone

import httpx

from config import StoreConfig
import db
import shopify_client

BROAD_TERMS = [
    "supplement",
    "supplements",
    "nmn",
    "nad",
    "nad+",
    "berberine",
    "creatine",
    "collagen",
    "omega-3",
    "omega 3",
    "probiotic",
    "resveratrol",
    "methylene blue",
    "lion's mane",
    "nitric oxide",
    "keto bhb",
    "ox bile",
    "blood sugar support",
    "adaptogen",
    "shilajit",
    "prostate support",
    "female enhancement",
    "mushroom",
]

STRICT_TERMS = [
    "supplement",
    "supplements",
    "berberine",
    "creatine",
    "collagen",
    "nmn",
    "nad",
    "nad+",
    "resveratrol",
    "methylene blue",
    "lion's mane",
    "nitric oxide",
    "keto bhb",
    "ox bile",
    "omega-3",
    "omega 3",
    "shilajit",
    "adaptogen",
    "prostate support",
    "female enhancement",
    "blood sugar support",
    "capsule",
    "capsules",
    "drops",
    "powder",
]


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower())


def has_any(text: str, terms: list[str]) -> bool:
    normalized = norm(text)
    return any(term in normalized for term in terms)


async def put_with_retry(client: httpx.AsyncClient, url: str, headers: dict, payload: dict) -> bool:
    for attempt in range(10):
        response = await client.put(url, headers=headers, json=payload)
        if response.status_code < 300:
            return True
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                wait_seconds = float(retry_after)
            else:
                wait_seconds = min(1.5 + attempt, 12.0)
            await asyncio.sleep(wait_seconds)
            continue
        return False
    return False


async def fetch_store() -> StoreConfig:
    stores = await db.get_stores()
    if not stores:
        raise RuntimeError("No stores configured")
    store = stores[0]
    return StoreConfig(
        id=store["id"],
        name=store["name"],
        myshopify_domain=store["myshopify_domain"],
        custom_domain=store.get("custom_domain", ""),
        client_id=store["client_id"],
        client_secret=store["client_secret"],
        default_blog_handle=store.get("default_blog_handle", "news"),
        default_author=store.get("default_author", "Store Team"),
    )


async def main() -> None:
    store = await fetch_store()
    token = await shopify_client._get_token(store)
    base = f"https://{store.myshopify_domain}/admin/api/{shopify_client.SHOPIFY_API_VERSION}"

    products_to_draft = []
    articles_to_republish = []

    async with httpx.AsyncClient(timeout=60) as client:
        product_page = None
        while True:
            products_url = (
                f"{base}/products.json?limit=250"
                "&fields=id,title,handle,tags,product_type,status,published_at"
            )
            if product_page:
                products_url += f"&page_info={product_page}"
            response = await client.get(products_url, headers={"X-Shopify-Access-Token": token})
            response.raise_for_status()

            for product in response.json().get("products", []):
                product_blob = " ".join(
                    [
                        str(product.get("title", "")),
                        str(product.get("handle", "")),
                        str(product.get("tags", "")),
                        str(product.get("product_type", "")),
                    ]
                )
                if has_any(product_blob, STRICT_TERMS) and (product.get("status") or "").lower() != "draft":
                    products_to_draft.append(product)

            match = re.search(
                r'<[^>]*[?&]page_info=([^&>]+)[^>]*>; rel="next"',
                response.headers.get("Link", ""),
            )
            if not match:
                break
            product_page = match.group(1)

        blogs = await shopify_client.fetch_blogs(store)
        for blog in blogs:
            article_page = None
            while True:
                articles_url = (
                    f"{base}/blogs/{blog.id}/articles.json?limit=250"
                    "&fields=id,title,handle,tags,body_html,published_at"
                )
                if article_page:
                    articles_url += f"&page_info={article_page}"

                response = await client.get(articles_url, headers={"X-Shopify-Access-Token": token})
                response.raise_for_status()

                for article in response.json().get("articles", []):
                    broad_blob = " ".join(
                        [
                            str(article.get("title", "")),
                            str(article.get("handle", "")),
                            str(article.get("tags", "")),
                            re.sub(r"<[^>]+>", " ", str(article.get("body_html", ""))),
                        ]
                    )
                    strict_blob = " ".join(
                        [
                            str(article.get("title", "")),
                            str(article.get("handle", "")),
                            str(article.get("tags", "")),
                        ]
                    )
                    # Undo over-broad hides from earlier run while preserving strict supplement hides.
                    if has_any(broad_blob, BROAD_TERMS) and not has_any(strict_blob, STRICT_TERMS):
                        if not article.get("published_at"):
                            articles_to_republish.append((blog, article))

                match = re.search(
                    r'<[^>]*[?&]page_info=([^&>]+)[^>]*>; rel="next"',
                    response.headers.get("Link", ""),
                )
                if not match:
                    break
                article_page = match.group(1)

        product_ok = 0
        product_fail = 0
        for product in products_to_draft:
            payload = {"product": {"id": product["id"], "status": "draft"}}
            update_url = f"{base}/products/{product['id']}.json"
            ok = await put_with_retry(
                client,
                update_url,
                {"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
                payload,
            )
            if ok:
                product_ok += 1
            else:
                product_fail += 1
            await asyncio.sleep(0.7)

        article_ok = 0
        article_fail = 0
        publish_now = datetime.now(timezone.utc).isoformat()
        for blog, article in articles_to_republish:
            payload = {
                "article": {
                    "id": article["id"],
                    "published": True,
                    "published_at": publish_now,
                }
            }
            update_url = f"{base}/blogs/{blog.id}/articles/{article['id']}.json"
            ok = await put_with_retry(
                client,
                update_url,
                {"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
                payload,
            )
            if ok:
                article_ok += 1
            else:
                article_fail += 1
            await asyncio.sleep(0.7)

    print("=== CORRECTION SUMMARY ===")
    print(
        f"Products moved to draft: target={len(products_to_draft)} "
        f"success={product_ok} failed={product_fail}"
    )
    print(
        f"Articles republished (over-hidden non-supplement): target={len(articles_to_republish)} "
        f"success={article_ok} failed={article_fail}"
    )
    print("Sample drafted products:")
    for product in products_to_draft[:20]:
        print(f"  PRODUCT {product['id']} | {product.get('title')}")
    print("Sample republished articles:")
    for blog, article in articles_to_republish[:20]:
        print(f"  ARTICLE {article['id']} | blog={blog.handle} | {article.get('title')}")


if __name__ == "__main__":
    asyncio.run(main())
