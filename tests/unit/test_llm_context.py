from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest
from datetime import datetime, timezone

from selara.infrastructure.llm.context import load_context, save_interaction, maybe_compress


@pytest.mark.asyncio
async def test_load_context_isolates_by_chat_id():
    llm_repo = AsyncMock()
    
    # Setup mock returns
    summary_mock = MagicMock()
    summary_mock.content = "Summary content"
    summary_mock.period_end = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)
    llm_repo.get_latest_summary.return_value = summary_mock

    msg_mock = MagicMock()
    msg_mock.role = "user"
    msg_mock.content = "Message content"
    msg_mock.tool_call_id = None
    llm_repo.get_uncompressed_context_messages.return_value = [msg_mock]

    chat_id = 999123
    loaded = await load_context(chat_id=chat_id, llm_repo=llm_repo)

    # Verify repository calls used correct chat_id
    llm_repo.get_latest_summary.assert_awaited_once_with(chat_id=chat_id)
    llm_repo.get_uncompressed_context_messages.assert_awaited_once_with(chat_id=chat_id)

    # Verify context structure
    assert len(loaded.messages) == 2
    assert loaded.messages[0]["role"] == "system"
    assert "Summary content" in loaded.messages[0]["content"]
    assert loaded.messages[1]["role"] == "user"
    assert loaded.messages[1]["content"] == "Message content"


@pytest.mark.asyncio
async def test_save_interaction_isolates_by_chat_id():
    llm_repo = AsyncMock()
    
    chat_id = 999123
    admin_user_id = 111
    
    await save_interaction(
        chat_id=chat_id,
        admin_user_id=admin_user_id,
        user_query_content="user query",
        assistant_response="assistant response",
        tool_messages=[{"content": "tool content", "tool_call_id": "call_1"}],
        llm_repo=llm_repo,
        is_context=True,
    )

    # Verify all message additions were made with the correct chat_id
    llm_repo.add_context_message.assert_any_call(
        chat_id=chat_id,
        role="user",
        content="user query",
        is_context=True,
        admin_user_id=admin_user_id,
    )
    llm_repo.add_context_message.assert_any_call(
        chat_id=chat_id,
        role="tool",
        content="tool content",
        is_context=False,
        admin_user_id=admin_user_id,
        tool_call_id="call_1",
    )
    llm_repo.add_context_message.assert_any_call(
        chat_id=chat_id,
        role="assistant",
        content="assistant response",
        is_context=True,
        admin_user_id=admin_user_id,
    )


@pytest.mark.asyncio
async def test_maybe_compress_isolates_by_chat_id():
    llm_repo = AsyncMock()
    llm_client = AsyncMock()

    # If count is less than threshold, it returns False immediately
    llm_repo.count_uncompressed_context_messages.return_value = 5
    
    chat_id = 999123
    compressed = await maybe_compress(
        chat_id=chat_id,
        threshold=10,
        llm_repo=llm_repo,
        llm_client=llm_client,
    )

    assert compressed is False
    # Verify count query was scoped to correct chat_id
    llm_repo.count_uncompressed_context_messages.assert_awaited_once_with(chat_id=chat_id)
