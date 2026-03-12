from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from selara.domain.entities import ActivityStats
from selara.presentation.handlers import stats as stats_module


class _DummyMessage:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(type="group", id=-100321, title="Test chat")
        self.answers: list[tuple[str, dict[str, object]]] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append((text, kwargs))


class _FakeActivityRepo:
    def __init__(self, rows: list[ActivityStats]) -> None:
        self._rows = rows
        self.called_with = None

    async def list_inactive_members(self, *, chat_id: int, inactive_since: datetime):
        self.called_with = (chat_id, inactive_since)
        return list(self._rows)


@pytest.mark.asyncio
async def test_send_inactive_members_prefers_bot_nick_then_name_then_username(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(stats_module, "_now_utc", lambda: now)

    rows = [
        ActivityStats(
            chat_id=-100321,
            user_id=11,
            message_count=5,
            last_seen_at=now - timedelta(days=4, hours=2),
            username="nick_fallback",
            first_name="Wrong",
            last_name="Priority",
            chat_display_name="Ник в боте",
        ),
        ActivityStats(
            chat_id=-100321,
            user_id=22,
            message_count=2,
            last_seen_at=now - timedelta(days=2, hours=1),
            username="name_fallback",
            first_name="Имя",
            last_name="Фамилия",
            chat_display_name=None,
        ),
        ActivityStats(
            chat_id=-100321,
            user_id=33,
            message_count=1,
            last_seen_at=now - timedelta(days=1, hours=3),
            username="only_username",
            first_name=None,
            last_name=None,
            chat_display_name=None,
        ),
        ActivityStats(
            chat_id=-100321,
            user_id=44,
            message_count=1,
            last_seen_at=now - timedelta(days=1, hours=1),
            username=None,
            first_name=None,
            last_name=None,
            chat_display_name=None,
        ),
    ]
    repo = _FakeActivityRepo(rows)
    message = _DummyMessage()

    await stats_module.send_inactive_members(message, repo)

    assert repo.called_with == (-100321, now - timedelta(days=1))
    assert len(message.answers) == 1
    text, kwargs = message.answers[0]
    assert "Ник в боте" in text
    assert "Имя Фамилия" in text
    assert "@only_username" in text
    assert "Неизвестный" in text
    assert "nick_fallback" not in text
    assert "name_fallback" not in text
    assert "🗓 <b>Всего неактивных:</b> 4" in text
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["disable_notification"] is True


@pytest.mark.asyncio
async def test_send_inactive_members_returns_empty_state(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(stats_module, "_now_utc", lambda: now)

    repo = _FakeActivityRepo([])
    message = _DummyMessage()

    await stats_module.send_inactive_members(message, repo)

    assert len(message.answers) == 1
    text, kwargs = message.answers[0]
    lowered = text.lower()
    assert "неактива" in lowered or "последних 24 часов" in lowered or "последние сутки" in lowered
    assert kwargs["parse_mode"] == "HTML"
