"""Этап 5 (игры), закрывающий чек-лист-пункт: "Visual snapshots только
стабильных ключевых сцен, с маскированием динамических timer/id значений".

Раньше (см. журнал `docs/WEB_UI_MODERNIZATION_TODO.md`) этот пункт дважды
осознанно откладывался: в проекте уже есть pixel-diff инфраструктура
(`test_visual_snapshots.py`), но она покрывает генерацию картинок для
Telegram (family tree, activity chart), не веб-страницы. Этот файл — первая
такая инфраструктура для games: реальный `/app/games` рендер через тот же
`_web_client`/`GameStore` харнесс, что и `test_web_games_dashboard_browser.py`
(не ручная сборка Jinja-контекста — риск разойтись с реальной формой
`game.*_view`), только стабильные ключевые сцены (не все 9 режимов — это
для того и есть параметризованные overflow-тесты в другом файле), с
маскированием единственных двух реально динамических видимых текстовых
полей во всём дашборде: "Старт <дата>"/"Создана <дата>" в
`.game-meta-card` и "старт <дата>" в `.recent-game-head`.

`data-game-id` (10-символьный hex) и `data-live-signature` НЕ требуют
маскирования для целей этого файла: оба — HTML-атрибуты, не рендерятся как
пиксели, поэтому невидимы для pixel-diff по построению (проверено грепом
шаблона — ни то, ни другое не появляется как видимый текст, только как
`data-*`/`value=` в hidden input).
"""

import random
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import httpx
import pytest
from playwright.async_api import async_playwright

from selara.core.chat_settings import ChatSettings
from selara.core.config import Settings
from selara.domain.entities import UserChatOverview, UserSnapshot
from selara.presentation.game_state import GameStore
from selara.web import app as web_app_module
from tests.unit.test_visual_snapshots import compare_images

import os

STATIC_DIR = None


def _resolve_static_dir():
    from pathlib import Path

    return Path(__file__).resolve().parents[2] / "src/selara/web/static"


STATIC_DIR = _resolve_static_dir()

SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), "snapshots", "games")

VIEWPORT = {"width": 1440, "height": 900}


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

    for user_id, label in [(303, "Чайник знатный"), (404, "Ложка Огромная-Преогромная")]:
        joined_game, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=label)
        assert joined_game is not None
        assert status == "joined"

    # whoami card assignment shuffles a pool inside store.start() — without a
    # fixed seed, role card text length varies between runs and shifts the
    # panel's rendered height, making the snapshot diff meaningless.
    random.seed(42)
    started_game, start_error = await store.start(game_id=game.game_id)
    assert start_error is None
    assert started_game is not None
    started_game.roles = {owner_user_id: "Любовница", 303: "Чайник", 404: "Ложка"}
    return started_game


_MASK_SCRIPT = """
() => {
  const placeholder = '01.01.2026 00:00 UTC';
  document.querySelectorAll('.game-meta-card strong').forEach((el) => {
    if (el.textContent.includes('Старт')) el.textContent = 'Старт ' + placeholder;
  });
  document.querySelectorAll('.game-meta-card p').forEach((el) => {
    if (el.textContent.includes('Создана')) el.textContent = 'Создана ' + placeholder;
  });
  document.querySelectorAll('.recent-game-head p').forEach((el) => {
    if (el.textContent.includes('старт')) {
      el.textContent = el.textContent.replace(/старт .+$/, 'старт ' + placeholder);
    }
  });
}
"""


async def _mount_and_mask(page, html: str) -> None:
    await page.set_content(html)
    for stylesheet in ("panel.css", "server-ui-foundation.css"):
        await page.add_style_tag(path=str(STATIC_DIR / stylesheet))
    await page.add_style_tag(content="*, *::before, *::after { transition: none !important; }")
    await page.evaluate(_MASK_SCRIPT)


async def _assert_games_snapshot(image_bytes: bytes, snapshot_name: str, *, threshold: float = 0.02) -> None:
    golden_path = os.path.join(SNAPSHOTS_DIR, f"{snapshot_name}.png")

    if os.environ.get("UPDATE_SNAPSHOTS") == "1" or not os.path.exists(golden_path):
        os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
        with open(golden_path, "wb") as f:
            f.write(image_bytes)
        return

    is_match, diff_pct = compare_images(image_bytes, golden_path, threshold=threshold)
    assert is_match, f"Games snapshot {snapshot_name} mismatch by {diff_pct:.2%}"


@pytest.mark.asyncio
async def test_games_snapshot_empty_state(monkeypatch) -> None:
    state = WebRepoState(
        settings=_settings(),
        user=UserSnapshot(telegram_user_id=77, username="viewer", first_name="View", last_name="Er", is_bot=False),
    )

    async with _web_client(monkeypatch, state) as (client, _store):
        response = await client.get("/app/games")
    assert response.status_code == 200

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport=VIEWPORT)
            await _mount_and_mask(page, response.text)
            image_bytes = await page.screenshot()
            await page.close()
        finally:
            await browser.close()

    await _assert_games_snapshot(image_bytes, "G01_empty_state")


@pytest.mark.asyncio
async def test_games_snapshot_create_game_kind_grid(monkeypatch) -> None:
    state = WebRepoState(
        settings=_settings(),
        user=UserSnapshot(telegram_user_id=77, username="gm", first_name="Game", last_name="Master", is_bot=False),
        manageable_groups=[_overview(-1001, "Клуб настолок «Ложка и Чайник»", bot_role="game_master")],
    )

    async with _web_client(monkeypatch, state) as (client, _store):
        response = await client.get("/app/games")
    assert response.status_code == 200
    assert "game-kind-grid" in response.text

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport=VIEWPORT)
            await _mount_and_mask(page, response.text)
            image_bytes = await page.screenshot()
            await page.close()
        finally:
            await browser.close()

    await _assert_games_snapshot(image_bytes, "G02_create_game_kind_grid")


@pytest.mark.asyncio
async def test_games_snapshot_active_whoami_game(monkeypatch) -> None:
    state = WebRepoState(
        settings=_settings(),
        user=UserSnapshot(telegram_user_id=77, username="gm", first_name="Game", last_name="Master", is_bot=False),
        manageable_groups=[_overview(-1001, "Клуб настолок «Ложка и Чайник»", bot_role="game_master")],
    )
    store = GameStore()

    async with _web_client(monkeypatch, state, store=store) as (client, store):
        await _create_started_whoami_game(store, owner_user_id=77, chat_id=-1001, chat_title="Клуб настолок «Ложка и Чайник»")
        response = await client.get("/app/games")
    assert response.status_code == 200
    assert "game-meta-grid" in response.text

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport=VIEWPORT)
            await _mount_and_mask(page, response.text)
            image_bytes = await page.screenshot()
            await page.close()
        finally:
            await browser.close()

    await _assert_games_snapshot(image_bytes, "G03_active_whoami_game")
