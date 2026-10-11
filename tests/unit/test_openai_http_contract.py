"""Exercise the installed SDK and its default transport over real loopback HTTP."""
from __future__ import annotations

import asyncio
import json
from collections import deque
from decimal import Decimal

import pytest
import pytest_asyncio

from selara.infrastructure.llm.client import LlmAccountingContext, LlmClient, LlmClientError, LlmConfig
from selara.infrastructure.stt.client import SttClient, SttClientError, SttConfig


@pytest_asyncio.fixture
async def provider_http():
    requests = []
    replies = deque()
    workers = set()

    async def handle(reader, writer):
        task = asyncio.current_task()
        workers.add(task)
        try:
            header = (await reader.readuntil(b"\r\n\r\n")).decode("latin1")
            lines = header.split("\r\n")
            headers = {key.lower(): value.strip() for key, value in
                       (line.split(":", 1) for line in lines[1:] if ":" in line)}
            body = await reader.readexactly(int(headers.get("content-length", "0")))
            requests.append((lines[0], headers, body))
            status, response, delay = replies.popleft()
            await asyncio.sleep(delay)
            encoded = json.dumps(response).encode()
            writer.write(
                f"HTTP/1.1 {status} Result\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(encoded)}\r\nConnection: close\r\nRetry-After: 0\r\n\r\n".encode()
                + encoded
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            workers.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/v1", requests, replies
    finally:
        server.close()
        await server.wait_closed()
        for worker in list(workers):
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)


def completion(content="привет", *, tool_calls=None, usage=True):
    response = {
        "id": "chat-fixture", "object": "chat.completion", "created": 1, "model": "gpt-4o-mini",
        "choices": [{"index": 0, "finish_reason": "tool_calls" if tool_calls else "stop",
                     "message": {"role": "assistant", "content": content, "tool_calls": tool_calls}}],
    }
    if usage:
        response["usage"] = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.0123}
    return response


@pytest.mark.asyncio
async def test_real_completion_serialization_usage_and_function_call(provider_http):
    url, requests, replies = provider_http
    replies.extend([(200, completion(), 0), (200, completion(None, tool_calls=[{
        "id": "call-1", "type": "function", "function": {"name": "read_skill", "arguments": '{"name":"artifacts"}'},
    }]), 0)])
    client = LlmClient(LlmConfig(api_key="fixture-key", model="gpt-4o-mini", base_url=url,
                                 include_usage_cost=True))
    try:
        result = await client.chat_simple([{"role": "user", "content": "Привет"}], max_tokens=24)
        assert result.value == "привет"
        assert result.usages[0].total_tokens == 18
        assert result.usages[0].provider_cost_usd == Decimal("0.012300000")
        tools = [{"type": "function", "function": {"name": "read_skill", "parameters": {"type": "object"}}}]
        response = await client.chat_with_tools([{"role": "user", "content": "Draw"}], tools)
        message = response.value.choices[0].message
        assert message.content is None
        assert message.tool_calls[0].function.name == "read_skill"
        assert json.loads(message.tool_calls[0].function.arguments) == {"name": "artifacts"}
        sent = [json.loads(r[2]) for r in requests]
        assert requests[0][0] == "POST /v1/chat/completions HTTP/1.1"
        assert sent[0]["max_tokens"] == 24
        assert sent[0]["usage"] == {"include": True}
        assert sent[1]["tools"] == tools and sent[1]["tool_choice"] == "auto"
    finally:
        await client._client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, ""])
async def test_empty_content_without_usage_is_accounted_as_unknown(provider_http, content):
    url, requests, replies = provider_http
    replies.append((200, completion(content, usage=False), 0))
    client = LlmClient(LlmConfig(api_key="fixture-key", model="gpt-4o-mini", base_url=url))
    try:
        result = await client.chat_simple([])
        assert result.value == ""
        assert result.usages[0].pricing_status == "unknown"
        assert result.usages[0].total_tokens is None
        assert len(requests) == 1
    finally:
        await client._client.close()


@pytest.mark.asyncio
async def test_real_status_retry_records_each_http_attempt(provider_http):
    url, requests, replies = provider_http
    replies.extend([(429, {"error": {"message": "rate limited"}}, 0), (200, completion(), 0)])
    recorded = []

    async def record(context, usage):
        recorded.append(usage)

    client = LlmClient(LlmConfig(api_key="fixture-key", model="gpt-4o-mini", base_url=url), usage_recorder=record)
    try:
        result = await client.chat_simple([], accounting_context=LlmAccountingContext(1, "llm_admin", "round", -100))
        assert len(requests) == len(recorded) == 2
        assert tuple(recorded) == result.usages
        assert [u.attempt_number for u in recorded] == [1, 2]
        assert [u.status for u in recorded] == ["failed", "succeeded"]
        assert recorded[0].error_category == "api_status"
        assert len({u.request_id for u in recorded}) == 1
    finally:
        await client._client.close()


@pytest.mark.asyncio
async def test_real_timeout_is_bounded_and_recorded(provider_http):
    url, requests, replies = provider_http
    replies.extend([(200, completion(), 0.25)] * 3)
    client = LlmClient(LlmConfig(api_key="fixture-key", model="gpt-4o-mini", base_url=url, timeout_seconds=0.05))
    try:
        with pytest.raises(LlmClientError) as error:
            await client.chat_simple([])
        assert len(requests) == len(error.value.usages) == 3
        assert all(u.error_category == "timeout" for u in error.value.usages)
    finally:
        await client._client.close()


@pytest.mark.asyncio
async def test_stt_real_multipart_and_language_fallback(provider_http):
    url, requests, replies = provider_http
    replies.extend([(200, {"text": " привет ", "language": ""}, 0),
                    (200, {"text": " привет ", "language": "russian"}, 0)])
    client = SttClient(SttConfig(api_key="fixture-key", model="whisper-1", base_url=url))
    try:
        assert await client.transcribe(b"fixture-audio", filename="voice.ogg") == "привет"
        assert len(requests) == 2
        for line, headers, body in requests:
            assert line == "POST /v1/audio/transcriptions HTTP/1.1"
            assert headers["content-type"].startswith("multipart/form-data; boundary=")
            assert b'filename="voice.ogg"' in body and b"fixture-audio" in body
            assert b"verbose_json" in body and b"whisper-1" in body
        assert b'name="language"' not in requests[0][2]
        assert b'name="language"' in requests[1][2] and b"\r\nru\r\n" in requests[1][2]
    finally:
        await client._client.close()


@pytest.mark.asyncio
async def test_stt_api_status_has_no_hidden_sdk_retries(provider_http):
    url, requests, replies = provider_http
    replies.extend([(500, {"error": {"message": "fixture unavailable"}}, 0)] * 3)
    client = SttClient(SttConfig(api_key="fixture-key", model="whisper-1", base_url=url))
    try:
        with pytest.raises(SttClientError, match="fixture unavailable"):
            await client.transcribe_with_retry(b"fixture-audio", retry_delay=0)
        assert len(requests) == 1
    finally:
        await client._client.close()


@pytest.mark.asyncio
async def test_stt_timeout_uses_only_the_requested_application_retries(provider_http):
    url, requests, replies = provider_http
    replies.extend([(200, {"text": "hello", "language": "english"}, 0.25)] * 2)
    client = SttClient(SttConfig(api_key="fixture-key", model="whisper-1", base_url=url, timeout_seconds=0.05))
    try:
        with pytest.raises(SttClientError, match="не ответил вовремя"):
            await client.transcribe_with_retry(b"fixture-audio", retries=1, retry_delay=0)
        assert len(requests) == 2
    finally:
        await client._client.close()


@pytest.mark.asyncio
async def test_llm_permanent_error_is_not_retried(provider_http):
    url, requests, replies = provider_http
    replies.append((400, {"error": {"message": "unsupported model"}}, 0))
    client = LlmClient(LlmConfig(api_key="fixture-key", model="gpt-4o-mini", base_url=url))
    try:
        with pytest.raises(LlmClientError, match="unsupported model") as error:
            await client.chat_simple([])
        assert len(requests) == len(error.value.usages) == 1
        assert error.value.usages[0].error_category == "api_status"
    finally:
        await client._client.close()
