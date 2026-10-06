"""Read-only tools of member mode (a member calling Selara by a chat call name).

Members get a fixed allow-list of reading tools from the admin registry; nothing
that moderates or changes the chat is advertised or executed. Reading the chat's
recent messages is a separate tool that exists only while the admins ticked
``member_history_access``; it reads the message archive, never the admin
assistant's own context.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from selara.domain.entities import ChatSnapshot
from selara.infrastructure.db.models import MessageArchiveModel, UserModel
from selara.infrastructure.llm.tools import (
    _TOOL_REGISTRY,
    _UNTRUSTED_MARKER,
    ToolCall,
    ToolResult,
    _err,
    _ok,
    _untrusted,
    execute_tool,
)

MEMBER_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "get_top",
        "get_chat_stats",
        "get_current_time",
        "lookup_glossary",
        "search_glossary",
        "list_bot_docs",
        "read_bot_doc",
    }
)
HISTORY_TOOL_NAME = "get_recent_chat_messages"
MAX_HISTORY_HOURS = 24
MAX_HISTORY_MESSAGES = 80
_MAX_MESSAGE_CHARS = 300

_HISTORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": HISTORY_TOOL_NAME,
        "description": (
            "Последние сообщения этого чата (текст и подписи) за несколько часов, не больше суток. "
            "Используй, когда вопрос касается недавнего разговора в чате."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "minimum": 1, "maximum": MAX_HISTORY_HOURS, "default": 3},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_HISTORY_MESSAGES, "default": 40},
            },
        },
    },
}


def member_tool_names(*, history_access: bool) -> frozenset[str]:
    return MEMBER_TOOL_NAMES | ({HISTORY_TOOL_NAME} if history_access else frozenset())


def member_tool_definitions(*, history_access: bool) -> list[dict]:
    definitions = [_TOOL_REGISTRY[name].schema for name in sorted(MEMBER_TOOL_NAMES) if name in _TOOL_REGISTRY]
    if history_access:
        definitions.append(_HISTORY_SCHEMA)
    return definitions


async def execute_member_tool(
    call: ToolCall,
    *,
    history_access: bool,
    chat_snapshot: ChatSnapshot,
    db_session: AsyncSession,
    **ctx: Any,
) -> ToolResult:
    """The allow-list is enforced here, not only in what the model was offered."""
    if call.name not in member_tool_names(history_access=history_access):
        return _err(call.call_id, call.name, "Этот инструмент недоступен в разговоре с участниками.")
    if call.name == HISTORY_TOOL_NAME:
        try:
            return await _recent_chat_messages(call, chat_snapshot=chat_snapshot, db_session=db_session)
        except Exception as exc:  # same contract as execute_tool: a failed tool is a result, not a crash
            return ToolResult(
                call_id=call.call_id,
                name=call.name,
                result_text=json.dumps({"error": str(exc)}, ensure_ascii=False),
                action_description=f"Ошибка инструмента {call.name}",
                success=False,
            )
    return _without_identifiers(await execute_tool(call, chat_snapshot=chat_snapshot, **ctx))


_PRIVATE_KEYS = frozenset({"user_id", "telegram_user_id"})


def _strip_private(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_private(item) for key, item in value.items() if key not in _PRIVATE_KEYS}
    if isinstance(value, list):
        return [_strip_private(item) for item in value]
    return value


def _without_identifiers(result: ToolResult) -> ToolResult:
    """Members get names, never numeric Telegram ids; the admin tools return them for moderation."""
    try:
        data = json.loads(result.result_text)
    except (TypeError, ValueError):
        return result
    return replace(result, result_text=json.dumps(_strip_private(data), ensure_ascii=False))


def _bounded_int(value: Any, *, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


async def _recent_chat_messages(
    call: ToolCall, *, chat_snapshot: ChatSnapshot, db_session: AsyncSession
) -> ToolResult:
    hours = _bounded_int(call.arguments.get("hours"), default=3, low=1, high=MAX_HISTORY_HOURS)
    limit = _bounded_int(call.arguments.get("limit"), default=40, low=1, high=MAX_HISTORY_MESSAGES)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    # Edits are stored as extra snapshots of the same message: the newest one is what the chat shows now.
    newest = (
        select(
            MessageArchiveModel.telegram_message_id.label("message_id"),
            func.max(MessageArchiveModel.snapshot_at).label("snapshot_at"),
        )
        .where(
            MessageArchiveModel.chat_id == chat_snapshot.telegram_chat_id,
            MessageArchiveModel.sent_at >= since,
        )
        .group_by(MessageArchiveModel.telegram_message_id)
        .subquery()
    )
    rows = (
        await db_session.execute(
            select(
                MessageArchiveModel.telegram_message_id,
                MessageArchiveModel.text,
                MessageArchiveModel.caption,
                MessageArchiveModel.sent_at,
                UserModel.first_name,
                UserModel.username,
            )
            .join(
                newest,
                (newest.c.message_id == MessageArchiveModel.telegram_message_id)
                & (newest.c.snapshot_at == MessageArchiveModel.snapshot_at),
            )
            .join(UserModel, UserModel.telegram_user_id == MessageArchiveModel.user_id)
            .where(MessageArchiveModel.chat_id == chat_snapshot.telegram_chat_id)
            .order_by(MessageArchiveModel.sent_at.desc(), MessageArchiveModel.id.desc())
            .limit(limit * 2)
        )
    ).all()
    latest: dict[int, tuple] = {}
    for message_id, *rest in rows:
        latest.setdefault(message_id, tuple(rest))  # two snapshots with the same time: keep one
    picked = list(latest.values())[:limit]
    messages = []
    for text, caption, sent_at, first_name, username in reversed(picked):
        body = (text or caption or "").strip()
        if not body:
            continue
        messages.append(
            {
                "author": _untrusted(first_name or (f"@{username}" if username else "участник")),
                "sent_at": sent_at.isoformat() if sent_at else None,
                "text": _untrusted(body[:_MAX_MESSAGE_CHARS]),
            }
        )
    return _ok(
        call.call_id,
        call.name,
        {"data_notice": _UNTRUSTED_MARKER, "hours": hours, "messages": messages, "count": len(messages)},
        f"Последние сообщения чата за {hours} ч",
    )
