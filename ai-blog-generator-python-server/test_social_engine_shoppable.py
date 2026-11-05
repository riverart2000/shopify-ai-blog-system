"""Shoppable bio-link page: the products most recently posted, on one Shopify page that only we update."""
from __future__ import annotations

import pytest

from db.social_engine import products as product_store
from db.social_engine.connection import now
from services.social_engine import shoppable
from services.social_engine.context import load_store_context
from services.social_engine.errors import ConfigurationError, ExternalServiceError
from services.social_engine.settings import save_settings
from services.social_engine.shoppable import CREATE, FIND, GET, MARKER, UPDATE


class FakeShopify:
    """Answers the page calls. `pages` maps handle → {"id", "body"}."""

    def __init__(self, pages: dict[str, dict] | None = None, refuse: str = "") -> None:
        self.pages = dict(pages or {})
        self.refuse = refuse
        self.writes: list[tuple[str, dict]] = []

    async def __call__(self, store, query: str, variables: dict) -> dict:
        if query == FIND:
            handle = variables["q"].removeprefix("handle:")
            page = self.pages.get(handle)
            return {"pages": {"nodes": [{"handle": handle, **page}] if page else []}}
        if query == GET:
            match = next(((h, p) for h, p in self.pages.items() if p["id"] == variables["id"]), None)
            return {"page": {"handle": match[0], **match[1]} if match else None}
        errors = [{"message": self.refuse}] if self.refuse else []
        if query == CREATE:
            page = variables["page"]
            self.writes.append(("create", page))
            if not errors:
                self.pages[page["handle"]] = {"id": "gid://shopify/Page/1", "body": page["body"]}
            return {"pageCreate": {"page": None if errors else {"id": "gid://shopify/Page/1"}, "userErrors": errors}}
        if query == UPDATE:
            self.writes.append(("update", variables))
            return {"pageUpdate": {"page": {"id": variables["id"]}, "userErrors": errors}}
        raise AssertionError("unexpected query")


async def _product(store_id: str, pid: str, *, posted_days_ago: float | None, **changes) -> None:
    await product_store.upsert_product(store_id, {
        "product_id": pid, "gid": f"gid://shopify/Product/{pid}", "handle": f"item-{pid}", "title": f"Item {pid}",
        "description": "", "status": "ACTIVE", "url": f"https://bioluxelab.com/products/item-{pid}",
        "featured_image_url": f"https://cdn.shopify.com/item-{pid}.webp?v=1", "price_min": 12.5, "price_max": 12.5,
        "currency": "GBP", "available": True, "tags": [], "images": [], "variants": [], "collections": [], **changes,
    })
    if posted_days_ago is not None:
        await product_store.mark_posted(store_id, [pid], posted_at=now() - int(posted_days_ago * 86400))


async def _context(store_id: str, **settings):
    await save_settings(store_id, {"shoppable": {"enabled": True, **settings}})
    return await load_store_context(store_id)


@pytest.mark.asyncio
async def test_the_page_shows_what_was_posted_newest_first(se_db):
    await _product(se_db, "1", posted_days_ago=3)
    await _product(se_db, "2", posted_days_ago=1, title="Mat <b>& Pillow</b>", price_max=20.0)
    await _product(se_db, "3", posted_days_ago=None)              # never posted
    await _product(se_db, "4", posted_days_ago=2, available=False)  # sold out
    await _product(se_db, "5", posted_days_ago=90)               # too long ago
    await _product(se_db, "6", posted_days_ago=2, featured_image_url="")
    shopify = FakeShopify()
    sctx = await _context(se_db)

    result = await shoppable.refresh(sctx, graphql=shopify)
    assert result == {"status": "created", "url": "https://bioluxelab.com/pages/shop-our-feed", "products": 2}
    kind, page = shopify.writes[0]
    assert kind == "create" and page["handle"] == "shop-our-feed" and page["isPublished"] is True
    body = page["body"]
    assert body.startswith(MARKER) and body.index("item-2") < body.index("item-1")
    assert "Mat &lt;b&gt;&amp; Pillow&lt;/b&gt;" in body and "<b>" not in body          # catalogue text is escaped
    assert "From £12.50" in body and ">£12.50<" in body
    assert "utm_source=linkinbio" in body and "utm_campaign=shop-our-feed" in body and "utm_content=item-2" in body
    assert "item-2.webp?v=1&amp;width=600" in body
    for missing in ("item-3", "item-4", "item-5", "item-6"):
        assert missing not in body
    assert (await shoppable.status(sctx))["published"] is True


@pytest.mark.asyncio
async def test_shopify_is_only_written_when_the_page_changes(se_db):
    await _product(se_db, "1", posted_days_ago=1)
    shopify = FakeShopify()
    sctx = await _context(se_db)
    await shoppable.refresh(sctx, graphql=shopify)
    assert (await shoppable.refresh(sctx, graphql=shopify))["status"] == "unchanged" and len(shopify.writes) == 1

    await _product(se_db, "2", posted_days_ago=0)
    assert (await shoppable.refresh(sctx, graphql=shopify))["status"] == "updated"
    kind, update = shopify.writes[-1]
    assert kind == "update" and update["id"] == "gid://shopify/Page/1" and "item-2" in update["page"]["body"]


@pytest.mark.asyncio
async def test_someone_elses_page_is_never_overwritten(se_db):
    await _product(se_db, "1", posted_days_ago=1)
    shopify = FakeShopify({"shop-our-feed": {"id": "gid://shopify/Page/77", "body": "<p>Hand-written page</p>"}})
    sctx = await _context(se_db)
    with pytest.raises(ConfigurationError, match="wasn't made by Social Autopilot"):
        await shoppable.refresh(sctx, graphql=shopify)
    assert shopify.writes == []

    ours = FakeShopify({"shop-our-feed": {"id": "gid://shopify/Page/78", "body": f"{MARKER}<div>old</div>"}})
    assert (await shoppable.refresh(sctx, graphql=ours))["status"] == "updated"  # one we made before: adopted
    assert ours.writes[0][1]["id"] == "gid://shopify/Page/78"


@pytest.mark.asyncio
async def test_off_empty_and_refused(se_db):
    sctx = await load_store_context(se_db)
    assert await shoppable.refresh(sctx, graphql=FakeShopify()) == {"status": "off"}  # off unless switched on
    sctx = await _context(se_db)
    empty = FakeShopify()
    assert (await shoppable.refresh(sctx, graphql=empty))["status"] == "empty" and empty.writes == []
    await _product(se_db, "1", posted_days_ago=1)
    with pytest.raises(ExternalServiceError, match="Handle is taken"):
        await shoppable.refresh(sctx, graphql=FakeShopify(refuse="Handle is taken"))
    assert (await shoppable.status(sctx))["published"] is False


def test_the_page_address_must_be_a_plain_handle():
    from pydantic import ValidationError
    from services.social_engine.settings import ShoppableSettings

    assert ShoppableSettings(page_handle="shop-the-look").page_handle == "shop-the-look"
    for bad in ("Shop Our Feed", "/pages/x", "a", "feed--x"):
        with pytest.raises(ValidationError):
            ShoppableSettings(page_handle=bad)
