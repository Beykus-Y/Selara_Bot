"""Mini App home and chats endpoints expose the add-bot link derived from BOT_USERNAME.

The link used to be hardcoded to Selara_Bot in the frontend, so a deployment with another
BOT_USERNAME sent users to the wrong bot. These tests pin the API contract the pages rely on.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest

from selara.domain.entities import UserSnapshot
from selara.web import app as web_app_module

import test_web_chat_hub_routes as hub_test


class _HomeActivityRepo(hub_test.FakeActivityRepo):
    async def list_user_manageable_game_chats(self, *, user_id: int):
        _ = user_id
        return []


class _EmptyGameStore:
    async def list_active_games(self):
        return []

    async def list_recent_games_for_user(self, *, user_id: int, limit: int):
        _ = user_id, limit
        return []


@asynccontextmanager
async def _client(monkeypatch, state: hub_test.ChatHubState):
    monkeypatch.setattr(web_app_module, "SqlAlchemyActivityRepository", lambda session: _HomeActivityRepo(state))
    monkeypatch.setattr(web_app_module, "SqlAlchemyEconomyRepository", lambda session: hub_test.FakeEconomyRepo(state))
    monkeypatch.setattr(web_app_module, "SqlAlchemyWebAuthRepository", lambda session: hub_test.FakeWebAuthRepo(state))
    monkeypatch.setattr(web_app_module, "has_permission", hub_test._has_permission)
    monkeypatch.setattr(web_app_module, "GAME_STORE", _EmptyGameStore())

    app = web_app_module.create_web_app(settings=state.settings, session_factory=hub_test.DummySessionFactory())
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    client.cookies.set(state.settings.web_session_cookie_name, "session-token")
    try:
        yield client
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()


def _state(settings) -> hub_test.ChatHubState:
    return hub_test.ChatHubState(
        settings=settings,
        user=UserSnapshot(telegram_user_id=77, username="viewer", first_name="View", last_name="Er", is_bot=False),
        activity_groups=[hub_test._overview(-1001, "Selara Hub")],
    )


@pytest.mark.asyncio
async def test_home_and_chats_endpoints_expose_add_bot_link_from_bot_username(monkeypatch) -> None:
    settings = hub_test._settings()
    expected = "https://t.me/selara_test_bot?startgroup=true"

    async with _client(monkeypatch, _state(settings)) as client:
        home = await client.get("/api/miniapp/home")
        groups = await client.get("/api/miniapp/groups")

    assert home.status_code == 200
    assert home.json()["ok"] is True
    assert home.json()["page"]["bot_add_url"] == expected
    assert groups.status_code == 200
    assert groups.json()["ok"] is True
    assert groups.json()["page"]["bot_add_url"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("bot_username", ["other_deploy_bot", "selara_ru_bot"])
async def test_add_bot_link_follows_configured_bot_username(monkeypatch, bot_username: str) -> None:
    settings = hub_test._settings().model_copy(update={"bot_username": bot_username})

    async with _client(monkeypatch, _state(settings)) as client:
        home = await client.get("/api/miniapp/home")

    assert home.json()["page"]["bot_add_url"] == f"https://t.me/{bot_username}?startgroup=true"
