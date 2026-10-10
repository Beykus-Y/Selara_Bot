#!/usr/bin/env python3
"""Opt-in billed LLM provider smoke for OpenAI SDK compatibility (#177).

Example (do not put secrets into command-line arguments):
    export LLM_API_KEY=... LLM_MODEL=...
    export LLM_BASE_URL=https://your-compatible-provider.example/v1
    python scripts/smoke_llm_sdk_provider.py --allow-billing

Requires two real provider API calls and incurs tokens/costs. Runs **only** when
invoked manually with --allow-billing; it is not part of CI or deployment.
Never print the API key, message payloads, URLs, or raw exception bodies.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from selara.infrastructure.llm.client import LlmClient, LlmConfig


async def _smoke() -> None:
    key = os.environ.get("LLM_API_KEY", "").strip()
    model = os.environ.get("LLM_MODEL", "").strip()
    if not key or not model:
        raise RuntimeError("LLM_API_KEY and LLM_MODEL must be set in the environment")

    client = LlmClient(
        LlmConfig(
            api_key=key,
            model=model,
            summary_model=model,
            base_url=os.environ.get("LLM_BASE_URL") or None,
            timeout_seconds=float(os.environ.get("LLM_TIMEOUT_SECONDS", "45")),
        )
    )
    try:
        plain = await client.chat_simple(
            [
                {"role": "system", "content": "Return a short plain-text health confirmation."},
                {"role": "user", "content": "Respond with the word OK."},
            ],
            max_tokens=256,
        )
        if not plain.value or not plain.value.strip():
            raise RuntimeError("provider returned empty text for a basic chat request")
        print("PASS: basic Chat Completions text response")

        tool = {
            "type": "function",
            "function": {
                "name": "healthcheck_ping",
                "description": "Report a simple health check. Invoke this now.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }
        tools_reply = await client.chat_with_tools(
            [
                {
                    "role": "system",
                    "content": (
                        "Use the provided healthcheck_ping function to complete the "
                        "user's request. Call the tool instead of answering in text."
                    ),
                },
                {"role": "user", "content": "Call healthcheck_ping now."},
            ],
            tools=[tool],
            max_tokens=256,
        )
        msg = tools_reply.value.choices[0].message
        calls = msg.tool_calls or []
        if not any(
            call.type == "function" and call.function.name == "healthcheck_ping"
            for call in calls
        ):
            raise RuntimeError("provider did not return the requested tool call")
        print("PASS: structured tool-call response parsed by Selara LlmClient")
        print("PASS: provider compatibility smoke (two billable API calls)")
    finally:
        # The SDK HTTP transport is intentionally owned by LlmClient; close it
        # on this short-lived diagnostic even after a failed call.
        await client._client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-billing", action="store_true",
        help="Explicitly allow two real token-billed API requests",
    )
    args = parser.parse_args()
    if not args.allow_billing:
        parser.error("Refusing to call provider without --allow-billing (costs API tokens)")
    try:
        asyncio.run(_smoke())
    except Exception as exc:
        # SDK / provider exceptions may contain private response data; don't
        # print message, request headers, URL, prompt or API key.
        print(f"FAIL: {type(exc).__name__} (details redacted)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
