"""Durable jobs: registry (what can run), queue (enqueue), worker (runs them)."""
from __future__ import annotations

import importlib

from .queue import enqueue
from .registry import JobContext, job

# Modules whose import registers job handlers via @job(...). Add new handler
# modules here so every worker process knows about them.
HANDLER_MODULES: tuple[str, ...] = (
    "services.social_engine.pipeline.handlers",
)

_loaded = False


def load_handlers() -> None:
    global _loaded
    if _loaded:
        return
    for module in HANDLER_MODULES:
        importlib.import_module(module)
    _loaded = True


__all__ = ["JobContext", "enqueue", "job", "load_handlers", "HANDLER_MODULES"]
