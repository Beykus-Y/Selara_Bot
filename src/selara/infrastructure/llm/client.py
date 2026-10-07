from __future__ import annotations

import asyncio
import email.utils
import json
import logging
import math
from dataclasses import dataclass, field
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable, Generic, TypeVar
from uuid import uuid4

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, ValidationError

from selara.infrastructure.llm.pricing import estimate_llm_cost_usd
from selara.application.model_catalog import CatalogProvider, CatalogSnapshot, ModelCapabilities, validate_text
from selara.application.model_router import DefaultModelRouter, ModelRouter, ResolvedModel

log = logging.getLogger(__name__)

_StructuredModel = TypeVar("_StructuredModel", bound=BaseModel)
_Response = TypeVar("_Response")

_DEFAULT_TIMEOUT = 60.0
_MAX_PROVIDER_ATTEMPTS = 3
_MAX_RETRY_AFTER_SECONDS = 120.0
# #37: chat_with_tools has the highest fan-out (up to 8 rounds per admin
# query) of the three chat methods, but was the only one with no max_tokens
# cap -- a single round could otherwise produce an unbounded-length
# completion, limited only by the provider's model-level ceiling.
_DEFAULT_MAX_TOKENS_CHAT_WITH_TOOLS = 4000
_COST_QUANTUM = Decimal("0.000000001")
_MAX_PROVIDER_COST_USD = Decimal("1000000")


def _provider_reported_cost(provider_usage: object) -> Decimal | None:
    """OpenRouter's ``usage.cost`` in USD, or ``None`` when absent or not a sane number."""
    if provider_usage is None:
        return None
    raw = getattr(provider_usage, "cost", None)
    if raw is None:
        extra = getattr(provider_usage, "model_extra", None)
        raw = extra.get("cost") if isinstance(extra, dict) else None
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = Decimal(str(raw))
    except Exception:
        return None
    if not value.is_finite() or value < 0 or value > _MAX_PROVIDER_COST_USD:
        return None
    return value.quantize(_COST_QUANTUM)


@dataclass(frozen=True, slots=True)
class LlmConfig:
    api_key: str
    model: str
    base_url: str | None = None
    timeout_seconds: float = _DEFAULT_TIMEOUT
    summary_model: str = "gpt-4o-mini"
    supports_structured_output: bool = False
    # OpenRouter: ask for ``usage.cost`` and optionally pass ``provider`` routing preferences.
    include_usage_cost: bool = False
    provider_preferences: dict | None = None

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("LLM_API_KEY не задан.")
        if not self.model.strip():
            raise ValueError("LLM_MODEL не задан.")
        if not self.summary_model.strip():
            raise ValueError("LLM_SUMMARY_MODEL не задан.")
        if self.timeout_seconds <= 0:
            raise ValueError("LLM_TIMEOUT_SECONDS должен быть > 0.")


class LlmClientError(RuntimeError):
    def __init__(
        self, message: str, *, usages: tuple[LlmCallUsage, ...] = (), corrective_retries: int = 0
    ) -> None:
        super().__init__(message)
        self.message = message
        self.usages = usages
        self.corrective_retries = corrective_retries


@dataclass(frozen=True, slots=True)
class LlmAccountingContext:
    invocation_id: int
    feature: str
    stage: str
    chat_id: int | None
    actor_user_id: int | None = None
    telegram_message_id: int | None = None


@dataclass(frozen=True, slots=True)
class LlmCallUsage:
    call_id: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    estimated_cost_usd: Decimal | None
    pricing_status: str
    attempt_number: int
    status: str
    error_category: str | None = None
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    request_id: str | None = None
    model_profile: str | None = None
    # The cost the provider itself reported for this call (OpenRouter ``usage.cost``);
    # when set it is also what ``estimated_cost_usd`` holds.
    provider_cost_usd: Decimal | None = None


@dataclass(frozen=True, slots=True)
class LlmCallResult(Generic[_Response]):
    value: _Response
    usages: tuple[LlmCallUsage, ...]
    corrective_retries: int = 0


UsageRecorder = Callable[[LlmAccountingContext, LlmCallUsage], Awaitable[None]]


class LlmClient:
    def __init__(self, config: LlmConfig, *, usage_recorder: UsageRecorder | None = None,
                 accounting_service=None, model_catalog: CatalogProvider | None = None,
                 model_router: ModelRouter | None = None, feature_routes=None) -> None:
        self._config = config
        self._feature_routes = feature_routes
        self._model_catalog = model_catalog
        self._model_router = model_router or DefaultModelRouter(config.model, model_catalog)
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            # SDK-internal retries hide billable HTTP attempts from usage rows.
            # Each retry must be an explicit, visible call in this client.
            max_retries=0,
        )
        self.accounting_service = accounting_service
        self._usage_recorder = usage_recorder or (
            accounting_service.report_provider_attempt if accounting_service is not None else None
        )

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        max_tokens: int | None = _DEFAULT_MAX_TOKENS_CHAT_WITH_TOOLS,
        accounting_context: LlmAccountingContext | None = None,
        model: str | None = None,
        model_profile: str | None = None,
    ):
        model_profile = await self._routed_profile(model, model_profile, accounting_context)
        selected, snapshot, _ = await self._prepare_model(
            model, model_profile, self._config.model, ModelCapabilities(supports_tools=bool(tools)),
        )
        response, usages = await self._request_with_retries(
            "chat_with_tools", selected, accounting_context,
            catalog_snapshot=snapshot, model_profile=model_profile,
            model=selected, messages=messages, tools=tools or None,
            tool_choice="auto" if tools else None, max_tokens=max_tokens,
        )
        return LlmCallResult(response, usages)

    @property
    def model_catalog(self) -> CatalogProvider | None:
        """The catalog this client prices and routes with (read-only use by feature code)."""
        return self._model_catalog

    @property
    def default_model(self) -> str:
        return self._config.model

    async def chat_simple(self, messages: list[dict], *, max_tokens: int | None = None,
                          accounting_context: LlmAccountingContext | None = None,
                          model: str | None = None, model_profile: str | None = None,
                          resolved_model: ResolvedModel | None = None) -> LlmCallResult[str]:
        if resolved_model is not None:
            # Already resolved by the caller (Personal AI): use exactly that model and its pricing
            # snapshot instead of resolving the profile a second time.
            if model is not None or model_profile is not None:
                raise ValueError("Pass either resolved_model or model/model_profile")
            validate_text(resolved_model.model_id, "model override", 255)
            response, usages = await self._request_with_retries(
                "chat_simple", resolved_model.model_id, accounting_context,
                catalog_snapshot=resolved_model.catalog, model_profile=resolved_model.profile_key,
                model=resolved_model.model_id, messages=messages, max_tokens=max_tokens,
            )
            return LlmCallResult(response.choices[0].message.content or "", usages)
        model_profile = await self._routed_profile(model, model_profile, accounting_context)
        selected, snapshot, _ = await self._prepare_model(model, model_profile, self._config.model)
        response, usages = await self._request_with_retries(
            "chat_simple", selected, accounting_context,
            catalog_snapshot=snapshot, model_profile=model_profile,
            model=selected, messages=messages, max_tokens=max_tokens,
        )
        return LlmCallResult(response.choices[0].message.content or "", usages)

    async def summarize(self, messages: list[dict], *, max_tokens: int | None = None,
                        accounting_context: LlmAccountingContext | None = None,
                        model: str | None = None, model_profile: str | None = None) -> LlmCallResult[str]:
        selected, snapshot, _ = await self._prepare_model(model, model_profile, self._config.summary_model)
        response, usages = await self._request_with_retries(
            "summarize", selected, accounting_context,
            catalog_snapshot=snapshot, model_profile=model_profile,
            model=selected, messages=messages, max_tokens=max_tokens,
        )
        return LlmCallResult(response.choices[0].message.content or "", usages)

    async def chat_structured(
        self,
        messages: list[dict],
        *,
        response_model: type[_StructuredModel],
        max_tokens: int | None = None,
        accounting_context: LlmAccountingContext | None = None,
        model: str | None = None,
        model_profile: str | None = None,
    ) -> LlmCallResult[_StructuredModel]:
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
        selected, snapshot, native_structured = await self._prepare_model(
            model, model_profile, self._config.summary_model,
            ModelCapabilities(supports_structured_output=self._config.supports_structured_output),
        )
        schema = response_model.model_json_schema()
        request_messages = list(messages)

        usages: list[LlmCallUsage] = []
        request_id = str(uuid4())
        for correction_round in range(2):
            request_kwargs = dict(
                model=selected, messages=request_messages, max_tokens=max_tokens,
            )
            if native_structured:
                request_kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": response_model.__name__,
                        "schema": schema,
                        "strict": True,
                    },
                }
            try:
                response, request_usages = await self._request_with_retries(
                    "chat_structured", selected, accounting_context,
                    catalog_snapshot=snapshot, model_profile=model_profile,
                    request_id=request_id, attempt_number_start=len(usages) + 1, **request_kwargs,
                )
            except LlmClientError as exc:
                raise LlmClientError(
                    exc.message, usages=tuple([*usages, *exc.usages]), corrective_retries=correction_round,
                ) from exc

            usages.extend(request_usages)
            usage = usages[-1]
            content = response.choices[0].message.content or ""
            try:
                parsed = response_model.model_validate(json.loads(content))
                return LlmCallResult(parsed, tuple(usages), corrective_retries=correction_round)
            except (json.JSONDecodeError, ValidationError) as exc:
                usage = replace(usage, status="validation_failed")
                usages[-1] = usage
                await self._record(accounting_context, usage)
                if correction_round == 0:
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
                raise LlmClientError(
                    f"LLM вернула невалидный структурированный ответ: {exc}",
                    usages=tuple(usages), corrective_retries=correction_round,
                ) from exc

        raise AssertionError("unreachable")  # loop always returns or raises

    async def _routed_profile(
        self, model: str | None, profile: str | None, context: LlmAccountingContext | None
    ) -> str | None:
        """The owner-chosen profile of the call's feature, unless the caller picked a model itself."""
        if model is not None or profile is not None or self._feature_routes is None or context is None:
            return profile
        try:
            return await self._feature_routes.profile_for_feature(context.feature)
        except Exception:
            log.exception("Feature model route unavailable; using the default model")
            return None

    async def _prepare_model(
        self, model: str | None, profile: str | None, legacy_model: str,
        required: ModelCapabilities = ModelCapabilities(),
    ) -> tuple[str, CatalogSnapshot | None, bool]:
        if model is not None and profile is not None:
            raise ValueError("Pass either model or model_profile, not both")
        if model is not None:
            validate_text(model, "model override", 255)
        if profile is not None:
            validate_text(profile, "model_profile", 64)
        selected = model or legacy_model
        native_structured = self._config.supports_structured_output
        if profile is not None:
            resolved = await self._model_router.resolve(
                profile_key=profile, legacy_model=legacy_model, required=required,
            )
            selected = resolved.model_id
            if resolved.capabilities is not None:
                native_structured = resolved.capabilities.supports_structured_output
        snapshot = None
        if self._model_catalog is not None:
            try:
                snapshot = await self._model_catalog.get()
            except Exception:
                # Telemetry must never prevent a provider call.
                log.exception("Catalog pricing unavailable")
        if model is not None and snapshot is not None:
            configured = snapshot.models_by_id.get(model)
            if configured is not None:
                if not configured.capabilities.satisfies(required):
                    raise ValueError("model override does not support the required capabilities")
                native_structured = configured.capabilities.supports_structured_output
        return selected, snapshot, native_structured

    async def _request_with_retries(
        self,
        method: str,
        configured_model: str,
        accounting_context: LlmAccountingContext | None,
        *,
        request_id: str | None = None,
        attempt_number_start: int = 1,
        catalog_snapshot: CatalogSnapshot | None = None,
        model_profile: str | None = None,
        **request_kwargs,
    ) -> tuple[object, tuple[LlmCallUsage, ...]]:
        """Make up to three visible provider attempts, persisting every one.

        Retryable transport and HTTP errors are retried explicitly because the
        OpenAI SDK's hidden retries are disabled. Each retry shares one logical
        request_id while retaining its own call_id and attempt number.
        """
        request_id = request_id or str(uuid4())
        usages: list[LlmCallUsage] = []
        for offset in range(_MAX_PROVIDER_ATTEMPTS):
            attempt_number = attempt_number_start + offset
            marker = getattr(self.accounting_service, "mark_provider_attempt_started", None)
            if accounting_context is not None and marker is not None:
                # This must commit before the HTTP request. Usage-row writes
                # happen after a response and can fail independently.
                await marker(invocation_id=accounting_context.invocation_id)
            try:
                response = await self._client.chat.completions.create(
                    **self._with_provider_options(
                        request_kwargs,
                        route=accounting_context is not None and accounting_context.feature == "personal_chat",
                    )
                )
            except asyncio.CancelledError:
                usage = self._failed_usage(
                    configured_model, attempt_number, "cancelled", request_id=request_id, model_profile=model_profile,
                )
                usages.append(usage)
                log.warning(
                    "LLM provider attempt cancelled invocation_id=%s feature=%s stage=%s call_id=%s attempt=%s",
                    accounting_context.invocation_id if accounting_context else None,
                    accounting_context.feature if accounting_context else None,
                    accounting_context.stage if accounting_context else method,
                    usage.call_id, attempt_number,
                )
                await self._record(accounting_context, usage)
                raise
            except (APITimeoutError, APIConnectionError, APIStatusError) as exc:
                category = (
                    "timeout" if isinstance(exc, APITimeoutError)
                    else "connection" if isinstance(exc, APIConnectionError)
                    else "api_status"
                )
                usage = self._failed_usage(
                    configured_model, attempt_number, category, request_id=request_id, model_profile=model_profile,
                )
                usages.append(usage)
                await self._record(accounting_context, usage)
                retryable = (
                    _status_error_should_retry(exc)
                    if isinstance(exc, APIStatusError)
                    else True
                )
                if retryable and offset + 1 < _MAX_PROVIDER_ATTEMPTS:
                    delay = (
                        _status_error_retry_delay(exc, offset)
                        if isinstance(exc, APIStatusError)
                        else 0.5 * (2 ** offset)
                    )
                    await asyncio.sleep(delay)
                    continue
                message = _extract_api_error(exc) if isinstance(exc, APIStatusError) else (
                    "LLM-сервис не ответил вовремя." if isinstance(exc, APITimeoutError)
                    else "Не удалось подключиться к LLM-сервису."
                )
                raise LlmClientError(message, usages=tuple(usages)) from exc

            usage = self._usage(
                method, response, model=self._reported_model(response, configured_model),
                attempt=attempt_number, request_id=request_id,
                catalog_snapshot=catalog_snapshot, model_profile=model_profile,
            )
            usages.append(usage)
            await self._record(accounting_context, usage)
            return response, tuple(usages)

        raise AssertionError("provider retry loop always returns or raises")

    def _with_provider_options(self, request_kwargs: dict, *, route: bool = True) -> dict:
        """Add the OpenRouter extras (real cost, provider preferences) without mutating the caller's kwargs.

        Provider preferences (``max_price`` and friends) are only meant for the Personal chat turn, so ``route``
        keeps them away from groups, ``?``/``??``, pets and the internal operations.
        """
        extra: dict = {}
        if self._config.include_usage_cost:
            extra["usage"] = {"include": True}
        if route and self._config.provider_preferences:
            extra["provider"] = dict(self._config.provider_preferences)
        if not extra:
            return request_kwargs
        merged = dict(request_kwargs.get("extra_body") or {})
        for key, value in extra.items():
            merged.setdefault(key, value)
        return {**request_kwargs, "extra_body": merged}

    @staticmethod
    def _reported_model(response: object, configured_model: str) -> str:
        reported = getattr(response, "model", None)
        return reported if isinstance(reported, str) and reported else configured_model

    @staticmethod
    def _usage(
        method: str, response: object, *, model: str, attempt: int, request_id: str | None = None,
        catalog_snapshot: CatalogSnapshot | None = None, model_profile: str | None = None,
    ) -> LlmCallUsage:
        _log_usage(method, response)
        provider_usage = getattr(response, "usage", None)
        provider_cost = _provider_reported_cost(provider_usage)
        prompt = getattr(provider_usage, "prompt_tokens", None) if provider_usage is not None else None
        completion = getattr(provider_usage, "completion_tokens", None) if provider_usage is not None else None
        total = getattr(provider_usage, "total_tokens", None) if provider_usage is not None else None
        catalog_model = catalog_snapshot.models_by_id.get(model) if catalog_snapshot is not None else None
        try:
            # An explicit NULL price wins over legacy pricing; disabled models are still priced.
            cost = (catalog_model.estimate(prompt, completion) if catalog_model is not None
                    else estimate_llm_cost_usd(model=model, prompt_tokens=prompt, completion_tokens=completion))
        except Exception:
            log.exception("Model cost estimate unavailable model=%s", model)
            cost = None
        if provider_cost is not None:
            # The provider's own figure beats any estimate: routing, caching and fallbacks change prices.
            cost = provider_cost
        status = "known" if cost is not None else "unknown"
        if provider_usage is None:
            log.warning("provider response has no token usage method=%s model=%s", method, model)
        elif cost is None:
            log.warning("unknown LLM pricing model=%s method=%s", model, method)
        return LlmCallUsage(
            str(uuid4()), model, prompt, completion, total, cost, status, attempt, "succeeded",
            request_id=request_id or str(uuid4()), model_profile=model_profile,
            provider_cost_usd=provider_cost,
        )

    @staticmethod
    def _failed_usage(
        model: str, attempt: int, category: str, *, request_id: str | None = None,
        model_profile: str | None = None,
    ) -> LlmCallUsage:
        return LlmCallUsage(
            str(uuid4()), model, None, None, None, None, "unknown", attempt, "failed", category,
            request_id=request_id or str(uuid4()), model_profile=model_profile,
        )

    async def _record(self, context: LlmAccountingContext | None, usage: LlmCallUsage) -> None:
        if context is None or self._usage_recorder is None:
            return
        task = asyncio.create_task(self._usage_recorder(context, usage))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Once a provider has replied, finish the short DB write even when
            # the handler is cancelled so shutdown does not discard known cost.
            try:
                await task
            except Exception:
                self._log_accounting_error(context)
            raise
        except Exception:
            self._log_accounting_error(context)

    @staticmethod
    def _log_accounting_error(context: LlmAccountingContext) -> None:
        log.exception(
            "LLM accounting persistence error invocation_id=%s feature=%s stage=%s",
            context.invocation_id, context.feature, context.stage,
        )


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


def _retry_after_seconds(headers) -> float | None:
    """Parse Retry-After in milliseconds, seconds, or HTTP-date form."""
    retry_after_ms = headers.get("retry-after-ms")
    if retry_after_ms is not None:
        try:
            delay = float(retry_after_ms) / 1000
            if math.isfinite(delay):
                return delay
        except (TypeError, ValueError):
            pass

    retry_after = headers.get("retry-after")
    if retry_after is None:
        return None
    try:
        delay = float(retry_after)
    except (TypeError, ValueError):
        try:
            retry_date = email.utils.parsedate_to_datetime(retry_after)
            if retry_date.tzinfo is None:
                retry_date = retry_date.replace(tzinfo=timezone.utc)
            delay = (retry_date - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError, OSError):
            return None
    return delay if math.isfinite(delay) else None


def _status_error_should_retry(exc: APIStatusError) -> bool:
    headers = exc.response.headers
    retry_after = _retry_after_seconds(headers)
    if retry_after is not None and retry_after > _MAX_RETRY_AFTER_SECONDS:
        return False
    directive = headers.get("x-should-retry", "").lower()
    if directive == "true":
        return True
    if directive == "false":
        return False
    return exc.status_code in {408, 409, 429} or exc.status_code >= 500


def _status_error_retry_delay(exc: APIStatusError, offset: int) -> float:
    retry_after = _retry_after_seconds(exc.response.headers)
    if retry_after is not None and 0 < retry_after <= _MAX_RETRY_AFTER_SECONDS:
        return retry_after
    return 0.5 * (2 ** offset)
