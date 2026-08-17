"""Этап 5 (игры) sub-slice 2: characterize the games.html live-update client
(WS/SSE/fallback-poll reconnect logic) before any further DOM/JS change, per
the roadmap checklist ("Сохранить SSE/live update contracts и reconnect
behavior"). `games.html`'s ~935-line inline script had zero prior test
coverage of this area.

Found and fixed a real bug while characterizing: WS reconnection was only
ever attempted from `connectGameSockets()`, which was only called from page
init and from `applyDashboardHtml()` (i.e. inside `commit()`, games.html:657).
But `refreshDashboard()`'s "nothing changed" branch (games.html:697-701)
returned *before* ever calling `applyDashboardHtml`. So once a WebSocket
errored and the 30s backoff (`wsDisabledUntil`) started, the client fell back
to 4s polling of `/app/games/live` — and if that dashboard payload never
reported a change (an idle lobby, nobody acting), `connectGameSockets()` was
never invoked again. The backoff timer expired but nothing ever retried: the
client was stuck on fallback polling forever, not just for 30s. Fixed by
also calling `connectGameSockets()` from the "nothing changed" branch — it is
a no-op while still inside the backoff window and skips games that already
have a live socket, so calling it on every poll is safe.
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

FAKE_TRANSPORTS_INIT_SCRIPT = """
window.__wsInstances = [];
class FakeWebSocket extends EventTarget {
  constructor(url) {
    super();
    this.url = url;
    this.readyState = 0;
    window.__wsInstances.push(this);
  }
  send() {}
  close() {
    this.readyState = 3;
  }
}
window.WebSocket = FakeWebSocket;

window.__esInstances = [];
class FakeEventSource extends EventTarget {
  constructor(url) {
    super();
    this.url = url;
    window.__esInstances.push(this);
  }
  close() {}
}
window.EventSource = FakeEventSource;
"""


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


async def _create_started_whoami_game(store: GameStore, *, owner_user_id: int, chat_id: int, chat_title: str):
    game, error = await store.create_lobby(
        kind="whoami",
        chat_id=chat_id,
        chat_title=chat_title,
        owner_user_id=owner_user_id,
        owner_label="Хозяйка вечера",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None
    for user_id, label in [(303, "Чайник"), (404, "Ложка")]:
        joined_game, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=label)
        assert joined_game is not None
        assert status == "joined"
    started_game, start_error = await store.start(game_id=game.game_id)
    assert start_error is None
    assert started_game is not None
    started_game.roles = {owner_user_id: "Любовница", 303: "Чайник", 404: "Ложка"}
    return started_game


async def _get_games_page_html(monkeypatch) -> str:
    state = WebRepoState(
        settings=_settings(),
        user=UserSnapshot(telegram_user_id=77, username="gm", first_name="Game", last_name="Master", is_bot=False),
        manageable_groups=[_overview(-1001, "Клуб настолок", bot_role="game_master")],
    )
    store = GameStore()
    async with _web_client(monkeypatch, state, store=store) as (client, store):
        await _create_started_whoami_game(store, owner_user_id=77, chat_id=-1001, chat_title="Клуб настолок")
        response = await client.get("/app/games")
        assert response.status_code == 200
        return response.text


@pytest.mark.asyncio
async def test_ws_reconnects_after_backoff_even_when_dashboard_never_changes(monkeypatch) -> None:
    """Regression guard for the reconnect gap described in the module
    docstring: after a WS error+close and the 30s backoff expiring, the next
    fallback poll — even one reporting "nothing changed" — must still
    attempt to reopen a WebSocket for the game panel.
    """
    html = await _get_games_page_html(monkeypatch)
    assert "data-game-panel" in html and "data-game-id" in html

    poll_calls = {"count": 0}

    async def handle_live(route):
        poll_calls["count"] += 1
        await route.fulfill(
            status=200,
            content_type="application/json",
            body='{"ok": true, "changed": false, "signature": "same-signature"}',
        )

    async def serve_page(route):
        await route.fulfill(status=200, content_type="text/html", body=html)

    games_js_source = (STATIC_DIR / "games.js").read_text(encoding="utf-8")

    async def serve_games_js(route):
        await route.fulfill(status=200, content_type="application/javascript", body=games_js_source)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.clock.install()
            await page.add_init_script(FAKE_TRANSPORTS_INIT_SCRIPT)
            await page.route("http://selara.test/app/games", serve_page)
            await page.route("**/app/games/live**", handle_live)
            await page.route("**/static/games.js*", serve_games_js)
            await page.goto("http://selara.test/app/games")

            ws_count_before = await page.evaluate("window.__wsInstances.length")
            assert ws_count_before == 1, "expected exactly one WebSocket for the one active game panel"

            # Simulate a real browser WS failure: error, then close (matches
            # the app's own listeners at games.html:763-775).
            await page.evaluate(
                """() => {
                    const ws = window.__wsInstances[0];
                    ws.dispatchEvent(new Event('error'));
                    ws.dispatchEvent(new Event('close'));
                }"""
            )

            # Past the 30s backoff, plus enough 4s polling ticks to prove the
            # backoff expiring alone changes nothing without the fix. Advance
            # in small steps with a real-time pause between each: `fast_forward`
            # fires due `setInterval` callbacks synchronously, but the mocked
            # fetch route resolves over a real (CDP) round trip, so batching
            # the whole 34s in one jump would fire every tick before the
            # first fetch's `refreshPromise` guard ever clears.
            for _ in range(9):
                await page.clock.fast_forward(4000)
                await page.wait_for_timeout(60)

            assert poll_calls["count"] >= 2, "fallback polling should have started after the WS error"
            ws_count_after = await page.evaluate("window.__wsInstances.length")
            assert ws_count_after > ws_count_before, (
                "no new WebSocket was opened after the 30s backoff elapsed, even though fallback "
                "polling kept running — the client is stuck on polling forever"
            )
            await page.close()
        finally:
            await browser.close()
