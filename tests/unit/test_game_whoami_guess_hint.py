"""Regression test for docs/GAMES_UX_MODERNIZATION_TODO.md Stage 3, the
exact case Ilya named: WhoAmI's identity-guess required a memorized magic
phrase ("Я думаю, что я ...") with zero button fallback and zero in-context
hint -- a message that matched neither the guess patterns nor a "?" question
was silently dropped. Now the current actor gets an explicit hint instead of
silence."""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from selara.presentation.game_state import GameStore

game_router = importlib.import_module("selara.presentation.handlers.game.router")


class FakeMessage:
    def __init__(self, *, user_id: int, chat_id: int, text: str) -> None:
        self.chat = SimpleNamespace(id=chat_id, type="group")
        self.from_user = SimpleNamespace(id=user_id, username="u", first_name="U", last_name=None, is_bot=False)
        self.text = text
        self.replies: list[str] = []

    async def reply(self, text: str, **kwargs):
        self.replies.append(text)


class FakeActivityRepo:
    async def get_chat_display_name(self, *, chat_id, user_id):
        return None


def _chat_settings():
    from selara.core.chat_settings import default_chat_settings
    from selara.core.config import Settings

    settings = Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
            "BOT_USERNAME": "selara_test_bot",
            "WEB_AUTH_SECRET": "secret",
        }
    )
    return default_chat_settings(settings)


@pytest.mark.asyncio
async def test_off_pattern_message_from_current_actor_gets_a_hint(monkeypatch) -> None:
    store = GameStore()
    monkeypatch.setattr(game_router, "GAME_STORE", store)

    game, error = await store.create_lobby(
        kind="whoami", chat_id=-100, chat_title="chat",
        owner_user_id=1, owner_label="owner", reveal_eliminated_role=True,
    )
    assert error is None
    for uid in (2, 3):
        await store.join(game_id=game.game_id, user_id=uid, user_label=f"u{uid}")
    started, start_error = await store.start(game_id=game.game_id)
    assert start_error is None
    assert started.phase == "whoami_ask"
    actor_id = started.whoami_current_actor_user_id
    assert actor_id is not None

    message = FakeMessage(user_id=actor_id, chat_id=-100, text="кто я")
    try:
        await game_router.whoami_group_message_handler(
            message, bot=SimpleNamespace(), chat_settings=_chat_settings(),
            economy_repo=SimpleNamespace(), activity_repo=FakeActivityRepo(),
        )
    except Exception as exc:
        if type(exc).__name__ != "SkipHandler":
            raise
        pytest.fail("expected a hint reply, not SkipHandler, for the current actor's off-pattern text")

    assert message.replies, "expected a hint reply"
    assert "?" in message.replies[0]
    assert "догадка" in message.replies[0]
