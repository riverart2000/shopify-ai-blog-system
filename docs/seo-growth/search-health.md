# Search health: the daily check on Google and Bing

Once a day the system checks how both search engines see the shop, puts right what it can, and reports
the rest once. Nobody has to open Search Console or Bing Webmaster Tools for it.

Everything is set in `ai-blog-generator-python-server/rules/search_health.json`. The code has no values
of its own.

## What it checks

| Engine | Check | When something is wrong |
|---|---|---|
| Google | Sitemap: errors, or not read for `google.sitemap_stale_days` days | Alert |
| Google | `google.inspect_posts_per_run` blog posts a day through the URL Inspection API, those checked longest ago first | Recorded per post (indexed, declined, unknown) |
| Google | Indexed blog posts against `alerts.compare_with_days_ago` days ago | Alert on a fall of `alerts.indexed_drop_percent` percent |
| Bing | Sitemap status | Submitted again at once; alert only if it still fails at the next check |
| Bing | Pages indexed, pages not found, server errors on the last crawl day | Alert on a fall in indexed pages or more than `bing.server_errors_alert_above` server errors |

An alert is an error on System Health (activity log, stage `search`). Each problem is said once per
`alerts.repeat_after_days` days, and again if it goes away and comes back.

The check never changes or unpublishes a post. What it records is there for decisions: the list of
declined posts, and how long each has been declined.

## Connections

- **Google** uses the Search Console service account already saved on the SEO Growth page.
- **Bing** uses an API key from Bing Webmaster Tools → Settings → API access, saved in the store settings
  named by `bing.api_key_setting` and `bing.site_setting`. Shopify already tells Bing about every changed
  page through IndexNow, so pages are not submitted from here.

## Seeing it

```
cd ai-blog-generator-python-server
python -m services.seo_growth.search_health              # the last seven checks
python -m services.seo_growth.search_health --run        # check now
python -m services.seo_growth.search_health --declined   # posts Google has not indexed, longest first
```

Google allows 2,000 inspections a day for a site; at 150 a day every post is re-checked about twice a
week. Related: [search demand](search-demand.md) (the weekly use of search phrases).
