"""Model calls of AI pets: one short line per talk and occasional neutral dialogue notes."""

from __future__ import annotations

import json
import logging
from typing import Sequence

from selara.application.ai_pets.dialogue import (
    EXTRACTION_PROMPT,
    MAX_REPLY_TOKENS,
    MAX_TALK_TEXT_LEN,
    DialogueTurn,
    parse_notes,
)
from selara.infrastructure.llm.client import LlmAccountingContext, LlmClient, LlmClientError

log = logging.getLogger(__name__)

MAX_REPLY_CHARS = 600
MAX_TOKENS_EXTRACTION = 200


async def generate_pet_reply(
    *, llm_client: LlmClient, messages: list[dict], accounting_context: LlmAccountingContext | None
) -> str:
    """No tools are offered: a pet can only speak, never act."""
    kwargs: dict = {"max_tokens": MAX_REPLY_TOKENS}
    if accounting_context is not None:
        kwargs["accounting_context"] = accounting_context
    result = await llm_client.chat_simple(messages, **kwargs)
    value = result.value if hasattr(result, "value") else result
    return (value or "").strip()[:MAX_REPLY_CHARS]


async def extract_notes(
    *,
    llm_client: LlmClient,
    turns: Sequence[DialogueTurn],
    accounting_context: LlmAccountingContext | None,
) -> list[str]:
    """Distil at most a couple of neutral notes; failures are logged and yield nothing."""
    if not turns:
        return []
    body = "\n".join(
        f"[{turn.speaker}]: {json.dumps(turn.content[:MAX_TALK_TEXT_LEN], ensure_ascii=False)}" for turn in turns
    )
    kwargs: dict = {"max_tokens": MAX_TOKENS_EXTRACTION}
    if accounting_context is not None:
        kwargs["accounting_context"] = accounting_context
    try:
        result = await llm_client.summarize(
            [{"role": "system", "content": EXTRACTION_PROMPT}, {"role": "user", "content": body}], **kwargs
        )
    except LlmClientError as exc:
        log.warning("ai pet note extraction failed: %s", exc.message)
        return []
    value = result.value if hasattr(result, "value") else result
    return parse_notes(value or "")
