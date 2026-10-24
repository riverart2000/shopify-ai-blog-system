"""LLM providers and the router used by every AI feature.

* `OpenAICompatibleProvider` talks to the local Gemma server (oMLX) — or any
  OpenAI-compatible endpoint — through `ServiceClient("llm", …)`, so every
  attempt lands in the API-call ledger.
* `RegistryModelProvider` wraps a model from the existing Models page and is
  only ever used as the configured fallback (it may be a paid model, so it is
  called once, never retried).
* `LLMRouter.structured` returns validated pydantic objects: JSON extraction,
  one repair round on the same model, then the fallback; every call is written
  to the AI decision log.
"""
from __future__ import annotations

import asyncio
import base64
import inspect
import io
import logging
import mimetypes
import time
import weakref
from contextlib import nullcontext
from dataclasses import dataclass, field, is_dataclass, asdict
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

import db
from db.social_engine import observability

from ..context import StoreContext
from ..errors import ConfigurationError, ExternalServiceError, SocialEngineError, ValidationError, describe
from ..http import CallContext, ServiceClient
from ..settings import AISettings
from .base import LLMProvider, LLMRequest, LLMResponse
from .text import extract_json, strip_reasoning

logger = logging.getLogger("ai_blog_server.social_engine.ai")

T = TypeVar("T", bound=BaseModel)

_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})
_LOCAL_SLOTS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Semaphore]]" = weakref.WeakKeyDictionary()
_IMAGE_MAX_SIDE = 1024
_JSON_ONLY_SUFFIX = "\n\nReturn only one valid JSON object — no commentary, no code fences."


def _local_slot(base_url: str) -> asyncio.Semaphore | None:
    """The local oMLX server runs one request at a time: queue here, not in its socket.

    Waiting for the slot does not count towards the HTTP timeout, so a long
    caption job never makes a short decision call time out.
    """
    host = (urlsplit(base_url).hostname or "").lower()
    if host not in _LOCAL_HOSTS:
        return None
    loop = asyncio.get_running_loop()
    slots = _LOCAL_SLOTS.setdefault(loop, {})
    return slots.setdefault(base_url.rstrip("/"), asyncio.Semaphore(1))


def image_data_uri(image: str, *, max_side: int = _IMAGE_MAX_SIDE) -> str:
    """Return a data: URI for a local image (downscaled JPEG); URLs pass through."""
    value = str(image or "").strip()
    if value.startswith(("data:", "http://", "https://")):
        return value
    path = Path(value)
    if not path.is_file():
        raise ValidationError(f"Image file not found for the vision model: {value}")
    try:
        from PIL import Image

        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((max_side, max_side))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _message_text(body: Any, operation: str) -> tuple[str, str]:
    try:
        choice = body["choices"][0]
        content = choice["message"].get("content")
        finish = str(choice.get("finish_reason") or "")
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ExternalServiceError(
            "llm", f"{operation} returned an unexpected response shape: {str(body)[:300]}", retryable=False,
        ) from exc
    if isinstance(content, list):
        content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return strip_reasoning(str(content or "")), finish


class OpenAICompatibleProvider:
    """Chat-completions client for oMLX/Gemma (or any OpenAI-compatible server)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 240.0,
        name: str = "gemma",
        *,
        max_attempts: int = 2,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.api_key = api_key or ""
        self.timeout = float(timeout)
        self.name = name
        self.max_attempts = max(1, max_attempts)

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def build_payload(self, request: LLMRequest) -> dict[str, Any]:
        user_content: Any = request.prompt
        if request.images:
            user_content = [{"type": "text", "text": request.prompt}] + [
                {"type": "image_url", "image_url": {"url": image_data_uri(image)}} for image in request.images
            ]
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": user_content})
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": False}
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens
        if request.json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def complete(self, request: LLMRequest, ctx: CallContext, *, operation: str = "llm.complete") -> LLMResponse:
        if not self.base_url or not self.model:
            raise ConfigurationError("The AI base URL and model must be set in Social Engine → AI settings.")
        payload = self.build_payload(request)
        slot = _local_slot(self.base_url)
        async with slot or nullcontext():
            started = time.monotonic()
            async with ServiceClient(
                "llm", self.base_url, headers=self.headers(), timeout=self.timeout,
                max_attempts=self.max_attempts, backoff_base=1.0, backoff_cap=5.0,
            ) as client:
                body = await client.json(
                    "POST", "/chat/completions", operation=operation, ctx=ctx, idempotent=True, json=payload,
                )
            duration_ms = int((time.monotonic() - started) * 1000)
        text, finish = _message_text(body, operation)
        if not text:
            raise ExternalServiceError(
                "llm", f"{operation}: model '{self.model}' returned an empty reply (finish_reason={finish or 'unknown'})",
                retryable=True,
            )
        usage = dict(body.get("usage") or {}) if isinstance(body, dict) else {}
        usage["finish_reason"] = finish
        return LLMResponse(
            text=text, model=str(body.get("model") or self.model), provider=self.name,
            duration_ms=duration_ms, usage=usage,
        )


class RegistryModelProvider:
    """A text model from the existing Models page, used as the fallback.

    Called through `providers.get_text_provider(...).generate_raw` with a
    single attempt (it may be a paid model); the call is written to the ledger.
    """

    def __init__(self, store_id: str, model_id: str) -> None:
        self.store_id = store_id
        self.model_id = model_id
        self.name = "registry"
        self.model = model_id
        self.label = model_id

    async def _record(self) -> Any:
        from providers import ModelRecord

        row = await db.get_model(self.model_id)
        if not row or str(row.get("store_id") or "") != self.store_id:
            raise ConfigurationError(
                f"Fallback model '{self.model_id}' does not exist for store '{self.store_id}'. "
                "Choose another fallback in Social Engine → AI settings."
            )
        if row.get("model_type") != "text":
            raise ConfigurationError(f"Fallback model '{row.get('name')}' is not a text model.")
        record = ModelRecord.from_dict(row)
        self.model = record.model_name or record.name
        self.label = record.name
        return record

    async def complete(self, request: LLMRequest, ctx: CallContext, *, operation: str = "llm.complete") -> LLMResponse:
        from providers import ProviderError, get_text_provider

        record = await self._record()
        try:
            provider = get_text_provider(record)
        except ValueError as exc:
            raise ConfigurationError(f"Fallback model '{record.name}': {exc}") from exc
        prompt = request.prompt + (_JSON_ONLY_SUFFIX if request.json_mode else "")
        started = time.monotonic()
        text = ""
        error = ""
        failure: SocialEngineError | None = None
        try:
            text = strip_reasoning(await provider.generate_raw(prompt, request.system))
            if not text:
                failure = ExternalServiceError("llm", f"{operation}: fallback model '{record.name}' returned an empty reply")
        except ProviderError as exc:
            failure = ExternalServiceError(
                "llm", f"{operation}: fallback model '{record.name}' failed: {exc}", retryable=exc.retryable,
            )
        except NotImplementedError as exc:
            failure = ConfigurationError(
                f"Fallback model '{record.name}' ({record.provider}) cannot be used for raw completions: {exc}"
            )
        except Exception as exc:  # network errors inside third-party providers
            failure = ExternalServiceError(
                "llm", f"{operation}: fallback model '{record.name}' failed: {type(exc).__name__}: {exc}",
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        if failure is not None:
            error = failure.message
        try:
            await observability.record_api_call(
                service="llm", operation=operation, method="POST",
                url_path=f"registry/{record.provider}/{record.model_name or record.name}",
                status_code=200 if failure is None else None, ok=failure is None, duration_ms=duration_ms,
                attempt=1, error=error, store_id=ctx.store_id or self.store_id, correlation_id=ctx.correlation_id,
                request_excerpt={"model": record.name, "system": request.system[:400], "prompt": prompt[:1500]},
                response_excerpt=text[:1500],
            )
        except Exception:
            logger.debug("Could not record fallback LLM call", exc_info=True)
        if failure is not None:
            raise failure
        return LLMResponse(
            text=text, model=self.model, provider=self.name, duration_ms=duration_ms,
            usage={"registry_model": record.name},
        )


@dataclass
class StructuredMeta:
    model: str
    provider: str
    used_fallback: bool
    duration_ms: int
    repaired: bool = False
    attempts: list[dict] = field(default_factory=list)
    raw_text: str = ""
    result: Any = None              # value returned by the `postprocess` hook
    decision_id: int = 0
    usage: dict = field(default_factory=dict)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def describe_invalid(exc: BaseException) -> str:
    """Short, model-readable description of why an output was rejected."""
    if isinstance(exc, PydanticValidationError):
        parts = []
        for error in exc.errors()[:8]:
            location = ".".join(str(item) for item in error.get("loc", ())) or "(root)"
            parts.append(f"{location}: {error.get('msg')}")
        return "; ".join(parts)
    return str(exc) or type(exc).__name__


def _short_error(exc: BaseException) -> str:
    message = describe(exc)[1]
    return message[5:] if message.startswith("llm: ") else message


async def _invoke(provider: LLMProvider, request: LLMRequest, ctx: CallContext, operation: str) -> LLMResponse:
    complete = provider.complete
    try:
        accepts_operation = "operation" in inspect.signature(complete).parameters
    except (TypeError, ValueError):
        accepts_operation = False
    if accepts_operation:
        return await complete(request, ctx, operation=operation)  # type: ignore[call-arg]
    return await complete(request, ctx)


class LLMRouter:
    """Gemma first, then the configured fallback. See module docstring."""

    def __init__(
        self,
        primary: LLMProvider,
        fallback: LLMProvider | None = None,
        *,
        store_id: str = "",
        settings: AISettings | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.store_id = store_id
        self.settings = settings or AISettings()

    def _prepared(self, request: LLMRequest) -> LLMRequest:
        updates: dict[str, Any] = {}
        if request.temperature is None:
            updates["temperature"] = self.settings.temperature
        if not request.max_tokens:
            updates["max_tokens"] = self.settings.max_tokens
        return request.model_copy(update=updates) if updates else request

    def _providers(self) -> list[tuple[LLMProvider, bool]]:
        chain: list[tuple[LLMProvider, bool]] = [(self.primary, False)]
        if self.fallback is not None:
            chain.append((self.fallback, True))
        return chain

    async def complete_with_meta(self, task: str, request: LLMRequest, *, ctx: CallContext) -> tuple[LLMResponse, bool]:
        """Like `complete`, also returning whether the fallback answered."""
        request = self._prepared(request)
        failures: list[tuple[LLMProvider, BaseException]] = []
        for provider, is_fallback in self._providers():
            try:
                return await _invoke(provider, request, ctx, f"llm.{task}"), is_fallback
            except Exception as exc:
                logger.warning(
                    "LLM %s failed on %s (%s): %s", task, provider.name, provider.model, _short_error(exc),
                    extra={"operation": f"social_engine.llm.{task}", "store_id": ctx.store_id or self.store_id,
                           "correlation_id": ctx.correlation_id},
                )
                failures.append((provider, exc))
        raise self._unavailable(task, failures)

    async def complete(self, task: str, request: LLMRequest, *, ctx: CallContext) -> LLMResponse:
        response, _ = await self.complete_with_meta(task, request, ctx=ctx)
        return response

    def _unavailable(self, task: str, failures: list[tuple[LLMProvider, BaseException]]) -> ExternalServiceError:
        parts = [f"{provider.name} ({provider.model}): {_short_error(exc)}" for provider, exc in failures]
        if self.fallback is None:
            parts.append("no fallback model is configured")
        primary_exc = failures[0][1] if failures else None
        retryable = any(getattr(exc, "retryable", True) for _, exc in failures)
        return ExternalServiceError(
            "llm", f"{task} failed — " + "; ".join(parts), retryable=retryable,
            details={"task": task, "primary_error": _short_error(primary_exc) if primary_exc else ""},
        )

    async def structured(
        self,
        task: str,
        request: LLMRequest,
        schema: type[T],
        *,
        ctx: CallContext,
        decision_type: str = "",
        subject_id: str = "",
        postprocess: Callable[[T], Any] | None = None,
    ) -> tuple[T, StructuredMeta]:
        """Return a validated `schema` instance and call metadata.

        `postprocess(instance)` may raise ValueError to reject an output that is
        valid JSON but unusable (e.g. every product id invented); that triggers
        the repair round just like a schema error. Its return value is stored
        in `meta.result` and in the decision log.

        Raises ExternalServiceError (retryable) when the primary model could not
        be reached, ValidationError when no provider produced valid output.
        """
        request = self._prepared(request.model_copy(update={"json_mode": True}))
        started = time.monotonic()
        attempts: list[dict] = []
        primary_error: BaseException | None = None
        fallback_error: BaseException | None = None
        last_problem = ""
        last_text = ""
        for provider, is_fallback in self._providers():
            current = request
            for round_number in (1, 2):
                operation = f"llm.{task}" if round_number == 1 else f"llm.{task}.repair"
                try:
                    response = await _invoke(provider, current, ctx, operation)
                except Exception as exc:
                    attempts.append({
                        "provider": provider.name, "model": provider.model, "round": round_number,
                        "outcome": "unreachable", "error": _short_error(exc),
                    })
                    if is_fallback:
                        fallback_error = exc
                    else:
                        primary_error = exc
                    break
                last_text = response.text
                try:
                    instance = schema.model_validate(extract_json(response.text))
                    result = postprocess(instance) if postprocess is not None else None
                except (ValueError, TypeError) as exc:
                    last_problem = describe_invalid(exc)
                    truncated = response.usage.get("finish_reason") == "length"
                    attempts.append({
                        "provider": provider.name, "model": response.model, "round": round_number,
                        "outcome": "invalid", "error": last_problem, "duration_ms": response.duration_ms,
                        "truncated": truncated,
                    })
                    if round_number == 1:
                        current = self._repair_request(request, response.text, last_problem, truncated)
                        continue
                    break
                attempts.append({
                    "provider": provider.name, "model": response.model, "round": round_number,
                    "outcome": "valid", "duration_ms": response.duration_ms,
                })
                meta = StructuredMeta(
                    model=response.model, provider=provider.name, used_fallback=is_fallback,
                    duration_ms=int((time.monotonic() - started) * 1000), repaired=round_number == 2,
                    attempts=attempts, raw_text=response.text, result=result, usage=response.usage,
                )
                meta.decision_id = await self._log(
                    task, decision_type, subject_id, request, ctx, meta,
                    output={"output": _jsonable(instance), "result": _jsonable(result), "attempts": attempts},
                    valid=True,
                )
                return instance, meta

        duration_ms = int((time.monotonic() - started) * 1000)
        summary = "; ".join(
            f"{item['provider']} round {item['round']}: {item['outcome']}"
            + (f" ({item['error']})" if item.get("error") else "")
            for item in attempts
        )
        failed_meta = StructuredMeta(
            model=self.primary.model, provider=self.primary.name, used_fallback=self.fallback is not None,
            duration_ms=duration_ms, attempts=attempts, raw_text=last_text,
        )
        await self._log(
            task, decision_type, subject_id, request, ctx, failed_meta,
            output={"attempts": attempts, "raw": last_text[:3000]}, valid=False, error=summary,
        )
        logger.warning(
            "LLM %s produced no valid output: %s", task, summary[:600],
            extra={"operation": f"social_engine.llm.{task}", "store_id": ctx.store_id or self.store_id,
                   "correlation_id": ctx.correlation_id or subject_id},
        )
        if primary_error is not None:
            failures: list[tuple[LLMProvider, BaseException]] = [(self.primary, primary_error)]
            if self.fallback is not None:
                fallback_problem = fallback_error or ValueError(f"invalid output: {last_problem}")
                failures.append((self.fallback, fallback_problem))
            error = self._unavailable(task, failures)
            if fallback_error is None and self.fallback is not None:
                error.retryable = bool(getattr(primary_error, "retryable", True))
            raise error
        raise ValidationError(f"{task}: no valid output after a repair round — {summary}", details={"attempts": attempts})

    @staticmethod
    def _repair_request(request: LLMRequest, previous: str, problem: str, truncated: bool) -> LLMRequest:
        note = (
            "Your reply was cut off before the end. Keep every field shorter so the whole JSON fits.\n"
            if truncated else ""
        )
        prompt = (
            f"{request.prompt}\n\n---\nYour previous reply could not be used: {problem}\n{note}"
            f"Previous reply:\n{previous[:3000]}\n\n"
            "Reply again with ONLY the corrected JSON object in the required format."
        )
        updates: dict[str, Any] = {"prompt": prompt, "temperature": min(request.temperature or 0.3, 0.3)}
        if truncated and request.max_tokens:
            updates["max_tokens"] = min(16384, int(request.max_tokens * 1.6))
        return request.model_copy(update=updates)

    async def _log(
        self,
        task: str,
        decision_type: str,
        subject_id: str,
        request: LLMRequest,
        ctx: CallContext,
        meta: StructuredMeta,
        *,
        output: Any,
        valid: bool,
        error: str = "",
    ) -> int:
        try:
            return await observability.record_decision(
                store_id=ctx.store_id or self.store_id,
                decision_type=decision_type or task,
                subject_id=subject_id or ctx.correlation_id,
                model=meta.model,
                valid=valid,
                used_fallback=meta.used_fallback,
                duration_ms=meta.duration_ms,
                input_data={
                    "task": task, "temperature": request.temperature, "max_tokens": request.max_tokens,
                    "system_chars": len(request.system), "images": len(request.images),
                    "prompt": request.prompt[:6000],
                },
                output_data=output,
                error=error,
            )
        except Exception:
            logger.debug("Could not record AI decision", exc_info=True)
            return 0


def primary_provider(settings: AISettings) -> OpenAICompatibleProvider:
    name = "gemma" if "gemma" in settings.model.lower() else "local"
    return OpenAICompatibleProvider(
        settings.base_url, settings.model, api_key=settings.api_key(), timeout=settings.timeout_seconds, name=name,
    )


def get_router(sctx: StoreContext) -> LLMRouter:
    ai = sctx.settings.ai
    fallback = RegistryModelProvider(sctx.store_id, ai.fallback_model_id.strip()) if ai.fallback_model_id.strip() else None
    return LLMRouter(primary_provider(ai), fallback, store_id=sctx.store_id, settings=ai)


async def check_llm(settings: AISettings) -> dict:
    """Doctor check: is the server up, is the model served, does JSON mode work?"""
    started = time.monotonic()
    result: dict[str, Any] = {
        "ok": False, "status": "unreachable", "base_url": settings.base_url, "model": settings.model,
        "model_listed": False, "vision_model": settings.vision_model, "vision_model_listed": False,
        "fallback_model_id": settings.fallback_model_id, "api_key_configured": bool(settings.api_key()),
        "available_models": [], "models_ms": None, "completion_ms": None, "latency_ms": None,
        "error": "",
    }
    ctx = CallContext()
    provider = OpenAICompatibleProvider(
        settings.base_url, settings.model, api_key=settings.api_key(),
        timeout=min(float(settings.timeout_seconds), 120.0), max_attempts=1,
    )
    try:
        list_started = time.monotonic()
        async with ServiceClient(
            "llm", settings.base_url, headers=provider.headers(), timeout=min(float(settings.timeout_seconds), 30.0),
            max_attempts=1,
        ) as client:
            body = await client.json("GET", "/models", operation="llm.health.models", ctx=ctx)
        result["models_ms"] = int((time.monotonic() - list_started) * 1000)
        ids = [str(item.get("id")) for item in (body or {}).get("data", []) if isinstance(item, dict)]
        result["available_models"] = ids[:50]
        result["model_listed"] = settings.model in ids
        result["vision_model_listed"] = bool(settings.vision_model) and settings.vision_model in ids
        if not result["model_listed"]:
            result["status"] = "model_missing"
            result["error"] = (
                f"Model '{settings.model}' is not served at {settings.base_url}. "
                f"Available: {', '.join(ids[:12]) or 'none'}"
            )
            return result
        response = await provider.complete(
            LLMRequest(
                system="You are a health check. Reply with JSON only.",
                prompt='Reply with exactly this JSON object: {"status": "ok"}',
                json_mode=True, temperature=0, max_tokens=32,
            ),
            ctx, operation="llm.health.ping",
        )
        result["completion_ms"] = response.duration_ms
        try:
            data = extract_json(response.text)
        except ValueError as exc:
            result["status"] = "invalid_json"
            result["error"] = str(exc)
            return result
        if not isinstance(data, dict):
            result["status"] = "invalid_json"
            result["error"] = f"Expected a JSON object, got: {response.text[:120]}"
            return result
        result.update(ok=True, status="ok")
        return result
    except SocialEngineError as exc:
        result["status"] = "unreachable" if result["models_ms"] is None else "error"
        result["error"] = describe(exc)[1]
        return result
    except Exception as exc:  # the doctor must report, never raise
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
