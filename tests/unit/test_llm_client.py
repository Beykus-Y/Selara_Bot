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
