#!/usr/bin/env python3
"""Create five new products (drafts) on 1 Nov 2026 from researched AliExpress listings.
Images are pulled from the supplier gallery; copy is ours. DSers mapping is done afterwards.
Usage: SHOPIFY_TOKEN=... NEWP_DIR=<dir with <id>.json scrapes> python3 this.py
"""
import json, os, sys, urllib.request
API = "https://xfxs8g-hn.myshopify.com/admin/api/2026-07"; TOK = os.environ["SHOPIFY_TOKEN"]; D = os.environ["NEWP_DIR"]
def rest(method, path, payload=None):
    req = urllib.request.Request(API + path, data=json.dumps(payload).encode() if payload else None, method=method, headers={"X-Shopify-Access-Token": TOK, "Content-Type": "application/json"})
    try: return json.load(urllib.request.urlopen(req, timeout=90))
    except urllib.error.HTTPError as e: return {"error": e.code, "body": e.read().decode()[:300]}
def scrape(aid): return json.load(open(f"{D}/{aid}.json"))["result"]
def gallery(aid, n=8): return [u for u in scrape(aid)["gallery"] if not u.lower().endswith(".png")][:n]
def opt_img(aid, label):
    for s in scrape(aid)["skus"]:
        for o in s["options"]:
            if o["label"] == label: return o.get("img")
DELIVERY = ('<h3>Delivery and returns</h3><p>Free UK delivery on every order. Orders are dispatched within 1–3 working days and usually arrive 3–14 days later, tracked. '
            'You have 30 days from delivery to return it. Details on our <a href="/pages/delivery-and-returns">Delivery &amp; Returns</a> page.</p>')
COLL = {"home-fitness": 697524289881, "gift-ideas": 697471730009, "yoga-practice": 697463505241, "meditation-essentials": 697463538009, "beauty-anti-ageing": 697524322649, "health-beauty": 698512867673, "health-care": 698512900441}

PRODUCTS = [
 {"aid": "1005007476990219", "handle": "acupressure-mat-and-pillow-set", "title": "Acupressure Mat & Pillow Set with Carry Bag",
  "type": "Acupressure Mat", "tags": "acupressure mat, spike mat, relaxation, back and neck, home wellness, gift ideas, recovery",
  "seo_title": "Acupressure Mat & Pillow Set | Bio Luxe Lab", "seo_desc": "Lotus-spike acupressure mat with matching neck pillow and carry bag. Four colours. Free UK delivery and 30-day returns.",
  "option": "Colour", "variants": [("Black & Gold", "3PCS-Gold"), ("Grey", "3PCS-Gray"), ("Purple", "3PCS-Purple"), ("Yellow", "3PCS-Yellow")], "price": "39.95",
  "collections": ["home-fitness", "gift-ideas"],
  "html": """<p>A spike mat and matching neck pillow for lying back and switching off. Thousands of small lotus-shaped points press evenly into the back, neck or feet; the first few minutes feel intense, then most people settle into a warm, heavy, deeply relaxed feeling. Many use it for ten to twenty minutes after work, after training or before bed.</p>
<h3>What's in the box</h3>
<ul><li>Acupressure mat with foam core and lotus spike panels</li><li>Matching neck pillow</li><li>Zipped carry bag</li></ul>
<h3>How to use</h3>
<ul><li>Start with a thin T-shirt between you and the spikes, then move to bare skin as you get used to it.</li><li>Lie down slowly and evenly; avoid dragging across the points. Stay still for 10–20 minutes and breathe slowly.</li><li>Use the pillow under the neck, or the mat under the feet while sitting.</li></ul>
<h3>Specifications</h3>
<table><tbody><tr><td>Set</td><td>Mat, neck pillow and carry bag</td></tr><tr><td>Spikes</td><td>Lotus-shaped ABS points on a cotton-blend cover</td></tr><tr><td>Core</td><td>Foam, with a removable washable cover</td></tr><tr><td>Colours</td><td>Black and gold, grey, purple or yellow</td></tr></tbody></table>
<h3>Good to know</h3>
<ul><li>This is a relaxation accessory, not a medical device. It does not treat any condition.</li><li>Do not use on broken, inflamed or sunburnt skin, during pregnancy, or if you have a bleeding disorder or sensory loss. Check with a healthcare professional if you are unsure.</li><li>Keep away from young children: the points are sharp.</li></ul>
<h3>Questions people ask</h3>
<p><strong>Does it hurt?</strong> The first two or three minutes are prickly. Keeping still and breathing slowly is the trick; after that it turns into a warm, heavy feeling.</p>
<p><strong>How do I wash it?</strong> Unzip the cover and hand wash cool; air dry. The foam core and the spike panels only need a wipe.</p>"""},
 {"aid": "1005009682923593", "handle": "heated-eye-massager-bluetooth", "title": "Heated Eye Massager with Air Pressure & Bluetooth Music",
  "type": "Eye Massager", "tags": "eye massager, heated eye mask, screen fatigue, sleep and recovery, gift ideas, USB-C",
  "seo_title": "Heated Eye Massager with Bluetooth | Bio Luxe Lab", "seo_desc": "Foldable heated eye massager with gentle air-pressure kneading, vibration and Bluetooth music. Rechargeable, boxed. Free UK delivery.",
  "option": None, "variants": [("Boxed", "With Box")], "price": "59.95",
  "collections": ["gift-ideas", "health-beauty"],
  "html": """<p>A soft, foldable mask that warms gently around the eyes while padded air cells press and release across the brow, temples and under-eye area. Pair it with your phone to play music or a sleep playlist through the built-in speakers. Fifteen minutes at the end of a screen-heavy day is what it's for.</p>
<h3>What it does</h3>
<ul><li>Gentle warmth across the eye area</li><li>Air-pressure kneading that cycles across the brow and temples</li><li>Light vibration</li><li>Bluetooth speakers for music or audio from your phone</li><li>Several preset programmes, selected from buttons on the mask</li></ul>
<h3>What's in the box</h3>
<ul><li>Eye massager in a gift box</li><li>USB charging cable</li><li>Storage pouch</li><li>User guide</li></ul>
<h3>Specifications</h3>
<table><tbody><tr><td>Power</td><td>Built-in rechargeable battery, USB charging</td></tr><tr><td>Fit</td><td>Adjustable elasticated strap; folds flat for travel</td></tr><tr><td>Controls</td><td>Buttons on the mask; Bluetooth pairing for audio</td></tr><tr><td>Session</td><td>Programmes run for a set time and switch off automatically</td></tr></tbody></table>
<h3>Good to know</h3>
<ul><li>A comfort product for tired eyes after screens or travel. It is not a medical device and does not treat eye conditions.</li><li>Do not use after eye surgery, with an eye infection or injury, or if you have glaucoma, retinal problems or a pacemaker, without medical advice.</li><li>Remove contact lenses and make-up first. Stop if the pressure or warmth is uncomfortable.</li></ul>
<h3>Questions people ask</h3>
<p><strong>Can I see through it?</strong> No, it covers the eyes fully. Lie back or sit comfortably and let it run.</p>
<p><strong>Is the sound loud enough?</strong> It is designed for quiet listening close to the ears, not room-filling volume.</p>"""},
 {"aid": "1005007048482101", "handle": "jade-roller-and-gua-sha-set", "title": "Natural Stone Face Roller & Gua Sha Set",
  "type": "Facial Tool", "tags": "gua sha, jade roller, rose quartz, facial massage, skincare ritual, beauty, gift ideas, stocking filler",
  "seo_title": "Face Roller & Gua Sha Set, Natural Stone | Bio Luxe Lab", "seo_desc": "Dual-ended natural stone face roller with a matching gua sha. Rose quartz, green jade, black obsidian or white jade. Free UK delivery.",
  "option": "Stone", "variants": [("Rose Quartz", "Pink Set"), ("Green Jade", "Green Set  A"), ("Black Obsidian", "Black Set"), ("White Jade", "White Set")], "price": "19.95",
  "collections": ["beauty-anti-ageing", "health-beauty", "gift-ideas"],
  "html": """<p>A two-piece set for a slow, cool facial massage: a dual-ended roller with a large stone for cheeks and forehead and a small one for under the eyes, plus a heart-shaped gua sha for the jaw, neck and brow. Keep it in the fridge for a cooler feel.</p>
<h3>How to use</h3>
<ul><li>Apply a facial oil or serum first so the stone glides.</li><li>Roll from the centre of the face outwards and upwards, light pressure, 5–10 strokes per area.</li><li>With the gua sha, use the curved edge along the jaw and the notched edge along the brow, always moving outwards.</li><li>Rinse in warm water, dry and store in the pouch.</li></ul>
<h3>Specifications</h3>
<table><tbody><tr><td>Set</td><td>Dual-ended roller and gua sha</td></tr><tr><td>Stones</td><td>Rose quartz, green jade, black obsidian or white jade</td></tr><tr><td>Frame</td><td>Metal, gold-tone (rose-gold on the rose quartz set)</td></tr><tr><td>Care</td><td>Rinse and dry; avoid drops on hard floors</td></tr></tbody></table>
<h3>Good to know</h3>
<ul><li>Natural stone varies in colour and veining, so each set is slightly different.</li><li>This is a massage accessory, not a treatment. It makes no claims about wrinkles or skin conditions.</li><li>Avoid broken skin, active acne or sunburn.</li></ul>
<h3>Questions people ask</h3>
<p><strong>Which stone should I pick?</strong> Rose quartz is the classic gift choice; jade stays coolest; obsidian is the heaviest in the hand. They do the same job.</p>
<p><strong>Does the roller squeak?</strong> A drop of oil on the axle sorts most squeaks.</p>"""},
 {"aid": "1005007524393871", "handle": "bluetooth-sleep-headband", "title": "Bluetooth Sleep Headband with Thin Speakers",
  "type": "Sleep Headphones", "tags": "sleep headband, sleep headphones, side sleeper, white noise, sleep and recovery, travel, gift ideas",
  "seo_title": "Bluetooth Sleep Headband | Bio Luxe Lab", "seo_desc": "Soft stretch headband with flat Bluetooth speakers for side sleepers, sleep stories and white noise. Rechargeable, washable band. Free UK delivery.",
  "option": "Colour", "variants": [("Grey", "Gray"), ("Black", "Black")], "price": "24.95",
  "collections": ["gift-ideas", "health-care"],
  "html": """<p>A soft stretch headband with two flat speakers sewn into the fabric, so you can fall asleep to a podcast, sleep story or white noise without earbuds digging in. It doubles as an eye mask when pulled down, and the speakers lift out so the band can be washed.</p>
<h3>Why people like it</h3>
<ul><li>Side sleepers: nothing hard between your ear and the pillow</li><li>Blocks a little light and muffles a little noise on its own</li><li>Useful on flights and trains, or for a quiet workout</li></ul>
<h3>Specifications</h3>
<table><tbody><tr><td>Connection</td><td>Bluetooth to any phone or tablet</td></tr><tr><td>Speakers</td><td>Two thin speakers, adjustable position inside the band</td></tr><tr><td>Controls</td><td>Buttons on the module: play, pause, volume, track</td></tr><tr><td>Power</td><td>Built-in rechargeable battery, USB charging; several nights per charge</td></tr><tr><td>Band</td><td>Stretch knit, one size; remove the speaker module before washing</td></tr><tr><td>Colours</td><td>Grey or black</td></tr></tbody></table>
<h3>Good to know</h3>
<ul><li>It is a comfort and audio accessory. It does not treat insomnia or any condition.</li><li>Keep the volume low for sleep. It has no microphone, so it is not for calls.</li></ul>
<h3>Questions people ask</h3>
<p><strong>Can I wash it?</strong> Yes. Slide the speaker module out through the opening, then hand wash the band and air dry.</p>
<p><strong>Will it stay on all night?</strong> The band is snug but soft. If you move a lot, the speakers can shift; a quick adjustment puts them back over the ears.</p>"""},
 {"aid": "1005008248114366", "handle": "yoga-wheel-back-stretch", "title": "Yoga Wheel for Back Stretching & Balance",
  "type": "Yoga Wheel", "tags": "yoga wheel, back stretch, backbend, yoga props, pilates, home fitness, gift ideas",
  "seo_title": "Yoga Wheel for Back Stretching | Bio Luxe Lab", "seo_desc": "Padded yoga wheel for opening the chest and upper back, supporting backbends and balance work. Grey or purple. Free UK delivery.",
  "option": "Colour", "variants": [("Grey", "Grey"), ("Purple", "Purple")], "price": "34.95",
  "collections": ["yoga-practice", "home-fitness", "gift-ideas"],
  "html": """<p>A sturdy padded wheel for lying back over to open the chest and upper back, for supported backbends, and for balance and core work. Beginners use it to feel what a safe backbend should feel like; experienced practitioners use it to go deeper with support.</p>
<h3>Ways to use it</h3>
<ul><li>Lie back along it with knees bent and arms overhead for a slow upper-back stretch</li><li>Hands or feet on the wheel for supported bridges, planks and balances</li><li>Roll slowly along the spine muscles after a long day sitting</li></ul>
<h3>Specifications</h3>
<table><tbody><tr><td>Construction</td><td>Rigid inner ring with a padded, non-slip outer layer</td></tr><tr><td>Colours</td><td>Grey or purple</td></tr><tr><td>Care</td><td>Wipe clean; store away from direct heat</td></tr></tbody></table>
<h3>Good to know</h3>
<ul><li>Go slowly, especially the first time. Keep the neck relaxed and come off the wheel by rolling to one side.</li><li>If you have a back or neck injury, osteoporosis or are pregnant, check with a professional before using it.</li><li>A yoga prop, not a medical device.</li></ul>
<h3>Questions people ask</h3>
<p><strong>Will it hold my weight?</strong> It is built for full body weight in supported poses; avoid jumping or dropping onto it.</p>"""},
]
created = []
for P in PRODUCTS:
    variants = [{"option1": lab, "price": P["price"], "inventory_management": None, "requires_shipping": True} for lab, _ in P["variants"]] if P["option"] else [{"price": P["price"], "inventory_management": None}]
    payload = {"product": {"title": P["title"], "handle": P["handle"], "body_html": P["html"] + DELIVERY, "vendor": "Bio Luxe Lab", "product_type": P["type"], "tags": P["tags"], "status": "draft",
               "metafields_global_title_tag": P["seo_title"], "metafields_global_description_tag": P["seo_desc"],
               "images": [{"src": u, "alt": P["title"]} for u in gallery(P["aid"])], "variants": variants}}
    if P["option"]: payload["product"]["options"] = [{"name": P["option"]}]
    r = rest("POST", "/products.json", payload)
    if "product" not in r: print("FAILED", P["handle"], r); continue
    p = r["product"]; print("created", p["id"], p["status"], p["title"], "| images", len(p["images"]), "| variants", len(p["variants"]))
    # variant images from the supplier swatches
    for v in p["variants"]:
        lab = v["option1"]; sup = dict(P["variants"]).get(lab)
        src = opt_img(P["aid"], sup) if sup else None
        if src and P["option"]:
            ri = rest("POST", f"/products/{p['id']}/images.json", {"image": {"src": src, "alt": f"{P['title']}, {lab}", "variant_ids": [v["id"]]}})
            print("   variant image", lab, "->", ri.get("image", ri).get("id") if isinstance(ri.get("image"), dict) else ri)
    for ch in P["collections"]:
        rc = rest("POST", "/collects.json", {"collect": {"product_id": p["id"], "collection_id": COLL[ch]}})
        print("   collection", ch, "->", "ok" if "collect" in rc else rc)
    created.append({"id": p["id"], "handle": p["handle"], "aliexpress": P["aid"], "variant_map": {lab: sup for lab, sup in P["variants"]}})
json.dump(created, open("/Users/joebains/shopify-ai-blog-system/docs/store/new-products-2026-11-01.json", "w"), indent=1)
print(json.dumps(created, indent=1))
