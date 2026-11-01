#!/usr/bin/env python3
"""Hero product pages, 1 Nov 2026: massage gun, gel bead facial mask, 4mm yoga mat.
Rewrites title/description/SEO/tags/type, tidies variant names, sets the default variant
and alt text. Prices are never touched. Usage: SHOPIFY_TOKEN=... python3 this.py
"""
import json, os, sys, urllib.request
SHOP = "xfxs8g-hn.myshopify.com"; API = f"https://{SHOP}/admin/api/2026-07"; TOK = os.environ["SHOPIFY_TOKEN"]
def rest(method, path, payload=None):
    req = urllib.request.Request(API + path, data=json.dumps(payload).encode() if payload else None, method=method,
                                 headers={"X-Shopify-Access-Token": TOK, "Content-Type": "application/json"})
    try: return json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e: return {"error": e.code, "body": e.read().decode()[:300]}
def gql(q, v=None):
    req = urllib.request.Request(API + "/graphql.json", data=json.dumps({"query": q, "variables": v or {}}).encode(),
                                 headers={"X-Shopify-Access-Token": TOK, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))

GUN, MASK, MAT = 16475426553433, 16529278239065, 16475424981337  # overwritten below by handle lookup
prods = rest("GET", "/products.json?limit=250&fields=id,handle,images,variants,options,body_html")["products"]
byh = {p["handle"]: p for p in prods}
gun = next(p for h, p in byh.items() if h.startswith("30-level-massage-gun"))
mask = next(p for h, p in byh.items() if h.startswith("ice-mask-gel-bead"))
mat = next(p for h, p in byh.items() if h.startswith("4mm-thick-eva-yoga-mats"))
def guide_link(p):
    b = p["body_html"]; i = b.rfind("<p><strong>Related")
    return b[i:] if i >= 0 else ""
DELIVERY = ('<h3>Delivery and returns</h3><p>Free UK delivery on every order. Orders are dispatched within 1–3 working days and '
            'usually arrive 3–14 days later, tracked. You have 30 days from delivery to return it. '
            'Details on our <a href="/pages/delivery-and-returns">Delivery &amp; Returns</a> page.</p>')

# ---------------------------------------------------------------- massage gun
gun_html = f"""<p>A cordless percussion massager for tight, tired muscles after training, long days at a desk or a lot of time on your feet. Pick the compact 6-speed model for a bag-friendly size, or the full-size 30-speed model with an LCD screen when you want more range and a bigger battery.</p>
<h3>Which model?</h3>
<ul>
<li><strong>6-speed compact</strong> (17.5 × 13.9 × 4.3 cm): light enough for one-handed use on calves, shoulders and forearms, with six intensity levels. The everyday choice.</li>
<li><strong>30-speed full size</strong> (23.6 × 25.8 × 4.9 cm): thirty levels with an LCD showing speed and battery, plus a carry case. For people who train hard and want fine control over intensity.</li>
</ul>
<h3>What's in the box</h3>
<ul>
<li>Massage gun (your chosen model)</li>
<li>Four heads: round ball for large muscle groups, U-shape for either side of the spine and Achilles, flat head for all-over use, bullet head for knots and the soles of the feet</li>
<li>USB-C charging cable</li>
<li>Carry case (30-speed model)</li>
</ul>
<h3>Specifications</h3>
<table><tbody>
<tr><td>Power</td><td>Built-in rechargeable battery, USB-C charging</td></tr>
<tr><td>Speed levels</td><td>6 (compact) or 30 (full size)</td></tr>
<tr><td>Display</td><td>LCD speed and battery readout (full-size model)</td></tr>
<tr><td>Attachments</td><td>4 interchangeable heads</td></tr>
<tr><td>Finish</td><td>Matte black ABS body with textured grip</td></tr>
<tr><td>Certifications (manufacturer stated)</td><td>CE, RoHS, FCC</td></tr>
</tbody></table>
<h3>Getting started</h3>
<p>Charge fully before first use. Start on the lowest level and let the head float over the muscle rather than pressing hard; 30 to 60 seconds per area is plenty. On the full-size model the manufacturer suggests levels 1–6 for warming up, 7–18 for everyday use and 19–30 for deep work on large muscles.</p>
<h3>Good to know</h3>
<ul>
<li>Avoid bones, joints, the front of the neck and any area that is injured, swollen or bruised.</li>
<li>If you are pregnant, have a medical condition or use a pacemaker, check with a healthcare professional before using a percussion massager.</li>
<li>This is a consumer massager for comfort and recovery after exercise. It is not a medical device and does not treat injuries.</li>
</ul>
<h3>Questions people ask</h3>
<p><strong>Is it loud?</strong> Noise rises with the speed level. On the lower half of the range it is quiet enough to use while watching TV.</p>
<p><strong>How long does the battery last?</strong> Several sessions per charge in normal use. Battery life depends on the speed level and head used, so we do not quote a fixed number of hours.</p>
<p><strong>Can I use it on my neck?</strong> On the muscles either side of the neck and across the shoulders, yes, on a low setting. Never on the throat or spine.</p>
{DELIVERY}
{guide_link(gun)}"""
r = rest("PUT", f"/products/{gun['id']}.json", {"product": {"id": gun["id"],
    "title": "Deep Tissue Massage Gun – 6 or 30 Speed Levels, 4 Heads, USB-C",
    "body_html": gun_html, "product_type": "Massage Gun", "vendor": "Bio Luxe Lab",
    "tags": "massage gun, percussion massager, muscle recovery, home fitness, USB-C, gift ideas, Electric, Type-C",
    "metafields_global_title_tag": "Deep Tissue Massage Gun, 6 or 30 Speeds | Bio Luxe Lab",
    "metafields_global_description_tag": "Cordless percussion massage gun with four heads and USB-C charging. Compact 6-speed or full-size 30-speed with LCD. Free UK delivery, 30-day returns."}})
print("gun product:", r.get("product", r).get("title") if isinstance(r.get("product"), dict) else r)
# variant names + default order (6-speed first: it is the stocked, everyday model)
opt_names = {o["name"]: o for o in gun["options"]}
for v in gun["variants"]:
    new = "6-speed compact" if v["option1"].startswith("6Gear") else "30-speed full size"
    pos = 1 if new.startswith("6") else 2
    rr = rest("PUT", f"/variants/{v['id']}.json", {"variant": {"id": v["id"], "option1": new, "position": pos}})
    print("  gun variant:", rr.get("variant", rr).get("title") if isinstance(rr.get("variant"), dict) else rr)
rr = rest("PUT", f"/products/{gun['id']}.json", {"product": {"id": gun["id"], "options": [{"id": opt_names["Color"]["id"], "name": "Model"}] + ([{"id": opt_names["Plug Type"]["id"], "name": "Charging"}] if "Plug Type" in opt_names else [])}})
print("  gun options:", [(o["name"], o["values"]) for o in rr.get("product", {}).get("options", [])] or rr)
# drop the single-value charging option entirely (cleaner buy box)
d = gql('{ product(id:"gid://shopify/Product/%d"){ options{ id name values } } }' % gun["id"])
charging = [o for o in d["data"]["product"]["options"] if o["name"] == "Charging"]
if charging:
    r2 = gql('mutation($pid:ID!,$opts:[ID!]!){ productOptionsDelete(productId:$pid, options:$opts, strategy:DEFAULT){ deletedOptionsIds userErrors{ message } } }',
             {"pid": f"gid://shopify/Product/{gun['id']}", "opts": [charging[0]["id"]]})
    print("  gun charging option removed:", r2["data"]["productOptionsDelete"])
gun_alts = ["Deep tissue massage gun, 30-speed model, with four attachment heads and carry case",
            "Massage gun LCD display showing speed level and battery with suggested level ranges",
            "Hand holding the compact massage gun with the round ball head",
            "Massage gun in use on the shoulder with the ball head",
            "Full-size 30-speed massage gun dimensions: 23.6 cm long, 25.8 cm tall, 4.9 cm wide",
            "Compact 6-speed massage gun dimensions: 17.5 cm long, 13.9 cm tall, 4.3 cm wide"]
for i, im in enumerate(gun["images"]):
    alt = gun_alts[i] if i < len(gun_alts) else f"Deep tissue massage gun, product image {i+1}"
    rest("PUT", f"/products/{gun['id']}/images/{im['id']}.json", {"image": {"id": im["id"], "alt": alt}})
print("  gun alts set:", len(gun["images"]))

# ---------------------------------------------------------------- facial mask
mask_html = f"""<p>A reusable gel bead mask you keep in the fridge or freezer for a cool, calming few minutes on the face, or warm gently for the opposite effect. It sits over the whole face with cut-outs for eyes, nose and mouth, and fastens at the back so you can lie down, read or scroll while you wear it.</p>
<h3>When people reach for it</h3>
<ul>
<li>First thing in the morning, chilled, when the face feels puffy or tired</li>
<li>After a hot day, a long flight or a workout</li>
<li>Warmed, as part of a slow evening skincare routine</li>
<li>As a quiet ten-minute pause with your eyes closed</li>
</ul>
<h3>How to use (manufacturer's guidance)</h3>
<ul>
<li><strong>Cold:</strong> lay the mask flat in the freezer for 2–6 hours, or in the fridge for 2–3 hours for a milder chill. Laying it flat stops the beads clumping.</li>
<li><strong>Warm:</strong> microwave in 10–15 second bursts, checking the temperature between bursts and never exceeding 30 seconds in total, or rest it in a bowl of warm (not boiling) water for a few minutes.</li>
<li>Test on the inside of your wrist before putting it on your face. Wear for 5–10 minutes. If it ever feels too hot or too cold, take it off.</li>
<li>Wipe clean with a damp cloth and store flat. Do not use if the mask is punctured or leaking.</li>
</ul>
<h3>Specifications</h3>
<table><tbody>
<tr><td>Size</td><td>About 19.5 × 18.5 cm, one size with an adjustable strap</td></tr>
<tr><td>Filling</td><td>Soft gel beads that hold warmth or cold</td></tr>
<tr><td>Colours</td><td>Pink, blue, green or red</td></tr>
<tr><td>Weight</td><td>About 255 g</td></tr>
<tr><td>Care</td><td>Wipe clean; reusable</td></tr>
</tbody></table>
<h3>Good to know</h3>
<ul>
<li>This is a comfort and self-care item. It is not a medical product and makes no claims about skin conditions.</li>
<li>Do not sleep in it, and do not apply it chilled for longer than 10 minutes at a time.</li>
<li>As a personal-care item it can only be returned unused in its sealed packaging, unless it is faulty.</li>
</ul>
<h3>Questions people ask</h3>
<p><strong>Does the cold last?</strong> From the freezer, expect a strong chill for around 10 minutes, easing off after that. From the fridge it is gentler and shorter.</p>
<p><strong>Which side goes on the face?</strong> The smooth bead side rests against the skin; the fabric backing faces out.</p>
<p><strong>Why do the colours cost slightly different amounts?</strong> They come from different supplier batches. The product is the same.</p>
{DELIVERY}
{guide_link(mask)}"""
r = rest("PUT", f"/products/{mask['id']}.json", {"product": {"id": mask["id"],
    "title": "Hot & Cold Gel Bead Facial Mask",
    "body_html": mask_html, "product_type": "Facial Mask", "vendor": "Bio Luxe Lab",
    "tags": "gel bead facial mask, hot and cold mask, cooling face mask, self-care, spa at home, beauty, gift ideas, pink, blue, green, red",
    "metafields_global_title_tag": "Hot & Cold Gel Bead Face Mask | Bio Luxe Lab",
    "metafields_global_description_tag": "Reusable gel bead face mask for a cool or gently warm ten-minute pause. Fridge, freezer or warm-water use, four colours, free UK delivery and 30-day returns."}})
print("mask product:", r.get("product", r).get("title") if isinstance(r.get("product"), dict) else r)
order = {"pink": 1, "Blue": 2, "green": 3, "red": 4}; nice = {"pink": "Pink", "Blue": "Blue", "green": "Green", "red": "Red"}
for v in mask["variants"]:
    rr = rest("PUT", f"/variants/{v['id']}.json", {"variant": {"id": v["id"], "option1": nice[v["option1"]], "position": order[v["option1"]]}})
    print("  mask variant:", rr.get("variant", rr).get("title") if isinstance(rr.get("variant"), dict) else rr)
mask_alts = ["Blue gel bead facial mask worn chilled, close up on a woman's face",
             "Woman lying on a sofa wearing the blue gel bead facial mask while using her phone",
             "Manufacturer's heating and chilling guidance for the gel bead mask",
             "Blue gel bead facial mask laid flat, about 19.5 by 18.5 cm"]
for i, im in enumerate(mask["images"]):
    alt = mask_alts[i] if i < len(mask_alts) else f"Hot and cold gel bead facial mask, product image {i+1}"
    rest("PUT", f"/products/{mask['id']}/images/{im['id']}.json", {"image": {"id": im["id"], "alt": alt}})
print("  mask alts set:", len(mask["images"]))

# ---------------------------------------------------------------- yoga mat
mat_html = f"""<p>A firm, light 4 mm mat for yoga, pilates and floor work at home or in class. It folds into a flat square rather than rolling, so it slips into a backpack or under an arm, and the closed-cell EVA surface wipes clean and does not soak up sweat.</p>
<h3>Who it suits</h3>
<ul>
<li>Beginners who want an affordable first mat that still grips</li>
<li>Anyone who carries a mat to class, the office or when travelling</li>
<li>Standing balances and slower floor work, where feeling the floor matters more than deep cushioning</li>
</ul>
<h3>Specifications</h3>
<table><tbody>
<tr><td>Size</td><td>173 × 61 cm</td></tr>
<tr><td>Thickness</td><td>4 mm</td></tr>
<tr><td>Weight</td><td>About 260 g</td></tr>
<tr><td>Material</td><td>Closed-cell EVA foam, textured anti-slip surface</td></tr>
<tr><td>Storage</td><td>Folds flat (about 43 × 61 cm) or rolls</td></tr>
<tr><td>Colours</td><td>Black, blue, deep blue, dusty pink or purple</td></tr>
</tbody></table>
<h3>Care</h3>
<p>Wipe with a damp cloth and mild soap, air dry away from direct sun, and store flat or loosely rolled. Use indoors on an even floor.</p>
<h3>Good to know</h3>
<ul>
<li>At 4 mm it is a stability mat, not a cushioning mat. If you have sensitive knees or want a mat for high-impact training, choose something 8 mm or thicker.</li>
<li>EVA is lighter and cheaper than natural rubber. It is not designed for heavy weightlifting or outdoor use.</li>
<li>New mats can carry a faint factory smell for a day or two; airing it out clears it.</li>
</ul>
<h3>Questions people ask</h3>
<p><strong>Is it non-slip on a wooden floor?</strong> Yes for yoga and pilates. For sweaty high-intensity sessions, expect to wipe it down.</p>
<p><strong>Does it come with a strap or bag?</strong> The mat only, unless the variant name says otherwise. It folds small enough not to need one.</p>
{DELIVERY}
{guide_link(mat)}"""
r = rest("PUT", f"/products/{mat['id']}.json", {"product": {"id": mat["id"],
    "title": "Essential 4mm Anti-Slip Yoga & Pilates Mat",
    "body_html": mat_html, "product_type": "Yoga Mat", "vendor": "Bio Luxe Lab",
    "tags": "yoga mat, pilates mat, 4mm mat, EVA, foldable yoga mat, travel yoga mat, beginner yoga, home fitness",
    "metafields_global_title_tag": "Essential 4mm Anti-Slip Yoga & Pilates Mat | Bio Luxe Lab",
    "metafields_global_description_tag": "Firm, light 4 mm EVA yoga and pilates mat, 173 x 61 cm, folds flat for travel. Five colours. Free UK delivery and 30-day returns."}})
print("mat product:", r.get("product", r).get("title") if isinstance(r.get("product"), dict) else r)
mat_alts = ["4mm EVA yoga mats in purple, pink and black, with a woman exercising on the purple mat",
            "Pink 4mm yoga mat dimensions: 173 by 61 cm, 0.4 cm thick",
            "Pink yoga mat shown flat, folded in a concertina and folded into a square"]
for i, im in enumerate(mat["images"]):
    alt = mat_alts[i] if i < len(mat_alts) else f"Essential 4mm anti-slip yoga and pilates mat, product image {i+1}"
    rest("PUT", f"/products/{mat['id']}/images/{im['id']}.json", {"image": {"id": im["id"], "alt": alt}})
print("  mat alts set:", len(mat["images"]))
