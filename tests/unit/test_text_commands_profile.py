from types import SimpleNamespace

from selara.presentation.handlers.text_commands import (
    _extract_profile_about_text,
    _extract_reply_award_title,
    _is_reply_profile_lookup,
)


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
