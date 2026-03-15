from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from selara.application.use_cases.iris_import import (
    parse_forwarded_awards_message,
    parse_forwarded_profile_message,
)
from selara.domain.entities import UserSnapshot
from selara.presentation.handlers.stats import (
    _PendingIrisImportSession,
    _build_iris_unrelated_message_text,
    _can_start_iris_import,
    _clear_pending_iris_import,
    _is_pending_iris_import_expired,
    _set_pending_iris_import,
    _validate_iris_forward_source,
    _validate_iris_message_step,
    _validate_iris_target_username,
    iris_missing_imports_command,
    pending_iris_import_handler,
)


def _text_link(url: str) -> SimpleNamespace:
    return SimpleNamespace(type="text_link", url=url)


def _forwarded_message(*, username: str | None, is_bot: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        forward_origin=SimpleNamespace(sender_user=SimpleNamespace(username=username, is_bot=is_bot)),
        forward_from=None,
        forward_from_chat=None,
        forward_sender_name=None,
    )


def test_parse_forwarded_profile_message_extracts_karma_first_seen_and_compact_activity() -> None:
    now = datetime(2026, 3, 12, 10, 0, tzinfo=timezone.utc)
    text = (
        "👤 Это пользователь Куколд\n"
        "Репутация: ✨ 0 | ➕ 14\n"
        "Первое появление: 18.01.2026 (1 месяц 21 дн)\n"
        "Последний актив: только что\n"
        "Актив (д|н|м|весь): 43 | 475 | 3,4k | 8,2k"
    )

    result = parse_forwarded_profile_message(
        text=text,
        entities=[_text_link("https://t.me/nigh_cord25")],
        timezone_name="Asia/Barnaul",
        now=now,
    )

    assert result.target_username == "nigh_cord25"
    assert result.karma_all_time == 14
    assert result.activity_1d == 43
    assert result.activity_7d == 475
    assert result.activity_30d == 3400
    assert result.activity_all == 8200
    assert result.last_seen_at == now
    assert result.first_seen_at.astimezone(ZoneInfo("Asia/Barnaul")).strftime("%d.%m.%Y %H:%M") == "18.01.2026 12:00"


def test_parse_forwarded_profile_message_parses_relative_last_activity() -> None:
    now = datetime(2026, 3, 12, 10, 0, tzinfo=timezone.utc)
    text = (
        "👤 Это пользователь Test\n"
        "Репутация: ✨ 0 | ➕ 3\n"
        "Первое появление: 18.01.2026\n"
        "Последний актив: 1 месяц 2 дн 3 ч 15 мин\n"
        "Актив (д|н|м|весь): 1 | 2 | 3 | 4"
    )

    result = parse_forwarded_profile_message(
        text=text,
        entities=[_text_link("https://t.me/test_user")],
        timezone_name="Asia/Barnaul",
        now=now,
    )

    assert result.last_seen_at == now - timedelta(days=32, hours=3, minutes=15)


def test_parse_forwarded_awards_message_extracts_titles_and_dates() -> None:
    text = (
        "🏆 Награды Куколд:\n\n"
        "1. 🎗₁ Ждун яйца | 01.03.2026\n"
        "2. 🎗₁ Возьми телефон, детка | 01.03.2026\n"
        "3. 🎗₁ Лучший влд | 01.02.2026"
    )

    result = parse_forwarded_awards_message(
        text=text,
        entities=[_text_link("https://t.me/nigh_cord25")],
        timezone_name="Asia/Barnaul",
    )

    assert result.target_username == "nigh_cord25"
    assert result.awards[0][0] == "Ждун яйца"
    assert result.awards[1][0] == "Возьми телефон, детка"
    assert result.awards[2][1].astimezone(ZoneInfo("Asia/Barnaul")).strftime("%d.%m.%Y") == "01.02.2026"


def test_parse_forwarded_awards_message_accepts_today_and_yesterday() -> None:
    now = datetime(2026, 3, 15, 15, 0, tzinfo=timezone.utc)
    text = (
        "🏆 Награды Leyak:\n\n"
        "1. 🎗₁ Архитектор что надо | сегодня в 22:50\n"
        "2. 🎗₁ Вчерашний тайтл | вчера в 21:10"
    )

    result = parse_forwarded_awards_message(
        text=text,
        entities=[_text_link("https://t.me/leyakkk")],
        timezone_name="Asia/Barnaul",
        now=now,
    )

    assert result.awards[0][1].astimezone(ZoneInfo("Asia/Barnaul")).strftime("%d.%m.%Y %H:%M") == "15.03.2026 22:50"
    assert result.awards[1][1].astimezone(ZoneInfo("Asia/Barnaul")).strftime("%d.%m.%Y %H:%M") == "14.03.2026 21:10"


def test_parse_forwarded_awards_message_allows_empty_awards_list() -> None:
    text = (
        "🏆 Награды Чужестранка:\n\n"
        "💬 Пока нет"
    )

    result = parse_forwarded_awards_message(
        text=text,
        entities=[_text_link("https://t.me/pollina16")],
        timezone_name="Asia/Barnaul",
    )

    assert result.target_username == "pollina16"
    assert result.awards == ()


def test_validate_iris_forward_source_requires_forwarded_message_and_exact_bot() -> None:
    not_forwarded = SimpleNamespace(
        forward_origin=None,
        forward_from=None,
        forward_from_chat=None,
        forward_sender_name=None,
    )
    wrong_bot = _forwarded_message(username="other_bot")
    correct_bot = _forwarded_message(username="iris_moon_bot")

    assert "именно ответ" in _validate_iris_forward_source(not_forwarded)
    assert "iris_moon_bot" in _validate_iris_forward_source(wrong_bot)
    assert _validate_iris_forward_source(correct_bot) is None


def test_validate_iris_message_step_rejects_wrong_order() -> None:
    awards_text = "🏆 Награды Test:\n1. 🎗₁ Награда | 01.03.2026"
    profile_text = "Первое появление: 18.01.2026\nАктив (д|н|м|весь): 1 | 2 | 3 | 4"

    assert "кто ты" in _validate_iris_message_step(expected_step="profile", text=awards_text, target_username="tester")
    assert "награды" in _validate_iris_message_step(expected_step="awards", text=profile_text, target_username="tester")


def test_validate_iris_target_username_rejects_mismatch() -> None:
    assert _validate_iris_target_username(expected_username="tester", actual_username="tester") is None
    assert "не к @tester" in _validate_iris_target_username(expected_username="tester", actual_username="other")


def test_can_start_iris_import_allows_self_and_privileged_foreign_import() -> None:
    assert _can_start_iris_import(actor_user_id=10, target_user_id=10, role_code=None) is True
    assert _can_start_iris_import(actor_user_id=10, target_user_id=20, role_code="participant") is False
    assert _can_start_iris_import(actor_user_id=10, target_user_id=20, role_code="owner") is True
    assert _can_start_iris_import(actor_user_id=10, target_user_id=20, role_code="co_owner") is True
    assert _can_start_iris_import(actor_user_id=10, target_user_id=20, role_code="senior_admin") is True


def test_pending_iris_import_session_expiry_detected() -> None:
    now = datetime(2026, 3, 12, 10, 0, tzinfo=timezone.utc)
    session = _PendingIrisImportSession(
        source_chat_id=-100,
        source_chat_type="group",
        source_chat_title="Test",
        target_user_id=20,
        target_username="tester",
        target_label="Tester",
        target_first_name="Test",
        target_last_name=None,
        target_chat_display_name=None,
        actor_user_id=10,
        step="profile",
        expires_at=now,
    )

    assert _is_pending_iris_import_expired(session, now=now) is True
    assert _is_pending_iris_import_expired(session, now=now - timedelta(seconds=1)) is False


class _DummyPrivateMessage:
    def __init__(self, *, user_id: int, text: str) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(type="private")
        self.text = text
        self.entities = ()
        self.caption = None
        self.caption_entities = ()
        self.forward_origin = None
        self.forward_from = None
        self.forward_from_chat = None
        self.forward_sender_name = None
        self.answers: list[tuple[str, dict[str, object]]] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append((text, kwargs))


class _DummyGroupMessage:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(id=-100500, type="group", title="Iris Group")
        self.from_user = SimpleNamespace(
            id=10,
            username="owner",
            first_name="Owner",
            last_name=None,
            is_bot=False,
        )
        self.answers: list[tuple[str, dict[str, object]]] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append((text, kwargs))


@pytest.mark.asyncio
async def test_pending_iris_import_handler_prompts_on_unrelated_private_message() -> None:
    user_id = 10
    _set_pending_iris_import(
        importer_user_id=user_id,
        session=_PendingIrisImportSession(
            source_chat_id=-100,
            source_chat_type="group",
            source_chat_title="Test",
            target_user_id=20,
            target_username="tester",
            target_label="Tester",
            target_first_name="Test",
            target_last_name=None,
            target_chat_display_name=None,
            actor_user_id=user_id,
            step="profile",
            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        ),
    )
    message = _DummyPrivateMessage(user_id=user_id, text="/login")

    try:
        await pending_iris_import_handler(
            message,
            activity_repo=object(),
            bot=object(),
            settings=SimpleNamespace(bot_timezone="Asia/Barnaul"),
        )
    finally:
        _clear_pending_iris_import(user_id)

    assert message.answers == [(_build_iris_unrelated_message_text(), {})]


@pytest.mark.asyncio
async def test_iris_missing_imports_command_lists_users(monkeypatch) -> None:
    message = _DummyGroupMessage()
    activity_repo = SimpleNamespace(
        list_chat_users_missing_iris_import=AsyncMock(
            return_value=[
                UserSnapshot(
                    telegram_user_id=20,
                    username="target",
                    first_name="Target",
                    last_name=None,
                    is_bot=False,
                    chat_display_name="Target",
                )
            ]
        )
    )

    monkeypatch.setattr(
        "selara.presentation.handlers.stats.get_actor_role_definition",
        AsyncMock(return_value=(SimpleNamespace(role_code="owner"), False)),
    )

    await iris_missing_imports_command(message, activity_repo=activity_repo, bot=SimpleNamespace())

    assert len(message.answers) == 1
    assert "не перенесён" in message.answers[0][0]
    assert "@target" in message.answers[0][0]
