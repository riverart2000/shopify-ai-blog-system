"""Social Engine HTTP API (mounted under /api/social-engine)."""
from __future__ import annotations

from .automation import router as automation_router
from .common import social_engine_error_handler
from .content import router as content_router
from .founder import router as founder_router
from .insights import router as insights_router
from .listings import router as listings_router
from .overview import router as overview_router
from .promotions import router as promotions_router
from .webhooks import internal_router, public_router

ROUTERS = (overview_router, content_router, founder_router, listings_router, automation_router, promotions_router,
           insights_router, internal_router, public_router)

__all__ = ["ROUTERS", "social_engine_error_handler"]
