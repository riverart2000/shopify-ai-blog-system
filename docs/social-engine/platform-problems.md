# Platform problems: what happens when a network refuses a post

Social Autopilot responds to a refused post by itself. A person is only asked when sending the post again
cannot fix it.

Everything is set in one file: `ai-blog-generator-python-server/rules/platform_problems.json`. The code has
no values of its own; a change to the file takes effect at the next status check (every 10 minutes), with
no restart.

## The order of things

1. **A rule matches the error message.** The rule's response is carried out at once.
2. **No rule matches.** The AI model reads the error (with the network's recent results) and picks one of
   the same responses. Its answer is remembered for that message for `ai.remember_days`, so the model is
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
| `needs_person` | Left failed, with the cause and the one thing to do. |

Limits on every automatic retry:

- at most `max_auto_retries` per channel version;
- never for a post older than `retry_max_age_days`;
- never when the message contains a `never_retry` phrase (the post may already be live);
- a channel takes at most `extra_posts_per_day` retried posts a day on top of its normal ones, at least
  `retry_spacing_minutes` from its other posts.

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
model is in the AI decision log as `failure_diagnosis`.

## Adding a rule

Add an entry to `rules`: a `key`, a plain `label`, the `providers` it applies to (empty = all), the
phrases to `match` in the error message, and the `response`. `wait_for_reset` needs an `allowance` block;
`retry_later` needs `retry_after_minutes`. A rule is only worth adding when the model's answer for that
message is wrong, or when the limit resets at a known time and its hours should be learned.
