import json, copy
from themelib import *
C=json.load(open('created_products.json'))
def btn(label,link,primary=True):
    b=button(label,link,"button-custom"); b['settings'].update({"custom_button_background":"{{ settings.color_palette.color14 }}" if primary else "{{ settings.color_palette.color9 }}","custom_button_text":"{{ settings.color_palette.color9 }}" if primary else "{{ settings.color_palette.color14 }}","custom_button_border":"{{ settings.color_palette.color14 }}","link_text_color":"","width":"fit-content","custom_width":100,"width_mobile":"fit-content","custom_width_mobile":100}); return b
def section(blocks, bg="", center=False, pad=56):
    s={"type":"section","blocks":blocks,"block_order":list(blocks.keys()),"settings":{"content_direction":"column","vertical_on_mobile":True,"horizontal_alignment":"center" if center else "flex-start","vertical_alignment":"center","align_baseline":False,"horizontal_alignment_flex_direction_column":"center" if center else "flex-start","vertical_alignment_flex_direction_column":"center","gap":16,"section_width":"page-width","section_height":"","section_height_custom":50,"background_media":"none","background_color":bg,"video_position":"cover","background_image_position":"cover","toggle_overlay":False,"overlay_color":"#00000026","overlay_style":"solid","gradient_direction":"to top","border":"none","border_width":1,"border_opacity":100,"border_radius":0,"padding-block-start":pad,"padding-block-end":pad}}
    if center:
        for b in blocks.values():
            if b['type']=='text': b['settings']['alignment']='center'
    return s
def slot_section(vid, hd, body, position="left", btns=None, light=False):
    cb={"slot": video_slot_eyebrow(vid), "hd": heading(hd), "body": text_block(body)}
    if btns: cb["btns"]=group_row(btns)
    bg="{{ settings.color_palette.color18 }}" if light else "{{ settings.color_palette.color9 }}"
    s=media_with_content(f"shopify://shop_images/founder-video-placeholder-{vid}.png", cb, position=position, bg=bg)
    if not light:
        for k in ('hd','body','slot'): s['blocks']['content']['blocks'][k]['settings']['text_color']="{{ settings.color_palette.color18 }}"
    return s
def accordion(rows):
    blocks={}
    for i,(q,a) in enumerate(rows):
        blocks[f"row_{i}"]={"type":"_accordion-row","settings":{"heading":q,"icon":"none"},"blocks":{"t":text_block(f"<p>{a}</p>")},"block_order":["t"]}
    return {"type":"accordion","settings":{"icon":"plus","dividers":True,"type_preset":"h6"},"blocks":blocks,"block_order":list(blocks.keys())}
signup={"type":"email-signup","settings":{"width":"custom","custom_width":60,"heading":"","heading_preset":"h3","border_style":"all","input_style":"default","border_width":1,"border_radius":100,"input_type_preset":"paragraph","style_class":"button","display_type":"text","label":"Send me the plan","integrated_button":False,"button_type_preset":"paragraph","padding-block-start":8,"padding-block-end":0,"padding-inline-start":0,"padding-inline-end":0},"blocks":{}}
base=get_json('templates/product.json')
cart=get_json('templates/cart.json')
def product_list(collection, heading_html, btn_label, btn_link, key_prefix):
    s=copy.deepcopy(cart['sections']['product_list_YRK996'])
    s['settings']['collection']=collection
    s['blocks']['static-header']['blocks']['product_list_text_Dkgy6X']['settings']['text']=heading_html
    s['blocks']['static-header']['blocks']['button_RCgx6a']['settings'].update({"label":btn_label,"link":btn_link})
    return s
def recs(heading_html, rtype="complementary"):
    s=copy.deepcopy(base['sections']['product_recommendations_8RVdzU'])
    s['settings']['recommendation_type']=rtype
    s['blocks']['text_w3FTGV']['settings']['text']=heading_html
    return s
reviews_sec=copy.deepcopy(base['sections']['1784803565554c4c13'])
reviews_sec['blocks']['bioluxelab_ai_blog_studio_product_reviews_azeNTr']['settings'].update({"heading":"Reviews, honestly","introduction":"This kit is new, so there are no reviews yet. We only show reviews from people who bought it here, never imported ones. Bought one? Email a line about your first 30 nights to info@bioluxelab.com and we will add it with your first name."})
PROMISE_BODY="<p>Thirty nights from the day it arrives. Use it. If it has not earned a place in your routine, email info@bioluxelab.com with your order number, send it back unused in its packaging, and we refund you. Any reason. Return postage is yours for a change of mind; if anything arrives faulty we cover it and replace it. Your legal rights sit underneath this, untouched.</p>"
DELIVERY_FAQ=[("How long does delivery take?","Free UK delivery on every order. Dispatched within 1–3 working days and usually with you 3–14 days later, tracked. Kit items can arrive as separate parcels on different days."),
 ("When do I get the video, the tracker and the call?","Straight after checkout, in your order confirmation email: the follow-along video link, both PDFs and the scheduling link for your 20-minute Routine Check-in with Joe ([CALENDLY LINK])."),
 ("Is any of this a treatment?","No. These are fitness and relaxation accessories and a routine, not medical devices or advice. If you are injured, pregnant or have a health condition, check with a healthcare professional first and stop anything that hurts."),
 ("What is the 30-Night Promise, exactly?","Thirty nights from delivery to decide. Email info@bioluxelab.com with your order number, send the kit back unused in its packaging, and we refund you for any reason. You pay return postage for a change of mind; we pay if anything is faulty.")]
KITS={
 'kit-evening':{"intro":("FV-02","Twenty minutes, three things, evening sorted.","<p>Joe walks through the kit and the routine it exists for: lights down, three stretches on the mat, the eight-minute roll, draw a card, bed. Then what is in the box, the bonuses, and the promise.</p>"),
   "stack":"<h2>What you get</h2><ul><li>Essential 4 mm Anti-Slip Yoga &amp; Pilates Mat, folds flat: £34.56</li><li>Grooved EPP Foam Roller, 33 × 14 cm: £29.73</li><li>Monochrome Watercolour Meditation Cards, set of 36: £27.23</li><li>Joe's 15-minute Evening Wind-Down follow-along video: Kit only</li><li>The 30-Day Routine Tracker (printable PDF): Kit only</li><li>The 7-Night Wind-Down Plan (PDF): Free guide</li><li>20-minute Routine Check-in call with Joe: Kit only</li></ul><p><strong>Separately £91.52. The kit £79, free UK delivery.</strong></p>",
   "bonus":("FV-03","The routine, on video, in real time","<p>Fifteen minutes of Joe doing the exact wind-down on his own mat: box breathing, three stretches, the eight-minute roll, a card. Press play at lights-down and follow along. Unlisted video, linked in your order email.</p>"),
   "faq":[("Can I choose the mat colour?","Matte black or dusty pink; pick it above. The roller is black and blue."),("I already own a mat.","Buy the roller and cards separately then; the kit price only makes sense with all three. Any first order still gets the tracker pack."),("Is the roller very firm?","Yes, it is a firm grooved roller. Put a folded towel over it or use less body weight until it feels right.")]+DELIVERY_FAQ},
 'kit-pilates':{"intro":("FV-04","No studio, no reformer. A ring, a block, a band and a mat.","<p>Joe lays out the four pieces, shows the ring squeeze and the band row, and explains why a starter kit beats buying a mat and stopping there. Then the bonuses and the promise.</p>"),
   "stack":"<h2>What you get</h2><ul><li>Ergonomic Pilates Resistance Ring, black: £26.93</li><li>Essential EVA Foam Yoga Block, blue: £14.41</li><li>Resistance Band with Handles, 15 lb: £12.37</li><li>Foldable EVA Travel Yoga Mat, 3 mm black: £35.60</li><li>Joe's 15-minute Pilates at-home follow-along session: Kit only</li><li>The 30-Day Routine Tracker (printable PDF): Kit only</li><li>20-minute Routine Check-in call with Joe: Kit only</li></ul><p><strong>Separately £89.31. The kit £74, free UK delivery.</strong></p>",
   "bonus":("FV-05","The session, on video, in real time","<p>Fifteen minutes with Joe on the mat: breathing and bracing, ring squeezes and presses, band rows, supported bridges on the block, dead bug, the hundred, and a stretch. Every move cued. Unlisted video, linked in your order email.</p>"),
   "faq":[("Is 15 lb the right band?","It is the lightest of the four levels and right for seated rows to start. Heavier bands are sold separately."),("Why a 3 mm folding mat?","It folds flat into a bag so it lives by the sofa. If you prefer a thicker mat, the Essential 4 mm mat is sold separately."),("Do I need experience?","No. The follow-along session cues every move.")]+DELIVERY_FAQ},
 'kit-recovery':{"intro":("FV-06","Just back from a run. Ten minutes before you sit down.","<p>Joe, trainers still on, shows the thirty-seconds-an-area pass with the massage gun and the four areas on the roller, then the bonuses and the promise.</p>"),
   "stack":"<h2>What you get</h2><ul><li>Deep Tissue Massage Gun, 6-speed compact, four heads, USB-C: £46.20</li><li>Grooved EPP Foam Roller, 33 × 14 cm: £29.73</li><li>Joe's 10-minute post-run follow-along: Kit only</li><li>The 30-Day Routine Tracker (printable PDF): Kit only</li><li>20-minute Routine Check-in call with Joe: Kit only</li></ul><p><strong>Separately £75.93. The kit £69, free UK delivery.</strong></p>",
   "bonus":("FV-07","The routine, on video, in real time","<p>Ten minutes: gun on the lowest speed, thirty seconds each on calves, hamstrings, quads, glutes and upper back, then the roller, then two slow breaths lying flat. Unlisted video, linked in your order email.</p>"),
   "faq":[("Which massage gun is it?","The compact 6-speed model with four heads and USB-C charging. The full-size 30-speed model is sold separately."),("Is it a medical device?","No. It is a massage accessory for tight, tired muscles after training and does not treat anything."),("Is it loud?","There is a hum; the compact model is the quieter of the two.")]+DELIVERY_FAQ},
}
for suffix,k in KITS.items():
    t={"sections":{},"order":[]}
    t['sections']['product-information']=copy.deepcopy(base['sections']['product-information'])
    vid,hd,body=k['intro']; t['sections']['kit_intro']=slot_section(vid,hd,body,"left",btns={"b":btn("Add the kit to basket","#product-information")})
    t['sections']['kit_stack']=section({"eb":eyebrow("The value stack"),"tbl":text_block(k['stack'])})
    vid,hd,body=k['bonus']; t['sections']['kit_bonus']=slot_section(vid,hd,body,"right",light=True)
    t['sections']['kit_promise']=slot_section("FV-08","The 30-Night Promise",PROMISE_BODY,"left")
    t['sections']['kit_faq']=section({"hd":heading("Questions people ask"),"acc":accordion(k['faq'])},bg="{{ settings.color_palette.color18 }}")
    t['sections']['kit_reviews']=copy.deepcopy(reviews_sec)
    t['sections']['kit_pairs']=recs("<h3>Pairs with this kit</h3>")
    t['sections']['kit_signup']=section({"eb":eyebrow("Not ready for the kit?"),"hd":heading("Try the routine first: the free 7-Night Wind-Down Plan"),"body":text_block("<p>Seven evenings, one small ritual each. The PDF lands in your inbox, then a short note from Joe.</p>"),"form":copy.deepcopy(signup)},bg="{{ settings.color_palette.color18 }}",center=True)
    t['order']=['product-information','kit_intro','kit_stack','kit_bonus','kit_promise','kit_faq','kit_reviews','kit_pairs','kit_signup']
    put(f'templates/product.{suffix}.json',t); print('wrote product.'+suffix)
SVC_FAQ=[("How do I book my time?","Your order confirmation email carries the scheduling link ([CALENDLY LINK]) and the Zoom or Google Meet details."),("Can I move a date?","Yes, once, free of charge, with 24 hours' notice to info@bioluxelab.com. Full refund up to 14 days after purchase if the session has not taken place."),("Is this medical advice?","No. Routines, habits, breathwork, meditation practice, sleep hygiene, training and recovery, measurement and accountability. If something in your health needs a professional, Joe will say so.")]
svc_reviews=copy.deepcopy(reviews_sec)
svc_reviews['blocks']['bioluxelab_ai_blog_studio_product_reviews_azeNTr']['settings'].update({"heading":"Reviews, honestly","introduction":"New on the store, so no reviews here yet. Joe's teaching reviews are on his Apprentus profile (apprentus.com/en-us/in/joebains)."})
for suffix,vid,hd,body in [('service-coaching','FV-09',"Meet Joe, and what coaching is","<p>Who Joe is, what coaching with him is (routines, habits, breathing, sleep hygiene, training and recovery, tracking, accountability) and what it is not. Plain language, small steps.</p>"),
                           ('service-workshop','FV-10',"Ninety minutes, eight people, a routine you'll keep","<p>What happens in the Evening Reset Workshop, the next dates, and the option to have the Evening Wind-Down Kit at your door before it starts.</p>")]:
    t={"sections":{},"order":[]}
    t['sections']['product-information']=copy.deepcopy(base['sections']['product-information'])
    t['sections']['svc_intro']=slot_section(vid,hd,body,"left",btns={"b":btn("Book","#product-information"),"b2":btn("Meet Joe","/pages/meet-joe",False)})
    t['sections']['svc_faq']=section({"hd":heading("Questions people ask"),"acc":accordion(SVC_FAQ)},bg="{{ settings.color_palette.color18 }}")
    t['sections']['svc_reviews']=copy.deepcopy(svc_reviews)
    t['sections']['svc_pairs']=recs("<h3>Goes with</h3>")
    t['order']=['product-information','svc_intro','svc_faq','svc_reviews','svc_pairs']
    put(f'templates/product.{suffix}.json',t); print('wrote product.'+suffix)
t={"sections":{"product-information":copy.deepcopy(base['sections']['product-information']),"svc_faq":section({"hd":heading("Questions people ask"),"acc":accordion(SVC_FAQ)},bg="{{ settings.color_palette.color18 }}"),"svc_pairs":recs("<h3>Goes with</h3>")},"order":["product-information","svc_faq","svc_pairs"]}
put('templates/product.service.json',t); print('wrote product.service')
# All products: "Complete the routine" complementary recommendations before "You may also like"
p=get_json('templates/product.json')
if 'complete_routine' not in p['sections']:
    p['sections']['complete_routine']=recs("<h3>Complete the routine</h3>")
    p['order'].insert(p['order'].index('product_recommendations_8RVdzU'),'complete_routine')
    put('templates/product.json',p); print('product.json: complementary section added', p['order'])
# Cart: featured picks -> hero kits
c=get_json('templates/cart.json')
s=c['sections']['product_list_YRK996']; s['settings']['collection']='hero-kits'
s['blocks']['static-header']['blocks']['product_list_text_Dkgy6X']['settings']['text']='<h3>Complete the routine: the kits</h3>'
s['blocks']['static-header']['blocks']['button_RCgx6a']['settings'].update({"label":"See all kits","link":"shopify://collections/hero-kits"})
put('templates/cart.json',c); print('cart.json updated')
# Home: hero kits list + coaching teaser after founder_welcome
i=get_json('templates/index.json')
if 'hero_kits' not in i['sections']:
    i['sections']['hero_kits']=product_list('hero-kits','<h2>The kits</h2>','All kits','shopify://collections/hero-kits','hk')
    i['sections']['coaching_teaser']=section({"eb":eyebrow("Coaching & workshops"),"hd":heading("Work with Joe directly"),"body":text_block("<p>A one-to-one Routine Reset Session, the online Evening Reset Workshop, and the 28-Day Routine Reset group programme with the kit included. Routines, habits and accountability in plain language.</p>"),"btns":group_row({"b":btn("See coaching & workshops","/collections/coaching-and-workshops"),"b2":btn("Meet Joe","/pages/meet-joe",False)})},bg="{{ settings.color_palette.color18 }}")
    idx=i['order'].index('founder_welcome')+1; i['order'][idx:idx]=['hero_kits','coaching_teaser']
    put('templates/index.json',i); print('index.json updated', i['order'])
