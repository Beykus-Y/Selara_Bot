from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.domain.entities import MarriageState, RelationshipState
from selara.presentation.handlers.relationships import (
    _build_relationship_action_keyboard,
    marriage_status_command,
    relationship_action_callback,
)


class _DummyMessage:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(id=-100500, type="group", title="Relationships")
        self.from_user = SimpleNamespace(
            id=1,
            username="actor",
            first_name="Actor",
            last_name=None,
            is_bot=False,
        )
        self.reply_to_message = None
        self.answer = AsyncMock()


class _DummyQuery:
    def __init__(self, *, data: str) -> None:
        self.data = data
        self.from_user = SimpleNamespace(
            id=1,
            username="actor",
            first_name="Actor",
            last_name=None,
            is_bot=False,
        )
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=-100500, type="group", title="Relationships"),
            answer=AsyncMock(),
            edit_text=AsyncMock(),
        )
        self.answer = AsyncMock()


def _pair_state() -> RelationshipState:
    return RelationshipState(
        kind="pair",
        id=10,
        user_low_id=1,
        user_high_id=2,
        chat_id=-100500,
        started_at=datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc),
        affection_points=9,
        last_affection_at=None,
        last_affection_by_user_id=None,
    )


def _marriage_state() -> MarriageState:
    return MarriageState(
        id=20,
        user_low_id=1,
        user_high_id=2,
        chat_id=-100500,
        married_at=datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc),
        affection_points=15,
        last_affection_at=None,
        last_affection_by_user_id=None,
    )


def test_relationship_keyboard_contains_stage_actions() -> None:
    pair_keyboard = _build_relationship_action_keyboard("pair", owner_user_id=7, view="relation")
    marriage_keyboard = _build_relationship_action_keyboard("marriage", owner_user_id=7, view="marriage")

    pair_callbacks = [button.callback_data for row in pair_keyboard.inline_keyboard for button in row if button.callback_data]
    marriage_callbacks = [button.callback_data for row in marriage_keyboard.inline_keyboard for button in row if button.callback_data]

    assert "relact:care:relation:7" in pair_callbacks
    assert "relact:flirt:relation:7" in pair_callbacks
    assert "relact:refresh:relation:7" in pair_callbacks
    assert "relact:love:relation:7" not in pair_callbacks

    assert "relact:love:marriage:7" in marriage_callbacks
    assert "relact:vow:marriage:7" in marriage_callbacks
    assert "relact:refresh:marriage:7" in marriage_callbacks
    assert "relact:flirt:marriage:7" not in marriage_callbacks


@pytest.mark.asyncio
async def test_marriage_status_command_renders_marriage_panel() -> None:
    message = _DummyMessage()
    activity_repo = SimpleNamespace(
        get_active_marriage=AsyncMock(return_value=_marriage_state()),
        get_active_relationship=AsyncMock(return_value=None),
        get_user_snapshot=AsyncMock(return_value=None),
        get_chat_display_name=AsyncMock(return_value="Partner"),
        get_relationship_action_last_used_at=AsyncMock(return_value=None),
    )

    await marriage_status_command(message, activity_repo=activity_repo)

    text = message.answer.await_args.args[0]
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row if button.callback_data]

    assert "Ваш брак" in text
    assert "Статус:</b> <code>Брак</code>" in text
    assert "relact:love:marriage:1" in callbacks
    assert "relact:refresh:marriage:1" in callbacks


@pytest.mark.asyncio
async def test_marriage_status_command_points_pair_users_to_relation_view() -> None:
    message = _DummyMessage()
    activity_repo = SimpleNamespace(
        get_active_marriage=AsyncMock(return_value=None),
        get_active_relationship=AsyncMock(return_value=_pair_state()),
    )

    await marriage_status_command(message, activity_repo=activity_repo)

    text = message.answer.await_args.args[0]
    assert "Сейчас у вас пара" in text


@pytest.mark.asyncio
async def test_marriage_status_command_reports_missing_marriage() -> None:
    message = _DummyMessage()
    activity_repo = SimpleNamespace(
        get_active_marriage=AsyncMock(return_value=None),
        get_active_relationship=AsyncMock(return_value=None),
    )

    await marriage_status_command(message, activity_repo=activity_repo)

    text = message.answer.await_args.args[0]
    assert "У вас нет активного брака" in text


@pytest.mark.asyncio
async def test_relationship_action_callback_shows_cooldown_alert() -> None:
    query = _DummyQuery(data="relact:care:relation:1")
    activity_repo = SimpleNamespace(
        get_active_relationship=AsyncMock(return_value=_pair_state()),
        get_chat_display_name=AsyncMock(return_value="Actor"),
        get_relationship_action_last_used_at=AsyncMock(
            return_value=datetime.now(timezone.utc) - timedelta(minutes=10)
        ),
    )

    await relationship_action_callback(query, activity_repo=activity_repo)

    assert query.message.answer.await_count == 0
    assert query.message.edit_text.await_count == 0
    assert query.answer.await_count == 1
    assert query.answer.await_args.kwargs["show_alert"] is True
    assert "Слишком рано" in query.answer.await_args.kwargs["text"]
