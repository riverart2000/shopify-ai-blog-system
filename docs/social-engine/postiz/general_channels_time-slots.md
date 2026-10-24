> ## Documentation Index
> Fetch the complete documentation index at: https://docs.postiz.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Posting time slots

> Set the times a channel normally posts, and what depends on them

**Where:** Calendar, channel `...` menu, **Edit Time Slots**

A time slot is a time of day that a channel normally posts at. Slots are set
per channel, so a client's LinkedIn can run at 09:00 and 13:00 while their
Instagram runs at 18:00.

## Setting them

**1. Open the editor**

Channel menu, **Edit Time Slots**.

**2. Add a slot**

Pick an **Hour** and **Minutes**, then **Add**. The slot joins the
**Scheduled Times** list.

**3. Save**

**Save Changes**. Nothing takes effect until you do.

Remove a slot from the list to delete it. A channel with no slots shows
**No time slots added yet**, which is valid: you just lose the conveniences
below.

## What slots actually control

Slots are a convenience, not a restriction. You can always schedule a post at
a time that is not a slot. What they drive:

1. **The calendar's Day view.** Its rows are your channels' slots, plus any
   time that already has a post. This is what makes Day view a plan for the
   day rather than 24 empty hours.
2. **RSS auto-posting.** When an [RSS feed](https://docs.postiz.com/general/automations/auto-post-rss.md)
   is set to "post on the next available slot", this is the list it picks
   from.
3. **The `find-slot` API endpoint.** Anything building on the
   [Public API](https://docs.postiz.com/public-api/integrations/find-slot.md) can ask Postiz for the next
   free slot rather than hardcoding times.

## Timezones

Slots are in **your browser's timezone**, like everything else in Postiz. There
is no per-account timezone setting.

If you schedule for a client in another timezone, do the conversion yourself
when you set the slots. A slot at 09:00 is 09:00 where you are sitting, not
where the audience is.


This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.