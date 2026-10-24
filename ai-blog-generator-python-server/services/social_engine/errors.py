"""Error taxonomy shared by every Social Engine module.

The worker uses these types to decide what happens next:

* `retryable=True`  → the job is retried with exponential backoff.
* `retryable=False` → the job fails immediately and shows the exact message.
* `RateLimited` / `Deferred` → the job is rescheduled without using an attempt.
* `UncertainOutcome` → a non-idempotent call may or may not have happened;
  the caller must reconcile before trying again (never blindly retried).
"""
from __future__ import annotations

from typing import Any


class SocialEngineError(Exception):
    code = "social_engine_error"
    retryable = False

    def __init__(self, message: str, *, details: dict[str, Any] | None = None, retryable: bool | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if retryable is not None:
            self.retryable = retryable

    def as_dict(self) -> dict[str, Any]:
        return {"type": type(self).__name__, "code": self.code, "message": self.message, "details": self.details}


class ConfigurationError(SocialEngineError):
    """Something must be configured by a person (API key, channel, board...)."""
    code = "configuration"
    retryable = False


class ValidationError(SocialEngineError):
    """Input or generated content failed validation."""
    code = "validation"
    retryable = False


class NotFoundError(SocialEngineError):
    code = "not_found"
    retryable = False


class ExternalServiceError(SocialEngineError):
    """An external API returned an error or could not be reached."""
    code = "external_service"

    def __init__(
        self,
        service: str,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if retryable is None:
            retryable = status_code is None or status_code == 429 or status_code >= 500
        super().__init__(f"{service}: {message}", details=details, retryable=retryable)
        self.service = service
        self.status_code = status_code


class RateLimited(SocialEngineError):
    """A rate budget is exhausted; try again at `retry_at` (epoch seconds)."""
    code = "rate_limited"
    retryable = True

    def __init__(self, message: str, *, retry_at: int, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, details=details)
        self.retry_at = retry_at


class UncertainOutcome(SocialEngineError):
    """A non-idempotent request timed out or failed ambiguously."""
    code = "uncertain_outcome"
    retryable = False


class Deferred(Exception):
    """Control flow: reschedule the current job without consuming an attempt."""

    def __init__(self, reason: str, *, run_at: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.run_at = run_at


def describe(exc: BaseException) -> tuple[str, str]:
    """Return (error_type, message) suitable for storing and showing to a person."""
    if isinstance(exc, SocialEngineError):
        return type(exc).__name__, exc.message
    message = str(exc) or repr(exc)
    return type(exc).__name__, message


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, SocialEngineError):
        return bool(exc.retryable)
    # Unknown failures (SQLite busy, transient I/O) are retried; every job
    # handler is written to be idempotent so a retry is safe.
    return True
