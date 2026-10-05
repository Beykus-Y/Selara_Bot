from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.presentation.handlers.game import router as game_router


@pytest.mark.asyncio
async def test_whoami_group_guess_is_blocked_by_chat_write_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings.model_validate(
        {"BOT_TOKEN": "123456:TEST", "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost/test"}
    )
    chat_settings = replace(default_chat_settings(settings), chat_write_locked=True)
    store = SimpleNamespace(
        get_active_game_for_chat=AsyncMock(return_value=SimpleNamespace(game_id="g1")),
        whoami_guess_identity=AsyncMock(),
    )
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    monkeypatch.setattr(game_router, "_should_handle_whoami_group_text", lambda *_a, **_k: True)
    message = SimpleNamespace(
        text="я думаю, что я кот",
        chat=SimpleNamespace(id=-100, type="group"),
        from_user=SimpleNamespace(id=1, username="a", first_name="A", last_name=None),
        reply=AsyncMock(),
    )

    await game_router.whoami_group_message_handler(
        message, bot=object(), chat_settings=chat_settings, economy_repo=object(), activity_repo=object()
    )

    store.whoami_guess_identity.assert_not_awaited()
    message.reply.assert_awaited_once()
