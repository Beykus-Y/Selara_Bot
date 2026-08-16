"""Regression guards for economy.html (Etap 4 slice: "responsive data
presentation, forms и JS extraction", see docs/WEB_UI_MODERNIZATION_TODO.md).

Found while working this slice: the inline <script> referenced
`{{ market_rows_json|safe }}` / `{{ trade_points_json|safe }}`, but the
context builder only ever set `market_rows` / `trade_points` (no "_json"
suffix) — those two Jinja variables were always undefined, which rendered
as `const marketRows = ;` (a JS syntax error). This killed every piece of
interactivity on the page: drag&drop, market buy/sell/cancel, filters,
sort, and the trade price chart never ran at all in production.
"""

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"
CHAT_ID = 123


def _context() -> dict[str, object]:
    return {
        "page_title": "Selara Economy", "page_name": "economy", "top_links": [], "show_logout": True,
        "flash": None, "error": None,
        "chat_title": "Selara Community", "chat_id": CHAT_ID,
        "scope_id": "chat:123", "economy_mode": "local",
        "dashboard": {"balance": 1200, "growth_size_mm": 42, "growth_actions": 8, "farm_level": 3, "farm_size_tier": "M"},
        "last_crop_label": "Пшеница",
        "chat_section_links": [
            {"href": f"/app/chat/{CHAT_ID}", "label": "Обзор", "variant": "ghost"},
            {"href": f"/app/chat/{CHAT_ID}/economy", "label": "Экономика", "variant": "primary"},
        ],
        "plot_cards": [
            {"plot_no": 1, "state": "empty", "crop_label": "—", "note": "Пусто"},
        ],
        "inventory_items": [
            {"item_code": "seed:wheat", "label": "Семена пшеницы", "quantity": 5, "target": "plot-empty"},
        ],
        "market_rows": [
            {
                "id": 1, "label": "Семена пшеницы", "item_code": "seed:wheat", "qty_left": 3, "qty_total": 5,
                "unit_price": 10, "filter_group": "seeds", "is_own": False,
            },
        ],
        "trade_points": {
            "seed:wheat": [{"when": "15.08.2026 10:00", "quantity": 2, "unit_price": 9, "total_price": 18}],
        },
        "extra_scripts": ["economy.js"],
    }


def _render() -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    return environment.get_template("economy.html").render(**_context())


async def _goto(page) -> None:
    html = _render()
    url = f"http://selara.test/app/chat/{CHAT_ID}/economy"

    async def serve_page(route):
        await route.fulfill(status=200, content_type="text/html", body=html)

    await page.route(url, serve_page)
    await page.goto(url)
    for stylesheet in ("panel.css", "server-ui-foundation.css"):
        await page.add_style_tag(path=str(STATIC_DIR / stylesheet))


def test_economy_page_data_island_has_no_undefined_leak() -> None:
    import json

    html = _render()
    data_island = html.split('id="economy-page-data">', 1)[1].split("</script>", 1)[0]
    parsed = json.loads(data_island)
    assert parsed["chat_id"] == CHAT_ID
    assert parsed["trade_points"]["seed:wheat"][0]["unit_price"] == 9


@pytest.mark.asyncio
async def test_economy_page_scripts_run_without_syntax_errors_and_draw_the_chart() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        page_errors: list[str] = []
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        try:
            await _goto(page)
            await page.add_script_tag(path=str(STATIC_DIR / "economy.js"), type="module")
            await page.wait_for_timeout(150)

            assert page_errors == []
            select = page.locator("#trade-item-select")
            assert await select.locator("option").count() == 1
            market_rows = page.locator("[data-market-row]")
            assert await market_rows.count() == 1
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_economy_market_buy_button_disables_during_the_request() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _goto(page)
            await page.add_script_tag(path=str(STATIC_DIR / "economy.js"), type="module")
            await page.wait_for_timeout(150)

            async def handle_buy(route):
                # Never resolves within the test — keeps the button disabled
                # so the assertion below can't race a fast response.
                pass

            await page.route(f"**/api/chat/{CHAT_ID}/economy/market/buy", handle_buy)
            buy_button = page.locator("[data-market-buy]").first
            await buy_button.click()
            await page.wait_for_timeout(80)
            assert await buy_button.is_disabled()
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "viewport",
    [
        {"width": 1440, "height": 900},
        {"width": 390, "height": 844},
        {"width": 820, "height": 1180},
    ],
)
async def test_economy_page_has_no_horizontal_overflow(viewport: dict[str, int]) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await _goto(page)
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
        finally:
            await browser.close()
