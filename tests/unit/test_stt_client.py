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
        return SimpleNamespaceLike(text="привет", language="russian")

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
        return SimpleNamespaceLike(text="привет", language="russian")

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


# --- #8: STT auto-detects language instead of hardcoded ru ---


@pytest.mark.asyncio
async def test_transcribe_does_not_force_language_on_first_attempt():
    """Auto-detection: the first API call must not pin language=ru (or any
    fixed language) -- Whisper auto-detects when language is omitted."""
    client = SttClient(_config())
    response = SimpleNamespaceLike(text="hello world", language="english")
    client._client.audio.transcriptions.create = AsyncMock(return_value=response)

    result = await client.transcribe(b"audio-bytes")

    assert result == "hello world"
    kwargs = client._client.audio.transcriptions.create.await_args.kwargs
    assert kwargs.get("language") is None
    assert client._client.audio.transcriptions.create.await_count == 1


@pytest.mark.asyncio
async def test_transcribe_uses_confidently_detected_non_russian_language():
    client = SttClient(_config())
    response = SimpleNamespaceLike(text="bonjour le monde", language="french")
    client._client.audio.transcriptions.create = AsyncMock(return_value=response)

    result = await client.transcribe(b"audio-bytes")

    assert result == "bonjour le monde"
    assert client._client.audio.transcriptions.create.await_count == 1


@pytest.mark.asyncio
async def test_transcribe_falls_back_to_configured_language_when_text_present_but_language_undetected():
    """Retry only makes sense when the model actually transcribed something
    but couldn't identify the language -- forcing a different language on
    genuinely empty output (see the silence test below) can't produce audio
    content that isn't there, so it must not trigger a second billed call."""
    client = SttClient(_config())
    responses = [
        SimpleNamespaceLike(text="привет как дела", language=""),
        SimpleNamespaceLike(text="привет как дела", language="russian"),
    ]

    async def fake_create(**kwargs):
        return responses.pop(0)

    client._client.audio.transcriptions.create = AsyncMock(side_effect=fake_create)

    result = await client.transcribe(b"audio-bytes")

    assert result == "привет как дела"
    assert client._client.audio.transcriptions.create.await_count == 2
    second_call_kwargs = client._client.audio.transcriptions.create.await_args.kwargs
    assert second_call_kwargs.get("language") == "ru"


@pytest.mark.asyncio
async def test_transcribe_does_not_retry_on_genuinely_silent_audio():
    """Empty text + empty language is almost always real silence/noise, not
    a fixable detection failure -- retrying with a forced language would
    just double the billed cost for no benefit (found in review)."""
    client = SttClient(_config())
    response = SimpleNamespaceLike(text="", language="")
    client._client.audio.transcriptions.create = AsyncMock(return_value=response)

    with pytest.raises(SttClientError):
        await client.transcribe(b"audio-bytes")

    assert client._client.audio.transcriptions.create.await_count == 1


class SimpleNamespaceLike:
    def __init__(self, *, text: str, language: str) -> None:
        self.text = text
        self.language = language
