from datetime import datetime, timezone

from selara.domain.entities import ActivityStats
from selara.domain.value_objects import display_name, display_name_from_parts


def test_display_name_prefers_chat_display_name() -> None:
    stats = ActivityStats(
        chat_id=1,
        user_id=10,
        message_count=5,
        last_seen_at=datetime(2026, 2, 14, 10, 0, tzinfo=timezone.utc),
        username="real_username",
        first_name="Real",
        last_name="Name",
        chat_display_name="ЛокальныйНик",
    )
    assert display_name(stats) == "ЛокальныйНик"


def test_display_name_from_parts_prefers_telegram_name_over_username() -> None:
    value = display_name_from_parts(
        user_id=10,
        username="real_username",
        first_name="Real",
        last_name="Name",
        chat_display_name=None,
    )
    assert value == "Real Name"


def test_display_name_from_parts_fallback_to_username() -> None:
    value = display_name_from_parts(
        user_id=10,
        username="real_username",
        first_name=None,
        last_name=None,
        chat_display_name=None,
    )
    assert value == "@real_username"


def test_display_name_ignores_technical_fallback_alias() -> None:
    value = display_name_from_parts(
        user_id=2105984481,
        username="Jullusionist",
        first_name="Julla",
        last_name=None,
        chat_display_name="user:2105984481",
    )
    assert value == "Julla"
