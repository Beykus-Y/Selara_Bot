"""Regression tests for finding #25 (LLM half) in docs/STT_LLM_AUDIT_TODO.md:

chat_with_tools translates API errors to safe, informative user-facing text
via _extract_api_error; chat_simple/summarize instead raised the raw SDK
exception's str() -- low-stakes for summarize (never shown to a user), but
chat_simple backs the DM-summary path, so an admin could see a raw SDK
traceback fragment as an "error message"."""
from __future__ import annotations

import httpx
import pytest
from openai import APIStatusError

from selara.infrastructure.llm.client import LlmClient, LlmClientError, LlmConfig


def _config() -> LlmConfig:
    return LlmConfig(api_key="test-key", model="test-model")


def _status_error(status_code: int, body_message: str) -> APIStatusError:
    response = httpx.Response(
        status_code=status_code,
        request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
        json={"error": {"message": body_message}},
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
    from unittest.mock import AsyncMock
    return AsyncMock(side_effect=exc)
