#!/usr/bin/env python3
"""Publish or roll back the November 2026 hero offers. Nothing runs without an explicit step name.

  python3 publish.py status                 # what is live now
  python3 publish.py theme                  # publish the duplicate theme (live theme stays as a rollback target)
  python3 publish.py products [kits|services|all]   # set products ACTIVE and attach the right sales channels
  python3 publish.py page                   # publish the Meet Joe page
  python3 publish.py rollback-theme         # republish the previous live theme
  python3 publish.py rollback-products      # set the new products back to DRAFT
  python3 publish.py rollback-page          # unpublish the Meet Joe page

Reads client credentials from the engine DB via shp.py (same folder).
"""
import json, os, sys, importlib.util
HERE=os.path.dirname(os.path.abspath(__file__))
spec=importlib.util.spec_from_file_location("shp",os.path.join(HERE,"shp.py")); shp=importlib.util.module_from_spec(spec); spec.loader.exec_module(shp)
DUP_THEME="gid://shopify/OnlineStoreTheme/210264916313"      # "Hero offers 2026-11 (copy of live)"
PREV_LIVE="gid://shopify/OnlineStoreTheme/209606738265"      # live on 2 Nov 2026: "Updated copy of BioLuxeLab Modern Botanical Pre..."
MEET_JOE_PAGE="gid://shopify/Page/725710733657"
C=json.load(open(os.path.join(HERE,"created-products-2026-11-02.json")))
KITS=['evening-wind-down-kit','pilates-at-home-starter-kit','post-run-recovery-kit']
SVCS=['routine-reset-session','evening-reset-workshop','28-day-routine-reset','learn-to-juggle-online-workshop']
PUB={"online":"gid://shopify/Publication/349848666457","shop":"gid://shopify/Publication/349848699225","pinterest":"gid://shopify/Publication/350570414425","google":"gid://shopify/Publication/350570578265","facebook":"gid://shopify/Publication/360256012633","tiktok":"gid://shopify/Publication/380457877849","uk":"gid://shopify/Publication/360215839065","usa":"gid://shopify/Publication/360213840217"}
# Microsoft Channel (gid://shopify/Publication/370407932249) is excluded: the API rejects it as "not publishable" (channel broken since Aug 2026).
KIT_PUBS=list(PUB.values())                      # every working channel + both market catalogs
SVC_PUBS=[PUB['online'],PUB['uk'],PUB['usa']]     # Online Store only (+ market catalogs so the URL resolves); never Google/Pinterest/Facebook/Shop/TikTok
def gql(q,v=None):
    r=shp.gql(q,v); 
    if r.get('errors'): print('GraphQL errors:', json.dumps(r['errors'])[:800]); sys.exit(1)
    return r['data']
def status():
    d=gql('{ themes(first:20){ nodes{ id name role } } }')
    print('Live theme:', [(t['name'],t['id']) for t in d['themes']['nodes'] if t['role']=='MAIN'])
    for h in KITS+SVCS:
        p=gql('query($id:ID!){ product(id:$id){ status unpublishedPublications(first:20){ nodes{ name } } } }',{"id":C[h]['id']})['product']
        print(f"  {h}: {p['status']}; not on: {[n['name'] for n in p['unpublishedPublications']['nodes'] if n['name']]}")
    pg=gql('query($id:ID!){ page(id:$id){ isPublished } }',{"id":MEET_JOE_PAGE})['page']; print('Meet Joe page published:', pg['isPublished'])
def set_status(handles, st):
    for h in handles:
        d=gql('mutation($p:ProductUpdateInput!){ productUpdate(product:$p){ product{ status } userErrors{ message } } }',{"p":{"id":C[h]['id'],"status":st}})
        print(h, d['productUpdate'])
def publish_products(handles):
    for h in handles:
        pubs=KIT_PUBS if h in KITS else SVC_PUBS
        d=gql('mutation($id:ID!,$in:[PublicationInput!]!){ publishablePublish(id:$id, input:$in){ userErrors{ field message } } }',{"id":C[h]['id'],"in":[{"publicationId":p} for p in pubs]})
        print(h, 'published to', len(pubs), 'publications', d['publishablePublish']['userErrors'])
        # services: make sure the sales-channel publications are off even if someone added them in admin
        if h in SVCS:
            d=gql('mutation($id:ID!,$in:[PublicationInput!]!){ publishableUnpublish(id:$id, input:$in){ userErrors{ field message } } }',{"id":C[h]['id'],"in":[{"publicationId":PUB[k]} for k in ('shop','pinterest','google','facebook','tiktok')]})
            print('  ', h, 'unpublished from sales channels', d['publishableUnpublish']['userErrors'])
if __name__=='__main__':
    step=sys.argv[1] if len(sys.argv)>1 else 'status'
    if step=='status': status()
    elif step=='theme': print(gql('mutation($id:ID!){ themePublish(id:$id){ theme{ name role } userErrors{ message } } }',{"id":DUP_THEME}))
    elif step=='rollback-theme': print(gql('mutation($id:ID!){ themePublish(id:$id){ theme{ name role } userErrors{ message } } }',{"id":PREV_LIVE}))
    elif step=='products':
        which=sys.argv[2] if len(sys.argv)>2 else 'all'; hs={'kits':KITS,'services':SVCS,'all':KITS+SVCS}[which]
        set_status(hs,'ACTIVE'); publish_products(hs)
    elif step=='rollback-products': set_status(KITS+SVCS,'DRAFT')
    elif step=='page': print(gql('mutation($id:ID!,$p:PageUpdateInput!){ pageUpdate(id:$id, page:$p){ page{ isPublished } userErrors{ message } } }',{"id":MEET_JOE_PAGE,"p":{"isPublished":True}}))
    elif step=='rollback-page': print(gql('mutation($id:ID!,$p:PageUpdateInput!){ pageUpdate(id:$id, page:$p){ page{ isPublished } userErrors{ message } } }',{"id":MEET_JOE_PAGE,"p":{"isPublished":False}}))
    else: print(__doc__)
