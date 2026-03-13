from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.domain.entities import UserChatAward
from selara.presentation.handlers import stats as stats_module


@pytest.mark.asyncio
async def test_build_profile_awards_message_strips_iris_prefix_and_numbers_rows(monkeypatch) -> None:
    activity_repo = SimpleNamespace(
        list_user_chat_awards=AsyncMock(
            return_value=[
                UserChatAward(
                    id=1,
                    chat_id=777,
                    user_id=10,
                    title="🎗₁ Ждун яйца",
                    granted_by_user_id=None,
                    created_at=datetime(2026, 3, 7, 10, 0, tzinfo=timezone.utc),
                ),
                UserChatAward(
                    id=2,
                    chat_id=777,
                    user_id=10,
                    title="Лучшая шутка",
                    granted_by_user_id=11,
                    created_at=datetime(2026, 3, 6, 10, 0, tzinfo=timezone.utc),
                ),
            ]
        )
    )
    monkeypatch.setattr(stats_module, "_resolve_profile_mention", AsyncMock(return_value="Target"))
    monkeypatch.setattr(stats_module, "_format_iris_import_date", lambda *_args, **_kwargs: "07.03.2026")
    monkeypatch.setattr(stats_module, "format_elapsed_compact", lambda *_args, **_kwargs: "5 дн 22 ч назад")

    text = await stats_module._build_profile_awards_message(
        activity_repo=activity_repo,
        chat_id=777,
        user_id=10,
        timezone_name="Asia/Barnaul",
    )

    assert text == (
        "<b>Награды:</b> Target\n"
        "1. Ждун яйца — 07.03.2026 • 5 дн 22 ч назад\n"
        "2. Лучшая шутка — 07.03.2026 • 5 дн 22 ч назад"
    )
