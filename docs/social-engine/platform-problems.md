# Platform problems: what happens when a network refuses a post

Social Autopilot responds to a refused post by itself: it sends the post again at a better time, or
changes the text that was refused and then sends it again. A person is only asked when neither can fix it.

Everything is set in one file: `ai-blog-generator-python-server/rules/platform_problems.json`. The code has
no values of its own; a change to the file takes effect at the next status check (every 10 minutes), with
no restart.

## The order of things

1. **A rule matches the error message.** The rule's response is carried out at once.
2. **No rule matches.** The AI model reads the error (with the refused post and the network's recent
   results) and picks one of the same responses. Its answer is remembered for that message for `ai.remember_days`, so the model is
   asked once per kind of problem, not once per post. If the local model can't answer, the fallback model
   on the Models page is asked (Settings → AI → fallback model).
3. **Nothing can be done automatically.** The post stays failed and the activity log says, once, what the
   cause is and what to do.

## Responses

| Response | What happens |
| --- | --- |
| `wait_for_reset` | A daily allowance ran out. The engine learns from it (below) and sends the post again after the reset. |
| `retry_later` | Sent again after `retry_after_minutes` (rule) or the wait the model chose, kept between `ai.min_retry_minutes` and `ai.max_retry_minutes`. |
| `retry_next_day` | Sent again the next day at the start of posting hours. |
| `fix_post` | The post's own text was refused. It is changed (below) and sent again `fix.resend_after_minutes` later. |
| `needs_person` | Left failed, with the cause and the one thing to do. |

Limits on every automatic retry:

- at most `max_auto_retries` per channel version, or the rule's own `max_retries` (a refusal with no
  reason given is tried once more only);
- never for a post older than `retry_max_age_days`;
- never when the message contains a `never_retry` phrase (the post may already be live);
- a channel takes at most `extra_posts_per_day` retried posts a day on top of its normal ones, at least
  `retry_spacing_minutes` from its other posts.

## Fixing the post itself

Only text is changed, and only on the channel version that was refused.

- **HTML tags left in the text** (no model involved): a caption writer sometimes answers in HTML
  (`<p>…</p>`). X refuses such a post without giving a reason. The tags are taken out and the post is sent
  again, whatever the error message says (`fix.strip_markup`). Posts already waiting in Postiz for the
  networks in `fix.clean_scheduled_markup_for` are cleaned before they fail. New captions are now stored
  as plain text, so this only matters for posts written earlier.
- **Too long** (rule `text_too_long`, no model involved): the wording before the link is cut at a sentence
  or word end. The link and signature stay; hashtags stay when there is room. When our own count said the
  text fitted and the network still refused it, the text is cut to `fix.shorten_step_percent` under what
  was sent.
- **Anything else in the text** (the model chose `fix_post`: too many hashtags, a title that is too long, a
  word or link the network refuses): the model rewrites the caption and title from its own diagnosis. The
  rewrite only goes out when it fits the network's limits, still has every link unchanged, differs from
  what was refused, and passes the health-claim and banned-term checks. Otherwise the post is left for a
  person, with the reason. `fix.ai_rewrite: false` switches this off.
- At most `fix.max_auto_fixes` changes per channel version. The text before and after each change is kept
  in the activity entry.
- Pictures and videos are never changed. A refused picture or video is left for a person.

**One wrong version refuses the whole request.** Postiz names the network whose version it refused. That
version is fixed; the other channels in the same request are sent again exactly as they were.

## Allowances (the YouTube case)

Postiz cloud uploads to YouTube through one Google project shared by all its customers. Google gives that
project a fixed number of uploads a day, reset at midnight Pacific time (08:00 UK in winter). When it is
gone, Postiz answers "YouTube's daily upload quota is exhausted for today". Nobody outside Postiz can ask
how much is left, so the engine learns the safe hours instead:

- YouTube posts are only planned between `earliest_minutes_after_reset` and a learned latest time
  (it starts at `latest_minutes_after_reset`). Other networks keep their own times; YouTube joins them
  when their time is inside its hours and otherwise gets its own time the same day.
- Each time the allowance runs out, the latest time moves to `safety_margin_minutes` before that moment
  (never earlier than `floor_minutes_after_reset`), and the rest of that day is closed for YouTube.
- After `relax_after_clean_days` days without it running out, the latest time moves
  `relax_step_minutes` later, up to `ceiling_minutes_after_reset`.
- Every 10 minutes, posts already waiting in Postiz at a time outside the safe hours are moved: earlier
  the same day when there is room, otherwise to the next opening. Posts due within
  `leave_alone_minutes_before_posting` are not touched.
- Just before a post is sent to Postiz the same check runs again ("is there room at that time?").

A self-hosted Postiz has its own Google project, so each Postiz server learns its own hours.

## Seeing what it has learned

```
cd ai-blog-generator-python-server
python -m services.social_engine.cli problems                      # rules and learned hours
python -m services.social_engine.cli problems --protect            # also move risky scheduled posts now
python -m services.social_engine.cli problems --backlog-days 7 --provider youtube   # handle recent failures now
```

Every decision is in the post's activity timeline under the stage `problem`, and every answer from the
model is in the AI decision log as `failure_diagnosis` (what to do) or `post_repair` (a rewritten text).

## Adding a rule

Add an entry to `rules`: a `key`, a plain `label`, the `providers` it applies to (empty = all), the
phrases to `match` in the error message, and the `response`. `wait_for_reset` needs an `allowance` block;
`retry_later` needs `retry_after_minutes`; `fix_post` needs `fix` (`shorten` is the only kind a rule can
ask for). `max_retries` gives one problem its own retry cap. The first matching rule wins. A rule is only worth adding when the model's answer for that
message is wrong, or when the limit resets at a known time and its hours should be learned.
