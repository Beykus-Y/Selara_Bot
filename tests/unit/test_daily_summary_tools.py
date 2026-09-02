from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from selara.application.daily_summary.tool_limits import ToolScope
from selara.domain.entities import ActivityWindowStats, ArchivedMessageView
from selara.infrastructure.llm.daily_summary_tools import (
    DailySummaryToolCall,
    DailySummaryToolContext,
    execute_daily_summary_tool,
)

_WINDOW_FROM = datetime(2026, 9, 2, 7, 0, tzinfo=timezone.utc)
_WINDOW_TO = datetime(2026, 9, 3, 7, 0, tzinfo=timezone.utc)


@dataclass
class _FakeRepo:
    message_context_rows: list[ArchivedMessageView] = field(default_factory=list)
    reply_thread_rows: list[ArchivedMessageView] = field(default_factory=list)
    search_rows: list[ArchivedMessageView] = field(default_factory=list)
    stats: ActivityWindowStats = field(
        default_factory=lambda: ActivityWindowStats(message_count=0, participant_count=0, reply_count=0)
    )
    last_search_call: dict | None = None
    last_stats_call: dict | None = None

    async def get_message_context(self, *, chat_id, around_telegram_message_id, limit):
        return self.message_context_rows

    async def get_reply_thread(self, *, chat_id, root_telegram_message_id, limit):
        return self.reply_thread_rows

    async def search_messages(self, *, chat_id, query, window_from, window_to, limit):
        self.last_search_call = {"chat_id": chat_id, "query": query, "window_from": window_from, "window_to": window_to, "limit": limit}
        return self.search_rows

    async def get_activity_stats_in_window(self, *, chat_id, window_from, window_to):
        self.last_stats_call = {"chat_id": chat_id, "window_from": window_from, "window_to": window_to}
        return self.stats


def _context(repo: _FakeRepo, *, author_tokens=None, alias_index=None) -> DailySummaryToolContext:
    return DailySummaryToolContext(
        repo=repo,
        scope=ToolScope(chat_id=-100999, window_from=_WINDOW_FROM, window_to=_WINDOW_TO),
        author_tokens=author_tokens or {1: "Вася", 2: "Участник #1"},
        alias_index=alias_index or {"петя": 2},
    )


@pytest.mark.asyncio
async def test_get_message_context_serializes_author_token_not_raw_name() -> None:
    row = ArchivedMessageView(
        telegram_message_id=42,
        user_id=2,
        sent_at=_WINDOW_FROM,
        text="привет всем",
        transcript=None,
        reply_to_telegram_message_id=None,
    )
    repo = _FakeRepo(message_context_rows=[row])
    call = DailySummaryToolCall(name="get_message_context", arguments={"around_telegram_message_id": 42}, call_id="c1")

    result = await execute_daily_summary_tool(call, context=_context(repo))

    payload = json.loads(result.result_text)
    assert payload["messages"][0]["author"] == "Участник #1"
    assert payload["messages"][0]["message_id"] == 42
    assert result.success is True


@pytest.mark.asyncio
async def test_get_message_context_redacts_known_alias_in_text() -> None:
    # a departed member's own alias index entry (username "петя" -> user 2) must be
    # stripped even from ANOTHER (active) author's message text
    row = ArchivedMessageView(
        telegram_message_id=7,
        user_id=1,
        sent_at=_WINDOW_FROM,
        text="а Петя вчера опять сервер сломал",
        transcript=None,
        reply_to_telegram_message_id=None,
    )
    repo = _FakeRepo(message_context_rows=[row])
    call = DailySummaryToolCall(name="get_message_context", arguments={"around_telegram_message_id": 7}, call_id="c1")

    result = await execute_daily_summary_tool(call, context=_context(repo, alias_index={"петя": 2}))

    payload = json.loads(result.result_text)
    assert "Петя" not in payload["messages"][0]["text"]
    assert "Участник #1" in payload["messages"][0]["text"]


@pytest.mark.asyncio
async def test_search_messages_passes_scope_window_when_none_requested() -> None:
    repo = _FakeRepo(search_rows=[])
    call = DailySummaryToolCall(name="search_messages", arguments={"query": "VPN"}, call_id="c1")

    await execute_daily_summary_tool(call, context=_context(repo))

    assert repo.last_search_call["chat_id"] == -100999
    assert repo.last_search_call["window_from"] == _WINDOW_FROM
    assert repo.last_search_call["window_to"] == _WINDOW_TO


@pytest.mark.asyncio
async def test_search_messages_empty_query_returns_empty_without_calling_repo() -> None:
    repo = _FakeRepo(search_rows=[ArchivedMessageView(1, 1, _WINDOW_FROM, "x", None, None)])
    call = DailySummaryToolCall(name="search_messages", arguments={"query": "   "}, call_id="c1")

    result = await execute_daily_summary_tool(call, context=_context(repo))

    payload = json.loads(result.result_text)
    assert payload["messages"] == []
    assert repo.last_search_call is None


@pytest.mark.asyncio
async def test_get_activity_stats_clamps_out_of_range_window() -> None:
    from datetime import timedelta

    repo = _FakeRepo(stats=ActivityWindowStats(message_count=5, participant_count=2, reply_count=1))
    call = DailySummaryToolCall(
        name="get_activity_stats",
        arguments={
            "from_iso": (_WINDOW_FROM - timedelta(days=10)).isoformat(),
            "to_iso": (_WINDOW_TO + timedelta(days=10)).isoformat(),
        },
        call_id="c1",
    )

    result = await execute_daily_summary_tool(call, context=_context(repo))

    payload = json.loads(result.result_text)
    assert repo.last_stats_call["window_from"] == _WINDOW_FROM
    assert repo.last_stats_call["window_to"] == _WINDOW_TO
    assert payload["message_count"] == 5


@pytest.mark.asyncio
async def test_unknown_tool_name_returns_error_without_raising() -> None:
    repo = _FakeRepo()
    call = DailySummaryToolCall(name="drop_all_tables", arguments={}, call_id="c1")

    result = await execute_daily_summary_tool(call, context=_context(repo))

    assert result.success is False
    assert "error" in json.loads(result.result_text)


@pytest.mark.asyncio
async def test_repo_exception_is_caught_and_reported_as_tool_failure() -> None:
    class _BoomRepo(_FakeRepo):
        async def get_message_context(self, *, chat_id, around_telegram_message_id, limit):
            raise RuntimeError("boom")

    call = DailySummaryToolCall(name="get_message_context", arguments={"around_telegram_message_id": 1}, call_id="c1")

    result = await execute_daily_summary_tool(call, context=_context(_BoomRepo()))

    assert result.success is False
    assert "boom" in result.result_text
