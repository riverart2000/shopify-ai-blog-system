> ## Documentation Index
> Fetch the complete documentation index at: https://docs.postiz.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Webhooks

> Notify another system when Postiz publishes

**Where:** Settings, **Webhooks**

> [!NOTE]
>
> **Availability:** Postiz Cloud, Standard plan and above, with the number of
> webhooks rising by plan. Self-hosted installs are unrestricted.

A webhook is an HTTP callback Postiz fires when a post publishes. Use it to
mirror content into Slack, log posts into a spreadsheet, trigger a Zapier or
n8n workflow, or update your own system.

## Adding one

**1. Name it**

For your own reference in the list.

**2. Give it a URL**

Where Postiz should POST.

**3. Choose the scope**

**All integrations**, or a specific set of channels. Scoping by channel is
how you route a client's posts to that client's system.

**4. Send Test**

Fires a test call immediately, so you can confirm the endpoint is reachable
and see the payload shape before anything real depends on it.

**5. Save**

The header shows how many webhooks you have against your allowance.

## Building the receiver

Always **Send Test** first and capture the body: that is the contract, and it
is more reliable than any description here.

Two habits worth having:

* **Return 2xx quickly**, then do the work asynchronously. A slow endpoint is
  indistinguishable from a broken one.
* **Be idempotent.** Treat a repeated delivery as a no-op rather than posting
  twice.

## When a webhook stops firing

> **The endpoint is not publicly reachable**
>
> Postiz calls from its servers, so localhost and private network addresses
> will not work. Use a tunnel while developing.

> **The endpoint is returning an error**
>
> Check your own logs first, then use **Send Test** to reproduce.

> **The webhook is scoped to the wrong channels**
>
> A webhook scoped to specific integrations stays silent for everything
> else. Re-check the scope.

> **Your plan changed**
>
> Downgrading below the webhook allowance can take webhooks out of service.
> See [Downgrades](https://docs.postiz.com/cloud/downgrades.md).

## Related

Webhooks tell you when Postiz acted. To make Postiz act, use the
[Public API](https://docs.postiz.com/public-api/introduction.md), the [CLI](https://docs.postiz.com/cli/introduction.md), or
[MCP](https://docs.postiz.com/mcp/introduction.md).


This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.