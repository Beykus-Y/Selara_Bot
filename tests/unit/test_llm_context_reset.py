"""Regression tests for finding #11 in docs/STT_LLM_AUDIT_TODO.md:

Only automatic threshold-triggered summarization existed
(context.py::maybe_compress), which rolls forward potentially-bad context
rather than discarding it. No `?reset`-style escape hatch.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import F

import selara.presentation.handlers.llm_admin as llm_admin_module
from selara.presentation.handlers.llm_admin import llm_context_reset_handler


def test_reset_filter_matches_bare_reset_command_only():
    filter_reset = F.chat.type.in_({"group", "supergroup"}) & F.text.regexp(r"^\?reset\s*$")

    matches = SimpleNamespace(chat=SimpleNamespace(type="group"), text="?reset")
    matches_trailing_space = SimpleNamespace(chat=SimpleNamespace(type="group"), text="?reset ")
    not_bare_reset = SimpleNamespace(chat=SimpleNamespace(type="group"), text="?resetuje context")
    not_reset_query = SimpleNamespace(chat=SimpleNamespace(type="group"), text="? сделай ресет")

    assert filter_reset.resolve(matches) is not None
    assert filter_reset.resolve(matches_trailing_space) is not None
    assert filter_reset.resolve(not_bare_reset) is None
    assert filter_reset.resolve(not_reset_query) is None


@pytest.mark.asyncio
async def test_reset_handler_requires_moderate_users():
    message = AsyncMock()
    message.chat = SimpleNamespace(id=-100123, type="group", title="Chat")
    message.from_user = SimpleNamespace(id=111, username="member", first_name="M", last_name=None, is_bot=False)
    message.text = "?reset"
    message.reply = AsyncMock()

    activity_repo = MagicMock()
    db_session = AsyncMock()

    with patch.object(llm_admin_module, "has_permission", new=AsyncMock(return_value=(False, None, None))), \
         patch.object(llm_admin_module, "LlmRepository") as mock_repo_cls:
        repo_mock = MagicMock()
        repo_mock.reset_context = AsyncMock()
        mock_repo_cls.return_value = repo_mock

        await llm_context_reset_handler(message, activity_repo, db_session)

    repo_mock.reset_context.assert_not_awaited()
    message.reply.assert_awaited_once()
    assert "прав" in message.reply.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_reset_handler_success_clears_context_and_confirms():
    message = AsyncMock()
    message.chat = SimpleNamespace(id=-100123, type="group", title="Chat")
    message.from_user = SimpleNamespace(id=111, username="admin", first_name="A", last_name=None, is_bot=False)
    message.text = "?reset"
    message.reply = AsyncMock()

    activity_repo = MagicMock()
    db_session = AsyncMock()

    with patch.object(llm_admin_module, "has_permission", new=AsyncMock(return_value=(True, None, None))), \
         patch.object(llm_admin_module, "LlmRepository") as mock_repo_cls:
        repo_mock = MagicMock()
        repo_mock.reset_context = AsyncMock(return_value=7)
        mock_repo_cls.return_value = repo_mock

        await llm_context_reset_handler(message, activity_repo, db_session)

    repo_mock.reset_context.assert_awaited_once_with(chat_id=-100123)
    message.reply.assert_awaited_once()
    assert "7" in message.reply.await_args.args[0]
