"""Read-only tools for the daily summary "analyst" stage (LLM #3).

A SEPARATE registry from `infrastructure/llm/tools.py` on purpose (see
docs/DAILY_SUMMARY_TODO.md) -- the admin assistant's tools carry write actions
(ban/warn/grant_persona/...) and a much broader context; the analyst only ever
needs 4 narrow read-only lookups scoped to one chat and one 24h window.

None of the 4 tool schemas below accept a chat_id parameter at all -- the chat is
fixed by `DailySummaryToolContext.scope`, never by model input, so there is no
"wrong chat_id" for a compromised/confused prompt to even attempt. Row counts are
clamped by `tool_limits.clamp_row_limit`, and any requested time window is clamped
into the run's own window by `tool_limits.clamp_window_to_scope` -- a tool call can
never read outside the day being summarized.

Every returned message's author is already replaced by its per-run display token
(`DailySummaryToolContext.author_tokens` -- see `application/daily_summary/sanitize.py`)
before it reaches the model, and known aliases of departed members
(username/persona/display name) are stripped from the text/transcript via
`redact_known_aliases`. This is the same guarantee the sanitized segment input for
LLM #1 gets -- the analyst must never see a live path to a departed member's name.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from selara.application.daily_summary.participants import ChatMemberInfo
from selara.application.daily_summary.sanitize import redact_known_aliases
from selara.application.daily_summary.tool_limits import (
    GET_MESSAGE_CONTEXT_MAX_ROWS,
    GET_REPLY_THREAD_MAX_ROWS,
    SEARCH_MESSAGES_MAX_ROWS,
    ToolScope,
    clamp_row_limit,
    clamp_window_to_scope,
)
from selara.domain.entities import ActivityWindowStats, ArchivedMessageView
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository

logger = logging.getLogger(__name__)


@dataclass
class DailySummaryToolCall:
    name: str
    arguments: dict
    call_id: str


@dataclass
class DailySummaryToolResult:
    call_id: str
    name: str
    result_text: str
    success: bool = True


@dataclass(frozen=True)
class DailySummaryToolContext:
    repo: SqlAlchemyActivityRepository
    scope: ToolScope
    author_tokens: dict[int, str]
    alias_index: dict[str, int]


GET_MESSAGE_CONTEXT_TOOL = "get_message_context"
GET_REPLY_THREAD_TOOL = "get_reply_thread"
SEARCH_MESSAGES_TOOL = "search_messages"
GET_ACTIVITY_STATS_TOOL = "get_activity_stats"

_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": GET_MESSAGE_CONTEXT_TOOL,
            "description": (
                "Показать сообщения этого чата вокруг заданного сообщения (контекст обсуждения), "
                "чтобы понять о чём вообще шла речь."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "around_telegram_message_id": {
                        "type": "integer",
                        "description": "ID сообщения, вокруг которого нужен контекст",
                    },
                    "limit": {
                        "type": "integer",
                        "description": f"Сколько сообщений вернуть (максимум {GET_MESSAGE_CONTEXT_MAX_ROWS})",
                    },
                },
                "required": ["around_telegram_message_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": GET_REPLY_THREAD_TOOL,
            "description": (
                "Показать цепочку ответов, ведущих от заданного сообщения (кто на кого отвечал), "
                "чтобы отделить один разговор от параллельных."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "root_telegram_message_id": {
                        "type": "integer",
                        "description": "ID сообщения, с которого начинается ветка ответов",
                    },
                    "limit": {
                        "type": "integer",
                        "description": f"Сколько сообщений вернуть (максимум {GET_REPLY_THREAD_MAX_ROWS})",
                    },
                },
                "required": ["root_telegram_message_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": SEARCH_MESSAGES_TOOL,
            "description": (
                "Найти сообщения этого чата за анализируемые сутки по подстроке в тексте, "
                "чтобы понять, всплывала ли тема повторно."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Слово или фраза для поиска"},
                    "limit": {
                        "type": "integer",
                        "description": f"Сколько сообщений вернуть (максимум {SEARCH_MESSAGES_MAX_ROWS})",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": GET_ACTIVITY_STATS_TOOL,
            "description": (
                "Получить статистику активности (число сообщений, участников, ответов) за анализируемые "
                "сутки или за их часть, без домыслов по тексту."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from_iso": {
                        "type": "string",
                        "description": "Начало под-периода в ISO 8601 (необязательно, по умолчанию — начало суток)",
                    },
                    "to_iso": {
                        "type": "string",
                        "description": "Конец под-периода в ISO 8601 (необязательно, по умолчанию — конец суток)",
                    },
                },
                "required": [],
            },
        },
    },
]


def get_daily_summary_tool_definitions() -> list[dict[str, Any]]:
    return list(_TOOL_SCHEMAS)


def build_alias_free_text(value: str | None, *, context: DailySummaryToolContext) -> str | None:
    if not value:
        return value
    return redact_known_aliases(value, alias_index=context.alias_index, tokens=context.author_tokens)


def _serialize_message(row: ArchivedMessageView, *, context: DailySummaryToolContext) -> dict[str, Any]:
    return {
        "message_id": row.telegram_message_id,
        "author": context.author_tokens.get(row.user_id, "Участник"),
        "sent_at": row.sent_at.isoformat(),
        "text": build_alias_free_text(row.text, context=context),
        "transcript": build_alias_free_text(row.transcript, context=context),
        "reply_to_message_id": row.reply_to_telegram_message_id,
    }


def _parse_optional_iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


async def _tool_get_message_context(context: DailySummaryToolContext, arguments: dict) -> dict[str, Any]:
    around_id = int(arguments["around_telegram_message_id"])
    limit = clamp_row_limit(arguments.get("limit"), max_rows=GET_MESSAGE_CONTEXT_MAX_ROWS)
    rows = await context.repo.get_message_context(
        chat_id=context.scope.chat_id,
        around_telegram_message_id=around_id,
        limit=limit,
    )
    return {"messages": [_serialize_message(row, context=context) for row in rows]}


async def _tool_get_reply_thread(context: DailySummaryToolContext, arguments: dict) -> dict[str, Any]:
    root_id = int(arguments["root_telegram_message_id"])
    limit = clamp_row_limit(arguments.get("limit"), max_rows=GET_REPLY_THREAD_MAX_ROWS)
    rows = await context.repo.get_reply_thread(
        chat_id=context.scope.chat_id,
        root_telegram_message_id=root_id,
        limit=limit,
    )
    return {"messages": [_serialize_message(row, context=context) for row in rows]}


async def _tool_search_messages(context: DailySummaryToolContext, arguments: dict) -> dict[str, Any]:
    query = str(arguments.get("query", "")).strip()
    limit = clamp_row_limit(arguments.get("limit"), max_rows=SEARCH_MESSAGES_MAX_ROWS)
    if not query:
        return {"messages": []}
    rows = await context.repo.search_messages(
        chat_id=context.scope.chat_id,
        query=query,
        window_from=context.scope.window_from,
        window_to=context.scope.window_to,
        limit=limit,
    )
    return {"messages": [_serialize_message(row, context=context) for row in rows]}


async def _tool_get_activity_stats(context: DailySummaryToolContext, arguments: dict) -> dict[str, Any]:
    requested_from = _parse_optional_iso(arguments.get("from_iso"))
    requested_to = _parse_optional_iso(arguments.get("to_iso"))
    window_from, window_to = clamp_window_to_scope(
        scope=context.scope,
        requested_from=requested_from,
        requested_to=requested_to,
    )
    stats: ActivityWindowStats = await context.repo.get_activity_stats_in_window(
        chat_id=context.scope.chat_id,
        window_from=window_from,
        window_to=window_to,
    )
    return {
        "from_iso": window_from.isoformat(),
        "to_iso": window_to.isoformat(),
        "message_count": stats.message_count,
        "participant_count": stats.participant_count,
        "reply_count": stats.reply_count,
    }


_TOOL_EXECUTORS = {
    GET_MESSAGE_CONTEXT_TOOL: _tool_get_message_context,
    GET_REPLY_THREAD_TOOL: _tool_get_reply_thread,
    SEARCH_MESSAGES_TOOL: _tool_search_messages,
    GET_ACTIVITY_STATS_TOOL: _tool_get_activity_stats,
}


async def execute_daily_summary_tool(
    call: DailySummaryToolCall,
    *,
    context: DailySummaryToolContext,
) -> DailySummaryToolResult:
    executor = _TOOL_EXECUTORS.get(call.name)
    if executor is None:
        return DailySummaryToolResult(
            call_id=call.call_id,
            name=call.name,
            result_text=json.dumps({"error": f"Неизвестный инструмент: {call.name}"}, ensure_ascii=False),
            success=False,
        )
    try:
        payload = await executor(context, call.arguments)
        return DailySummaryToolResult(
            call_id=call.call_id,
            name=call.name,
            result_text=json.dumps(payload, ensure_ascii=False),
        )
    except Exception as exc:
        logger.exception("daily summary tool %s failed", call.name)
        return DailySummaryToolResult(
            call_id=call.call_id,
            name=call.name,
            result_text=json.dumps({"error": str(exc)}, ensure_ascii=False),
            success=False,
        )


def build_author_alias_context(
    *,
    repo: SqlAlchemyActivityRepository,
    scope: ToolScope,
    members: list[ChatMemberInfo],
    persona_enabled: bool,
) -> DailySummaryToolContext:
    from selara.application.daily_summary.sanitize import build_alias_index, build_author_display_tokens

    return DailySummaryToolContext(
        repo=repo,
        scope=scope,
        author_tokens=build_author_display_tokens(members, persona_enabled=persona_enabled),
        alias_index=build_alias_index(members),
    )
