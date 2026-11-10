# Other brands on the same Postiz (RevenueMind Pro AI, joebains.com)

Postiz Pro (from 2026-11-10): 30 channels, 300 AI images and 30 AI videos a month, shared by every
brand on the account. Bio Luxe Lab uses 12 channels; the other two brands get about 6 each.

Social Autopilot writes posts from the Shopify catalogue only, so it is **Bio Luxe Lab's** engine.
The other brands are posted by Postiz itself (RSS auto-post) and the Task Agent for one-offs.
Decided 2026-11-10 ("option 1 for now"); a multi-brand Social Autopilot is the later option.

## 1. Keep the brands apart in Postiz: customers

In Postiz, each channel can belong to a *customer*. Make three: **Bio Luxe Lab**, **RevenueMind
Pro AI**, **Joe Bains** (channel avatar → Preferences / "Move to customer"). Then:

* Social Autopilot → Settings → Postiz → *Customer id* = Bio Luxe Lab's id (the channel list
  shows each channel's customer after a sync). From then on it ignores the other brands' channels.
  Until that is set, every new channel shows up in Social Autopilot's channel list; it gets no posts
  unless a campaign includes it, but keep the list clean.
* The Postiz calendar's customer selector shows one brand at a time.

## 2. Channels to connect (in Postiz, with each brand's own logins)

| Brand | Channels |
|---|---|
| RevenueMind Pro AI | LinkedIn page, personal LinkedIn (move it from Bio Luxe Lab), X, Medium, Dev.to, YouTube; Bluesky / Threads optional |
| joebains.com | Instagram, TikTok, YouTube, Facebook page, Threads, Bluesky |

Networks need a separate account per brand (Instagram, TikTok, YouTube, X); Postiz holds several
accounts of the same network side by side.

## 3. RevenueMind: RSS auto-post

The site has an RSS feed at `https://revenuemindproai.com/blog/feed.xml` (added 2026-11-10 in the
`~/revenuemindproai` repo: `src/app/revenuemindproai/blog/feed.xml/route.ts`, published blog posts
from the same database as the blog page; the blog page links to it with `rel="alternate"`).

In Postiz: Settings → **Auto Post** → add the feed URL, tick the RevenueMind channels, turn on
"generate picture" if the post should get an AI image (uses the shared 300 images a month; a new blog
post is rare, so this is a few images a month at most), and optionally a post prompt. Each new blog
post then becomes a post on those channels; existing posts are not re-posted.

Writing the blog is what drives it: no new article, no post. For more than one post per article
(a quote, a tip, a question) use Postiz's own "sets"/plugs or a Task Agent template.

## 4. joebains.com: no blog, so no feed

The site has no articles to feed from. Posts for it are made by hand in Postiz (performance clips,
event dates) or by the Task Agent. If a blog or events page is added later, give it a feed the same
way and repeat section 3.

## 5. Credits

Social Autopilot's own budgets (Settings → Postiz): 30 AI videos and 300 AI images a month, the
Pro plan's limits. The RSS "generate picture" draws from the same 300 images, so if the other
brands start using many, lower Social Autopilot's image budget by that amount.
