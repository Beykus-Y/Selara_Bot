"""Regression tests for finding #25 (LLM half) in docs/STT_LLM_AUDIT_TODO.md:

chat_with_tools translates API errors to safe, informative user-facing text
via _extract_api_error; chat_simple/summarize instead raised the raw SDK
exception's str() -- low-stakes for summarize (never shown to a user), but
chat_simple backs the DM-summary path, so an admin could see a raw SDK
traceback fragment as an "error message"."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from openai import APIStatusError

from selara.infrastructure.llm.client import LlmClient, LlmClientError, LlmConfig


def _config() -> LlmConfig:
    return LlmConfig(api_key="test-key", model="test-model")


def _status_error(status_code: int, body_message: str, *, headers: dict[str, str] | None = None) -> APIStatusError:
    response = httpx.Response(
        status_code=status_code,
        request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
        json={"error": {"message": body_message}},
        headers=headers,
    )
    return APIStatusError("raw sdk text", response=response, body=None)


@pytest.mark.asyncio
async def test_chat_simple_uses_translated_api_error_not_raw_sdk_text():
    client = LlmClient(_config())
    client._client.chat.completions.create = _raise(_status_error(401, "invalid api key"))

    with pytest.raises(LlmClientError) as excinfo:
        await client.chat_simple([{"role": "user", "content": "hi"}])

    assert "raw sdk text" not in excinfo.value.message
    assert "invalid api key" in excinfo.value.message


@pytest.mark.asyncio
async def test_summarize_uses_translated_api_error_not_raw_sdk_text():
    client = LlmClient(_config())
    client._client.chat.completions.create = _raise(_status_error(429, "rate limited"))

    with pytest.raises(LlmClientError) as excinfo:
        await client.summarize([{"role": "user", "content": "hi"}])

    assert "raw sdk text" not in excinfo.value.message
    assert "rate limited" in excinfo.value.message.lower()


def _raise(exc: Exception):
    return AsyncMock(side_effect=exc)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "expected_delay"),
    [
        ({"retry-after": "10"}, 10.0),
        ({"retry-after": "10", "retry-after-ms": "1500"}, 1.5),
    ],
)
async def test_retryable_status_respects_server_retry_after(headers, expected_delay, monkeypatch):
    import selara.infrastructure.llm.client as llm_client_module

    sleep = AsyncMock()
    monkeypatch.setattr(llm_client_module.asyncio, "sleep", sleep)
    client = LlmClient(_config())
    success = MagicMock(
        model="test-model",
        choices=[MagicMock(message=MagicMock(content="recovered"))],
        usage=None,
    )
    client._client.chat.completions.create = AsyncMock(
        side_effect=[_status_error(429, "rate limited", headers=headers), success]
    )

    result = await client.chat_simple([])

    assert result.value == "recovered"
    sleep.assert_awaited_once_with(expected_delay)
    assert client._client.chat.completions.create.await_count == 2


@pytest.mark.asyncio
async def test_status_error_retry_directive_overrides_status_code(monkeypatch):
    import selara.infrastructure.llm.client as llm_client_module

    sleep = AsyncMock()
    monkeypatch.setattr(llm_client_module.asyncio, "sleep", sleep)
    client = LlmClient(_config())
    client._client.chat.completions.create = _raise(
        _status_error(429, "rate limited", headers={"x-should-retry": "false"})
    )

    with pytest.raises(LlmClientError):
        await client.chat_simple([])

    assert client._client.chat.completions.create.await_count == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_error_retry_directive_can_retry_otherwise_nonretryable_status(monkeypatch):
    import selara.infrastructure.llm.client as llm_client_module

    sleep = AsyncMock()
    monkeypatch.setattr(llm_client_module.asyncio, "sleep", sleep)
    client = LlmClient(_config())
    success = MagicMock(
        model="test-model",
        choices=[MagicMock(message=MagicMock(content="recovered"))],
        usage=None,
    )
    client._client.chat.completions.create = AsyncMock(
        side_effect=[_status_error(400, "retry requested", headers={"x-should-retry": "true"}), success]
    )

    result = await client.chat_simple([])

    assert result.value == "recovered"
    assert client._client.chat.completions.create.await_count == 2
    sleep.assert_awaited_once_with(0.5)


@pytest.mark.asyncio
async def test_retry_after_over_two_minutes_stops_retries(monkeypatch):
    import selara.infrastructure.llm.client as llm_client_module

    sleep = AsyncMock()
    monkeypatch.setattr(llm_client_module.asyncio, "sleep", sleep)
    client = LlmClient(_config())
    client._client.chat.completions.create = _raise(
        _status_error(429, "rate limited", headers={"retry-after": "121"})
    )

    with pytest.raises(LlmClientError):
        await client.chat_simple([])

    assert client._client.chat.completions.create.await_count == 1
    sleep.assert_not_awaited()
