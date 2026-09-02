"""Tests for LlmClient.chat_structured (docs/DAILY_SUMMARY_TODO.md): schema-validated
JSON output for the daily summary pipeline's non-tool stages (topic extraction, merge).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from selara.infrastructure.llm.client import LlmClient, LlmClientError, LlmConfig


class _Topic(BaseModel):
    title: str
    start_message_id: int


def _response_with_content(content: str) -> MagicMock:
    message = MagicMock(content=content)
    choice = MagicMock(message=message)
    return MagicMock(choices=[choice], usage=None)


@pytest.mark.asyncio
async def test_chat_structured_parses_and_validates_valid_json() -> None:
    config = LlmConfig(api_key="test-key", model="test-model")
    client = LlmClient(config)
    client._client.chat.completions.create = AsyncMock(
        return_value=_response_with_content(json.dumps({"title": "VPN", "start_message_id": 42}))
    )

    result = await client.chat_structured(messages=[{"role": "user", "content": "go"}], response_model=_Topic)

    assert result == _Topic(title="VPN", start_message_id=42)


@pytest.mark.asyncio
async def test_chat_structured_uses_summary_model_not_main_model() -> None:
    config = LlmConfig(api_key="test-key", model="main-model", summary_model="cheap-model")
    client = LlmClient(config)
    client._client.chat.completions.create = AsyncMock(
        return_value=_response_with_content(json.dumps({"title": "x", "start_message_id": 1}))
    )

    await client.chat_structured(messages=[{"role": "user", "content": "go"}], response_model=_Topic)

    kwargs = client._client.chat.completions.create.await_args.kwargs
    assert kwargs["model"] == "cheap-model"


@pytest.mark.asyncio
async def test_chat_structured_without_native_support_sends_no_response_format() -> None:
    config = LlmConfig(api_key="test-key", model="test-model", supports_structured_output=False)
    client = LlmClient(config)
    client._client.chat.completions.create = AsyncMock(
        return_value=_response_with_content(json.dumps({"title": "x", "start_message_id": 1}))
    )

    await client.chat_structured(messages=[{"role": "user", "content": "go"}], response_model=_Topic)

    kwargs = client._client.chat.completions.create.await_args.kwargs
    assert "response_format" not in kwargs


@pytest.mark.asyncio
async def test_chat_structured_with_native_support_sends_json_schema() -> None:
    config = LlmConfig(api_key="test-key", model="test-model", supports_structured_output=True)
    client = LlmClient(config)
    client._client.chat.completions.create = AsyncMock(
        return_value=_response_with_content(json.dumps({"title": "x", "start_message_id": 1}))
    )

    await client.chat_structured(messages=[{"role": "user", "content": "go"}], response_model=_Topic)

    kwargs = client._client.chat.completions.create.await_args.kwargs
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["response_format"]["json_schema"]["name"] == "_Topic"


@pytest.mark.asyncio
async def test_chat_structured_retries_once_on_invalid_json_then_succeeds() -> None:
    config = LlmConfig(api_key="test-key", model="test-model")
    client = LlmClient(config)
    client._client.chat.completions.create = AsyncMock(
        side_effect=[
            _response_with_content("this is not json"),
            _response_with_content(json.dumps({"title": "x", "start_message_id": 1})),
        ]
    )

    result = await client.chat_structured(messages=[{"role": "user", "content": "go"}], response_model=_Topic)

    assert result.title == "x"
    assert client._client.chat.completions.create.await_count == 2
    assert client.last_retry_count == 1


@pytest.mark.asyncio
async def test_chat_structured_retry_message_embeds_the_actual_schema() -> None:
    # Production bug: a prompt that says "return a list" without ever saying
    # "wrapped in an object under this key" reliably gets a bare JSON array back.
    # The corrective round must hand the model the real schema, not just describe
    # the failure in prose, so it has a concrete shape to conform to.
    config = LlmConfig(api_key="test-key", model="test-model")
    client = LlmClient(config)
    client._client.chat.completions.create = AsyncMock(
        side_effect=[
            _response_with_content(json.dumps([{"title": "x", "start_message_id": 1}])),  # bare list, wrong shape
            _response_with_content(json.dumps({"title": "x", "start_message_id": 1})),
        ]
    )

    await client.chat_structured(messages=[{"role": "user", "content": "go"}], response_model=_Topic)

    second_call_messages = client._client.chat.completions.create.await_args_list[1].kwargs["messages"]
    correction_message = second_call_messages[-1]["content"]
    assert "start_message_id" in correction_message
    assert "properties" in correction_message  # a real JSON schema, not just prose


@pytest.mark.asyncio
async def test_chat_structured_records_zero_retries_on_first_try_success() -> None:
    config = LlmConfig(api_key="test-key", model="test-model")
    client = LlmClient(config)
    client._client.chat.completions.create = AsyncMock(
        return_value=_response_with_content(json.dumps({"title": "x", "start_message_id": 1}))
    )

    await client.chat_structured(messages=[{"role": "user", "content": "go"}], response_model=_Topic)

    assert client.last_retry_count == 0


@pytest.mark.asyncio
async def test_chat_structured_retries_once_on_schema_mismatch_then_succeeds() -> None:
    config = LlmConfig(api_key="test-key", model="test-model")
    client = LlmClient(config)
    client._client.chat.completions.create = AsyncMock(
        side_effect=[
            _response_with_content(json.dumps({"title": "x"})),  # missing start_message_id
            _response_with_content(json.dumps({"title": "x", "start_message_id": 1})),
        ]
    )

    result = await client.chat_structured(messages=[{"role": "user", "content": "go"}], response_model=_Topic)

    assert result.start_message_id == 1
    assert client._client.chat.completions.create.await_count == 2


@pytest.mark.asyncio
async def test_chat_structured_records_last_usage_for_cost_accounting() -> None:
    # the daily summary pipeline reads client.last_usage/last_model right after each
    # call to log per-stage cost -- chat_structured's return value stays the parsed
    # model, so usage has to travel out via this side-channel instead.
    config = LlmConfig(api_key="test-key", model="main-model", summary_model="cheap-model")
    client = LlmClient(config)
    response = _response_with_content(json.dumps({"title": "x", "start_message_id": 1}))
    response.usage = MagicMock(prompt_tokens=123, completion_tokens=45)
    client._client.chat.completions.create = AsyncMock(return_value=response)

    await client.chat_structured(messages=[{"role": "user", "content": "go"}], response_model=_Topic)

    assert client.last_usage == (123, 45)
    assert client.last_model == "cheap-model"


@pytest.mark.asyncio
async def test_chat_structured_gives_up_after_second_failure() -> None:
    config = LlmConfig(api_key="test-key", model="test-model")
    client = LlmClient(config)
    client._client.chat.completions.create = AsyncMock(
        side_effect=[
            _response_with_content("nope"),
            _response_with_content("still nope"),
        ]
    )

    with pytest.raises(LlmClientError):
        await client.chat_structured(messages=[{"role": "user", "content": "go"}], response_model=_Topic)

    assert client._client.chat.completions.create.await_count == 2
