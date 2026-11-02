import json, importlib.util
spec=importlib.util.spec_from_file_location("shp","shp.py"); shp=importlib.util.module_from_spec(spec); spec.loader.exec_module(shp)
LOC="gid://shopify/Location/118112846169"
KITIMG=json.load(open('kit_images.json'))
CDN="https://cdn.shopify.com/s/files/1/1037/0849/2121/files/"
PROMISE='''<h3>The 30-Night Promise</h3>
<p>You get thirty nights from the day it arrives. Use it. If it has not earned a place in your routine, email <a href="mailto:info@bioluxelab.com">info@bioluxelab.com</a> with your order number, send it back unused in its packaging, and we refund you. Any reason. Return postage is yours if you have simply changed your mind; if anything arrives faulty, we cover it and replace it. This sits on top of your legal rights, not instead of them: see the <a href="/policies/refund-policy">refund policy</a>.</p>'''
DELIVERY='''<h3>Delivery</h3>
<p>Free UK delivery on every order. Dispatched within 1–3 working days and usually with you 3–14 days later, tracked. Kit items are shipped by our fulfilment partners and may arrive as separate parcels on different days; the digital bonuses arrive by email straight after checkout.</p>'''
CALL='<li><strong>A 20-minute Routine Check-in call with Joe.</strong> Book it from the link in your order email ([CALENDLY LINK]). You pick two or three things you will actually do; Joe helps you set them up and checks in.</li>'
GOODTOKNOW='''<h3>Good to know</h3>
<ul><li>These are fitness and relaxation accessories, not medical devices, and the routine is general wellbeing information, not advice. Nothing here treats or prevents any condition.</li>
<li>If you are injured, pregnant or have a health condition, check with a healthcare professional before starting a new stretching, rolling or breathing routine. Stop anything that hurts.</li>
<li>Keep the roller off joints, the lower back and the neck.</li></ul>'''
REVIEWS='''<h3>Reviews</h3>
<p>This kit is new, so there are no reviews yet. We only show reviews from people who bought the item here, never imported ones. If you have the kit, email a line about how the first 30 nights went to info@bioluxelab.com and we will add it with your first name.</p>'''

KIT1='''<p>Twenty minutes, three things, evening sorted. This is the kit behind Joe's own wind-down routine: the mat he leaves rolled out, the roller by the sofa and the cards on the bedside table, with the routine on video and a tracker to keep it going.</p>
<h3>What's in the kit</h3>
<ul>
<li><strong>Essential 4 mm Anti-Slip Yoga &amp; Pilates Mat</strong>, 173 × 61 cm, closed-cell EVA, folds flat. Sold separately at £34.56.</li>
<li><strong>Grooved EPP Foam Roller</strong>, about 33 cm long and 14 cm across, black and blue. Sold separately at £29.73.</li>
<li><strong>Monochrome Watercolour Meditation Cards</strong>, set of 36. Sold separately at £27.23.</li>
</ul>
<h3>Plus the parts you cannot buy separately</h3>
<ul>
<li><strong>Joe's Evening Wind-Down follow-along video</strong> (15 minutes, founder video FV-03), linked in your order email.</li>
<li><strong>The 30-Day Routine Tracker</strong>, a printable PDF.</li>
<li><strong>The 7-Night Wind-Down Plan</strong>, the PDF guide to building the routine one night at a time.</li>
''' + CALL + '''
</ul>
<p>Bought separately, the three products come to £91.52. The kit is £79 with free UK delivery.</p>
<h3>The routine, in twenty minutes</h3>
<table><tbody>
<tr><td>0–2 min</td><td>Overhead light off, lamp on, phone on a shelf, warm drink on</td></tr>
<tr><td>2–8 min</td><td>Three slow stretches on the mat: cat and cow, child's pose, lying twist</td></tr>
<tr><td>8–16 min</td><td>The eight-minute roll: calves, hamstrings, glutes, upper back</td></tr>
<tr><td>16–18 min</td><td>Two minutes of box breathing, then four rounds of 4-7-8</td></tr>
<tr><td>18–20 min</td><td>Draw a card, read it, leave it face up for the morning. Bed.</td></tr>
</tbody></table>
''' + PROMISE + DELIVERY + '''
<h3>Questions people ask</h3>
<p><strong>Can I choose the mat colour?</strong> Yes: matte black or dusty pink. The roller comes in black and blue.</p>
<p><strong>I already have a mat.</strong> Then buy the roller and the cards separately; the kit price only makes sense with all three. You can still get the videos and tracker with any first order.</p>
<p><strong>Is the roller very firm?</strong> It is a firm, grooved roller. If it feels too much at first, put a folded towel over it or use less body weight.</p>
<p><strong>Will everything arrive together?</strong> Usually within a few days of each other; sometimes as separate parcels.</p>
<p><strong>What if I don't keep the routine?</strong> That is what the 30-Night Promise is for.</p>
''' + REVIEWS + GOODTOKNOW

KIT2='''<p>No studio, no reformer: a ring, a block, a band and a mat, and the fifteen-minute session Joe follows at home. Four pieces that cover a first proper Pilates session, with the session itself on video.</p>
<h3>What's in the kit</h3>
<ul>
<li><strong>Ergonomic Pilates Resistance Ring</strong>, black, crescent-shaped handles, glass-fibre and PP core with an NBR sleeve. Sold separately at £26.93.</li>
<li><strong>Essential EVA Foam Yoga Block</strong>, blue, about 140 g. Sold separately at £14.41.</li>
<li><strong>Resistance Band with Handles</strong>, 15 lb. Sold separately at £12.37.</li>
<li><strong>Foldable EVA Travel Yoga Mat</strong>, 3 mm, black, 173 × 61 cm, folds into a bag. Sold separately at £35.60.</li>
</ul>
<h3>Plus the parts you cannot buy separately</h3>
<ul>
<li><strong>Joe's 15-minute Pilates at-home follow-along session</strong> (founder video FV-05), linked in your order email.</li>
<li><strong>The 30-Day Routine Tracker</strong>, a printable PDF with the Pilates rows filled in.</li>
''' + CALL + '''
</ul>
<p>Bought separately, the four products come to £89.31. The kit is £74 with free UK delivery.</p>
<h3>The session, in fifteen minutes</h3>
<table><tbody>
<tr><td>0–3 min</td><td>Breathing and bracing, lying on the mat</td></tr>
<tr><td>3–7 min</td><td>Ring: three sets of ten inner-thigh squeezes, three sets of ten chest presses</td></tr>
<tr><td>7–9 min</td><td>Band: two sets of twelve seated rows</td></tr>
<tr><td>9–11 min</td><td>Block: five supported bridges with the block between the knees</td></tr>
<tr><td>11–13 min</td><td>Dead bug, one minute; the hundred, one minute</td></tr>
<tr><td>13–15 min</td><td>Two-minute stretch, lying twist each side</td></tr>
</tbody></table>
''' + PROMISE + DELIVERY + '''
<h3>Questions people ask</h3>
<p><strong>Is 15 lb the right band?</strong> It is the lightest of the four levels and the right place to start for seated rows. Heavier bands are sold separately.</p>
<p><strong>Why a 3 mm folding mat?</strong> It folds flat into a bag, so it lives by the sofa rather than in a cupboard. If you want a thicker mat, the Essential 4 mm mat is sold separately.</p>
<p><strong>Do I need any experience?</strong> No. The follow-along session cues every move.</p>
<p><strong>Will everything arrive together?</strong> Usually within a few days of each other; sometimes as separate parcels.</p>
''' + REVIEWS + GOODTOKNOW

KIT3='''<p>Just back from a run? This is the ten minutes Joe does before he is allowed to sit down: thirty seconds an area with the massage gun, then four areas on the roller. Two tools, one routine, on video.</p>
<h3>What's in the kit</h3>
<ul>
<li><strong>Deep Tissue Massage Gun, 6-speed compact</strong>, four heads, USB-C charging. Sold separately at £46.20.</li>
<li><strong>Grooved EPP Foam Roller</strong>, about 33 cm long and 14 cm across. Sold separately at £29.73.</li>
</ul>
<h3>Plus the parts you cannot buy separately</h3>
<ul>
<li><strong>Joe's 10-minute post-run follow-along</strong> (founder video FV-07), linked in your order email.</li>
<li><strong>The 30-Day Routine Tracker</strong>, a printable PDF with the recovery rows filled in.</li>
''' + CALL + '''
</ul>
<p>Bought separately, the two products come to £75.93. The kit is £69 with free UK delivery.</p>
<h3>The routine, in ten minutes</h3>
<table><tbody>
<tr><td>0–5 min</td><td>Massage gun, lowest speed first, thirty seconds each: calves, hamstrings, quads, glutes, upper back. Stay off bones and joints.</td></tr>
<tr><td>5–9 min</td><td>Roller: calves, hamstrings, glutes, upper back, one minute each</td></tr>
<tr><td>9–10 min</td><td>Lie flat, two slow breaths, then water and an early night</td></tr>
</tbody></table>
''' + PROMISE + DELIVERY + '''
<h3>Questions people ask</h3>
<p><strong>Which massage gun is this?</strong> The compact 6-speed model with four heads and USB-C charging. The full-size 30-speed model is sold separately.</p>
<p><strong>Is it loud?</strong> It is a percussion massager, so there is a hum; the compact model is the quieter of the two.</p>
<p><strong>Is it a medical device?</strong> No. It is a massage accessory for tight, tired muscles after training; it does not treat anything.</p>
''' + REVIEWS + GOODTOKNOW

SVC_FOOT='''<h3>What this is, and is not</h3>
<p>Coaching with Joe is about routines, habits, breathwork, meditation practice, sleep hygiene, training and recovery, measurement and accountability, in plain language. It is not medical advice, it does not diagnose or treat anything, and if something in your health needs a professional Joe will say so.</p>
<h3>Booking and changes</h3>
<p>After checkout your order email carries the scheduling link ([CALENDLY LINK]) and the Zoom or Google Meet details. Need to move a date? Email <a href="mailto:info@bioluxelab.com">info@bioluxelab.com</a> at least 24 hours before; sessions and seats can be rescheduled once free of charge, and refunded in full up to 14 days after purchase if the session has not yet taken place, under the Consumer Contracts Regulations.</p>
<h3>Reviews</h3><p>New on the store, so no reviews here yet. Joe's teaching reviews are on his <a href="https://www.apprentus.com/en-us/in/joebains" rel="noopener" target="_blank">Apprentus profile</a>.</p>'''

S1='''<p>Sixty minutes, one to one, online or in person in London by arrangement. You leave with a one-page routine plan: two or three things you will actually do, how you will track them, and when Joe checks in.</p>
<h3>How it works</h3>
<ol><li>Book, then answer five short intake questions by email (what your days look like, what you have tried, what you want to keep).</li>
<li>The call: we go through your mornings, evenings, training and sleep habits as they are now, and pick the smallest changes with the best chance of sticking.</li>
<li>Within 24 hours you get the one-page plan and the 30-Day Routine Tracker with your rows filled in.</li>
<li>A check-in email at day 14.</li></ol>
<h3>Who it suits</h3>
<p>People who have read enough and want someone to help them pick, set up and keep a routine. Not for anyone looking for a diagnosis, a diet or a treatment.</p>''' + SVC_FOOT

S2='''<p>Ninety minutes online, eight people, and you leave with an evening routine you will actually do. Breathing you can do anywhere, the twenty-minute wind-down, the meditation that works for people who tried it and gave up, and a tracker to take home.</p>
<h3>What happens</h3>
<ul><li>Fifteen minutes: why evenings go wrong, and the three rules (same start time, gentle beats intense, finish before bed).</li>
<li>Twenty minutes: breathing, done together: belly, box and 4-7-8.</li>
<li>Thirty minutes: the twenty-minute wind-down, walked through on the mat.</li>
<li>Fifteen minutes: meditation for people who gave up, the short version.</li>
<li>Ten minutes: build your own routine on the tracker; questions.</li></ul>
<h3>Tickets</h3>
<ul><li><strong>Seat only, £35.</strong> Bring a mat or a rug, something to roll on if you have it, a notebook.</li>
<li><strong>Seat + Evening Wind-Down Kit, £99.</strong> The mat, grooved roller and meditation cards arrive at your door before the workshop (free UK delivery), with Joe's follow-along video, the tracker and the 20-minute check-in call. Bought separately: £114.</li></ul>
<p>Dates on this page are the first two sessions, online over Zoom or Google Meet, 19:30 UK time. Maximum eight seats per date.</p>''' + SVC_FOOT

S3='''<p>Four weeks, four weekly group calls, a tracker, check-ins between calls, and the Evening Wind-Down Kit at your door before week one. For people who want a routine to stick this time, with company.</p>
<h3>The four weeks</h3>
<ol><li><strong>Week 1, Evenings.</strong> Lights, screens, the twenty-minute wind-down with the kit.</li>
<li><strong>Week 2, Breath and attention.</strong> Belly, box and 4-7-8 breathing; a short daily sit that survives a busy day.</li>
<li><strong>Week 3, Movement and recovery.</strong> Stretching and rolling that fits around training, work and travel.</li>
<li><strong>Week 4, Mornings and measurement.</strong> Anchoring habits, tracking without obsessing, and the next 30 days.</li></ol>
<h3>What's included</h3>
<ul><li>Four 60-minute group calls (Zoom or Google Meet), maximum ten people, same evening each week at 19:30 UK time.</li>
<li>The Evening Wind-Down Kit (mat, grooved roller, 36 meditation cards), free UK delivery, with the follow-along video and the 7-Night Wind-Down Plan.</li>
<li>The 30-Day Routine Tracker and a weekly email check-in.</li>
<li>A 20-minute one-to-one Routine Check-in call with Joe during the programme.</li></ul>
<p>Separately: the kit (£79), four hours of group coaching and the call come to well over £200. The programme is £149.</p>
<p>The cohort dates on this page are the first two start dates; each cohort runs for four consecutive weeks.</p>''' + SVC_FOOT

S4='''<p>Ninety minutes online, up to eight people, three balls, and by the end you will have the cascade or be one honest practice session away from it. Joe teaches juggling to eight-year-olds and boardrooms with the same method: one throw at a time.</p>
<h3>What you need</h3>
<ul><li>Three balls of roughly the same size and weight (tennis balls, beanbags, rolled socks all work).</li><li>A bit of floor space and a camera angle that shows your hands.</li></ul>
<h3>What happens</h3>
<ul><li>One ball: the throw that makes everything else easy.</li><li>Two balls: the exchange, and the drill that fixes the most common mistake.</li><li>Three balls: the cascade, built in short steps with rests.</li><li>How to practise in five minutes a day so it sticks.</li></ul>
<p>Good for teams and groups too: for a private session for your workplace or club, use the contact form on <a href="https://joebains.com/contact" rel="noopener" target="_blank">joebains.com</a>.</p>
<h3>Booking and changes</h3>
<p>Your order email carries the Zoom or Google Meet details. Need to move a date? Email <a href="mailto:info@bioluxelab.com">info@bioluxelab.com</a> at least 24 hours before; seats can be moved once free of charge and refunded in full up to 14 days after purchase if the session has not yet taken place.</p>'''

def var(optname, optval, price, sku, qty, ship, weight=None, compare=None, img=None, tracked=True):
    v={"optionValues":[{"optionName":optname,"name":optval}],"price":price,"sku":sku,"inventoryPolicy":"DENY",
       "inventoryItem":{"tracked":tracked,"requiresShipping":ship}}
    if tracked: v["inventoryQuantities"]=[{"locationId":LOC,"name":"available","quantity":qty}]
    if weight: v["inventoryItem"]["measurement"]={"weight":{"unit":"KILOGRAMS","value":weight}}
    if compare: v["compareAtPrice"]=compare
    if img: v["file"]={"originalSource":img,"contentType":"IMAGE"}
    return v
def var2(opts, price, sku, qty, ship, weight=None, compare=None):
    v=var(opts[0][0],opts[0][1],price,sku,qty,ship,weight,compare); v["optionValues"]=[{"optionName":o,"name":n} for o,n in opts]; return v

PRODUCTS=[
 {"title":"The Evening Wind-Down Kit","handle":"evening-wind-down-kit","productType":"Kit","vendor":"Bio Luxe Lab","tags":["kit","hero-kit","evening wind-down","yoga mat","foam roller","meditation cards","founder video FV-02"],
  "seo":{"title":"The Evening Wind-Down Kit | Bio Luxe Lab","description":"Yoga mat, grooved foam roller and 36 meditation cards with Joe's follow-along evening routine, a 30-day tracker and the 30-Night Promise. £79, free UK delivery."},
  "descriptionHtml":KIT1,
  "productOptions":[{"name":"Mat colour","position":1,"values":[{"name":"Matte Black"},{"name":"Dusty Pink"}]}],
  "variants":[var("Mat colour","Matte Black","79.00","BLL-KIT-EVENING-BLK",30,True,1.5,"91.52"), var("Mat colour","Dusty Pink","79.00","BLL-KIT-EVENING-PNK",20,True,1.5,"91.52", img=CDN+"essential-4mm-anti-slip-yoga-pilates-mat-3805604.webp")],
  "files":[{"originalSource":KITIMG["kit-evening-wind-down-kit.png"],"contentType":"IMAGE","alt":"The Evening Wind-Down Kit: black 4 mm yoga mat, grooved foam roller and 36 meditation cards"},
           {"originalSource":CDN+"essential-4mm-anti-slip-yoga-pilates-mat-7099944.webp","contentType":"IMAGE","alt":"Essential 4 mm anti-slip yoga mat, matte black, part of the Evening Wind-Down Kit"},
           {"originalSource":CDN+"grooved-epp-foam-roller-for-yoga-recovery-1776759.webp","contentType":"IMAGE","alt":"Grooved EPP foam roller, black and blue, part of the Evening Wind-Down Kit"},
           {"originalSource":CDN+"monochrome-watercolour-meditation-cards-set-of-36-3765458.webp","contentType":"IMAGE","alt":"Monochrome watercolour meditation cards, set of 36, part of the Evening Wind-Down Kit"},
           {"originalSource":CDN+"essential-4mm-anti-slip-yoga-pilates-mat-3805604.webp","contentType":"IMAGE","alt":"Essential 4 mm anti-slip yoga mat, dusty pink option"}],
  "metafields":[{"namespace":"custom","key":"kit_components","type":"json","value":json.dumps([{"product":"gid://shopify/Product/16475424981337","variant_by_option":{"Matte Black":"gid://shopify/ProductVariant/58782927028569","Dusty Pink":"gid://shopify/ProductVariant/58782927094105"},"qty":1},{"product":"gid://shopify/Product/16475424686425","variant":"gid://shopify/ProductVariant/58782925390169","qty":1},{"product":"gid://shopify/Product/16475426292057","variant":"gid://shopify/ProductVariant/58782935122265","qty":1}])},
                {"namespace":"custom","key":"founder_video_slot","type":"single_line_text_field","value":"FV-02 intro, FV-03 routine bonus, FV-08 guarantee"}]},
 {"title":"The Pilates-at-Home Starter Kit","handle":"pilates-at-home-starter-kit","productType":"Kit","vendor":"Bio Luxe Lab","tags":["kit","hero-kit","pilates","pilates ring","yoga block","resistance band","travel yoga mat","founder video FV-04"],
  "seo":{"title":"The Pilates-at-Home Starter Kit | Bio Luxe Lab","description":"Pilates ring, yoga block, handled resistance band and a foldable travel mat with Joe's 15-minute follow-along session, a 30-day tracker and the 30-Night Promise. £74, free UK delivery."},
  "descriptionHtml":KIT2,
  "productOptions":[{"name":"Title","position":1,"values":[{"name":"Default Title"}]}],
  "variants":[var("Title","Default Title","74.00","BLL-KIT-PILATES",20,True,1.4,"89.31")],
  "files":[{"originalSource":KITIMG["kit-pilates-at-home-starter-kit.png"],"contentType":"IMAGE","alt":"The Pilates-at-Home Starter Kit: foldable black mat, black Pilates ring, blue foam block and handled resistance band"},
           {"originalSource":CDN+"ergonomic-pilates-resistance-ring-1785745.webp","contentType":"IMAGE","alt":"Ergonomic Pilates resistance ring, black, part of the Pilates-at-Home Starter Kit"},
           {"originalSource":CDN+"essential-eva-foam-yoga-block-3068084.webp","contentType":"IMAGE","alt":"Essential EVA foam yoga block, blue, part of the Pilates-at-Home Starter Kit"},
           {"originalSource":CDN+"resistance-bands-with-handles-four-levels-2070944.webp","contentType":"IMAGE","alt":"Resistance band with handles, 15 lb, part of the Pilates-at-Home Starter Kit"},
           {"originalSource":CDN+"foldable-eva-travel-yoga-mat-7282943.webp","contentType":"IMAGE","alt":"Foldable EVA travel yoga mat, 3 mm black, part of the Pilates-at-Home Starter Kit"}],
  "metafields":[{"namespace":"custom","key":"kit_components","type":"json","value":json.dumps([{"product":"gid://shopify/Product/16475424588121","variant":"gid://shopify/ProductVariant/58782924636505","qty":1},{"product":"gid://shopify/Product/16475424751961","variant":"gid://shopify/ProductVariant/58782925685081","qty":1},{"product":"gid://shopify/Product/16475424850265","variant":"gid://shopify/ProductVariant/58782926373209","qty":1},{"product":"gid://shopify/Product/16475425014105","variant":"gid://shopify/ProductVariant/58782927323481","qty":1}])},
                {"namespace":"custom","key":"founder_video_slot","type":"single_line_text_field","value":"FV-04 intro, FV-05 session bonus, FV-08 guarantee"}]},
 {"title":"The Post-Run Recovery Kit","handle":"post-run-recovery-kit","productType":"Kit","vendor":"Bio Luxe Lab","tags":["kit","hero-kit","massage gun","foam roller","after running","founder video FV-06"],
  "seo":{"title":"The Post-Run Recovery Kit | Bio Luxe Lab","description":"Compact 6-speed massage gun and grooved foam roller with Joe's 10-minute post-run follow-along, a 30-day tracker and the 30-Night Promise. £69, free UK delivery."},
  "descriptionHtml":KIT3,
  "productOptions":[{"name":"Title","position":1,"values":[{"name":"Default Title"}]}],
  "variants":[var("Title","Default Title","69.00","BLL-KIT-RECOVERY",50,True,1.1,"75.93")],
  "files":[{"originalSource":KITIMG["kit-post-run-recovery-kit.png"],"contentType":"IMAGE","alt":"The Post-Run Recovery Kit: compact massage gun with four heads and a grooved foam roller"},
           {"originalSource":CDN+"deep-tissue-massage-gun-30-speed-levels-for-complete-muscle-recovery-3375632.webp","contentType":"IMAGE","alt":"Compact 6-speed massage gun with four heads, part of the Post-Run Recovery Kit"},
           {"originalSource":CDN+"grooved-epp-foam-roller-for-yoga-recovery-1776759.webp","contentType":"IMAGE","alt":"Grooved EPP foam roller, part of the Post-Run Recovery Kit"}],
  "metafields":[{"namespace":"custom","key":"kit_components","type":"json","value":json.dumps([{"product":"gid://shopify/Product/16486597853529","variant":"gid://shopify/ProductVariant/58835959480665","qty":1},{"product":"gid://shopify/Product/16475424686425","variant":"gid://shopify/ProductVariant/58782925390169","qty":1}])},
                {"namespace":"custom","key":"founder_video_slot","type":"single_line_text_field","value":"FV-06 intro, FV-07 routine bonus, FV-08 guarantee"}]},
 {"title":"Routine Reset Session with Joe (1:1, 60 minutes)","handle":"routine-reset-session","productType":"Coaching","vendor":"Joe Bains","tags":["service","coaching","no-social","no-feed","founder video FV-09"],
  "seo":{"title":"Routine Reset Session with Joe, 1:1 Coaching | Bio Luxe Lab","description":"A 60-minute one-to-one session with Joe Bains, online or in London: pick two or three routine changes you will actually keep, with a one-page plan and a 30-day tracker. £65."},
  "descriptionHtml":S1,
  "productOptions":[{"name":"Format","position":1,"values":[{"name":"60 minutes, online (Zoom or Google Meet)"}]}],
  "variants":[var("Format","60 minutes, online (Zoom or Google Meet)","65.00","BLL-SVC-SESSION-60",0,False,tracked=False)],
  "files":[{"originalSource":CDN+"founder-video-placeholder-FV-09.png","contentType":"IMAGE","alt":"Founder video placeholder FV-09, Meet Joe: replace with a photo of Joe before publishing"}],
  "metafields":[{"namespace":"custom","key":"founder_video_slot","type":"single_line_text_field","value":"FV-09"},{"namespace":"custom","key":"scheduling_link","type":"single_line_text_field","value":"[CALENDLY LINK]"}]},
 {"title":"Evening Reset Workshop (online, 90 minutes)","handle":"evening-reset-workshop","productType":"Workshop","vendor":"Joe Bains","tags":["service","workshop","no-social","no-feed","founder video FV-10"],
  "seo":{"title":"Evening Reset Workshop with Joe, Online | Bio Luxe Lab","description":"A 90-minute online workshop, eight seats: breathing, the 20-minute wind-down and meditation for people who gave up. £35 a seat, or £99 with the Evening Wind-Down Kit delivered first."},
  "descriptionHtml":S2,
  "productOptions":[{"name":"Date","position":1,"values":[{"name":"Tue 17 Nov 2026, 19:30 UK (placeholder date)"},{"name":"Tue 1 Dec 2026, 19:30 UK (placeholder date)"}]},{"name":"Ticket","position":2,"values":[{"name":"Seat only"},{"name":"Seat + Evening Wind-Down Kit"}]}],
  "variants":[var2([("Date","Tue 17 Nov 2026, 19:30 UK (placeholder date)"),("Ticket","Seat only")],"35.00","BLL-SVC-WS-1117-SEAT",5,False),
              var2([("Date","Tue 17 Nov 2026, 19:30 UK (placeholder date)"),("Ticket","Seat + Evening Wind-Down Kit")],"99.00","BLL-SVC-WS-1117-KIT",3,True,1.5,"114.00"),
              var2([("Date","Tue 1 Dec 2026, 19:30 UK (placeholder date)"),("Ticket","Seat only")],"35.00","BLL-SVC-WS-1201-SEAT",5,False),
              var2([("Date","Tue 1 Dec 2026, 19:30 UK (placeholder date)"),("Ticket","Seat + Evening Wind-Down Kit")],"99.00","BLL-SVC-WS-1201-KIT",3,True,1.5,"114.00")],
  "files":[{"originalSource":CDN+"founder-video-placeholder-FV-10.png","contentType":"IMAGE","alt":"Founder video placeholder FV-10, Evening Reset Workshop promo: replace with a workshop photo before publishing"}],
  "metafields":[{"namespace":"custom","key":"founder_video_slot","type":"single_line_text_field","value":"FV-10"},{"namespace":"custom","key":"scheduling_link","type":"single_line_text_field","value":"[CALENDLY LINK]"}]},
 {"title":"The 28-Day Routine Reset (4-week group programme, kit included)","handle":"28-day-routine-reset","productType":"Programme","vendor":"Joe Bains","tags":["service","programme","coaching","no-social","no-feed","founder video FV-09"],
  "seo":{"title":"The 28-Day Routine Reset, Group Programme with Joe | Bio Luxe Lab","description":"Four weekly group calls with Joe Bains, a 30-day tracker, check-ins and the Evening Wind-Down Kit delivered before week one. Ten places per cohort. £149."},
  "descriptionHtml":S3,
  "productOptions":[{"name":"Cohort","position":1,"values":[{"name":"Cohort 1, starts Mon 23 Nov 2026 (placeholder date)"},{"name":"Cohort 2, starts Mon 11 Jan 2027 (placeholder date)"}]}],
  "variants":[var("Cohort","Cohort 1, starts Mon 23 Nov 2026 (placeholder date)","149.00","BLL-SVC-PROG-C1",10,True,1.5),
              var("Cohort","Cohort 2, starts Mon 11 Jan 2027 (placeholder date)","149.00","BLL-SVC-PROG-C2",10,True,1.5)],
  "files":[{"originalSource":KITIMG["kit-evening-wind-down-kit.png"],"contentType":"IMAGE","alt":"The Evening Wind-Down Kit, included in the 28-Day Routine Reset programme"}],
  "metafields":[{"namespace":"custom","key":"founder_video_slot","type":"single_line_text_field","value":"FV-09"},{"namespace":"custom","key":"scheduling_link","type":"single_line_text_field","value":"[CALENDLY LINK]"},
                {"namespace":"custom","key":"kit_components","type":"json","value":json.dumps([{"product":"gid://shopify/Product/16475424981337","variant":"gid://shopify/ProductVariant/58782927028569","qty":1},{"product":"gid://shopify/Product/16475424686425","variant":"gid://shopify/ProductVariant/58782925390169","qty":1},{"product":"gid://shopify/Product/16475426292057","variant":"gid://shopify/ProductVariant/58782935122265","qty":1}])}]},
 {"title":"Learn to Juggle Online Workshop (90 minutes)","handle":"learn-to-juggle-online-workshop","productType":"Workshop","vendor":"Joe Bains","tags":["service","workshop","juggling","no-social","no-feed"],
  "seo":{"title":"Learn to Juggle Online Workshop with Joe | Bio Luxe Lab","description":"A 90-minute online juggling workshop for up to eight people: one throw at a time to the three-ball cascade. £25 a seat."},
  "descriptionHtml":S4,
  "productOptions":[{"name":"Date","position":1,"values":[{"name":"Thu 26 Nov 2026, 19:00 UK (placeholder date)"}]}],
  "variants":[var("Date","Thu 26 Nov 2026, 19:00 UK (placeholder date)","25.00","BLL-SVC-JUGGLE-1126",8,False)],
  "files":[],
  "metafields":[]},
]
M='mutation($input: ProductSetInput!){ productSet(input:$input, synchronous:true){ product{ id handle status variants(first:10){nodes{id title sku price inventoryQuantity}} media(first:10){nodes{id}} } userErrors{field message} } }'
created={}
for p in PRODUCTS:
    inp=dict(p); inp["status"]="DRAFT"
    if not inp["files"]: inp.pop("files")
    if not inp["metafields"]: inp.pop("metafields")
    r=shp.gql(M,{"input":inp})
    d=r.get('data',{}).get('productSet') or {}
    if r.get('errors') or d.get('userErrors'): print('ERROR', p['handle'], json.dumps(r.get('errors') or d.get('userErrors'))[:800]); continue
    pr=d['product']; created[p['handle']]=pr; print('OK', p['handle'], pr['id'], [(v['title'],v['price'],v['inventoryQuantity']) for v in pr['variants']['nodes']], 'media', len(pr['media']['nodes']))
json.dump(created,open('created_products.json','w'),indent=1)
