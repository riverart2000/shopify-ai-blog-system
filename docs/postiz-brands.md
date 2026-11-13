# Other brands on the same Postiz (RevenueMind Pro AI, joebains.com)

Postiz Pro (from 2026-11-10): 30 channels, 300 AI images and 30 AI videos a month, shared by every
brand on the account. Bio Luxe Lab uses 12 channels; the other two brands get about 6 each.

Update 2026-11-10 (evening): Social Autopilot is now multi-brand. RevenueMindProAI is a brand of its own
(`ai-blog-generator-python-server/brands/revenuemind.json`, how-to in `docs/social-engine/brands.md`): one
value post a day on all its channels, written by Postiz AI, each asking for a booked call. The RSS auto-post
below is still available for blog articles.

Update 2026-11-13: Joe Bains is a brand too (`brands/joebains.json`): one post a day, only on the channels
of the **Joe Bains** Postiz group. That group is still empty, so the brand waits; it starts by itself once
channels are connected there. Section 4 below is superseded.

## 1. Keep the brands apart in Postiz: customers

In Postiz, each channel can belong to a *customer* (shown as a group in the channel list). The three
exist since 2026-11-10: **Bio Luxe Lab** (`da02d764-…`, all 11 current channels), **RevenueMind Pro AI**
(`c8e63859-…`; 6 channels connected 2026-11-10: Facebook page, Instagram, Instagram standalone "Joe Bains", LinkedIn page, Pinterest, Tumblr) and **Joe Bains** (`3fc46263-…`, empty). A channel's ⋮ menu → *Move / add to group*
→ type the group name → Save moves it; typing a new name creates a group (there is no other way to
create one, and the public API / CLI / MCP can only list them). A stray empty group " Luxe Lab" exists
from a typo; it has no delete in the API. Then:

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
