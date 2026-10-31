"""Real Shopify discount codes for promotions.

Rules (set by the merchant on 31 Oct 2026): a percentage off everything, once per customer,
doesn't combine with other discounts, no minimum order. Codes this module creates carry
MARKER in their Shopify title, so a later year can reuse its own code by moving the dates,
while a code the merchant made by hand is never touched.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from config import StoreConfig

MARKER = "(Social Autopilot)"

FIND = """
query FindDiscount($code: String!) {
  codeDiscountNodeByCode(code: $code) {
    id
    codeDiscount { __typename ... on DiscountCodeBasic { title status startsAt endsAt } }
  }
}"""

CREATE = """
mutation CreateDiscount($discount: DiscountCodeBasicInput!) {
  discountCodeBasicCreate(basicCodeDiscount: $discount) {
    codeDiscountNode { id }
    userErrors { field code message }
  }
}"""

UPDATE = """
mutation UpdateDiscount($id: ID!, $discount: DiscountCodeBasicInput!) {
  discountCodeBasicUpdate(id: $id, basicCodeDiscount: $discount) {
    codeDiscountNode { id }
    userErrors { field code message }
  }
}"""

GraphQL = Callable[[StoreConfig, str, dict], Awaitable[dict]]


class DiscountError(RuntimeError):
    """Shopify refused or couldn't create the code (message is shown in Activity)."""


@dataclass(frozen=True)
class DiscountResult:
    code: str
    discount_id: str
    action: str  # created | updated


def code_for(label: str, percent: int) -> str:
    """"Mother's Day (UK)", 20 → "MOTHERSDAY20"."""
    base = re.sub(r"\(.*?\)", "", label)
    base = re.sub(r"[^A-Za-z0-9]", "", base).upper()
    return f"{base or 'OFFER'}{percent}"


def discount_input(*, code: str, title: str, starts_at: datetime, ends_at: datetime, percent: int) -> dict[str, Any]:
    return {
        "title": title,
        "code": code,
        "startsAt": starts_at.isoformat(),
        "endsAt": ends_at.isoformat(),
        "context": {"all": "ALL"},  # every customer (2025-01 replaced customerSelection with context)
        "customerGets": {"value": {"percentage": round(percent / 100, 4)}, "items": {"all": True}},
        "appliesOncePerCustomer": True,
        "combinesWith": {"orderDiscounts": False, "productDiscounts": False, "shippingDiscounts": False},
    }


async def _default_graphql(store: StoreConfig, query: str, variables: dict) -> dict:
    import shopify_client

    return await shopify_client.graphql_request(store, query, variables)


def _check(result: dict | None, what: str) -> dict:
    if not result:
        raise DiscountError(f"Shopify returned nothing for {what}.")
    errors = result.get("userErrors") or []
    if errors:
        raise DiscountError(f"Shopify refused {what}: " + "; ".join(e.get("message", "") for e in errors)[:300])
    return result


async def ensure_code(
    store: StoreConfig,
    *,
    code: str,
    title: str,
    starts_at: datetime,
    ends_at: datetime,
    percent: int,
    graphql: GraphQL | None = None,
) -> DiscountResult:
    """Create the code (or move one of ours to these dates). Falls back to CODE-YYYY if the
    plain code belongs to a discount someone else made."""
    call = graphql or _default_graphql
    for candidate in (code, f"{code}-{starts_at.year}"):
        found = (await call(store, FIND, {"code": candidate})).get("codeDiscountNodeByCode")
        payload = discount_input(code=candidate, title=title, starts_at=starts_at, ends_at=ends_at, percent=percent)
        if found is None:
            data = await call(store, CREATE, {"discount": payload})
            node = _check(data.get("discountCodeBasicCreate"), f"the code {candidate}").get("codeDiscountNode") or {}
            return DiscountResult(candidate, str(node.get("id", "")), "created")
        existing_title = str((found.get("codeDiscount") or {}).get("title") or "")
        if existing_title.endswith(MARKER):
            data = await call(store, UPDATE, {"id": found["id"], "discount": payload})
            _check(data.get("discountCodeBasicUpdate"), f"the code {candidate}")
            return DiscountResult(candidate, str(found["id"]), "updated")
    raise DiscountError(f"The codes {code} and {code}-{starts_at.year} are already used by other discounts in Shopify.")
