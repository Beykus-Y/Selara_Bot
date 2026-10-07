"""Selara's own social actions in member mode and the Mini App settings logic."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.ai_character.group import (
    GroupCharacter,
    build_member_messages,
)
from selara.core.config import Settings
from selara.domain.entities import UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.chat_ai_character_repository import ChatAiCharacterRepository
from selara.infrastructure.db.models import ChatModel
from selara.presentation.handlers import group_character, member_actions
from selara.web import selara_chat_settings

_CHAT = -4001


def _user(user_id: int, username: str, *, bot: bool = False) -> UserSnapshot:
    return UserSnapshot(
        telegram_user_id=user_id, username=username, first_name=username.title(), last_name=None, is_bot=bot
    )


def _message():
    return SimpleNamespace(
        chat=SimpleNamespace(id=_CHAT, type="supergroup", title="Chat"),
        from_user=SimpleNamespace(id=11, username="asker", first_name="Asker", last_name=None, is_bot=False),
        message_id=5,
        reply_to_message=None,
    )


def _repo(disabled=frozenset(), users=None, member=True):
    users = users or {}

    async def find_user(*, chat_id, username):
        return users.get(username.lstrip("@").lower())

    return SimpleNamespace(
        get_disabled_rp_actions=AsyncMock(return_value=set(disabled)),
        get_chat_display_name=AsyncMock(return_value=None),
        find_chat_user_by_username=find_user,
        is_active_chat_member=AsyncMock(return_value=member),
    )


def _bot():
    member_actions._bot_identity.clear()
    return SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(id=999, first_name="Selara")),
        send_message=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_action_is_sent_as_selara_to_the_target():
    bot = _bot()
    repo = _repo(users={"vasya": _user(21, "vasya")})
    text, ok = await member_actions.perform_member_action(
        message=_message(), bot=bot, activity_repo=repo, arguments={"action": "обнять", "target": "@vasya"},
        actor_label="Селя",
    )
    assert ok and "обнять" in text
    sent = bot.send_message.await_args
    body = sent.args[1]
    assert 'tg://user?id=999">Селя<' in body and "tg://user?id=21" in body
    assert sent.kwargs["reply_to_message_id"] == 5 and sent.kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_asker_target_refers_to_the_person_who_called_selara():
    bot = _bot()
    text, ok = await member_actions.perform_member_action(
        message=_message(), bot=bot, activity_repo=_repo(), arguments={"action": "обнять", "target": "asker"},
        actor_label=None,
    )
    assert ok and "tg://user?id=11" in bot.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_adult_disabled_and_unknown_actions_and_bad_targets_are_refused():
    bot = _bot()
    repo = _repo(disabled={"hug"}, users={"botty": _user(31, "botty", bot=True)})
    for arguments in (
        {"action": "трахнуть", "target": "asker"},  # 18+ is never available to Selara
        {"action": "убить", "target": "asker"},  # hostile actions are outside the allowlist
        {"action": "унизить", "target": "asker"},
        {"action": "обнять", "target": "12345"},  # numeric ids are not an accepted target form
        {"action": "обнять", "target": "@12345 "},
        {"action": "обнять", "target": "asker"},  # disabled by the chat admins
        {"action": "летать", "target": "asker"},  # not an action
        {"action": "погладить", "target": "@nobody"},  # nobody by that name
        {"action": "погладить", "target": "@botty"},  # bots are not targets
    ):
        text, ok = await member_actions.perform_member_action(
            message=_message(), bot=bot, activity_repo=repo, arguments=arguments, actor_label=None
        )
        assert ok is False, arguments
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_target_must_be_an_active_chat_member():
    bot = _bot()
    repo = _repo(users={"vasya": _user(21, "vasya")}, member=False)
    text, ok = await member_actions.perform_member_action(
        message=_message(), bot=bot, activity_repo=repo, arguments={"action": "обнять", "target": "@vasya"},
        actor_label=None,
    )
    assert ok is False
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_actions_fail_closed_when_the_disabled_list_cannot_be_loaded():
    bot = _bot()
    repo = _repo()
    repo.get_disabled_rp_actions = AsyncMock(side_effect=RuntimeError("db timeout"))
    text, ok = await member_actions.perform_member_action(
        message=_message(), bot=bot, activity_repo=repo, arguments={"action": "обнять", "target": "asker"},
        actor_label=None,
    )
    assert ok is False
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_delivery_is_reported_to_the_model():
    bot = _bot()
    bot.send_message = AsyncMock(side_effect=RuntimeError("blocked"))
    text, ok = await member_actions.perform_member_action(
        message=_message(), bot=bot, activity_repo=_repo(), arguments={"action": "обнять", "target": "asker"},
        actor_label=None,
    )
    assert ok is False and "отправить" in text


def test_prompt_mentions_actions_only_when_enabled():
    kwargs = dict(call_name="Селя", chat_title="Чат", speaker_name="Вася", recent=[], user_text="привет")
    on = build_member_messages(character=GroupCharacter(member_actions_enabled=True), **kwargs)[0]["content"]
    off = build_member_messages(character=GroupCharacter(member_actions_enabled=False), **kwargs)[0]["content"]
    assert "perform_action" in on and "perform_action" not in off


# ----- Mini App settings logic ---------------------------------------------------


@pytest.fixture
async def settings_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(ChatModel(telegram_chat_id=_CHAT, type="supergroup", title="Chat"))
        await session.commit()
        yield session
    await engine.dispose()


def _settings():
    return Settings(_env_file=None, bot_token="1:x", database_url="sqlite:///")


async def _apply(session, action, value=""):
    return await selara_chat_settings.apply_selara_action(
        db_session=session,
        activity_repo=SimpleNamespace(
            list_chat_aliases=AsyncMock(return_value=[]), list_chat_triggers=AsyncMock(return_value=[])
        ),
        chat_id=_CHAT,
        actor_id=11,
        action=action,
        value=value,
        session_factory=None,
        settings=_settings(),
    )


async def _payload(session, can_manage=True):
    return await selara_chat_settings.build_selara_settings_payload(
        db_session=session, chat_id=_CHAT, session_factory=None, settings=_settings(), can_manage=can_manage
    )


@pytest.mark.asyncio
async def test_settings_actions_mirror_the_command_rules(settings_db):
    ok, _ = await _apply(settings_db, "add_name", "Селя")
    assert ok
    ok, message = await _apply(settings_db, "add_name", "Лена")
    assert not ok and message  # a free chat keeps one name
    ok, message = await _apply(settings_db, "add_name", "обнять")
    assert not ok  # clashes with a bot text command
    ok, _ = await _apply(settings_db, "set_preset", "friendly")
    assert ok
    ok, _ = await _apply(settings_db, "set_custom", "Говорит коротко и по делу")
    assert ok
    for key in ("member_mode", "history", "actions"):
        ok, _ = await _apply(settings_db, key, "true")
        assert ok
    ok, _ = await _apply(settings_db, "actions", "false")
    assert ok
    payload = await _payload(settings_db)
    assert payload["member_mode"] is True and payload["history"] is True and payload["actions"] is False
    assert payload["character"]["preset"] == "custom" and payload["names"][0]["display"] == "Селя"
    assert payload["names"][0]["is_primary"] and payload["name_limit"] == 1 and payload["paid"] is False
    ok, _ = await _apply(settings_db, "remove_name", "селя")
    assert ok and (await _payload(settings_db))["names"] == []


@pytest.mark.asyncio
async def test_unknown_action_and_bad_preset_are_refused(settings_db):
    assert (await _apply(settings_db, "explode"))[0] is False
    assert (await _apply(settings_db, "set_preset", "nope"))[0] is False
    assert (await _apply(settings_db, "set_custom", "x" * 501))[0] is False


@pytest.mark.asyncio
async def test_actions_default_to_on_and_survive_the_repository(settings_db):
    repo = ChatAiCharacterRepository(settings_db)
    assert (await repo.get_character(chat_id=_CHAT)).member_actions_enabled is True
    await repo.update_character(chat_id=_CHAT, actor_user_id=1, member_actions_enabled=False)
    assert (await repo.get_character(chat_id=_CHAT)).member_actions_enabled is False
