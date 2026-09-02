from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TypeVar

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

_StructuredModel = TypeVar("_StructuredModel", bound=BaseModel)

_DEFAULT_TIMEOUT = 60.0
# #37: chat_with_tools has the highest fan-out (up to 8 rounds per admin
# query) of the three chat methods, but was the only one with no max_tokens
# cap -- a single round could otherwise produce an unbounded-length
# completion, limited only by the provider's model-level ceiling.
_DEFAULT_MAX_TOKENS_CHAT_WITH_TOOLS = 4000


@dataclass(frozen=True, slots=True)
class LlmConfig:
    api_key: str
    model: str
    base_url: str | None = None
    timeout_seconds: float = _DEFAULT_TIMEOUT
    summary_model: str = "gpt-4o-mini"
    supports_structured_output: bool = False

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
        # Usage of the most recent chat_* call, for callers (the daily summary
        # pipeline) that need per-call token counts for cost accounting without
        # changing what each method returns. None until a call succeeds.
        self.last_usage: tuple[int | None, int | None] | None = None
        self.last_model: str | None = None
        # How many corrective retries chat_structured needed for its most recent
        # call (0 = first attempt parsed/validated cleanly). Read by the daily
        # summary pipeline for its per-run reliability diagnostics.
        self.last_retry_count: int = 0

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        max_tokens: int | None = _DEFAULT_MAX_TOKENS_CHAT_WITH_TOOLS,
    ):
        try:
            response = await self._client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                tools=tools or None,
                tool_choice="auto" if tools else None,
                max_tokens=max_tokens,
            )
            self._record_usage("chat_with_tools", response, model=self._config.model)
            return response
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
            self._record_usage("chat_simple", response, model=self._config.model)
            return response.choices[0].message.content or ""
        except APITimeoutError as exc:
            raise LlmClientError("LLM-сервис не ответил вовремя.") from exc
        except APIConnectionError as exc:
            raise LlmClientError("Не удалось подключиться к LLM-сервису.") from exc
        except APIStatusError as exc:
            raise LlmClientError(_extract_api_error(exc)) from exc

    async def summarize(self, messages: list[dict], *, max_tokens: int | None = None) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=self._config.summary_model,
                messages=messages,
                max_tokens=max_tokens,
            )
            self._record_usage("summarize", response, model=self._config.summary_model)
            return response.choices[0].message.content or ""
        except APITimeoutError as exc:
            raise LlmClientError("LLM-сервис не ответил вовремя.") from exc
        except APIConnectionError as exc:
            raise LlmClientError("Не удалось подключиться к LLM-сервису.") from exc
        except APIStatusError as exc:
            raise LlmClientError(_extract_api_error(exc)) from exc

    async def chat_structured(
        self,
        messages: list[dict],
        *,
        response_model: type[_StructuredModel],
        max_tokens: int | None = None,
    ) -> _StructuredModel:
        """Get a schema-validated response from the cheap summary_model.

        Used by the daily summary pipeline's non-tool stages (per-segment topic
        extraction, theme merge -- see docs/DAILY_SUMMARY_TODO.md), which need
        reliable structured JSON, not a text answer or a tool call.

        If `LlmConfig.supports_structured_output` is set, this uses the provider's
        native `response_format={"type": "json_schema", ...}` -- but the response is
        ALWAYS re-validated against `response_model` afterwards regardless, since a
        provider can claim schema support and still drift. On the first validation
        failure, one corrective follow-up round is attempted before giving up.
        """
        schema = response_model.model_json_schema()
        request_messages = list(messages)

        for attempt in range(2):
            try:
                if self._config.supports_structured_output:
                    response = await self._client.chat.completions.create(
                        model=self._config.summary_model,
                        messages=request_messages,
                        max_tokens=max_tokens,
                        response_format={
                            "type": "json_schema",
                            "json_schema": {
                                "name": response_model.__name__,
                                "schema": schema,
                                "strict": True,
                            },
                        },
                    )
                else:
                    response = await self._client.chat.completions.create(
                        model=self._config.summary_model,
                        messages=request_messages,
                        max_tokens=max_tokens,
                    )
                self._record_usage("chat_structured", response, model=self._config.summary_model)
            except APITimeoutError as exc:
                raise LlmClientError("LLM-сервис не ответил вовремя.") from exc
            except APIConnectionError as exc:
                raise LlmClientError("Не удалось подключиться к LLM-сервису.") from exc
            except APIStatusError as exc:
                raise LlmClientError(_extract_api_error(exc)) from exc

            content = response.choices[0].message.content or ""
            try:
                parsed = response_model.model_validate(json.loads(content))
                self.last_retry_count = attempt
                return parsed
            except (json.JSONDecodeError, ValidationError) as exc:
                if attempt == 0:
                    # Include the actual schema, not just a description of the
                    # failure -- a system prompt that never explicitly says "wrap
                    # the array in an object under this key" reliably produces a
                    # bare JSON array on the first try (seen in production: the
                    # model returned `[{...}]` instead of `{"topics": [...]}`).
                    # Naming the exact schema here gives the corrective round a
                    # real chance regardless of how the original prompt was worded.
                    request_messages = [
                        *request_messages,
                        {"role": "assistant", "content": content},
                        {
                            "role": "user",
                            "content": (
                                "Ответ не прошёл валидацию по JSON-схеме: "
                                f"{exc}. Схема, которой должен соответствовать ответ:\n"
                                f"{json.dumps(schema, ensure_ascii=False)}\n"
                                "Верни ТОЛЬКО валидный JSON по этой схеме (это JSON-объект, "
                                "а не голый список), без пояснений."
                            ),
                        },
                    ]
                    continue
                raise LlmClientError(f"LLM вернула невалидный структурированный ответ: {exc}") from exc

        raise AssertionError("unreachable")  # loop always returns or raises

    def _record_usage(self, method: str, response: object, *, model: str) -> None:
        _log_usage(method, response)
        usage = getattr(response, "usage", None)
        self.last_model = model
        if usage is None:
            self.last_usage = None
            return
        self.last_usage = (getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))


def _log_usage(method: str, response: object) -> None:
    """#10: response.usage was never read/logged anywhere -- only
    failure-path warnings existed, despite a single ?? query fanning out to
    ~10 billed calls. This is deliberately just a log line (no DB/metrics
    store) -- per Ilya's note on the skills-design doc, measure actual
    token cost first before building anything more elaborate on top."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    log.info(
        "llm usage method=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
        method,
        getattr(usage, "prompt_tokens", None),
        getattr(usage, "completion_tokens", None),
        getattr(usage, "total_tokens", None),
    )


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
