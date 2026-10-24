> ## Documentation Index
> Fetch the complete documentation index at: https://docs.postiz.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Start a Clipping

> Turn a long YouTube video into short vertical clips with burned-in captions. The best parts are picked automatically, every clip is saved to the media library, and when integrations are passed a draft post is created for every clip on every integration. Nothing is scheduled or published.

Clipping takes several minutes, so this only starts it and returns the clipping `id`. Poll [Get a clipping](/public-api/clipping/get) until the status is `completed` or `failed`.

See [Video clipping](https://docs.postiz.com/general/clipping.md) for what the feature does, the clipping minutes it uses and its limits.

> [!NOTE]
>
> Clipping takes several minutes. This endpoint only starts it and returns the clipping `id`: poll [Get a Clipping](https://docs.postiz.com/public-api/clipping/get.md) until the status is `completed` or `failed`.

## Clipping minutes

On Postiz Cloud a clipping spends one clipping minute for every minute of the source video, from the same monthly allowance the agent and MCP use:

* With no minutes left the request is refused with `402` and nothing is charged.
* The minutes are charged once the real length of the video is known. A video longer than the minutes left fails the clipping (the `error` of [Get a Clipping](https://docs.postiz.com/public-api/clipping/get.md) says so) and nothing is charged.
* A clipping that fails without producing a single clip gives the minutes back.

Starting a clipping and polling its status do not count against the hourly [rate limit](https://docs.postiz.com/public-api/introduction.md#rate-limits), which applies to the create post endpoint only. Clipping has its own caps: one running clipping per organization and 20 starts a day, both answered with `429`.


## OpenAPI

````yaml POST /clipping
openapi: 3.1.0
info:
  title: Postiz Public API
  description: >-
    API for managing social media posts, integrations, and media uploads in
    Postiz.


    ## Authentication


    All endpoints require an API key passed in the `Authorization` header:


    ```

    Authorization: your-api-key

    ```


    Get your API key from Postiz Settings.


    ## Rate Limits


    There is a limit of **30 requests per hour**.


    ## Terminology


    The UI uses `channel`, but the API uses `integration`. They refer to the
    same thing.


    ## Supported Platforms (27)


    **Social Platforms:** X (Twitter), LinkedIn, LinkedIn Page, Facebook,
    Instagram, Instagram Standalone, Threads, Bluesky, Mastodon, Warpcast
    (Farcaster), Nostr, VK


    **Video Platforms:** YouTube, TikTok


    **Community Platforms:** Reddit, Lemmy, Discord, Slack, Telegram


    **Design Platforms:** Pinterest, Dribbble


    **Blogging Platforms:** Medium, Dev.to, Hashnode, WordPress


    **Business:** Google My Business (GMB), Listmonk (newsletters)
  license:
    name: MIT
  version: 1.0.0
servers:
  - url: https://api.postiz.com/public/v1
    description: Postiz Cloud
  - url: https://{your-domain}/api/public/v1
    description: Self-hosted
    variables:
      your-domain:
        default: localhost:5000
        description: Your Postiz instance domain
security:
  - ApiKeyAuth: []
tags:
  - name: Integrations
    description: Manage connected social media channels
  - name: Posts
    description: Create, list, and delete posts
  - name: Uploads
    description: Upload media files
  - name: Notifications
    description: View organization notifications
  - name: Analytics
    description: View analytics for integrations and posts
  - name: Video Generation
    description: Generate videos with AI
  - name: Clipping
    description: Turn long YouTube videos into short vertical clips
paths:
  /clipping:
    post:
      tags:
        - Clipping
      summary: Start a clipping
      description: >-
        Turn a long YouTube video into short vertical clips with burned-in
        captions. The best parts are picked automatically, every clip is saved
        to the media library, and when integrations are passed a draft post is
        created for every clip on every integration. Nothing is scheduled or
        published.


        Clipping takes several minutes, so this only starts it and returns the
        clipping `id`. Poll [Get a clipping](/public-api/clipping/get) until the
        status is `completed` or `failed`.
      operationId: startClipping
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ClippingRequest'
            examples:
              library:
                summary: Clips to the media library
                value:
                  url: https://www.youtube.com/watch?v=VIDEO_ID
              drafts:
                summary: 3 cropped clips drafted on two channels
                value:
                  url: https://www.youtube.com/watch?v=VIDEO_ID
                  clips: 3
                  fit: crop
                  integrations:
                    - your-tiktok-integration-id
                    - your-instagram-integration-id
      responses:
        '200':
          description: Clipping started
          content:
            application/json:
              schema:
                type: object
                properties:
                  id:
                    type: string
                    description: ID of the clipping
              example:
                id: 0b6f1c1e-5f0a-4c55-9a53-2f1f6f7f7c11
        '400':
          description: Not a YouTube URL, or an integration was not found
          content:
            application/json:
              example:
                statusCode: 400
                message: Only YouTube videos can be clipped
        '402':
          description: >-
            No clipping minutes are left for this month (or the plan has none).
            Nothing is charged
          content:
            application/json:
              example:
                statusCode: 402
                message: >-
                  You have used all the clipping minutes of your subscription
                  for this month. Please upgrade your subscription to clip more
                  videos.
                url: https://platform.postiz.com/billing
        '406':
          description: The organization is still in trial
          content:
            application/json:
              example:
                statusCode: 406
                message: Clipping is not available in trial mode
        '429':
          description: A clipping is already running, or the daily cap of 20 was reached
          content:
            application/json:
              example:
                statusCode: 429
                message: A clipping is already running, wait for it to finish
        '503':
          description: Clipping is not configured on this instance
          content:
            application/json:
              example:
                statusCode: 503
                message: Clipping is not available
components:
  schemas:
    ClippingRequest:
      type: object
      required:
        - url
      properties:
        url:
          type: string
          format: uri
          description: URL of the YouTube video (`youtube.com` or `youtu.be`)
        integrations:
          type: array
          maxItems: 20
          items:
            type: string
          description: >-
            IDs of the integrations to create a draft post for, one draft per
            clip per integration. Without any, the clips only land in the media
            library
        clips:
          type: integer
          minimum: 1
          maximum: 10
          default: 5
          description: Maximum number of clips
        fit:
          type: string
          enum:
            - blur
            - crop
          default: blur
          description: >-
            How the horizontal video fills the vertical clip. `blur` keeps the
            whole picture over a blurred copy of itself. `crop` fills the clip
            with the middle of the picture and cuts the sides away; there is no
            face tracking
  securitySchemes:
    ApiKeyAuth:
      type: apiKey
      in: header
      name: Authorization
      description: Your Postiz API key

````

This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.