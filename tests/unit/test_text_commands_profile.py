from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.presentation.handlers.text_commands import (
    _extract_award_remove_index,
    _extract_award_request,
    _extract_profile_about_text,
    _extract_reply_award_title,
    _is_reply_profile_lookup,
)
from selara.presentation.handlers import text_commands as text_commands_module


def test_extract_profile_about_text_with_quotes() -> None:
    matched, value, error = _extract_profile_about_text('добавить о себе "Люблю котов и мемы"')

    assert matched is True
    assert value == "Люблю котов и мемы"
    assert error is None


def test_extract_profile_about_text_requires_body() -> None:
    matched, value, error = _extract_profile_about_text("добавить о себе")

    assert matched is True
    assert value is None
    assert error is not None


def test_extract_reply_award_title_without_quotes() -> None:
    matched, value, error = _extract_reply_award_title("наградить Лучший мем месяца")

    assert matched is True
    assert value == "Лучший мем месяца"
    assert error is None


def test_extract_reply_award_title_rejects_unclosed_quotes() -> None:
    matched, value, error = _extract_reply_award_title('наградить "Лучший мем')

    assert matched is True
    assert value is None
    assert error is not None


def test_extract_award_request_supports_username_target() -> None:
    matched, target_token, value, error = _extract_award_request("наградить @weiinnya Месть подаётся выпечкой")

    assert matched is True
    assert target_token == "@weiinnya"
    assert value == "Месть подаётся выпечкой"
    assert error is None


def test_extract_award_request_supports_username_target_on_new_line() -> None:
    matched, target_token, value, error = _extract_award_request("наградить @Hislorr\nМесть подаётся выпечкой")

    assert matched is True
    assert target_token == "@Hislorr"
    assert value == "Месть подаётся выпечкой"
    assert error is None


def test_extract_award_request_supports_persona_target_on_new_line() -> None:
    matched, target_token, value, error = _extract_award_request("наградить Ху Тао\nМесть подаётся выпечкой")

    assert matched is True
    assert target_token == "Ху Тао"
    assert value == "Месть подаётся выпечкой"
    assert error is None


def test_extract_award_remove_index_reads_positive_number() -> None:
    matched, award_index, error = _extract_award_remove_index("снять награду 6")

    assert matched is True
    assert award_index == 6
    assert error is None


def test_is_reply_profile_lookup_matches_reply_who_are_you() -> None:
    message = SimpleNamespace(
        reply_to_message=SimpleNamespace(from_user=SimpleNamespace(id=77, is_bot=False)),
    )

    assert _is_reply_profile_lookup(message, "кто ты") is True


def test_is_reply_profile_lookup_skips_non_reply_or_bot_reply() -> None:
    no_reply = SimpleNamespace(reply_to_message=None)
    bot_reply = SimpleNamespace(reply_to_message=SimpleNamespace(from_user=SimpleNamespace(id=1, is_bot=True)))

    assert _is_reply_profile_lookup(no_reply, "кто ты") is False
    assert _is_reply_profile_lookup(bot_reply, "кто ты") is False


@pytest.mark.asyncio
async def test_reply_profile_lookup_works_after_alias_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    message = SimpleNamespace(
        text="ты кто",
        chat=SimpleNamespace(type="group", id=-100123, title="Test chat"),
        from_user=SimpleNamespace(id=1, username="actor", first_name="Actor", last_name=None, is_bot=False),
        reply_to_message=SimpleNamespace(
            from_user=SimpleNamespace(id=77, username="target", first_name="Target", last_name=None, is_bot=False)
        ),
        answers=[],
    )

    async def _answer(text: str, **kwargs) -> None:
        message.answers.append((text, kwargs))

    message.answer = _answer

    activity_repo = SimpleNamespace(
        get_chat_alias_mode=AsyncMock(return_value="both"),
        list_chat_aliases=AsyncMock(
            return_value=[
                SimpleNamespace(
                    alias_text_norm="ты кто",
                    command_key="me",
                    source_trigger_norm="кто ты",
                )
            ]
        ),
    )
    send_user_stats = AsyncMock()
    send_me_stats = AsyncMock()

    monkeypatch.setattr(text_commands_module, "send_user_stats", send_user_stats)
    monkeypatch.setattr(text_commands_module, "send_me_stats", send_me_stats)
    monkeypatch.setattr(text_commands_module, "_enforce_command_access", AsyncMock(return_value=True))
    monkeypatch.setattr(text_commands_module, "_handle_command_rank_phrase", AsyncMock(return_value=False))

    await text_commands_module.text_commands_handler(
        message,
        activity_repo=activity_repo,
        economy_repo=object(),
        bot=object(),
        settings=SimpleNamespace(supported_chat_types={"group", "supergroup"}),
        chat_settings=SimpleNamespace(
            text_commands_enabled=True,
            text_commands_locale="ru",
            custom_rp_enabled=False,
            smart_triggers_enabled=False,
            top_limit_default=10,
            top_limit_max=50,
        ),
        session_factory=object(),
    )

    send_user_stats.assert_awaited_once()
    assert send_user_stats.await_args.kwargs["user_id"] == 77
    send_me_stats.assert_not_awaited()
