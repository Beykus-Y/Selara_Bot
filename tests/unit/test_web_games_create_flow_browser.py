"""Этап 5 (игры) sub-slice 7/N: exercise the real create-game flow through
the browser — kind-picker click, chat selection, form submit — against the
extracted `games.js`, closing the "создание... для каждого режима" part of
the roadmap checklist's Playwright item. Earlier sub-slices only checked
static server-rendered HTML for overflow; this is the first test in the
whole games surface that actually drives the client-side JS.
"""

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"


def _render_games_page(*, create_chat_options: list[dict[str, str]]) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    game_catalog = [
        {
            "key": "dice",
            "title": "Дуэль кубиков",
            "tone": "orange",
            "description": "Моментальная партия на один экран.",
            "min_players_label": "от 2 игроков",
            "mode_label": "общий экран",
            "note": "Моментальная партия на один экран.",
        },
        {
            "key": "whoami",
            "title": "Кто я",
            "tone": "cyan",
            "description": "Карточки на лбу.",
            "min_players_label": "от 3 игроков",
            "mode_label": "скрытые роли",
            "note": "Карточки на лбу.",
        },
    ]
    return environment.get_template("games.html").render(
        page_title="Selara • Активные игры",
        page_name="games",
        extra_scripts=["games.js"],
        hero_title="Активные игры",
        hero_subtitle="Сессии из Telegram отображаются здесь в реальном времени.",
        live_signature="sig-1",
        flash=None,
        error=None,
        top_links=[],
        show_logout=True,
        metrics=[],
        game_cards=[],
        recent_game_cards=[],
        game_catalog=game_catalog,
        default_create_kind="dice",
        default_create_game=game_catalog[0],
        has_manageable_chats=bool(create_chat_options),
        create_chat_options=create_chat_options,
        busy_create_chat_options=[],
        spy_category_options=[],
        whoami_category_options=[],
        zlob_category_options=[],
    )


async def _mount_real_page(page, html: str) -> None:
    games_js = (STATIC_DIR / "games.js").read_text(encoding="utf-8")

    async def serve_page(route):
        await route.fulfill(status=200, content_type="text/html", body=html)

    async def serve_js(route):
        await route.fulfill(status=200, content_type="application/javascript", body=games_js)

    async def serve_static_file(path: Path):
        async def handler(route):
            await route.fulfill(path=str(path))
        return handler

    await page.route("http://selara.test/app/games", serve_page)
    await page.route("**/static/games.js", serve_js)
    await page.route("**/static/panel.css", await serve_static_file(STATIC_DIR / "panel.css"))
    await page.route(
        "**/static/server-ui-foundation.css", await serve_static_file(STATIC_DIR / "server-ui-foundation.css")
    )
    await page.route("**/api/live/stream**", lambda route: route.abort())
    await page.route("**/api/live/ws/**", lambda route: route.abort())
    await page.goto("http://selara.test/app/games")
    await page.wait_for_timeout(100)


@pytest.mark.asyncio
async def test_create_game_form_submits_the_selected_kind_and_chat() -> None:
    html = _render_games_page(
        create_chat_options=[
            {"chat_id": "-1001", "title": "Клуб настолок «Ложка и Чайник»", "actions_18_enabled": "true"},
            {"chat_id": "-1002", "title": "Второй чат", "actions_18_enabled": "true"},
        ]
    )

    captured: dict[str, str] = {}

    async def handle_create(route):
        request = route.request
        for pair in (request.post_data or "").split("&"):
            if "=" in pair:
                key, _, value = pair.partition("=")
                captured[key] = value
        await route.fulfill(
            status=200,
            content_type="application/json",
            body='{"ok": true, "message": "Лобби создано.", "redirect": null}',
        )

    async def handle_live(route):
        await route.fulfill(
            status=200,
            content_type="application/json",
            body='{"ok": true, "changed": false, "signature": "sig-1"}',
        )

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.route("**/app/games/create", handle_create)
            await page.route("**/app/games/live**", handle_live)
            await _mount_real_page(page, html)

            await page.locator('[data-kind-choice="whoami"]').click()
            hidden_kind = await page.locator('input[name="kind"]').input_value()
            assert hidden_kind == "whoami", "kind picker did not update the hidden kind input"

            preview_title = await page.locator("[data-kind-preview-title]").inner_text()
            assert preview_title == "Кто я", "kind picker did not update the live preview"

            await page.locator('select[name="chat_id"]').select_option("-1002")
            await page.locator('button:has-text("Создать игру")').click()
            await page.wait_for_timeout(200)

            await page.close()
        finally:
            await browser.close()

    assert captured.get("kind") == "whoami"
    assert captured.get("chat_id") == "-1002"


@pytest.mark.asyncio
async def test_create_game_form_shows_error_toast_on_failure() -> None:
    html = _render_games_page(
        create_chat_options=[{"chat_id": "-1001", "title": "Клуб настолок", "actions_18_enabled": "true"}]
    )

    async def handle_create(route):
        await route.fulfill(
            status=400,
            content_type="application/json",
            body='{"ok": false, "message": "Недостаточно прав для запуска игры в этом чате."}',
        )

    async def handle_live(route):
        await route.fulfill(
            status=200,
            content_type="application/json",
            body='{"ok": true, "changed": false, "signature": "sig-1"}',
        )

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.route("**/app/games/create", handle_create)
            await page.route("**/app/games/live**", handle_live)
            await _mount_real_page(page, html)

            await page.locator('button:has-text("Создать игру")').click()
            await page.wait_for_timeout(200)

            toast = page.locator(".toast")
            assert await toast.count() >= 1, "no toast shown after a failed create-game submit"
            toast_text = await toast.first.inner_text()
            assert "Недостаточно прав" in toast_text

            await page.close()
        finally:
            await browser.close()
