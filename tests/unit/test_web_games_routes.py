from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from selara.core.chat_settings import ChatSettings, default_chat_settings
from selara.core.config import Settings
from selara.domain.entities import ChatRoleDefinition, ChatSnapshot, UserChatOverview, UserSnapshot
from selara.presentation.game_state import GameStore
from selara.web import app as web_app_module


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
            "BOT_USERNAME": "selara_test_bot",
            "WEB_AUTH_SECRET": "secret",
            "WEB_BASE_URL": "http://127.0.0.1:8080",
        }
    )


def _overview(chat_id: int, title: str, *, bot_role: str | None = None) -> UserChatOverview:
    return UserChatOverview(
        chat_id=chat_id,
        chat_type="group",
        chat_title=title,
        bot_role=bot_role,
        message_count=None,
        last_seen_at=None,
    )


@dataclass
class WebRepoState:
    settings: Settings
    user: UserSnapshot
    admin_groups: list[UserChatOverview] = field(default_factory=list)
    activity_groups: list[UserChatOverview] = field(default_factory=list)
    manageable_groups: list[UserChatOverview] = field(default_factory=list)
    role_definitions: dict[tuple[int, int], ChatRoleDefinition] = field(default_factory=dict)
    display_names: dict[tuple[int, int], str] = field(default_factory=dict)
    chat_settings_by_chat: dict[int, ChatSettings] = field(default_factory=dict)
    upserted_chat_settings: list[tuple[ChatSnapshot, dict[str, object]]] = field(default_factory=list)
    bootstrap_calls: int = 0


class FakeActivityRepo:
    def __init__(self, state: WebRepoState) -> None:
        self._state = state

    async def list_user_admin_chats(self, *, user_id: int):
        return list(self._state.admin_groups)

    async def list_user_activity_chats(self, *, user_id: int, limit: int = 50):
        return list(self._state.activity_groups)

    async def list_user_manageable_game_chats(self, *, user_id: int):
        return list(self._state.manageable_groups)

    async def get_chat_settings(self, *, chat_id: int):
        return self._state.chat_settings_by_chat.get(chat_id)

    async def get_chat_display_name(self, *, chat_id: int, user_id: int):
        return self._state.display_names.get((chat_id, user_id))

    async def get_effective_role_definition(self, *, chat_id: int, user_id: int):
        return self._state.role_definitions.get((chat_id, user_id))

    async def upsert_chat_settings(self, *, chat: ChatSnapshot, values: dict[str, object]):
        current = self._state.chat_settings_by_chat.get(chat.telegram_chat_id) or default_chat_settings(self._state.settings)
        updated = replace(current, **values)
        self._state.chat_settings_by_chat[chat.telegram_chat_id] = updated
        self._state.upserted_chat_settings.append((chat, dict(values)))
        return updated

    async def bootstrap_chat_owner_role(self, *, chat: ChatSnapshot, user: UserSnapshot):
        self._state.bootstrap_calls += 1
        return None, False


class FakeEconomyRepo:
    def __init__(self, state: WebRepoState) -> None:
        self._state = state


class FakeWebAuthRepo:
    def __init__(self, state: WebRepoState) -> None:
        self._state = state

    async def get_user_by_session(self, *, session_digest: str, now, touch: bool):
        return self._state.user


class _FakeBotSession:
    async def close(self) -> None:
        return None


class FakeBot:
    instances: list["FakeBot"] = []
    sent_messages: list[dict[str, object]] = []

    def __init__(self, token: str) -> None:
        self.token = token
        self.session = _FakeBotSession()
        FakeBot.instances.append(self)

    async def send_message(self, chat_id: int, text: str, **kwargs):
        FakeBot.sent_messages.append({"chat_id": chat_id, "text": text, "kwargs": kwargs})
        return SimpleNamespace(message_id=999)


class DummySession:
    async def commit(self) -> None:
        return None


class DummySessionFactory:
    def __call__(self):
        session = DummySession()

        class _Manager:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Manager()


@asynccontextmanager
async def _web_client(monkeypatch, state: WebRepoState):
    settings = state.settings
    store = GameStore()
    safe_edit_mock = AsyncMock()
    send_roles_mock = AsyncMock(return_value=0)
    grant_rewards_mock = AsyncMock(return_value=None)

    FakeBot.instances = []
    FakeBot.sent_messages = []

    monkeypatch.setattr(web_app_module, "SqlAlchemyActivityRepository", lambda session: FakeActivityRepo(state))
    monkeypatch.setattr(web_app_module, "SqlAlchemyEconomyRepository", lambda session: FakeEconomyRepo(state))
    monkeypatch.setattr(web_app_module, "SqlAlchemyWebAuthRepository", lambda session: FakeWebAuthRepo(state))
    monkeypatch.setattr(web_app_module, "Bot", FakeBot)
    monkeypatch.setattr(web_app_module, "GAME_STORE", store)
    monkeypatch.setattr(web_app_module.game_router_module, "_safe_edit_or_send_game_board", safe_edit_mock)
    monkeypatch.setattr(web_app_module.game_router_module, "_send_roles_to_private", send_roles_mock)
    monkeypatch.setattr(web_app_module.game_router_module, "_send_game_feed_event", AsyncMock())
    monkeypatch.setattr(web_app_module.game_router_module, "_sync_quiz_feed_message", AsyncMock())
    monkeypatch.setattr(web_app_module.game_router_module, "_notify_mafia_night_actions", AsyncMock())
    monkeypatch.setattr(web_app_module.game_router_module, "_notify_bunker_reveal_turn", AsyncMock())
    monkeypatch.setattr(web_app_module.game_router_module, "_grant_game_rewards_if_needed", grant_rewards_mock)
    monkeypatch.setattr(web_app_module.game_router_module, "_schedule_phase_timer", lambda bot, game, chat_settings: None)

    app = web_app_module.create_web_app(settings=settings, session_factory=DummySessionFactory())
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.web_session_cookie_name, "session-token")

    try:
        yield client, store, safe_edit_mock, send_roles_mock
    finally:
        await client.aclose()
        await app.router.shutdown()


async def _create_started_whoami_game(store: GameStore, *, owner_user_id: int, owner_label: str, chat_id: int, chat_title: str):
    game, error = await store.create_lobby(
        kind="whoami",
        chat_id=chat_id,
        chat_title=chat_title,
        owner_user_id=owner_user_id,
        owner_label=owner_label,
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    for user_id in [303, 404]:
        joined_game, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=f"u{user_id}")
        assert joined_game is not None
        assert status == "joined"

    started_game, start_error = await store.start(game_id=game.game_id)
    assert start_error is None
    assert started_game is not None
    assert started_game.kind == "whoami"
    assert started_game.status == "started"

    started_game.roles = {
        owner_user_id: "Любовница",
        303: "Чайник",
        404: "Ложка",
    }
    return started_game


@pytest.mark.asyncio
async def test_games_page_shows_manage_games_chat_without_bootstrap(monkeypatch) -> None:
    settings = _settings()
    state = WebRepoState(
        settings=settings,
        user=UserSnapshot(telegram_user_id=77, username="gm", first_name="Game", last_name="Master", is_bot=False),
        manageable_groups=[_overview(-1001, "Manage Games Chat", bot_role="game_master")],
    )

    async with _web_client(monkeypatch, state) as (client, _store, _safe_edit_mock, _send_roles_mock):
        response = await client.get("/app/games")

    assert response.status_code == 200
    assert "Manage Games Chat" in response.text
    assert state.bootstrap_calls == 0


@pytest.mark.asyncio
async def test_games_live_reports_changed_and_unchanged_without_bootstrap(monkeypatch) -> None:
    settings = _settings()
    state = WebRepoState(
        settings=settings,
        user=UserSnapshot(telegram_user_id=88, username="viewer", first_name="View", last_name="Er", is_bot=False),
        manageable_groups=[_overview(-1002, "Live Games Chat", bot_role="game_master")],
    )

    async with _web_client(monkeypatch, state) as (client, _store, _safe_edit_mock, _send_roles_mock):
        first = await client.get("/app/games/live")
        first_payload = first.json()
        second = await client.get("/app/games/live", params={"signature": first_payload["signature"]})

    assert first.status_code == 200
    assert first_payload["ok"] is True
    assert first_payload["changed"] is True
    assert isinstance(first_payload["html"], str) and first_payload["html"]
    assert second.status_code == 200
    assert second.json() == {"ok": True, "changed": False, "signature": first_payload["signature"]}
    assert state.bootstrap_calls == 0


@pytest.mark.asyncio
async def test_web_game_create_requires_manage_games(monkeypatch) -> None:
    settings = _settings()
    state = WebRepoState(
        settings=settings,
        user=UserSnapshot(telegram_user_id=91, username="plain", first_name="Plain", last_name="User", is_bot=False),
    )

    async with _web_client(monkeypatch, state) as (client, _store, _safe_edit_mock, _send_roles_mock):
        response = await client.post(
            "/app/games/create",
            data={"kind": "dice", "chat_id": "-2001"},
            headers={"accept": "application/json"},
        )

    assert response.status_code == 403
    assert response.json()["ok"] is False
    assert response.json()["message"] == "Недостаточно прав для запуска игры в этом чате."


@pytest.mark.asyncio
async def test_web_game_create_allows_manage_games_chat(monkeypatch) -> None:
    settings = _settings()
    state = WebRepoState(
        settings=settings,
        user=UserSnapshot(telegram_user_id=92, username="gm", first_name="Game", last_name="Manager", is_bot=False),
        manageable_groups=[_overview(-2002, "Create Chat", bot_role="game_master")],
        chat_settings_by_chat={-2002: default_chat_settings(settings)},
    )

    async with _web_client(monkeypatch, state) as (client, store, safe_edit_mock, _send_roles_mock):
        response = await client.post(
            "/app/games/create",
            data={"kind": "dice", "chat_id": "-2002"},
            headers={"accept": "application/json"},
        )
        active_games = await store.list_active_games()

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert len(active_games) == 1
    assert active_games[0].chat_id == -2002
    safe_edit_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_games_page_and_action_work_for_active_member_without_activity(monkeypatch) -> None:
    settings = _settings()
    state = WebRepoState(
        settings=settings,
        user=UserSnapshot(telegram_user_id=99, username="member", first_name="Dice", last_name="Player", is_bot=False),
    )

    async with _web_client(monkeypatch, state) as (client, store, safe_edit_mock, _send_roles_mock):
        game, error = await store.create_lobby(
            kind="dice",
            chat_id=-3001,
            chat_title="Hidden Dice Chat",
            owner_user_id=1,
            owner_label="owner",
            reveal_eliminated_role=True,
        )
        assert error is None
        assert game is not None
        await store.join(game_id=game.game_id, user_id=99, user_label="member")
        started_game, start_error = await store.start(game_id=game.game_id)
        assert start_error is None
        assert started_game is not None

        page = await client.get("/app/games")
        action = await client.post(
            "/app/games/action",
            data={"callback_data": f"gdice:{game.game_id}:roll"},
            headers={"accept": "application/json"},
        )

    assert page.status_code == 200
    assert "Hidden Dice Chat" in page.text
    assert action.status_code == 200
    assert action.json()["ok"] is True
    assert "Бросок" in action.json()["message"]
    safe_edit_mock.assert_awaited()


@pytest.mark.asyncio
async def test_games_page_and_join_work_for_visible_chat_member_before_join(monkeypatch) -> None:
    settings = _settings()
    state = WebRepoState(
        settings=settings,
        user=UserSnapshot(telegram_user_id=111, username="viewer", first_name="View", last_name="Only", is_bot=False),
        activity_groups=[_overview(-3301, "Visible Lobby Chat")],
    )

    async with _web_client(monkeypatch, state) as (client, store, safe_edit_mock, _send_roles_mock):
        game, error = await store.create_lobby(
            kind="dice",
            chat_id=-3301,
            chat_title="Visible Lobby Chat",
            owner_user_id=1,
            owner_label="owner",
            reveal_eliminated_role=True,
        )
        assert error is None
        assert game is not None

        page = await client.get("/app/games")
        action = await client.post(
            "/app/games/action",
            data={"callback_data": f"game:join:{game.game_id}"},
            headers={"accept": "application/json"},
        )
        updated_game = await store.get_game(game.game_id)

    assert page.status_code == 200
    assert "Visible Lobby Chat" in page.text
    assert action.status_code == 200
    assert action.json()["ok"] is True
    assert "присоединились" in action.json()["message"]
    assert updated_game is not None
    assert updated_game.players[111] == "@viewer"
    safe_edit_mock.assert_awaited()


@pytest.mark.asyncio
async def test_web_reveal_elim_persists_chat_default(monkeypatch) -> None:
    settings = _settings()
    state = WebRepoState(
        settings=settings,
        user=UserSnapshot(telegram_user_id=101, username="gm", first_name="Mafia", last_name="Host", is_bot=False),
        manageable_groups=[_overview(-4001, "Mafia Chat", bot_role="game_master")],
        chat_settings_by_chat={-4001: default_chat_settings(settings)},
    )

    async with _web_client(monkeypatch, state) as (client, store, safe_edit_mock, _send_roles_mock):
        game, error = await store.create_lobby(
            kind="mafia",
            chat_id=-4001,
            chat_title="Mafia Chat",
            owner_user_id=55,
            owner_label="owner",
            reveal_eliminated_role=True,
        )
        assert error is None
        assert game is not None

        response = await client.post(
            "/app/games/action",
            data={"callback_data": f"gcfg:{game.game_id}:reveal_elim"},
            headers={"accept": "application/json"},
        )
        updated_game = await store.get_game(game.game_id)

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert updated_game is not None
    assert updated_game.reveal_eliminated_role is False
    assert state.upserted_chat_settings
    assert state.upserted_chat_settings[-1][1]["mafia_reveal_eliminated_role"] is False
    safe_edit_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_web_start_returns_warning_and_notifies_chat_on_failed_dm(monkeypatch) -> None:
    settings = _settings()
    user = UserSnapshot(telegram_user_id=202, username="owner", first_name="Owner", last_name=None, is_bot=False)
    state = WebRepoState(
        settings=settings,
        user=user,
    )

    async with _web_client(monkeypatch, state) as (client, store, safe_edit_mock, send_roles_mock):
        send_roles_mock.return_value = 2
        game, error = await store.create_lobby(
            kind="whoami",
            chat_id=-5001,
            chat_title="Whoami Chat",
            owner_user_id=user.telegram_user_id,
            owner_label="owner",
            reveal_eliminated_role=True,
        )
        assert error is None
        assert game is not None
        await store.join(game_id=game.game_id, user_id=303, user_label="other")
        await store.join(game_id=game.game_id, user_id=404, user_label="third")

        response = await client.post(
            "/app/games/action",
            data={"callback_data": f"game:start:{game.game_id}"},
            headers={"accept": "application/json"},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "Не удалось отправить ЛС" in response.json()["message"]
    assert any(
        item["chat_id"] == -5001 and "Не удалось отправить ЛС" in str(item["text"])
        for item in FakeBot.sent_messages
    )
    safe_edit_mock.assert_awaited()


@pytest.mark.asyncio
async def test_web_whoami_midgame_guess_updates_board_without_group_feed(monkeypatch) -> None:
    settings = _settings()
    user = UserSnapshot(telegram_user_id=202, username="owner", first_name="Owner", last_name=None, is_bot=False)
    state = WebRepoState(settings=settings, user=user)

    async with _web_client(monkeypatch, state) as (client, store, safe_edit_mock, _send_roles_mock):
        started_game = await _create_started_whoami_game(
            store,
            owner_user_id=user.telegram_user_id,
            owner_label="owner",
            chat_id=-5101,
            chat_title="Whoami Chat",
        )
        started_game.whoami_turn_order = [user.telegram_user_id, 303, 404]
        started_game.whoami_current_actor_index = 0
        started_game.whoami_current_actor_user_id = user.telegram_user_id
        started_game.phase = "whoami_ask"

        feed_mock = web_app_module.game_router_module._send_game_feed_event
        response = await client.post(
            "/app/games/action",
            data={"action": "whoami_guess", "game_id": started_game.game_id, "guess_text": "любовница"},
            headers={"accept": "application/json"},
        )
        updated_game = await store.get_game(started_game.game_id)

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "Карточка разгадана" in response.json()["message"]
    assert updated_game is not None
    assert updated_game.status == "started"
    assert updated_game.whoami_solved_user_ids == {user.telegram_user_id}
    feed_mock.assert_not_awaited()
    safe_edit_mock.assert_awaited()
    note = safe_edit_mock.await_args_list[-1].kwargs["note"]
    assert "<b>Результат:</b> карточка разгадана." in note
    assert "Любовница" not in note
    assert "Догадка:" not in note


@pytest.mark.asyncio
async def test_web_whoami_final_guess_sends_group_feed_once(monkeypatch) -> None:
    settings = _settings()
    user = UserSnapshot(telegram_user_id=202, username="owner", first_name="Owner", last_name=None, is_bot=False)
    state = WebRepoState(settings=settings, user=user)

    async with _web_client(monkeypatch, state) as (client, store, safe_edit_mock, _send_roles_mock):
        started_game = await _create_started_whoami_game(
            store,
            owner_user_id=user.telegram_user_id,
            owner_label="owner",
            chat_id=-5102,
            chat_title="Whoami Chat Final",
        )
        started_game.whoami_turn_order = [303, 404, user.telegram_user_id]
        started_game.whoami_current_actor_index = 2
        started_game.whoami_current_actor_user_id = user.telegram_user_id
        started_game.whoami_solved_user_ids = {303, 404}
        started_game.whoami_finish_order = [303, 404]
        started_game.phase = "whoami_ask"

        feed_mock = web_app_module.game_router_module._send_game_feed_event
        response = await client.post(
            "/app/games/action",
            data={"action": "whoami_guess", "game_id": started_game.game_id, "guess_text": "любовница"},
            headers={"accept": "application/json"},
        )
        updated_game = await store.get_game(started_game.game_id)

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "Все карточки разгаданы" in response.json()["message"]
    assert updated_game is not None
    assert updated_game.status == "finished"
    assert updated_game.whoami_finish_order == [303, 404, user.telegram_user_id]
    safe_edit_mock.assert_awaited()
    feed_mock.assert_awaited_once()
    assert "Все карточки разгаданы" in feed_mock.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_web_bred_category_pick_uses_board_only_and_refreshes_label(monkeypatch) -> None:
    settings = _settings()
    user = UserSnapshot(telegram_user_id=707, username=None, first_name="Host", last_name=None, is_bot=False)
    state = WebRepoState(
        settings=settings,
        user=user,
        display_names={(-5201, 707): "Ведущий с сайта"},
    )

    async with _web_client(monkeypatch, state) as (client, store, safe_edit_mock, _send_roles_mock):
        game, error = await store.create_lobby(
            kind="bredovukha",
            chat_id=-5201,
            chat_title="Bred Chat",
            owner_user_id=user.telegram_user_id,
            owner_label="user:707",
            reveal_eliminated_role=True,
        )
        assert error is None
        assert game is not None
        await store.join(game_id=game.game_id, user_id=808, user_label="u808")
        await store.join(game_id=game.game_id, user_id=909, user_label="u909")
        started_game, start_error = await store.start(game_id=game.game_id)
        assert start_error is None
        assert started_game is not None
        assert started_game.phase == "category_pick"
        started_game.bred_current_selector_user_id = user.telegram_user_id

        feed_mock = web_app_module.game_router_module._send_game_feed_event
        response = await client.post(
            "/app/games/action",
            data={"callback_data": f"gbredcat:{game.game_id}:0"},
            headers={"accept": "application/json"},
        )
        updated_game = await store.get_game(game.game_id)

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "Категория выбрана" in response.json()["message"]
    assert updated_game is not None
    assert updated_game.phase == "private_answers"
    assert updated_game.players[user.telegram_user_id] == "Ведущий с сайта"
    feed_mock.assert_not_awaited()
    safe_edit_mock.assert_awaited()
