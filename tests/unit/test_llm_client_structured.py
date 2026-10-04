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

    assert result.value == _Topic(title="VPN", start_message_id=42)
    assert len(result.usages) == 1


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

    assert result.value.title == "x"
    assert client._client.chat.completions.create.await_count == 2
    assert [usage.attempt_number for usage in result.usages] == [1, 2]


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

    result = await client.chat_structured(messages=[{"role": "user", "content": "go"}], response_model=_Topic)
    assert len(result.usages) == 1


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

    assert result.value.start_message_id == 1
    assert client._client.chat.completions.create.await_count == 2


@pytest.mark.asyncio
async def test_chat_structured_returns_per_call_usage_and_actual_model() -> None:
    config = LlmConfig(api_key="test-key", model="main-model", summary_model="gpt-4o-mini")
    client = LlmClient(config)
    response = _response_with_content(json.dumps({"title": "x", "start_message_id": 1}))
    response.usage = MagicMock(prompt_tokens=123, completion_tokens=45)
    client._client.chat.completions.create = AsyncMock(return_value=response)

    result = await client.chat_structured(messages=[{"role": "user", "content": "go"}], response_model=_Topic)
    usage = result.usages[0]
    assert (usage.prompt_tokens, usage.completion_tokens) == (123, 45)
    assert usage.model == "gpt-4o-mini"
    assert usage.estimated_cost_usd is not None


@pytest.mark.asyncio
async def test_structured_retry_returns_both_billable_attempts() -> None:
    client = LlmClient(LlmConfig(api_key="test-key", model="test-model", summary_model="gpt-4o-mini"))
    first = _response_with_content("not json")
    first.usage = MagicMock(prompt_tokens=100, completion_tokens=20, total_tokens=120)
    second = _response_with_content(json.dumps({"title": "ok", "start_message_id": 1}))
    second.usage = MagicMock(prompt_tokens=120, completion_tokens=30, total_tokens=150)
    client._client.chat.completions.create = AsyncMock(side_effect=[first, second])

    result = await client.chat_structured(messages=[], response_model=_Topic)

    assert [(u.prompt_tokens, u.completion_tokens) for u in result.usages] == [(100, 20), (120, 30)]
    assert sum(u.prompt_tokens for u in result.usages) == 220
    assert sum(u.completion_tokens for u in result.usages) == 50
    assert result.usages[0].status == "validation_failed"
    assert result.usages[1].status == "succeeded"
    assert result.usages[0].request_id == result.usages[1].request_id
    assert result.usages[0].call_id != result.usages[1].call_id
    assert sum(u.estimated_cost_usd for u in result.usages) > 0


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
