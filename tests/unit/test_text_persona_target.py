from types import SimpleNamespace

import pytest

from selara.domain.entities import ChatPersonaAssignment, UserSnapshot
from selara.presentation.commands.resolver import resolve_persona_target_text_candidate
from selara.presentation.handlers.text_commands import (
    _apply_alias_mode_to_text,
    _resolve_persona_target_intent,
)
from selara.presentation.targeting import resolve_chat_target_user


class _FakeActivityRepo:
    async def find_chat_user_by_username(self, *, chat_id: int, username: str):
        return None

    async def find_chat_persona_owner(self, *, chat_id: int, persona_label: str):
        return None

    async def list_chat_persona_assignments(self, *, chat_id: int):
        if chat_id != -100:
            return []
        return [
            ChatPersonaAssignment(
                chat_id=-100,
                user=UserSnapshot(
                    telegram_user_id=901,
                    username="colombina_main",
                    first_name="Colombina",
                    last_name=None,
                    is_bot=False,
                    chat_display_name="Коломбина",
                ),
                persona_label="Коломбина",
                persona_label_norm="коломбина",
                granted_by_user_id=1,
            )
        ]

    async def get_chat_display_name(self, *, chat_id: int, user_id: int):
        return None


def _message(chat_id: int = -100):
    return SimpleNamespace(
        chat=SimpleNamespace(type="group", id=chat_id),
        from_user=SimpleNamespace(id=111, username="self", first_name="Self", last_name=None),
        reply_to_message=None,
    )


@pytest.mark.parametrize(
    ("text", "command_name"),
    [
        ("пара Коломбина", "pair"),
        ("брак Коломбина", "marry"),
        ("когда был Коломбина", "lastseen"),
        ("семья Коломбина", "family"),
    ],
)
def test_bare_name_tail_becomes_persona_candidate(text: str, command_name: str) -> None:
    intent = resolve_persona_target_text_candidate(text)

    assert intent is not None
    assert intent.name == command_name
    assert intent.args == {"raw_args": "коломбина"}


@pytest.mark.parametrize(
    "text",
    [
        "пара",
        "пара @alice",
        "пара 42",
        "рынок сегодня шумный",
        "/pair Коломбина",
    ],
)
def test_non_name_text_is_not_a_persona_candidate(text: str) -> None:
    assert resolve_persona_target_text_candidate(text) is None


@pytest.mark.asyncio
async def test_bare_name_resolves_to_chat_persona_owner() -> None:
    intent = resolve_persona_target_text_candidate("пара Коломбина")
    assert intent is not None

    target = await resolve_chat_target_user(
        _message(),
        _FakeActivityRepo(),
        explicit_target=intent.args["raw_args"],
        prefer_reply=False,
    )

    assert target is not None
    assert target.telegram_user_id == 901


def test_tail_longer_than_persona_label_limit_is_not_a_candidate() -> None:
    assert resolve_persona_target_text_candidate("пара " + "а" * 48) is not None
    assert resolve_persona_target_text_candidate("пара " + "а" * 49) is None


def test_persona_name_form_is_suppressed_when_command_has_alias() -> None:
    aliases = [SimpleNamespace(alias_text_norm="сватай тут", command_key="pair", source_trigger_norm="пара")]

    assert _apply_alias_mode_to_text(text="пара Коломбина", mode="aliases_if_exists", aliases=aliases) is None
    assert _apply_alias_mode_to_text(text="пара Коломбина", mode="standard_only", aliases=aliases) == "пара Коломбина"
    assert _apply_alias_mode_to_text(text="рынок сегодня", mode="aliases_if_exists", aliases=aliases) == "рынок сегодня"


@pytest.mark.asyncio
async def test_persona_form_in_reply_is_not_resolved_to_named_persona() -> None:
    message = _message()
    message.reply_to_message = SimpleNamespace(from_user=SimpleNamespace(id=222))

    assert await _resolve_persona_target_intent(message, _FakeActivityRepo(), text="пара Коломбина") is None


@pytest.mark.asyncio
async def test_persona_form_resolves_to_intent_outside_reply() -> None:
    intent = await _resolve_persona_target_intent(_message(), _FakeActivityRepo(), text="пара Коломбина")

    assert intent is not None
    assert intent.name == "pair"


@pytest.mark.asyncio
async def test_ordinary_speech_with_bare_word_does_not_resolve() -> None:
    intent = resolve_persona_target_text_candidate("пара слов")
    assert intent is not None

    target = await resolve_chat_target_user(
        _message(),
        _FakeActivityRepo(),
        explicit_target=intent.args["raw_args"],
        prefer_reply=False,
    )

    assert target is None
