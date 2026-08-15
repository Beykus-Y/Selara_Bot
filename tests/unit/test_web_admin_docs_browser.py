from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.admin_docs import build_admin_docs_context
from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"

DESKTOP = {"width": 1440, "height": 900}


def _render_admin_docs() -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    context = build_admin_docs_context(chat=None)
    return environment.get_template("admin_docs.html").render(
        top_links=[],
        show_logout=False,
        flash=None,
        error=None,
        body_classes="",
        navigation_label="Навигация документации",
        **context,
    )


async def _load(page) -> None:
    await page.set_content(_render_admin_docs())
    await page.add_style_tag(path=str(STATIC_DIR / "panel.css"))
    await page.add_style_tag(path=str(STATIC_DIR / "server-ui-foundation.css"))
    await page.add_style_tag(path=str(STATIC_DIR / "docs-search.css"))
    await page.add_script_tag(path=str(STATIC_DIR / "docs-search.js"))
    await page.add_style_tag(content="*, *::before, *::after { transition: none !important; }")


@pytest.mark.asyncio
async def test_admin_docs_search_filters_cards_and_hides_empty_sections() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=DESKTOP)
        try:
            await _load(page)
            total_cards = await page.locator("[data-docs-search-card]").count()
            assert total_cards > 1

            search = page.locator("[data-docs-search-input]")
            await search.fill("backup")

            visible_cards = page.locator("[data-docs-search-card]:visible")
            assert await visible_cards.count() == 1
            assert "backup" in (await visible_cards.first.inner_text()).lower()

            empty_message = page.locator("[data-docs-search-empty]")
            assert await empty_message.is_hidden()

            await search.fill("совершенно-несуществующий-запрос-xyz")
            assert await empty_message.is_visible()
            assert await page.locator("[data-docs-search-card]:visible").count() == 0
        finally:
            await browser.close()
