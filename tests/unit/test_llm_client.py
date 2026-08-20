"""Regression test for finding #37 in docs/STT_LLM_AUDIT_TODO.md:

chat_with_tools (the highest-fan-out call, up to 8x per admin query) had no
max_tokens cap, unlike chat_simple and summarize -- a single one of the up
to 8 rounds could produce an unbounded-length completion, limited only by
the provider's model-level ceiling."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from selara.infrastructure.llm.client import LlmClient, LlmConfig


@pytest.mark.asyncio
async def test_chat_with_tools_forwards_max_tokens_to_the_api_call():
    config = LlmConfig(api_key="test-key", model="test-model")
    client = LlmClient(config)
    client._client.chat.completions.create = AsyncMock(return_value=MagicMock())

    await client.chat_with_tools(messages=[{"role": "user", "content": "hi"}], tools=[], max_tokens=4000)

    kwargs = client._client.chat.completions.create.await_args.kwargs
    assert kwargs["max_tokens"] == 4000


@pytest.mark.asyncio
async def test_chat_with_tools_defaults_to_a_bounded_max_tokens():
    config = LlmConfig(api_key="test-key", model="test-model")
    client = LlmClient(config)
    client._client.chat.completions.create = AsyncMock(return_value=MagicMock())

    await client.chat_with_tools(messages=[{"role": "user", "content": "hi"}], tools=[])

    kwargs = client._client.chat.completions.create.await_args.kwargs
    assert kwargs["max_tokens"] is not None
    assert kwargs["max_tokens"] > 0


# --- #10: token usage observability ---


@pytest.mark.asyncio
async def test_chat_with_tools_logs_token_usage(caplog):
    import logging
    config = LlmConfig(api_key="test-key", model="test-model")
    client = LlmClient(config)
    response = MagicMock(usage=MagicMock(prompt_tokens=100, completion_tokens=20, total_tokens=120))
    client._client.chat.completions.create = AsyncMock(return_value=response)

    with caplog.at_level(logging.INFO, logger="selara.infrastructure.llm.client"):
        await client.chat_with_tools(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert any("120" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_chat_simple_logs_token_usage(caplog):
    import logging
    config = LlmConfig(api_key="test-key", model="test-model")
    client = LlmClient(config)
    message = MagicMock(content="ok")
    choice = MagicMock(message=message)
    response = MagicMock(choices=[choice], usage=MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15))
    client._client.chat.completions.create = AsyncMock(return_value=response)

    with caplog.at_level(logging.INFO, logger="selara.infrastructure.llm.client"):
        await client.chat_simple([{"role": "user", "content": "hi"}])

    assert any("15" in record.message for record in caplog.records)
