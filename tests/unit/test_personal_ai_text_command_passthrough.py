"""A private text that text_commands handles must never reach the Personal AI dialogue (and its quota)."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.dispatcher.event.bases import SkipHandler

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.presentation.handlers import text_commands


def _settings() -> Settings:
    return Settings.model_validate({"BOT_TOKEN": "123456:TEST", "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/t"})


def _chat_settings(**overrides):
    base = replace(
        default_chat_settings(_settings()),
        text_commands_enabled=True,
        custom_rp_enabled=False,
        smart_triggers_enabled=False,
        pets_enabled=False,
    )
    return replace(base, **overrides)


def _message(text: str, chat_type: str = "private"):
    message = MagicMock()
    message.text = text
    message.caption = None
    message.chat = SimpleNamespace(type=chat_type, id=5 if chat_type == "private" else -100, title=None)
    message.from_user = SimpleNamespace(id=5, username="u", first_name="U", last_name=None, is_bot=False)
    message.reply_to_message = None
    message.answer = AsyncMock()
    message.reply = AsyncMock()
    message.answer_photo = AsyncMock()
    return message


async def _run(monkeypatch, text, *, chat_type="private", chat_settings=None, supported=("private", "group", "supergroup")):
    monkeypatch.setattr(text_commands, "_enforce_command_access", AsyncMock(return_value=True))
    activity_repo = AsyncMock()
    activity_repo.get_chat_alias_mode = AsyncMock(return_value="both")
    activity_repo.list_chat_aliases = AsyncMock(return_value=[])
    await text_commands.text_commands_handler(
        _message(text, chat_type),
        activity_repo=activity_repo,
        economy_repo=AsyncMock(),
        bot=AsyncMock(),
        settings=SimpleNamespace(supported_chat_types=set(supported)),
        chat_settings=chat_settings or _chat_settings(),
        session_factory=MagicMock(),
    )


@pytest.mark.parametrize(
    "text",
    ["расскажи анекдот", "привет, как дела?", "помоги выбрать подарок маме", "что такое асинхронность?", "расскажи про жмыхи"],
)
async def test_plain_private_chat_text_is_handed_to_the_next_handler(monkeypatch, text):
    with pytest.raises(SkipHandler):
        await _run(monkeypatch, text)


@pytest.mark.parametrize(
    "text",
    [
        "гороскоп",
        "мой гороскоп",
        "характеристика",
        "охарактеризуй",
        'добавить о себе "Люблю котов"',
        "жмых 3",
        "научить слово ответ",
        "добавить_действие обнять",
        "наградить @friend Лучший мем",
        "снять награду 2",
        "объява всем привет",
    ],
)
async def test_text_commands_stay_with_text_commands_and_are_never_passed_to_ai(monkeypatch, text):
    try:
        await _run(monkeypatch, text)
    except SkipHandler:
        pytest.fail(f"{text!r} is a text command and must not fall through to the AI dialogue")
    except Exception:
        # The command's own business logic may trip over the mocks; only the hand-over matters here.
        pass


async def test_private_text_is_passed_on_even_when_text_commands_are_off_or_chat_type_unsupported(monkeypatch):
    with pytest.raises(SkipHandler):
        await _run(monkeypatch, "привет", chat_settings=_chat_settings(text_commands_enabled=False))
    with pytest.raises(SkipHandler):
        await _run(monkeypatch, "привет", supported=("group", "supergroup"))


async def test_group_text_is_never_passed_on(monkeypatch):
    await _run(monkeypatch, "привет всем", chat_type="supergroup")
