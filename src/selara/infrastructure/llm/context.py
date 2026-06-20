from __future__ import annotations

import logging
from dataclasses import dataclass, field

from selara.infrastructure.db.llm_repository import LlmRepository
from selara.infrastructure.llm.client import LlmClient, LlmClientError
from selara.infrastructure.llm.prompts import CONTEXT_COMPRESSION_SYSTEM_PROMPT, MAX_TOKENS_COMPRESSION

log = logging.getLogger(__name__)


@dataclass
class LoadedContext:
    messages: list[dict] = field(default_factory=list)


async def load_context(*, chat_id: int, llm_repo: LlmRepository) -> LoadedContext:
    latest_summary = await llm_repo.get_latest_summary(chat_id=chat_id)
    recent_msgs = await llm_repo.get_uncompressed_context_messages(chat_id=chat_id)

    messages: list[dict] = []
    if latest_summary:
        messages.append({
            "role": "system",
            "content": (
                f"[Краткое содержание предыдущего контекста до {latest_summary.period_end.isoformat()}]\n"
                f"{latest_summary.content}"
            ),
        })
    for msg in recent_msgs:
        entry: dict = {"role": msg.role, "content": msg.content}
        if msg.tool_call_id:
            entry["tool_call_id"] = msg.tool_call_id
        messages.append(entry)

    return LoadedContext(messages=messages)


async def save_interaction(
    *,
    chat_id: int,
    admin_user_id: int,
    user_query_content: str,
    assistant_response: str,
    tool_messages: list[dict],
    llm_repo: LlmRepository,
    is_context: bool,
) -> None:
    await llm_repo.add_context_message(
        chat_id=chat_id,
        role="user",
        content=user_query_content,
        is_context=is_context,
        admin_user_id=admin_user_id,
    )
    # Tool messages сохраняем только как лог (is_context=False) — они не валидны
    # как контекст без предшествующего assistant+tool_calls поворота.
    for tm in tool_messages:
        await llm_repo.add_context_message(
            chat_id=chat_id,
            role="tool",
            content=tm.get("content", ""),
            is_context=False,
            admin_user_id=admin_user_id,
            tool_call_id=tm.get("tool_call_id"),
        )
    if assistant_response:
        await llm_repo.add_context_message(
            chat_id=chat_id,
            role="assistant",
            content=assistant_response,
            is_context=is_context,
            admin_user_id=admin_user_id,
        )


async def maybe_compress(
    *,
    chat_id: int,
    threshold: int,
    llm_repo: LlmRepository,
    llm_client: LlmClient,
) -> bool:
    count = await llm_repo.count_uncompressed_context_messages(chat_id=chat_id)
    if count < threshold:
        return False

    msgs = await llm_repo.get_uncompressed_context_messages(chat_id=chat_id, limit=threshold)
    if len(msgs) < threshold:
        return False

    period_start = msgs[0].created_at
    period_end = msgs[-1].created_at

    compression_prompt = [
        {"role": "system", "content": CONTEXT_COMPRESSION_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(f"[{m.role}]: {m.content}" for m in msgs)},
    ]

    try:
        summary_text = await llm_client.summarize(compression_prompt, max_tokens=MAX_TOKENS_COMPRESSION)
    except LlmClientError as exc:
        log.warning("llm context compression failed: %s", exc.message)
        return False

    await llm_repo.add_summary(
        chat_id=chat_id,
        content=summary_text,
        period_start=period_start,
        period_end=period_end,
        messages_count=len(msgs),
        level=1,
    )
    await llm_repo.mark_messages_compressed(message_ids=[m.id for m in msgs])
    return True
