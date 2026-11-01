#!/usr/bin/env python3
"""Apply the 2026-11-01 customer-trust fixes to the live BioLuxeLab theme.

Reads the pre-change snapshot in themes/live-snapshots/2026-11-01-before/, applies
the edits in memory, writes the result to themes/live-snapshots/2026-11-01-after/
and upserts the files to the live theme through the Admin GraphQL API.

Usage:  SHOPIFY_TOKEN=... python3 scripts/storefront/apply_trust_fixes.py [--dry-run]
"""
import json, os, re, sys, urllib.request, pathlib

SHOP = "xfxs8g-hn.myshopify.com"
THEME_ID = "gid://shopify/OnlineStoreTheme/209606738265"
ROOT = pathlib.Path(__file__).resolve().parents[2]
BEFORE = ROOT / "themes/live-snapshots/2026-11-01-before"
AFTER = ROOT / "themes/live-snapshots/2026-11-01-after"
DRY = "--dry-run" in sys.argv

def load(rel):
    raw = (BEFORE / rel).read_text()
    return json.loads(re.sub(r"^\s*/\*.*?\*/\s*", "", raw, flags=re.S))

def text_block(bid, html, preset="rte"):
    return {"type": "text", "settings": {"text": html, "width": "100%", "max_width": "normal",
            "alignment": "left", "type_preset": preset, "font": "var(--font-body--family)"}}

files = {}

# ---- homepage -----------------------------------------------------------
idx = load("templates/index.json")
mq = idx["sections"]["marquee_trust"]["blocks"]
mq["m3"]["settings"]["text"] = "<p>30-Day Returns</p>"
mq["m7"]["settings"]["text"] = "<p>Secure Checkout · Klarna, PayPal &amp; Apple Pay</p>"
mq["m9"]["settings"]["text"] = "<p>UK-Based Customer Support</p>"
idx["sections"]["category_grid"]["settings"]["collection_list"] = [
    "yoga-practice", "meditation-essentials", "home-fitness",
    "aromatherapy-relaxation", "gift-ideas", "mindful-decor"]
for sid in ("collection_list_8Bgcg8", "17848001012127bac0"):   # duplicate collection carousel, empty store-reviews
    idx["order"].remove(sid); idx["sections"].pop(sid)
files["templates/index.json"] = idx

# ---- product page -------------------------------------------------------
prod = load("templates/product.json")
details = prod["sections"]["product-information"]["blocks"]["product-details"]["blocks"]["group_J87VfC"]
row = details["blocks"]["group_JK8NUb"]["blocks"]
for gid, icon, label in (("group_xKNtX4", "truck", "Free UK delivery"),
                         ("group_AKU3P8", "return", "30-day returns"),
                         ("group_49bT4f", "lock", "Secure checkout"),
                         ("group_WXkUAx", "chat_bubble", "UK-based support")):
    g = row[gid]["blocks"]
    for b in g.values():
        if b["type"] == "icon": b["settings"]["icon"] = icon
        if b["type"] == "text": b["settings"]["text"] = f"<p>{label}</p>"
details["blocks"]["accordion_trust"] = {
    "type": "accordion",
    "settings": {"icon": "plus", "dividers": True, "type_preset": "h6"},
    "blocks": {
        "row_delivery": {"type": "_accordion-row", "settings": {"heading": "Delivery", "icon": "truck"},
            "blocks": {"t": text_block("t", "<p>Free UK delivery on every order. Orders are processed within 1–3 working days and sent tracked; you'll get a tracking link by email. UK delivery usually takes 3–14 days depending on the item, because some products ship directly from our fulfilment partners. See our <a href=\"/policies/shipping-policy\">Shipping Policy</a>.</p>")},
            "block_order": ["t"]},
        "row_returns": {"type": "_accordion-row", "settings": {"heading": "30-day returns", "icon": "return"},
            "blocks": {"t": text_block("t", "<p>Not right for you? Email <a href=\"mailto:info@bioluxelab.com\">info@bioluxelab.com</a> within 30 days of delivery and we'll send return instructions. Damaged or faulty items are replaced or refunded. See <a href=\"/pages/delivery-and-returns\">Delivery &amp; Returns</a>.</p>")},
            "block_order": ["t"]},
        "row_help": {"type": "_accordion-row", "settings": {"heading": "Need help?", "icon": "chat_bubble"},
            "blocks": {"t": text_block("t", "<p>We're a UK-based team. Use the chat button, email <a href=\"mailto:info@bioluxelab.com\">info@bioluxelab.com</a> or visit our <a href=\"/pages/contact\">contact page</a>.</p>")},
            "block_order": ["t"]},
    },
    "block_order": ["row_delivery", "row_returns", "row_help"],
}
details["block_order"].insert(details["block_order"].index("group_JK8NUb") + 1, "accordion_trust")
prod["order"].remove("media_with_content_3R3mTy"); prod["sections"].pop("media_with_content_3R3mTy")  # theme demo copy
files["templates/product.json"] = prod

# ---- header / footer ----------------------------------------------------
hdr = load("sections/header-group.json")
hdr["sections"]["announcement_bar_modern"]["blocks"]["announcement_delivery"]["settings"]["text"] = \
    "Free UK delivery · 30-day returns · Secure checkout"
files["sections/header-group.json"] = hdr

ftr = load("sections/footer-group.json")
menus = ftr["sections"]["footer"]["blocks"]["group_x4FxXe"]["blocks"]
menus["menu_EQKgTP"]["settings"]["menu"] = "footer-shop"
menus["menu_kJtfJ8"]["settings"]["menu"] = "footer-support"
menus["menu_wjn6YT"]["settings"]["menu"] = "footer-story"
util = ftr["sections"]["utilities"]
util["block_order"].remove("social_icons"); util["blocks"].pop("social_icons")   # placeholder links to facebook.com etc.
files["sections/footer-group.json"] = ftr

# ---- 404 and cart: no "bestsellers" claim, show the curated front-page picks
for rel in ("templates/404.json", "templates/cart.json"):
    raw = (BEFORE / rel).read_text()
    raw = re.sub(r"^\s*/\*.*?\*/\s*", "", raw, flags=re.S).replace("Our bestsellers", "Featured picks")
    j = json.loads(raw)
    for s in j["sections"].values():
        if s.get("type") == "product-list" and s["settings"].get("collection") == "all":
            s["settings"]["collection"] = "frontpage"
    files[rel] = j

# ---- write + upsert -----------------------------------------------------
for rel, j in files.items():
    p = AFTER / rel; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(j, indent=2, ensure_ascii=False) + "\n")
print("prepared:", ", ".join(files))
if DRY: sys.exit(0)
tok = os.environ["SHOPIFY_TOKEN"]
payload = {"query": "mutation($id:ID!,$files:[OnlineStoreThemeFilesUpsertFileInput!]!){ themeFilesUpsert(themeId:$id, files:$files){ upsertedThemeFiles{ filename } userErrors{ filename code message } } }",
           "variables": {"id": THEME_ID, "files": [{"filename": rel, "body": {"type": "TEXT", "value": (AFTER / rel).read_text()}} for rel in files]}}
req = urllib.request.Request(f"https://{SHOP}/admin/api/2026-07/graphql.json", data=json.dumps(payload).encode(),
                             headers={"X-Shopify-Access-Token": tok, "Content-Type": "application/json"})
res = json.load(urllib.request.urlopen(req, timeout=90))
print(json.dumps(res.get("data", res), indent=1))
