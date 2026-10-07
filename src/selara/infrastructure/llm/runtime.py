"""Single validation of the LLM runtime configuration.

The bot process, the Selara AI checkout and the web/admin status views must agree
on whether the provider is usable, so they all derive it from here. Nothing in
this module builds a client or touches the network.
"""

from __future__ import annotations

import json
from typing import Any

from selara.infrastructure.llm.client import LlmConfig


def _provider_preferences(raw: str, name: str = "LLM_PROVIDER_PREFERENCES_JSON") -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} должен быть валидным JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{name} должен быть JSON-объектом.")
    return value


def _include_usage_cost(settings: Any) -> bool:
    explicit = getattr(settings, "llm_include_usage_cost", None)
    if explicit is not None:
        return bool(explicit)
    return "openrouter.ai" in (settings.llm_base_url or "").lower()


def llm_runtime_problem(settings: Any) -> tuple[LlmConfig | None, str | None]:
    """Return ``(config, None)`` when usable, else ``(None, human-readable reason)``."""
    if not settings.llm_enabled:
        return None, "LLM_ENABLED выключен."
    try:
        config = LlmConfig(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url or None,
            timeout_seconds=settings.llm_timeout_seconds,
            summary_model=settings.llm_summary_model,
            supports_structured_output=settings.llm_supports_structured_output,
            include_usage_cost=_include_usage_cost(settings),
            provider_preferences=_provider_preferences(getattr(settings, "llm_provider_preferences_json", "")),
            group_provider_preferences=_provider_preferences(
                getattr(settings, "llm_group_provider_preferences_json", ""), "LLM_GROUP_PROVIDER_PREFERENCES_JSON"
            ),
        )
    except ValueError as exc:
        return None, str(exc)
    return config, None


def llm_runtime_config(settings: Any) -> LlmConfig | None:
    return llm_runtime_problem(settings)[0]
