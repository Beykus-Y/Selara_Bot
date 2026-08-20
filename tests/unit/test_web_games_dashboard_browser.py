"""Этап 5 (игры), first characterization slice: lock down the real
`/app/games` dashboard's core states (empty, create-game kind-grid, one
active game) with browser-level coverage before any DOM/JS change, per the
roadmap checklist ("Зафиксировать все игровые сценарии и состояния до
изменения DOM"). No games-specific stylesheet exists — everything renders
from the shared `panel.css` + `server-ui-foundation.css` baseline, same as
every other page in this initiative.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import httpx
import pytest
from playwright.async_api import async_playwright

from selara.core.chat_settings import ChatSettings
from selara.core.config import Settings
from selara.domain.entities import ChatRoleDefinition, UserChatOverview, UserSnapshot
from selara.presentation.game_state import GameStore
from selara.web import app as web_app_module

STATIC_DIR = None  # set below, after resolving the repo root


def _resolve_static_dir():
    from pathlib import Path

    return Path(__file__).resolve().parents[2] / "src/selara/web/static"


STATIC_DIR = _resolve_static_dir()

VIEWPORTS = [
    {"width": 1440, "height": 900},
    {"width": 820, "height": 1180},
    {"width": 390, "height": 844},
]


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
    monkeypatch.setattr(web_app_module.game_router_module, "_send_roles_to_private", AsyncMock(return_value=[]))
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

    started_game, start_error = await store.start(game_id=game.game_id)
    assert start_error is None
    assert started_game is not None
    started_game.roles = {owner_user_id: "Любовница", 303: "Чайник", 404: "Ложка"}
    return started_game


ALL_GAME_KINDS = (
    "spy",
    "mafia",
    "dice",
    "quiz",
    "bredovukha",
    "bunker",
    "whoami",
    "zlobcards",
)


async def _create_started_game(store: GameStore, *, kind: str, owner_user_id: int, chat_id: int, chat_title: str):
    from selara.presentation.game_state import GAME_DEFINITIONS

    game, error = await store.create_lobby(
        kind=kind,
        chat_id=chat_id,
        chat_title=chat_title,
        owner_user_id=owner_user_id,
        owner_label="Хозяйка вечера",
        reveal_eliminated_role=True,
    )
    assert error is None, f"{kind}: {error}"
    assert game is not None

    min_players = GAME_DEFINITIONS[kind].min_players
    for index in range(min_players - 1):
        user_id = 300 + index
        joined_game, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=f"Игрок {index + 1}")
        assert joined_game is not None
        assert status == "joined", f"{kind}: {status}"

    started_game, start_error = await store.start(game_id=game.game_id)
    assert start_error is None, f"{kind}: {start_error}"
    assert started_game is not None
    assert started_game.status == "started"
    return started_game


async def _mount(page, html: str) -> None:
    await page.set_content(html)
    for stylesheet in ("panel.css", "server-ui-foundation.css", "games.css"):
        await page.add_style_tag(path=str(STATIC_DIR / stylesheet))
    # See the skip-link CSS-transition flake fixed in
    # test_web_admin_overview_browser.py this same session — disable
    # transitions up front so any later geometry read is deterministic.
    await page.add_style_tag(content="*, *::before, *::after { transition: none !important; }")


def _assert_no_overflow_at_all_viewports(results: dict[int, bool]) -> None:
    overflowing = [width for width, has_overflow in results.items() if has_overflow]
    assert not overflowing, f"page-level horizontal overflow at viewport widths: {overflowing}"


@pytest.mark.asyncio
async def test_games_dashboard_empty_state_has_no_overflow(monkeypatch) -> None:
    state = WebRepoState(
        settings=_settings(),
        user=UserSnapshot(telegram_user_id=77, username="viewer", first_name="View", last_name="Er", is_bot=False),
    )

    async with _web_client(monkeypatch, state) as (client, _store):
        response = await client.get("/app/games")
    assert response.status_code == 200
    html = response.text

    results: dict[int, bool] = {}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for viewport in VIEWPORTS:
                page = await browser.new_page(viewport=viewport)
                await _mount(page, html)
                results[viewport["width"]] = await page.evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth"
                )
                await page.close()
        finally:
            await browser.close()

    _assert_no_overflow_at_all_viewports(results)


@pytest.mark.asyncio
async def test_games_dashboard_create_game_kind_grid_has_no_overflow(monkeypatch) -> None:
    state = WebRepoState(
        settings=_settings(),
        user=UserSnapshot(telegram_user_id=77, username="gm", first_name="Game", last_name="Master", is_bot=False),
        manageable_groups=[_overview(-1001, "Клуб настолок «Ложка и Чайник»", bot_role="game_master")],
    )

    async with _web_client(monkeypatch, state) as (client, _store):
        response = await client.get("/app/games")
    assert response.status_code == 200
    html = response.text
    assert "game-kind-grid" in html

    results: dict[int, bool] = {}
    touch_targets_ok: dict[int, bool] = {}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for viewport in VIEWPORTS:
                page = await browser.new_page(viewport=viewport)
                await _mount(page, html)
                results[viewport["width"]] = await page.evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth"
                )
                card = page.locator(".game-kind-card").first
                touch_targets_ok[viewport["width"]] = await card.evaluate(
                    "element => element.getBoundingClientRect().height >= 44"
                )
                await page.close()
        finally:
            await browser.close()

    _assert_no_overflow_at_all_viewports(results)
    assert all(touch_targets_ok.values()), touch_targets_ok


@pytest.mark.asyncio
async def test_games_dashboard_active_whoami_game_has_no_overflow(monkeypatch) -> None:
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
    html = response.text
    assert "game-meta-grid" in html

    results: dict[int, bool] = {}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for viewport in VIEWPORTS:
                page = await browser.new_page(viewport=viewport)
                await _mount(page, html)
                results[viewport["width"]] = await page.evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth"
                )
                await page.close()
        finally:
            await browser.close()

    _assert_no_overflow_at_all_viewports(results)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ALL_GAME_KINDS)
async def test_games_dashboard_every_mode_active_panel_has_no_overflow(monkeypatch, kind: str) -> None:
    """Sub-slice 3/N of Этап 5's 'зафиксировать все игровые сценарии':
    the whoami-only coverage above was just one of 9 modes. Bunker in
    particular needs 6 players (the highest min_players of any mode) and
    renders a distinct seat-selection panel not shared with the other modes.
    """
    state = WebRepoState(
        settings=_settings(),
        user=UserSnapshot(telegram_user_id=77, username="gm", first_name="Game", last_name="Master", is_bot=False),
        manageable_groups=[_overview(-1001, "Клуб настолок «Ложка и Чайник»", bot_role="game_master")],
    )
    store = GameStore()

    async with _web_client(monkeypatch, state, store=store) as (client, store):
        await _create_started_game(store, kind=kind, owner_user_id=77, chat_id=-1001, chat_title="Клуб настолок «Ложка и Чайник»")
        response = await client.get("/app/games")
    assert response.status_code == 200
    html = response.text
    assert "game-meta-grid" in html

    results: dict[int, bool] = {}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for viewport in VIEWPORTS:
                page = await browser.new_page(viewport=viewport)
                await _mount(page, html)
                results[viewport["width"]] = await page.evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth"
                )
                await page.close()
        finally:
            await browser.close()

    _assert_no_overflow_at_all_viewports(results)


def _srgb_channel_to_linear(value: float) -> float:
    channel = value / 255
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def _relative_luminance(r: int, g: int, b: int) -> float:
    lr, lg, lb = _srgb_channel_to_linear(r), _srgb_channel_to_linear(g), _srgb_channel_to_linear(b)
    return 0.2126 * lr + 0.7152 * lg + 0.0722 * lb


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["whoami", "bredovukha", "dice"])
async def test_games_dashboard_finished_game_result_card_has_no_overflow(monkeypatch, kind: str) -> None:
    """Sub-slice 4/N: the "results" state (`recent-games-panel`,
    `_games_dashboard.html:1153-1244`) renders one of three mutually distinct
    reveal branches depending on the mode: `role_reveal_rows` (secret-role
    modes like whoami/spy/mafia), `bred_reveal_rows` (bredovukha only), or
    plain `score_rows` (score-only modes like dice/quiz) — plus an
    always-present `result_text` banner and optional personal notes. One
    representative mode per branch, not all 8.

    Also a regression guard for a real contrast bug found by reviewing this
    slice's screenshots: `.banner-ok`/`.banner-error` are styled for the
    app's dark page background (near-white text) but `.recent-result-banner`
    nests one inside a light pastel `.recent-game-card` — the text was
    technically present in the DOM but visually invisible. Since all
    `.recent-game-card*` background variants are light pastels (panel.css
    ~1781-1809), asserting the resolved text color is dark (not just
    "differs from the un-themed default") is a reliable proxy for readable
    contrast without needing to sample the card's gradient background.
    """
    state = WebRepoState(
        settings=_settings(),
        user=UserSnapshot(telegram_user_id=77, username="gm", first_name="Game", last_name="Master", is_bot=False),
        manageable_groups=[_overview(-1001, "Клуб настолок «Ложка и Чайник»", bot_role="game_master")],
    )
    store = GameStore()

    async with _web_client(monkeypatch, state, store=store) as (client, store):
        game = await _create_started_game(store, kind=kind, owner_user_id=77, chat_id=-1001, chat_title="Клуб настолок «Ложка и Чайник»")
        await store.finish(game_id=game.game_id, winner_text="Хозяйка вечера победила с явным перевесом.")
        response = await client.get("/app/games")
    assert response.status_code == 200
    html = response.text
    assert "recent-games-panel" in html
    assert "recent-result-banner" in html

    results: dict[int, bool] = {}
    banner_rgb: tuple[int, int, int] | None = None
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for viewport in VIEWPORTS:
                page = await browser.new_page(viewport=viewport)
                await _mount(page, html)
                results[viewport["width"]] = await page.evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth"
                )
                if viewport is VIEWPORTS[0]:
                    color = await page.locator(".recent-result-banner").evaluate(
                        "element => getComputedStyle(element).color"
                    )
                    banner_rgb = tuple(int(part) for part in color.split("(")[1].split(")")[0].split(",")[:3])
                await page.close()
        finally:
            await browser.close()

    _assert_no_overflow_at_all_viewports(results)
    assert banner_rgb is not None
    luminance = _relative_luminance(*banner_rgb)
    assert luminance < 0.35, (
        f"result banner text color {banner_rgb} is too light (luminance {luminance:.2f}) to read "
        "against the light pastel .recent-game-card background"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["whoami", "mafia"])
async def test_games_dashboard_large_roster_has_no_overflow(monkeypatch, kind: str) -> None:
    """Sub-slice 6/N: the roadmap checklist explicitly calls out "максимальное
    количество игроков" (max player count) as something to verify.
    GameStore.join() has no upper bound at all on lobby size — the only real
    ceiling is the whoami/spy card bank size (61 cards in the largest
    category), so a 24-player party is a realistic stress case for the
    roster/table displays, not an edge a real chat would rarely hit.
    """
    state = WebRepoState(
        settings=_settings(),
        user=UserSnapshot(telegram_user_id=77, username="gm", first_name="Game", last_name="Master", is_bot=False),
        manageable_groups=[_overview(-1001, "Клуб настолок «Ложка и Чайник»", bot_role="game_master")],
    )
    store = GameStore()

    async with _web_client(monkeypatch, state, store=store) as (client, store):
        game, error = await store.create_lobby(
            kind=kind,
            chat_id=-1001,
            chat_title="Клуб настолок «Ложка и Чайник»",
            owner_user_id=77,
            owner_label="Хозяйка вечера с очень длинным именем для проверки переполнения",
            reveal_eliminated_role=True,
        )
        assert error is None
        assert game is not None
        for index in range(23):
            user_id = 300 + index
            joined_game, status = await store.join(
                game_id=game.game_id, user_id=user_id, user_label=f"Участник номер {index + 1} с длинным именем"
            )
            assert joined_game is not None
            assert status == "joined"
        started_game, start_error = await store.start(game_id=game.game_id)
        assert start_error is None
        assert started_game is not None
        assert len(started_game.players) == 24

        response = await client.get("/app/games")
    assert response.status_code == 200
    html = response.text

    results: dict[int, bool] = {}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for viewport in VIEWPORTS:
                page = await browser.new_page(viewport=viewport)
                await _mount(page, html)
                results[viewport["width"]] = await page.evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth"
                )
                await page.close()
        finally:
            await browser.close()

    _assert_no_overflow_at_all_viewports(results)


@pytest.mark.asyncio
async def test_games_dashboard_lobby_state_has_no_overflow(monkeypatch) -> None:
    """Sub-slice 9/N: the "lobby" state (game created, not yet started —
    `status == "lobby"`, "Лобби открыто" spotlight) was the one game-panel
    state none of the earlier sub-slices exercised; every prior active-game
    test used store.start() to reach the "started" status. Closes the
    "унифицировать lobby/create/active/results/error компонентов" checklist
    item's last untested state — the lobby panel reuses the same
    .panel/.game-meta-grid/.game-spotlight shell already verified for active
    games and results, confirmed here rather than assumed.
    """
    state = WebRepoState(
        settings=_settings(),
        user=UserSnapshot(telegram_user_id=77, username="gm", first_name="Game", last_name="Master", is_bot=False),
        manageable_groups=[_overview(-1001, "Клуб настолок «Ложка и Чайник»", bot_role="game_master")],
    )
    store = GameStore()

    async with _web_client(monkeypatch, state, store=store) as (client, store):
        game, error = await store.create_lobby(
            kind="whoami",
            chat_id=-1001,
            chat_title="Клуб настолок «Ложка и Чайник»",
            owner_user_id=77,
            owner_label="Хозяйка вечера с очень длинным именем для проверки переполнения",
            reveal_eliminated_role=True,
        )
        assert error is None
        assert game is not None
        response = await client.get("/app/games")
    assert response.status_code == 200
    html = response.text
    assert "Лобби открыто" in html
    assert "Набор игроков" in html

    results: dict[int, bool] = {}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for viewport in VIEWPORTS:
                page = await browser.new_page(viewport=viewport)
                await _mount(page, html)
                results[viewport["width"]] = await page.evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth"
                )
                await page.close()
        finally:
            await browser.close()

    _assert_no_overflow_at_all_viewports(results)
