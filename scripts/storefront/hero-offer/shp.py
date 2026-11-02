import sqlite3, sys, json, time, urllib.request
DB="/Users/joebains/shopify-ai-blog-system/ai-blog-generator-python-server/data/ai_blog_server.db"
c=sqlite3.connect(DB)
dom,cid,cs=c.execute("select myshopify_domain,client_id,client_secret from stores where id='store_1'").fetchone()
row=c.execute("select token,expires_at from access_tokens where store_id='store_1'").fetchone()
tok=None
if row and row[1]>time.time()+600: tok=row[0]
if not tok:
    import urllib.parse
    d=urllib.parse.urlencode({"grant_type":"client_credentials","client_id":cid,"client_secret":cs}).encode()
    r=urllib.request.urlopen(urllib.request.Request(f"https://{dom}/admin/oauth/access_token",data=d))
    tok=json.load(r)["access_token"]
def gql(q,v=None):
    req=urllib.request.Request(f"https://{dom}/admin/api/2025-10/graphql.json",data=json.dumps({"query":q,"variables":v or {}}).encode(),headers={"X-Shopify-Access-Token":tok,"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(req))
if __name__=="__main__":
    q=sys.stdin.read()
    print(json.dumps(gql(q),indent=1))
