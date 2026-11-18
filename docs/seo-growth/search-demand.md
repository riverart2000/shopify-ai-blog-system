# Search demand: using what people really search for

Google Search Console reports the phrases the shop is shown for. Search demand reads them once a week
and does two things with the real ones:

1. **Refreshes a post that nearly ranks.** A blog post shown at positions 3 to 20 for a real phrase gets
   a search title and description written for that phrase, and a short section near the top that answers
   it. The headline and the rest of the post stay as they were.
2. **Feeds the blog title generator.** Real phrases with no page on the first two result pages are added
   to the title generator's prompt as the searches to write for: one phrase per topic, and none for a
   topic that already has a page there under other wording (`new_posts.same_topic_word_overlap`). The
   generator's own check still refuses a title too close to an existing one.

Everything is set in `ai-blog-generator-python-server/rules/search_demand.json`. The code has no values
of its own, and a change to the file is picked up at the next run.

## Which phrases count

Search Console also reports machine-made phrases ("genealogy equipment for light sleepers"). A phrase
counts when:

- it has between `phrases.min_words` and `phrases.max_words` words and none of `phrases.ignore_containing`
  (the shop's own name);
- with `phrases.ask_model_if_real`, the model agrees a person would type it to find the page it is shown
  with. Each verdict is remembered for `phrases.remember_days`.

The model is the one named by the store setting in `model_setting` (the blog title model).

## Refreshing a post

- Only English blog posts; the French copy follows when it is translated again.
- The phrase with the most impressions for the post is the one it is refreshed for, if it has at least
  `refresh.min_impressions`.
- At most `refresh.posts_per_run` posts per run, most searched first.
- A refreshed post is not touched again for `refresh.wait_days_before_touching_again` days, so Google's
  reaction can be seen before anything else changes.
- The model may only use what the post already says. Its text is refused when it is over the length
  limits, contains HTML, or trips the health-claim check; a refused text changes nothing.
- The section is marked `class="bll-answer"`; a later refresh replaces it instead of adding a second one.

## Seeing and undoing

```
cd ai-blog-generator-python-server
python -m services.seo_growth.search_demand                 # the plan, nothing changed
python -m services.seo_growth.search_demand --apply         # run now
python -m services.seo_growth.search_demand --log           # what was refreshed, with ids
python -m services.seo_growth.search_demand --restore <id>  # put one post back as it was
```

Every refresh is kept in the `seo_refreshes` table with the post's earlier body, search title and
description. The scheduler runs the cycle every `run_every_days` days for each store with Search Console
connected (Settings on the SEO Growth page).

## Judging whether it works

Compare the refreshed post's clicks and position for its phrase in Search Console over the six weeks
after the change with the six weeks before. Position 3 to 20 with no clicks can also mean the phrase is
rarely searched by people at all; the refresh cannot fix that.
