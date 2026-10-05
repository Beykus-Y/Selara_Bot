"""Single validation of the LLM runtime configuration.

The bot process, the Selara AI checkout and the web/admin status views must agree
on whether the provider is usable, so they all derive it from here. Nothing in
this module builds a client or touches the network.
"""

from __future__ import annotations

from typing import Any

from selara.infrastructure.llm.client import LlmConfig


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
        )
    except ValueError as exc:
        return None, str(exc)
    return config, None


def llm_runtime_config(settings: Any) -> LlmConfig | None:
    return llm_runtime_problem(settings)[0]
