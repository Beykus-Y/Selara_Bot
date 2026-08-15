from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.rendering import create_template_environment
from selara.web.user_docs import build_user_docs_context

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"

DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}
TABLET = {"width": 820, "height": 1180}


def _render_user_docs() -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    context = build_user_docs_context(chat=None)
    return environment.get_template("user_docs.html").render(
        top_links=[],
        show_logout=False,
        flash=None,
        error=None,
        body_classes="",
        navigation_label="Навигация документации",
        **context,
    )


async def _load(page) -> None:
    await page.set_content(_render_user_docs())
    await page.add_style_tag(path=str(STATIC_DIR / "panel.css"))
    await page.add_style_tag(path=str(STATIC_DIR / "server-ui-foundation.css"))
    await page.add_style_tag(path=str(STATIC_DIR / "docs-item-actions.css"))
    await page.add_style_tag(path=str(STATIC_DIR / "docs-search.css"))
    await page.add_script_tag(path=str(STATIC_DIR / "docs-item-actions.js"))
    await page.add_script_tag(path=str(STATIC_DIR / "docs-search.js"))
    await page.add_style_tag(content="*, *::before, *::after { transition: none !important; }")


@pytest.mark.asyncio
async def test_copy_button_shows_and_reverts_feedback() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=DESKTOP)
        try:
            await _load(page)
            button = page.locator(".docs-clip-button").first
            await button.scroll_into_view_if_needed()
            copy_text = await button.get_attribute("data-copy-text")
            assert copy_text

            await button.click()
            assert await button.inner_text() == "Скопировано"
            assert "is-copied" in (await button.get_attribute("class") or "")

            await page.wait_for_timeout(1700)
            assert await button.inner_text() == "Копировать"
            assert "is-copied" not in (await button.get_attribute("class") or "")
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_item_deep_link_anchor_scrolls_clear_of_sticky_topbar() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=DESKTOP)
        try:
            await _load(page)
            link = page.locator(".docs-item-anchor").first
            target_id = await link.get_attribute("href")
            assert target_id and target_id.startswith("#")

            await link.click()
            await page.wait_for_timeout(150)

            topbar_bottom = await page.locator(".topbar").evaluate("el => el.getBoundingClientRect().bottom")
            card_top = await page.locator(target_id).evaluate("el => el.getBoundingClientRect().top")
            assert card_top >= topbar_bottom
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_search_filters_cards_and_hides_sections_with_no_matches() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=DESKTOP)
        try:
            await _load(page)
            total_cards = await page.locator("[data-docs-search-card]").count()
            assert total_cards > 1

            search = page.locator("[data-docs-search-input]")
            await search.fill("уебать")

            visible_cards = page.locator("[data-docs-search-card]:not([hidden])")
            assert await visible_cards.count() == 1
            assert "уебать" in (await visible_cards.first.inner_text()).lower()

            hidden_sections = await page.locator("[data-docs-search-section][hidden]").count()
            total_sections = await page.locator("[data-docs-search-section]").count()
            assert 0 < hidden_sections < total_sections

            empty_message = page.locator("[data-docs-search-empty]")
            assert await empty_message.is_hidden()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_search_shows_empty_state_for_no_matches_and_clears_on_reset() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=DESKTOP)
        try:
            await _load(page)
            search = page.locator("[data-docs-search-input]")
            empty_message = page.locator("[data-docs-search-empty]")

            await search.fill("совершенно-несуществующий-запрос-xyz")
            assert await empty_message.is_visible()
            assert await page.locator("[data-docs-search-card]:not([hidden])").count() == 0

            await search.fill("")
            assert await empty_message.is_hidden()
            total_cards = await page.locator("[data-docs-search-card]").count()
            visible_cards = await page.locator("[data-docs-search-card]:not([hidden])").count()
            assert visible_cards == total_cards
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("viewport", [DESKTOP, MOBILE, TABLET])
async def test_every_docs_card_has_a_unique_deep_link_id(viewport: dict[str, int]) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await _load(page)
            ids = await page.locator(".docs-card").evaluate_all("els => els.map(el => el.id)")
            assert ids
            assert all(ids)
            assert len(ids) == len(set(ids))
        finally:
            await browser.close()
