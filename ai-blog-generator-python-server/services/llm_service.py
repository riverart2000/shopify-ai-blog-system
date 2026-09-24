"""services/llm_service.py — Single-attempt text generation with exact errors."""
from __future__ import annotations

import logging

import db
import providers

logger = logging.getLogger("ai_blog_server")


async def generate_text(
    store_id: str,
    prompt: str,
    system_prompt: str = "",
    model_id: str | None = None,
    prompt_ending_override: str | None = None,
) -> dict:
    """Run exactly one configured model once.

    Automatic model failover is disabled because a second model is another paid
    generation whose output can conceal the first failure.
    """
    rows = await db.get_active_text_models(store_id)
    if not rows:
        raise providers.AllModelsFailedError([("(none)", "No active text models configured")])

    # If a specific model was requested, only use that one (no fallback).
    # "__random__" picks one at random from all active models.
    if model_id == "__random__":
        import random
        random.shuffle(rows)
    elif model_id:
        rows = [r for r in rows if r["id"] == model_id]
        if not rows:
            raise providers.AllModelsFailedError([(model_id, "Model not found or not active")])
    rows = rows[:1]

    failures: list[tuple[str, str]] = []
    if prompt_ending_override is None:
        prompt_ending = await db.get_store_setting(store_id, "prompt_ending", "")
    else:
        prompt_ending = prompt_ending_override

    for row in rows:
        model = providers.ModelRecord.from_dict(row)
        try:
            provider = providers.get_text_provider(model)
            result = await provider.generate_text(prompt, system_prompt, prompt_ending=prompt_ending)
            logger.info(
                "Text generated via %s model=%s store=%s",
                model.provider, model.model_name, store_id,
            )
            result["_model_name"] = model.name
            result["_model_provider"] = model.provider
            return result
        except providers.ProviderError as exc:
            err_msg = (
                f"{exc}. Exactly one model was attempted; automatic retries and "
                "model failover are disabled."
            )
            logger.warning("Provider %s failed: %s", model.name, err_msg)
            failures.append((model.name, err_msg))
            await db.log_model_error(store_id, model.id, model.provider, "provider_error", err_msg)
        except Exception as exc:
            err_msg = (
                f"Unexpected error: {exc}. Exactly one model was attempted; "
                "automatic retries and model failover are disabled."
            )
            logger.exception("Unexpected error from provider %s", model.name)
            failures.append((model.name, err_msg))
            await db.log_model_error(store_id, model.id, model.provider, "unexpected_error", err_msg)

    raise providers.AllModelsFailedError(failures)
