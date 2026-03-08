import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.filters import CommandObject

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.domain.entities import ChatRoleDefinition
from selara.presentation.game_state import GameStore

game_router = importlib.import_module("selara.presentation.handlers.game.router")


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
            "BOT_USERNAME": "selara_test_bot",
            "WEB_AUTH_SECRET": "secret",
        }
    )


class FakeActivityRepo:
    def __init__(self, *, can_manage_games: bool) -> None:
        self._can_manage_games = can_manage_games
        self.bootstrap_calls = 0

    async def bootstrap_chat_owner_role(self, *, chat, user):
        self.bootstrap_calls += 1
        raise AssertionError("game permission checks must not bootstrap owner")

    async def get_effective_role_definition(self, *, chat_id: int, user_id: int):
        if not self._can_manage_games:
            return None
        return ChatRoleDefinition(
            chat_id=chat_id,
            role_code="game_master",
            title_ru="Game Master",
            rank=50,
            permissions=("manage_games",),
            is_system=False,
        )

    async def get_chat_display_name(self, *, chat_id: int, user_id: int):
        return None


class FakeMessage:
    def __init__(self, *, user_id: int = 7) -> None:
        self.chat = SimpleNamespace(id=-100500, type="group", title="Game Chat")
        self.from_user = SimpleNamespace(
            id=user_id,
            username="tester",
            first_name="Tester",
            last_name=None,
            is_bot=False,
        )
        self.answers: list[tuple[str, dict]] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append((text, kwargs))
        return SimpleNamespace(message_id=321)


class FakeCallbackQuery:
    def __init__(self, *, user_id: int = 7, data: str = "game:new:dice:u7") -> None:
        self.data = data
        self.from_user = SimpleNamespace(
            id=user_id,
            username="tester",
            first_name="Tester",
            last_name=None,
            is_bot=False,
        )
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=-100500, type="group", title="Game Chat"),
            message_id=777,
        )
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


@pytest.mark.asyncio
async def test_game_command_blocks_selection_without_manage_games(monkeypatch) -> None:
    store = GameStore()
    monkeypatch.setattr(game_router, "GAME_STORE", store)

    message = FakeMessage()
    repo = FakeActivityRepo(can_manage_games=False)

    await game_router.game_command(
        message,
        bot=SimpleNamespace(),
        command=CommandObject(prefix="/", command="game", mention=None, args=""),
        chat_settings=default_chat_settings(_settings()),
        activity_repo=repo,
    )

    assert repo.bootstrap_calls == 0
    assert message.answers == [("Недостаточно прав для запуска игр в этом чате.", {})]
    assert await store.list_active_games() == []


@pytest.mark.asyncio
async def test_game_command_creates_lobby_with_manage_games(monkeypatch) -> None:
    store = GameStore()
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    monkeypatch.setattr(game_router, "_get_bot_username", AsyncMock(return_value="selara_test_bot"))

    message = FakeMessage()
    repo = FakeActivityRepo(can_manage_games=True)

    await game_router.game_command(
        message,
        bot=SimpleNamespace(),
        command=CommandObject(prefix="/", command="game", mention=None, args="dice"),
        chat_settings=default_chat_settings(_settings()),
        activity_repo=repo,
    )

    active_games = await store.list_active_games()
    assert repo.bootstrap_calls == 0
    assert len(active_games) == 1
    assert active_games[0].kind == "dice"
    assert message.answers
    assert "<b>Дуэль кубиков</b>" in message.answers[0][0]


@pytest.mark.asyncio
async def test_game_new_callback_blocks_without_manage_games(monkeypatch) -> None:
    store = GameStore()
    monkeypatch.setattr(game_router, "GAME_STORE", store)

    query = FakeCallbackQuery()
    repo = FakeActivityRepo(can_manage_games=False)

    await game_router.game_new_callback(
        query,
        bot=SimpleNamespace(),
        chat_settings=default_chat_settings(_settings()),
        activity_repo=repo,
    )

    assert repo.bootstrap_calls == 0
    assert query.answers == [("Недостаточно прав для запуска игр в этом чате.", True)]
    assert await store.list_active_games() == []


@pytest.mark.asyncio
async def test_game_new_callback_creates_lobby_with_manage_games(monkeypatch) -> None:
    store = GameStore()
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    safe_edit_mock = AsyncMock()
    monkeypatch.setattr(game_router, "_safe_edit_or_send_game_board", safe_edit_mock)

    query = FakeCallbackQuery()
    repo = FakeActivityRepo(can_manage_games=True)

    await game_router.game_new_callback(
        query,
        bot=SimpleNamespace(),
        chat_settings=default_chat_settings(_settings()),
        activity_repo=repo,
    )

    active_games = await store.list_active_games()
    assert repo.bootstrap_calls == 0
    assert len(active_games) == 1
    assert active_games[0].kind == "dice"
    assert query.answers[-1] == ("Игра создана", False)
    safe_edit_mock.assert_awaited_once()
