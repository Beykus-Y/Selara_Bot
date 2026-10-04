"""Regression test for finding #37 in docs/STT_LLM_AUDIT_TODO.md:

chat_with_tools (the highest-fan-out call, up to 8x per admin query) had no
max_tokens cap, unlike chat_simple and summarize -- a single one of the up
to 8 rounds could produce an unbounded-length completion, limited only by
the provider's model-level ceiling."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
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


def _response(content: str, *, model: str, prompt: int, completion: int):
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion),
    )


@pytest.mark.asyncio
async def test_shared_client_concurrent_calls_keep_usage_with_their_result():
    client = LlmClient(LlmConfig(api_key="test-key", model="gpt-4o-mini"))
    unrelated_call_finished = asyncio.Event()

    async def provider(**kwargs):
        content = kwargs["messages"][-1]["content"]
        if content == "request A":
            await unrelated_call_finished.wait()
            return _response("answer A", model="gpt-4o", prompt=111, completion=11)
        unrelated_call_finished.set()
        return _response("answer B", model="gpt-4o-mini", prompt=222, completion=22)

    client._client.chat.completions.create = AsyncMock(side_effect=provider)
    result_a, result_b = await asyncio.gather(
        client.chat_simple([{"role": "user", "content": "request A"}]),
        client.chat_simple([{"role": "user", "content": "request B"}]),
    )

    assert (result_a.value, result_a.usages[0].model, result_a.usages[0].prompt_tokens) == ("answer A", "gpt-4o", 111)
    assert (result_b.value, result_b.usages[0].model, result_b.usages[0].prompt_tokens) == (
        "answer B", "gpt-4o-mini", 222
    )


@pytest.mark.asyncio
async def test_structured_retry_interleaves_with_another_call_without_usage_leakage():
    from pydantic import BaseModel

    class Payload(BaseModel):
        value: int

    client = LlmClient(LlmConfig(api_key="test-key", model="gpt-4o-mini"))
    first_structured_call = asyncio.Event()
    other_call_finished = asyncio.Event()

    async def provider(**kwargs):
        messages = kwargs["messages"]
        first_content = messages[-1]["content"]
        if first_content == "structured request":
            first_structured_call.set()
            await other_call_finished.wait()
            return _response("invalid json", model="gpt-4o-mini", prompt=100, completion=20)
        if first_content == "unrelated request":
            await first_structured_call.wait()
            other_call_finished.set()
            return _response("ordinary answer", model="gpt-4o", prompt=700, completion=70)
        return _response('{"value": 9}', model="gpt-4o-mini", prompt=120, completion=30)

    client._client.chat.completions.create = AsyncMock(side_effect=provider)
    structured, unrelated = await asyncio.gather(
        client.chat_structured([{"role": "user", "content": "structured request"}], response_model=Payload),
        client.chat_simple([{"role": "user", "content": "unrelated request"}]),
    )

    assert structured.value == Payload(value=9)
    assert [(u.prompt_tokens, u.completion_tokens) for u in structured.usages] == [(100, 20), (120, 30)]
    assert unrelated.value == "ordinary answer"
    assert [(u.model, u.prompt_tokens, u.completion_tokens) for u in unrelated.usages] == [("gpt-4o", 700, 70)]


@pytest.mark.asyncio
async def test_provider_response_without_usage_has_unknown_cost_not_zero():
    client = LlmClient(LlmConfig(api_key="test-key", model="gpt-4o-mini"))
    client._client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(model="gpt-4o-mini", choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], usage=None)
    )

    result = await client.chat_simple([])

    assert result.usages[0].pricing_status == "unknown"
    assert result.usages[0].estimated_cost_usd is None
    assert result.usages[0].status == "succeeded"


@pytest.mark.asyncio
async def test_provider_returned_unknown_model_stays_unknown_even_when_configured_model_is_known():
    client = LlmClient(LlmConfig(api_key="test-key", model="gpt-4o-mini"))
    client._client.chat.completions.create = AsyncMock(
        return_value=_response("ok", model="vendor/new-model", prompt=100, completion=10)
    )

    result = await client.chat_simple([])

    assert result.usages[0].model == "vendor/new-model"
    assert result.usages[0].pricing_status == "unknown"
    assert result.usages[0].estimated_cost_usd is None


@pytest.mark.asyncio
async def test_timeout_returns_unknown_failed_attempt_without_inventing_zero_cost():
    import httpx
    from openai import APITimeoutError
    from selara.infrastructure.llm.client import LlmAccountingContext, LlmClientError

    recorded = []

    async def record(context, usage):
        recorded.append((context, usage))

    client = LlmClient(LlmConfig(api_key="test-key", model="gpt-4o-mini"), usage_recorder=record)
    client._client.chat.completions.create = AsyncMock(
        side_effect=APITimeoutError(request=httpx.Request("POST", "https://provider.invalid"))
    )

    with pytest.raises(LlmClientError) as caught:
        await client.chat_simple([], accounting_context=LlmAccountingContext(7, "llm_admin", "round", -100))

    assert caught.value.usages[0].error_category == "timeout"
    assert caught.value.usages[0].status == "failed"
    assert caught.value.usages[0].estimated_cost_usd is None
    assert len(recorded) == 1


@pytest.mark.asyncio
async def test_cancellation_waits_for_already_received_usage_to_persist():
    from selara.infrastructure.llm.client import LlmAccountingContext

    recorder_started = asyncio.Event()
    allow_persist = asyncio.Event()
    persisted = []

    async def record(context, usage):
        recorder_started.set()
        await allow_persist.wait()
        persisted.append(usage.call_id)

    client = LlmClient(LlmConfig(api_key="test-key", model="gpt-4o-mini"), usage_recorder=record)
    client._client.chat.completions.create = AsyncMock(
        return_value=_response("already received", model="gpt-4o-mini", prompt=10, completion=2)
    )
    task = asyncio.create_task(client.chat_simple(
        [], accounting_context=LlmAccountingContext(9, "llm_admin", "round", -100)
    ))
    await recorder_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    allow_persist.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(persisted) == 1
