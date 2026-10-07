from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.domain.entities import ChatTextAlias
from selara.presentation.handlers import text_commands


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost/test",
        }
    )


def _chat_settings(**overrides):
    base = replace(
        default_chat_settings(_settings()),
        text_commands_enabled=True,
        custom_rp_enabled=False,
        smart_triggers_enabled=False,
        pets_enabled=False,
    )
    return replace(base, **overrides)


class _DummyMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.caption = None
        self.chat = SimpleNamespace(type="group", id=-100123, title="Test chat")
        self.from_user = SimpleNamespace(id=1, username="actor", first_name="Actor", last_name=None, is_bot=False)
        self.reply_to_message = None
        self.answers: list[tuple[str, dict[str, object]]] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append((text, kwargs))


def _activity_repo(*, aliases: list[ChatTextAlias] | None = None):
    return SimpleNamespace(
        get_chat_alias_mode=AsyncMock(return_value="both"),
        list_chat_aliases=AsyncMock(return_value=aliases or []),
    )


async def _run(monkeypatch: pytest.MonkeyPatch, *, text: str, chat_settings, aliases=None) -> tuple[AsyncMock, _DummyMessage]:
    message = _DummyMessage(text)
    tap = AsyncMock()
    monkeypatch.setattr(text_commands, "economy_tap_command", tap)
    monkeypatch.setattr(text_commands, "_enforce_command_access", AsyncMock(return_value=True))
    monkeypatch.setattr(text_commands, "_handle_command_rank_phrase", AsyncMock(return_value=False))
    await text_commands.text_commands_handler(
        message,
        activity_repo=_activity_repo(aliases=aliases),
        economy_repo=object(),
        bot=object(),
        settings=SimpleNamespace(supported_chat_types={"group", "supergroup"}),
        chat_settings=chat_settings,
        session_factory=object(),
    )
    return tap, message


def _alias() -> ChatTextAlias:
    return ChatTextAlias(
        id=1,
        chat_id=-100123,
        command_key="tap",
        alias_text_norm="клик",
        source_trigger_norm="тап",
        created_by_user_id=1,
    )


@pytest.mark.asyncio
async def test_builtin_text_command_works_with_ru_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    tap, _ = await _run(monkeypatch, text="тап", chat_settings=_chat_settings(text_commands_locale="ru"))
    tap.assert_awaited_once()


@pytest.mark.asyncio
async def test_builtin_text_command_works_with_en_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    tap, _ = await _run(monkeypatch, text="тап", chat_settings=_chat_settings(text_commands_locale="en"))
    tap.assert_awaited_once()


@pytest.mark.asyncio
async def test_custom_alias_works_with_en_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    tap, _ = await _run(
        monkeypatch,
        text="клик",
        chat_settings=_chat_settings(text_commands_locale="en"),
        aliases=[_alias()],
    )
    tap.assert_awaited_once()


@pytest.mark.asyncio
async def test_en_locale_does_not_override_text_commands_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    tap, _ = await _run(
        monkeypatch,
        text="тап",
        chat_settings=_chat_settings(text_commands_locale="en", text_commands_enabled=False),
    )
    tap.assert_not_awaited()


@pytest.mark.asyncio
async def test_alias_cannot_bypass_chat_write_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    tap, message = await _run(
        monkeypatch,
        text="клик",
        chat_settings=_chat_settings(chat_write_locked=True),
        aliases=[_alias()],
    )
    tap.assert_not_awaited()
    assert len(message.answers) == 1
    assert "заблок" in message.answers[0][0].lower()


@pytest.mark.asyncio
async def test_non_mutating_command_passes_chat_write_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _DummyMessage("помощь")
    send_help = AsyncMock()
    monkeypatch.setattr(text_commands, "send_help", send_help)
    monkeypatch.setattr(text_commands, "_enforce_command_access", AsyncMock(return_value=True))
    monkeypatch.setattr(text_commands, "_handle_command_rank_phrase", AsyncMock(return_value=False))
    await text_commands.text_commands_handler(
        message,
        activity_repo=_activity_repo(),
        economy_repo=object(),
        bot=object(),
        settings=SimpleNamespace(supported_chat_types={"group", "supergroup"}),
        chat_settings=_chat_settings(chat_write_locked=True),
        session_factory=object(),
    )
    send_help.assert_awaited_once()


def _locked(**overrides):
    return _chat_settings(chat_write_locked=True, gacha_enabled=True, **overrides)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "handler_attr"),
    [("гача генш", "_send_gacha_pull"), ("гача скип генш", "_send_gacha_skip")],
)
async def test_gacha_text_commands_are_blocked_by_chat_write_lock(
    monkeypatch: pytest.MonkeyPatch, text: str, handler_attr: str
) -> None:
    message = _DummyMessage(text)
    target = AsyncMock()
    monkeypatch.setattr(text_commands, handler_attr, target)
    monkeypatch.setattr(text_commands, "_enforce_command_access", AsyncMock(return_value=True))
    monkeypatch.setattr(text_commands, "_handle_command_rank_phrase", AsyncMock(return_value=False))
    monkeypatch.setattr(text_commands, "_require_channel_subscription", AsyncMock(return_value=True))
    monkeypatch.setattr(text_commands, "_check_and_maybe_restore_gacha", AsyncMock(side_effect=lambda m, r, cs: cs))

    await text_commands.text_commands_handler(
        message,
        activity_repo=_activity_repo(),
        economy_repo=object(),
        bot=object(),
        settings=SimpleNamespace(supported_chat_types={"group", "supergroup"}),
        chat_settings=_locked(),
        session_factory=object(),
    )

    target.assert_not_awaited()
    assert len(message.answers) == 1
    assert "заблок" in message.answers[0][0].lower()


@pytest.mark.asyncio
async def test_gacha_profile_is_not_blocked_by_chat_write_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _DummyMessage("моя гача генш")
    target = AsyncMock()
    monkeypatch.setattr(text_commands, "_send_gacha_profile", target)
    monkeypatch.setattr(text_commands, "_enforce_command_access", AsyncMock(return_value=True))
    monkeypatch.setattr(text_commands, "_handle_command_rank_phrase", AsyncMock(return_value=False))
    monkeypatch.setattr(text_commands, "_require_channel_subscription", AsyncMock(return_value=True))
    monkeypatch.setattr(text_commands, "_check_and_maybe_restore_gacha", AsyncMock(side_effect=lambda m, r, cs: cs))

    await text_commands.text_commands_handler(
        message,
        activity_repo=_activity_repo(),
        economy_repo=object(),
        bot=object(),
        settings=SimpleNamespace(supported_chat_types={"group", "supergroup"}),
        chat_settings=_locked(),
        session_factory=object(),
    )

    target.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_lock_does_not_alter_chat_settings_passed_to_gacha_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    """-чат, `гача выкл`, +чат не должны тихо выключить custom RP в сохранённых настройках."""
    message = _DummyMessage("гача выкл")
    toggle = AsyncMock()
    monkeypatch.setattr(text_commands, "_manage_gacha_toggle", toggle)
    monkeypatch.setattr(text_commands, "_require_channel_subscription", AsyncMock(return_value=True))
    monkeypatch.setattr(text_commands, "_enforce_command_access", AsyncMock(return_value=True))
    monkeypatch.setattr(text_commands, "_handle_command_rank_phrase", AsyncMock(return_value=False))

    await text_commands.text_commands_handler(
        message,
        activity_repo=_activity_repo(),
        economy_repo=object(),
        bot=object(),
        settings=SimpleNamespace(supported_chat_types={"group", "supergroup"}),
        chat_settings=_locked(custom_rp_enabled=True),
        session_factory=object(),
    )

    toggle.assert_awaited_once()
    assert toggle.await_args.args[3].custom_rp_enabled is True


@pytest.mark.asyncio
async def test_custom_rp_is_skipped_under_write_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _DummyMessage("мой кастомный рп")
    match = AsyncMock(return_value=object())
    send = AsyncMock()
    monkeypatch.setattr(text_commands, "match_custom_social_action", match)
    monkeypatch.setattr(text_commands, "send_custom_social_action", send)
    monkeypatch.setattr(text_commands, "_enforce_command_access", AsyncMock(return_value=True))
    monkeypatch.setattr(text_commands, "_handle_command_rank_phrase", AsyncMock(return_value=False))

    await text_commands.text_commands_handler(
        message,
        activity_repo=_activity_repo(),
        economy_repo=object(),
        bot=object(),
        settings=SimpleNamespace(supported_chat_types={"group", "supergroup"}),
        chat_settings=_locked(custom_rp_enabled=True),
        session_factory=object(),
    )

    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_builtin_social_action_is_blocked_under_write_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _DummyMessage("обнять")
    send = AsyncMock()
    monkeypatch.setattr(text_commands, "_send_social_action", send)
    monkeypatch.setattr(text_commands, "_enforce_command_access", AsyncMock(return_value=True))
    monkeypatch.setattr(text_commands, "_handle_command_rank_phrase", AsyncMock(return_value=False))

    await text_commands.text_commands_handler(
        message,
        activity_repo=_activity_repo(),
        economy_repo=object(),
        bot=object(),
        settings=SimpleNamespace(supported_chat_types={"group", "supergroup"}),
        chat_settings=_locked(),
        session_factory=object(),
    )

    send.assert_not_awaited()
    assert len(message.answers) == 1


@pytest.mark.asyncio
async def test_alias_to_social_action_is_blocked_under_write_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _DummyMessage("обнимашки")
    send = AsyncMock()
    monkeypatch.setattr(text_commands, "_send_social_action", send)
    monkeypatch.setattr(text_commands, "_enforce_command_access", AsyncMock(return_value=True))
    monkeypatch.setattr(text_commands, "_handle_command_rank_phrase", AsyncMock(return_value=False))
    alias = ChatTextAlias(
        id=2,
        chat_id=-100123,
        command_key="social_hug",
        alias_text_norm="обнимашки",
        source_trigger_norm="обнять",
        created_by_user_id=1,
    )

    await text_commands.text_commands_handler(
        message,
        activity_repo=_activity_repo(aliases=[alias]),
        economy_repo=object(),
        bot=object(),
        settings=SimpleNamespace(supported_chat_types={"group", "supergroup"}),
        chat_settings=_locked(),
        session_factory=object(),
    )

    send.assert_not_awaited()
    assert len(message.answers) == 1
