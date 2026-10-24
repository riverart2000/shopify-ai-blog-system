"""Resilient HTTP client shared by every external integration.

* Every attempt is written to `social_api_calls` (redacted), so the Activity
  page can show exactly what was sent to Postiz/Shopify/the LLM and what came back.
* Idempotent requests are retried with exponential backoff + jitter on network
  errors, 429 and 5xx (honouring Retry-After).
* Non-idempotent requests (e.g. "create post") are only retried when the
  connection was never established. An ambiguous failure raises
  `UncertainOutcome` so the caller reconciles instead of double-posting.
* Tests inject fake transports per service through `TRANSPORT_OVERRIDES`.
"""
from __future__ import annotations

import asyncio
import email.utils
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Mapping

import httpx

from db.social_engine import observability

from .errors import ExternalServiceError, RateLimited, UncertainOutcome

logger = logging.getLogger("ai_blog_server.social_engine.http")

TRANSPORT_OVERRIDES: dict[str, httpx.AsyncBaseTransport] = {}
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_NEVER_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


@dataclass(frozen=True)
class CallContext:
    store_id: str = ""
    correlation_id: str = ""


def _retry_after_seconds(response: httpx.Response | None) -> float | None:
    if response is None:
        return None
    value = response.headers.get("retry-after", "").strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        return max(0.0, parsed.timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def _body_excerpt(response: httpx.Response | None, limit: int = 1500) -> str:
    if response is None:
        return ""
    content_type = response.headers.get("content-type", "")
    textual = not content_type or any(t in content_type for t in ("json", "text", "xml", "javascript", "event-stream"))
    if not textual:
        return f"<{len(response.content)} bytes {content_type.split(';')[0]}>"
    try:
        text = response.text
    except Exception:
        return f"<{len(response.content)} bytes>"
    return text[:limit]


def _request_excerpt(json_body: Any, params: Any, files: Any) -> Any:
    if files:
        names = []
        for value in (files.values() if isinstance(files, Mapping) else files):
            item = value[1] if isinstance(value, tuple) else value
            names.append(getattr(item, "name", None) or (value[0] if isinstance(value, tuple) else "file"))
        return {"files": names, "params": params}
    if json_body is not None:
        return json_body
    return {"params": params} if params else ""


class ServiceClient:
    """Thin wrapper around httpx.AsyncClient for one external service."""

    def __init__(
        self,
        service: str,
        base_url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        max_attempts: int = 3,
        backoff_base: float = 1.5,
        backoff_cap: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.service = service
        self.base_url = base_url.rstrip("/")
        self.max_attempts = max(1, max_attempts)
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=dict(headers or {}),
            timeout=timeout,
            transport=transport or TRANSPORT_OVERRIDES.get(service),
            follow_redirects=True,
        )

    async def __aenter__(self) -> "ServiceClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _sleep_for(self, attempt: int, response: httpx.Response | None) -> float:
        retry_after = _retry_after_seconds(response)
        if retry_after is not None:
            return min(retry_after, self.backoff_cap * 4)
        delay = min(self.backoff_cap, self.backoff_base * (2 ** (attempt - 1)))
        return delay * (0.5 + random.random() / 2)

    async def request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        ctx: CallContext | None = None,
        idempotent: bool | None = None,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        files: Any = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        max_attempts: int | None = None,
        ok_statuses: tuple[int, ...] = (),
    ) -> httpx.Response:
        """Send a request and return the successful response or raise.

        `ok_statuses` lets callers treat e.g. 404 as a normal answer.
        """
        ctx = ctx or CallContext()
        method = method.upper()
        if idempotent is None:
            idempotent = method in {"GET", "HEAD", "PUT", "DELETE", "OPTIONS"}
        attempts = max(1, max_attempts or self.max_attempts)
        request_excerpt = _request_excerpt(json, params, files)
        last_response: httpx.Response | None = None

        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            response: httpx.Response | None = None
            error: BaseException | None = None
            try:
                kwargs: dict[str, Any] = {"params": params, "headers": headers}
                if json is not None:
                    kwargs["json"] = json
                if data is not None:
                    kwargs["data"] = data
                if files is not None:
                    kwargs["files"] = files
                if timeout is not None:
                    kwargs["timeout"] = timeout
                response = await self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                error = exc
            duration_ms = int((time.monotonic() - started) * 1000)
            success = response is not None and (response.is_success or response.status_code in ok_statuses)
            await self._record(
                operation=operation, method=method, path=path, ctx=ctx, attempt=attempt,
                response=response, error=error, duration_ms=duration_ms, ok=success,
                request_excerpt=request_excerpt,
            )
            if success:
                return response  # type: ignore[return-value]
            last_response = response

            # Decide whether another attempt is safe and useful.
            final_attempt = attempt >= attempts
            if error is not None:
                never_sent = isinstance(error, _NEVER_SENT)
                if not idempotent and not never_sent:
                    raise UncertainOutcome(
                        f"{self.service} {operation}: request may or may not have been applied "
                        f"({type(error).__name__}: {error}). Reconcile before retrying.",
                        details={"service": self.service, "operation": operation},
                    ) from error
                if final_attempt:
                    raise ExternalServiceError(
                        self.service, f"{operation} could not reach the service: {type(error).__name__}: {error}",
                        status_code=None,
                    ) from error
            else:
                assert response is not None
                status = response.status_code
                retryable_status = status in RETRY_STATUSES
                if status == 429 and (final_attempt or not idempotent):
                    wait = _retry_after_seconds(response) or 900
                    raise RateLimited(
                        f"{self.service} {operation} was rate limited (HTTP 429).",
                        retry_at=int(time.time() + wait),
                        details={"body": _body_excerpt(response, 500)},
                    )
                if status >= 500 and not idempotent:
                    raise UncertainOutcome(
                        f"{self.service} {operation} returned HTTP {status}; the request may have been "
                        f"applied. Reconcile before retrying. Body: {_body_excerpt(response, 400)}",
                        details={"status_code": status},
                    )
                if not retryable_status or final_attempt:
                    raise ExternalServiceError(
                        self.service,
                        f"{operation} failed with HTTP {status}: {_body_excerpt(response, 800)}",
                        status_code=status,
                    )
            await asyncio.sleep(self._sleep_for(attempt, last_response))

        raise ExternalServiceError(self.service, f"{operation} failed after {attempts} attempts")  # pragma: no cover

    async def json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self.request(method, path, **kwargs)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ExternalServiceError(
                self.service,
                f"{kwargs.get('operation', path)} returned non-JSON content: {_body_excerpt(response, 300)}",
                status_code=response.status_code, retryable=False,
            ) from exc

    async def _record(
        self,
        *,
        operation: str,
        method: str,
        path: str,
        ctx: CallContext,
        attempt: int,
        response: httpx.Response | None,
        error: BaseException | None,
        duration_ms: int,
        ok: bool,
        request_excerpt: Any,
    ) -> None:
        message = ""
        if error is not None:
            message = f"{type(error).__name__}: {error}"
        elif response is not None and not ok:
            message = f"HTTP {response.status_code}"
        try:
            await observability.record_api_call(
                service=self.service, operation=operation, method=method, url_path=path,
                status_code=response.status_code if response is not None else None,
                ok=ok, duration_ms=duration_ms, attempt=attempt, error=message,
                store_id=ctx.store_id, correlation_id=ctx.correlation_id,
                request_excerpt=request_excerpt, response_excerpt=_body_excerpt(response),
            )
        except Exception:  # the ledger must never break the real call
            logger.debug("Could not record API call", exc_info=True)
