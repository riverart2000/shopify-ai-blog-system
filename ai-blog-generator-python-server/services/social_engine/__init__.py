"""Social Engine — Outfy-style social automation built on Postiz.

Layers (each can be tested and debugged on its own):

    catalog/     Shopify product sync and change detection
    creatives/   branded images (Pillow) and videos (ffmpeg)
    ai/          LLM router (Gemma first), captions, decisions, review
    campaigns/   auto-pilot rules that plan posts
    scheduling/  posting windows, caps and time slots
    publishers/  Postiz (production) and dry-run publishers
    pipeline/    job handlers: generate → approve → publish → reconcile
    analytics/   metrics collection and roll-ups
    jobs/        durable queue, worker, recurring tasks

See docs/social-engine.md for the full guide.
"""
