"""Этап 5 (игры) sub-slice 8/N: exercise the real "ход" (in-game move) and
"завершение" (finish) actions through the browser against the extracted
`games.js`, closing those two parts of the roadmap checklist's Playwright
item — "создание" was covered by test_web_games_create_flow_browser.py.

There is no web-UI "join" action to test: joining a lobby happens only
through the Telegram bot (confirmed by grepping _games_dashboard.html for
any join control — none exists), the web panel is a companion viewer/
controller for games already running in Telegram, not a lobby entry point.

Every in-game action (roll a die, vote, finish the game, ...) renders as its
own tiny `<form data-game-action-form>` with a hidden `callback_data` and a
submit button, all funneled through the exact same delegated submit handler
in games.js already verified by the create-flow test. Rather than
hand-building a Jinja fixture again (which is what caused the
extra_scripts=["games.js"] omission bug in that earlier sub-slice), this
renders through the real /app/games route via the same GameStore-backed
harness as test_web_games_dashboard_browser.py, guaranteeing the page shape
matches production exactly.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from playwright.async_api import async_playwright

from selara.core.chat_settings import ChatSettings
from selara.core.config import Settings
from selara.domain.entities import UserChatOverview, UserSnapshot
from selara.presentation.game_state import GameStore
from selara.web import app as web_app_module

STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"


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
    manageable_groups: list[UserChatOverview] = field(default_factory=list)
    chat_settings_by_chat: dict[int, ChatSettings] = field(default_factory=dict)


class FakeActivityRepo:
    def __init__(self, state: WebRepoState) -> None:
        self._state = state

    async def list_user_admin_chats(self, *, user_id: int):
        return []

    async def list_user_activity_chats(self, *, user_id: int, limit: int = 50):
        return []

    async def list_user_manageable_game_chats(self, *, user_id: int):
        return list(self._state.manageable_groups)

    async def get_chat_settings(self, *, chat_id: int):
        return self._state.chat_settings_by_chat.get(chat_id)

    async def get_chat_display_name(self, *, chat_id: int, user_id: int):
        return None

    async def get_effective_role_definition(self, *, chat_id: int, user_id: int):
        return None


class FakeEconomyRepo:
    def __init__(self, state: WebRepoState) -> None:
        self._state = state


class FakeWebAuthRepo:
    def __init__(self, state: WebRepoState) -> None:
        self._state = state

    async def get_user_by_session(self, *, session_digest: str, now, touch: bool):
        return self._state.user


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
async def _web_client(monkeypatch, state: WebRepoState, *, store: GameStore | None = None):
    store = store or GameStore()

    monkeypatch.setattr(web_app_module, "SqlAlchemyActivityRepository", lambda session: FakeActivityRepo(state))
    monkeypatch.setattr(web_app_module, "SqlAlchemyEconomyRepository", lambda session: FakeEconomyRepo(state))
    monkeypatch.setattr(web_app_module, "SqlAlchemyWebAuthRepository", lambda session: FakeWebAuthRepo(state))
    monkeypatch.setattr(web_app_module, "GAME_STORE", store)
    monkeypatch.setattr(web_app_module.game_router_module, "GAME_STORE", store)
    monkeypatch.setattr(web_app_module.game_router_module, "_safe_edit_or_send_game_board", AsyncMock())
    monkeypatch.setattr(web_app_module.game_router_module, "_send_roles_to_private", AsyncMock(return_value=0))
    monkeypatch.setattr(web_app_module.game_router_module, "_send_game_feed_event", AsyncMock())
    monkeypatch.setattr(web_app_module.game_router_module, "_grant_game_rewards_if_needed", AsyncMock(return_value=None))
    monkeypatch.setattr(web_app_module.game_router_module, "_schedule_phase_timer", lambda bot, game, chat_settings: None)

    app = web_app_module.create_web_app(settings=state.settings, session_factory=DummySessionFactory())
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(state.settings.web_session_cookie_name, "session-token")

    try:
        yield client, store
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()


async def _create_started_dice_game(store: GameStore, *, owner_user_id: int, chat_id: int, chat_title: str):
    game, error = await store.create_lobby(
        kind="dice",
        chat_id=chat_id,
        chat_title=chat_title,
        owner_user_id=owner_user_id,
        owner_label="Хозяйка вечера",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None
    joined_game, status = await store.join(game_id=game.game_id, user_id=303, user_label="Игрок Два")
    assert joined_game is not None
    assert status == "joined"
    started_game, start_error = await store.start(game_id=game.game_id)
    assert start_error is None
    assert started_game is not None
    return started_game


async def _get_dice_game_html(monkeypatch) -> str:
    state = WebRepoState(
        settings=_settings(),
        user=UserSnapshot(telegram_user_id=77, username="gm", first_name="Game", last_name="Master", is_bot=False),
        manageable_groups=[_overview(-1001, "Клуб настолок", bot_role="game_master")],
    )
    store = GameStore()
    async with _web_client(monkeypatch, state, store=store) as (client, store):
        await _create_started_dice_game(store, owner_user_id=77, chat_id=-1001, chat_title="Клуб настолок")
        response = await client.get("/app/games")
        assert response.status_code == 200
        return response.text


def _parse_form_urlencoded(body: str) -> dict[str, str]:
    from urllib.parse import unquote_plus

    result: dict[str, str] = {}
    for pair in (body or "").split("&"):
        if "=" in pair:
            key, _, value = pair.partition("=")
            result[unquote_plus(key)] = unquote_plus(value)
    return result


@pytest.mark.asyncio
async def test_dice_roll_button_submits_its_callback_data(monkeypatch) -> None:
    html = await _get_dice_game_html(monkeypatch)
    assert "🎲 Бросить" in html and "🛑 Завершить" in html

    games_js = (STATIC_DIR / "games.js").read_text(encoding="utf-8")
    captured: dict[str, str] = {}

    async def handle_action(route):
        captured.update(_parse_form_urlencoded(route.request.post_data))
        await route.fulfill(
            status=200,
            content_type="application/json",
            body='{"ok": true, "message": "Бросок засчитан.", "redirect": null}',
        )

    async def handle_live(route):
        await route.fulfill(
            status=200,
            content_type="application/json",
            body='{"ok": true, "changed": false, "signature": "sig-1"}',
        )

    async def serve_page(route):
        await route.fulfill(status=200, content_type="text/html", body=html)

    async def serve_js(route):
        await route.fulfill(status=200, content_type="application/javascript", body=games_js)

    async def serve_file(path: Path):
        async def handler(route):
            await route.fulfill(path=str(path))

        return handler

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.route("http://selara.test/app/games", serve_page)
            await page.route("**/static/games.js*", serve_js)
            await page.route("**/static/panel.css*", await serve_file(STATIC_DIR / "panel.css"))
            await page.route(
                "**/static/server-ui-foundation.css", await serve_file(STATIC_DIR / "server-ui-foundation.css")
            )
            await page.route("**/api/live/stream**", lambda route: route.abort())
            await page.route("**/api/live/ws/**", lambda route: route.abort())
            await page.route("**/app/games/action", handle_action)
            await page.route("**/app/games/live**", handle_live)

            await page.goto("http://selara.test/app/games")
            await page.wait_for_timeout(100)

            await page.locator('button:has-text("🎲 Бросить")').click()
            await page.wait_for_timeout(200)

            toast = page.locator(".toast")
            assert await toast.count() >= 1, "no toast shown after submitting the roll action"
            assert "Бросок засчитан" in await toast.first.inner_text()

            await page.close()
        finally:
            await browser.close()

    assert captured.get("callback_data", "").startswith("gdice:"), captured
    assert captured["callback_data"].endswith(":roll"), captured


@pytest.mark.asyncio
async def test_finish_game_button_submits_a_cancel_callback(monkeypatch) -> None:
    html = await _get_dice_game_html(monkeypatch)

    games_js = (STATIC_DIR / "games.js").read_text(encoding="utf-8")
    captured: dict[str, str] = {}

    async def handle_action(route):
        captured.update(_parse_form_urlencoded(route.request.post_data))
        await route.fulfill(
            status=200,
            content_type="application/json",
            body='{"ok": true, "message": "Игра завершена.", "redirect": null}',
        )

    async def handle_live(route):
        await route.fulfill(
            status=200,
            content_type="application/json",
            body='{"ok": true, "changed": false, "signature": "sig-1"}',
        )

    async def serve_page(route):
        await route.fulfill(status=200, content_type="text/html", body=html)

    async def serve_js(route):
        await route.fulfill(status=200, content_type="application/javascript", body=games_js)

    async def serve_file(path: Path):
        async def handler(route):
            await route.fulfill(path=str(path))

        return handler

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.route("http://selara.test/app/games", serve_page)
            await page.route("**/static/games.js*", serve_js)
            await page.route("**/static/panel.css*", await serve_file(STATIC_DIR / "panel.css"))
            await page.route(
                "**/static/server-ui-foundation.css", await serve_file(STATIC_DIR / "server-ui-foundation.css")
            )
            await page.route("**/api/live/stream**", lambda route: route.abort())
            await page.route("**/api/live/ws/**", lambda route: route.abort())
            await page.route("**/app/games/action", handle_action)
            await page.route("**/app/games/live**", handle_live)

            await page.goto("http://selara.test/app/games")
            await page.wait_for_timeout(100)

            await page.locator('button:has-text("🛑 Завершить")').click()
            await page.wait_for_timeout(200)

            toast = page.locator(".toast")
            assert await toast.count() >= 1, "no toast shown after submitting the finish action"
            assert "Игра завершена" in await toast.first.inner_text()

            await page.close()
        finally:
            await browser.close()

    assert captured.get("callback_data", "").startswith("game:cancel:"), captured
