# Why most pages were not indexed, and what was changed (18 November 2026)

Search Console (data of 13 November) listed 673 pages indexed and 2,780 not. This note records what the
2,780 were, what was changed the same day, and how to undo it.

## What the "not indexed" pages were

| Group | Pages | Verdict |
|---|---|---|
| Blocked by robots.txt, noindex, alternative canonical, redirects | about 1,280 | Normal. Cart, login, account, tracking links, variant links and search pages. Nothing to fix. |
| Not found (404) | 462 | 441 already redirected on 5 November; Google had not re-checked them. 8 more were redirected today. The rest are cut-off junk addresses or unpublished products. |
| Crawled or discovered, not indexed | 1,002 | The real problem: blog posts, each counted in English and French. |
| Soft 404, duplicates, server errors | 36 | Mostly empty collections (see "Left for the owner"). |

## The blog

Of 540 published posts, Google had indexed 130 and declined 387 (23 were too new to judge).

- The declined posts were nearly all from one batch: 402 general how-to posts published in May and June,
  up to 41 a day. Google kept 34 of them.
- Many covered the same subject several times; a few had identical titles.
- Declined posts averaged 1.5 pictures and 0.9 product links. Indexed posts averaged 3.5 and 2.3.
- Posts from August to October were indexed 83 times out of 98, so the generator as it is now is not the
  cause. It also already refuses a title too close to a recent one (`services/quality_service.py`).

## Changed

| Change | Detail | Undo |
|---|---|---|
| Repeats merged | 115 declined posts that repeated another post's topic were unpublished and redirected to the post kept for that topic (the indexed one where there was one, otherwise the one with most pictures, links and words). The redirect also covers the `/fr` address. No indexed post was touched. | `.runtime/m5/backups/blog-merge-20261118.json`: republish the article ids and delete the redirect ids. |
| Declined posts improved | The other 295 declined posts got a closing block: up to three products that fit the post (picture and link, chosen by the local model, none where nothing fitted: 262 posts got products) and three related posts. | `.runtime/m5/backups/blog-improve-20261118.json` holds each original body. |
| Oversized posts | 4 of those posts carried a picture pasted in as raw data (about 300 KB of text each). The raw pictures were removed. | Same backup. |
| Dead links | 8 redirects for the remaining real dead links. | `.runtime/m5/backups/redirects-created-20261118.json` |
| Re-check requested | "Validate fix" started in Search Console for "Crawled - currently not indexed" and "Discovered - currently not indexed". | none needed |

Posts were grouped by topic with the title weighted over the body text (similarity 0.50 or more counted
as the same topic). The blocks added to posts are marked `class="bll-more"` so they can be found again.

## What to expect

Google re-checks over two to four weeks. The honest measure is the "Crawled - currently not indexed"
count for blog posts in Search Console; it should fall from the 13 November figure. Nothing here
guarantees a declined post is accepted: the posts are still general how-to articles.

## Left for the owner

- Empty collections that Google reports as "soft 404": hero-kits, coaching-and-workshops, clothing,
  clothing-accessories, shoe-accessories, puzzles, outdoor-play-equipment, office-furniture, books-guides,
  party-celebration. Either add products or hide them from the online store.
- The dead-link re-check from 5 November shows "Failed" (Google checked before the redirects were all
  in place). It can be restarted from its "See details" page.
- The French copies of the changed posts keep their old translation until they are translated again.
