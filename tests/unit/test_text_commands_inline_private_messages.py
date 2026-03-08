from datetime import datetime, timedelta, timezone

import pytest

from selara.domain.entities import UserSnapshot
from selara.presentation.handlers import text_commands
from selara.presentation.handlers.text_commands import (
    _build_inline_private_target_usernames,
    _cleanup_inline_private_pending,
    _extract_inline_private_usernames,
    _inline_private_callback_data,
    _parse_inline_private_payload,
    _inline_private_result_title,
    _resolve_inline_private_receivers,
    _split_inline_private_text,
)


def test_parse_inline_private_payload_multiple_receivers() -> None:
    payload = _parse_inline_private_payload(
        "@user1 @user2 РїСЂРёРІРµС‚ СЌС‚Рѕ С‚РµСЃС‚",
        bot_username="selara_ru_bot",
    )
    assert payload is not None
    assert payload.receiver_usernames == ("user1", "user2")
    assert payload.text == "РїСЂРёРІРµС‚ СЌС‚Рѕ С‚РµСЃС‚"


def test_parse_inline_private_payload_skips_bot_mention() -> None:
    payload = _parse_inline_private_payload(
        "@selara_ru_bot @user1 hello @user2 world",
        bot_username="selara_ru_bot",
    )
    assert payload is not None
    assert payload.receiver_usernames == ("user1", "user2")
    assert payload.text == "hello world"


def test_parse_inline_private_payload_allows_text_without_receivers() -> None:
    payload = _parse_inline_private_payload(
        "РїСЂРёРІРµС‚ Р±РµР· С‚РµРіРѕРІ",
        bot_username="selara_ru_bot",
    )
    assert payload is not None
    assert payload.receiver_usernames == ()
    assert payload.text == "РїСЂРёРІРµС‚ Р±РµР· С‚РµРіРѕРІ"


def test_split_inline_private_text_respects_180_limit_and_words() -> None:
    text = " ".join(["СЃР»РѕРІРѕ"] * 90)
    chunks = _split_inline_private_text(text, max_len=180)
    assert chunks
    assert all(len(chunk) <= 180 for chunk in chunks)
    assert " ".join(chunks) == " ".join(text.split())


class _FakeInlineRepo:
    def __init__(self) -> None:
        self._known: dict[str, UserSnapshot] = {
            "user1": UserSnapshot(
                telegram_user_id=101,
                username="user1",
                first_name="One",
                last_name=None,
                is_bot=False,
                chat_display_name="OneInChat",
            ),
            "user2": UserSnapshot(
                telegram_user_id=202,
                username="user2",
                first_name="Two",
                last_name=None,
                is_bot=False,
                chat_display_name=None,
            ),
        }

    async def find_shared_group_user_by_username(self, *, sender_user_id: int, username: str) -> UserSnapshot | None:
        _ = sender_user_id
        return self._known.get(username.lstrip("@").lower())


@pytest.mark.asyncio
async def test_resolve_inline_private_receivers_keeps_known_and_skips_unknown() -> None:
    repo = _FakeInlineRepo()
    receivers = await _resolve_inline_private_receivers(
        repo,
        sender_user_id=999,
        receiver_usernames=("user1", "unknown"),
    )
    assert [item.telegram_user_id for item in receivers] == [101]


def test_inline_private_callback_data_does_not_store_message_text() -> None:
    callback_data = _inline_private_callback_data("123e4567-e89b-12d3-a456-426614174000")
    assert callback_data.startswith("ipm:")
    assert "СЃРµРєСЂРµС‚РЅС‹Р№ С‚РµРєСЃС‚" not in callback_data


def test_inline_private_result_title_for_single_receiver() -> None:
    title = _inline_private_result_title(("user1",))
    assert "@user1" in title
    assert "2-" not in title


def test_inline_private_result_title_for_multiple_receivers() -> None:
    title = _inline_private_result_title(("user1", "user2"))
    assert "2-" in title
    assert "@user1" not in title


def test_extract_inline_private_usernames_from_group_message() -> None:
    text = "рџ“© Р›РёС‡РЅРѕРµ СЃРѕРѕР±С‰РµРЅРёРµ РґР»СЏ РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№: @user1, @USER_2"
    assert _extract_inline_private_usernames(text) == {"user1", "user_2"}


def test_build_inline_private_target_usernames_order() -> None:
    targets = _build_inline_private_target_usernames(("user1", "user2", "user3"))
    assert targets == [("user1", "user2", "user3"), ("user1",), ("user2",), ("user3",)]


def test_cleanup_inline_private_runtime_caches_expires_old_entries() -> None:
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=2)
    recent = now - timedelta(seconds=1)
    old_page_key = ("old-message", 1)
    recent_page_key = ("recent-message", 2)

    original_last_sent = dict(text_commands._INLINE_PM_LAST_SENT_AT)
    original_alert_pages = dict(text_commands._INLINE_PM_ALERT_PAGE)
    original_pending = dict(text_commands._INLINE_PM_PENDING)
    try:
        text_commands._INLINE_PM_LAST_SENT_AT.clear()
        text_commands._INLINE_PM_ALERT_PAGE.clear()
        text_commands._INLINE_PM_PENDING.clear()

        text_commands._INLINE_PM_LAST_SENT_AT.update({1: old, 2: recent})
        text_commands._INLINE_PM_ALERT_PAGE.update(
            {
                old_page_key: (0, old),
                recent_page_key: (0, recent),
            }
        )

        _cleanup_inline_private_pending(now=now)

        assert 1 not in text_commands._INLINE_PM_LAST_SENT_AT
        assert 2 in text_commands._INLINE_PM_LAST_SENT_AT
        assert old_page_key not in text_commands._INLINE_PM_ALERT_PAGE
        assert recent_page_key in text_commands._INLINE_PM_ALERT_PAGE
    finally:
        text_commands._INLINE_PM_LAST_SENT_AT.clear()
        text_commands._INLINE_PM_LAST_SENT_AT.update(original_last_sent)
        text_commands._INLINE_PM_ALERT_PAGE.clear()
        text_commands._INLINE_PM_ALERT_PAGE.update(original_alert_pages)
        text_commands._INLINE_PM_PENDING.clear()
        text_commands._INLINE_PM_PENDING.update(original_pending)
