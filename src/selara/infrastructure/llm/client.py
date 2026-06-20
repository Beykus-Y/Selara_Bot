from __future__ import annotations

import logging
from dataclasses import dataclass

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 60.0


@dataclass(frozen=True, slots=True)
class LlmConfig:
    api_key: str
    model: str
    base_url: str | None = None
    timeout_seconds: float = _DEFAULT_TIMEOUT
    summary_model: str = "gpt-4o-mini"

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("LLM_API_KEY не задан.")
        if not self.model.strip():
            raise ValueError("LLM_MODEL не задан.")
        if self.timeout_seconds <= 0:
            raise ValueError("LLM_TIMEOUT_SECONDS должен быть > 0.")


class LlmClientError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LlmClient:
    def __init__(self, config: LlmConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
        )

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ):
        try:
            return await self._client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                tools=tools or None,
                tool_choice="auto" if tools else None,
            )
        except APITimeoutError as exc:
            raise LlmClientError("LLM-сервис не ответил вовремя.") from exc
        except APIConnectionError as exc:
            raise LlmClientError("Не удалось подключиться к LLM-сервису.") from exc
        except APIStatusError as exc:
            raise LlmClientError(_extract_api_error(exc)) from exc

    async def chat_simple(self, messages: list[dict], *, max_tokens: int | None = None) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except (APITimeoutError, APIConnectionError, APIStatusError) as exc:
            raise LlmClientError(str(exc)) from exc

    async def summarize(self, messages: list[dict], *, max_tokens: int | None = None) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=self._config.summary_model,
                messages=messages,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except (APITimeoutError, APIConnectionError, APIStatusError) as exc:
            raise LlmClientError(str(exc)) from exc


def _extract_api_error(exc: APIStatusError) -> str:
    try:
        body = exc.response.json()
    except Exception:
        body = None

    if isinstance(body, dict):
        error = body.get("error", {})
        if isinstance(error, dict):
            msg = error.get("message", "")
            if msg:
                return f"LLM API: {msg}"
        message = body.get("message", "")
        if message:
            return f"LLM API: {message}"

    code = exc.status_code
    if code == 401:
        return "LLM API: неверный API-ключ."
    if code == 429:
        return "LLM API: превышен лимит запросов. Попробуйте позже."
    if code >= 500:
        return "LLM API: внутренняя ошибка сервиса."
    return f"LLM API вернул ошибку: HTTP {code}."
