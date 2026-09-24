"""Modular SEO Growth subsystem.

Provider integrations, Shopify audits and recommendation rules intentionally
live in separate modules so each can be tested or replaced independently.
"""

from .orchestrator import run_audit, run_scheduled_scans

__all__ = ["run_audit", "run_scheduled_scans"]
