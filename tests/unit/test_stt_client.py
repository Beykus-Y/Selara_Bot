"""Regression tests for finding #25 (STT half) in docs/STT_LLM_AUDIT_TODO.md:

transcribe_with_retry decided whether to retry by substring-matching the
already-*translated Russian* error message ("не ответил", "подключиться")
instead of the exception type -- any future edit to those message strings
would silently break retry behavior with no test to catch it.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError

from selara.infrastructure.stt.client import SttClient, SttClientError, SttConfig


def _config() -> SttConfig:
    return SttConfig(api_key="test-key", model="whisper-1")


@pytest.mark.asyncio
async def test_retries_on_timeout_error_regardless_of_message_text():
    client = SttClient(_config())
    request = httpx.Request("POST", "https://example.test/v1/audio/transcriptions")

    attempts = 0

    async def fake_create(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise APITimeoutError(request=request)
        return "привет"

    client._client.audio.transcriptions.create = AsyncMock(side_effect=fake_create)

    result = await client.transcribe_with_retry(b"audio-bytes", retries=2, retry_delay=0)
    assert result == "привет"
    assert attempts == 3


@pytest.mark.asyncio
async def test_retries_on_connection_error_regardless_of_message_text():
    client = SttClient(_config())
    request = httpx.Request("POST", "https://example.test/v1/audio/transcriptions")

    attempts = 0

    async def fake_create(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise APIConnectionError(request=request)
        return "привет"

    client._client.audio.transcriptions.create = AsyncMock(side_effect=fake_create)

    result = await client.transcribe_with_retry(b"audio-bytes", retries=2, retry_delay=0)
    assert result == "привет"
    assert attempts == 2


@pytest.mark.asyncio
async def test_does_not_retry_on_api_status_error_even_if_extracted_message_contains_retry_keywords():
    """The old implementation decided retryability by substring-matching the
    translated message text ("не ответил"/"подключиться"). This proves the
    decision is now based on exception type: a permanent 500 error must never
    be retried, even when the provider's own error text happens to contain
    one of those old trigger keywords (a realistic case: a provider-side
    message like "сервис временно не ответил, попробуйте позже" for a 500)."""
    client = SttClient(_config())
    response = httpx.Response(
        status_code=500,
        request=httpx.Request("POST", "https://example.test/v1/audio/transcriptions"),
        json={"error": {"message": "сервис временно не ответил, попробуйте позже"}},
    )

    attempts = 0

    async def fake_create(**kwargs):
        nonlocal attempts
        attempts += 1
        raise APIStatusError("upstream 500", response=response, body=None)

    client._client.audio.transcriptions.create = AsyncMock(side_effect=fake_create)

    with pytest.raises(SttClientError):
        await client.transcribe_with_retry(b"audio-bytes", retries=2, retry_delay=0)

    assert attempts == 1, "APIStatusError must never be retried, regardless of its message text"


@pytest.mark.asyncio
async def test_gives_up_after_exhausting_retries_on_persistent_timeout():
    client = SttClient(_config())
    request = httpx.Request("POST", "https://example.test/v1/audio/transcriptions")

    async def fake_create(**kwargs):
        raise APITimeoutError(request=request)

    client._client.audio.transcriptions.create = AsyncMock(side_effect=fake_create)

    with pytest.raises(SttClientError):
        await client.transcribe_with_retry(b"audio-bytes", retries=2, retry_delay=0)

    assert client._client.audio.transcriptions.create.await_count == 3
