from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from scripts.render_admin_broadcast_detail_fixture import render

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT / "src/selara/web/static"


async def _load_page(page, *, delivery_status: str = "all") -> None:
    await page.set_content(render(delivery_status=delivery_status))
    await page.add_style_tag(path=str(STATIC_DIR / "panel.css"))
    await page.add_style_tag(path=str(STATIC_DIR / "server-ui-foundation.css"))
    detail_css = STATIC_DIR / "admin-broadcast-detail.css"
    if detail_css.exists():
        await page.add_style_tag(path=str(detail_css))


@pytest.mark.asyncio
async def test_broadcast_detail_desktop_exposes_four_operational_blocks() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        try:
            await _load_page(page)

            assert await page.locator(".broadcast-history-status").count() == 1
            assert await page.locator(".broadcast-detail-metric").count() == 4
            assert await page.locator(".broadcast-message-preview").count() == 1
            assert await page.locator(".broadcast-message-media").get_by_text("Фото").is_visible()
            assert await page.locator(".broadcast-message-reaction").count() == 2
            assert await page.locator(".broadcast-delivery-filter").count() == 4
            assert await page.locator("[data-delivery-status]").count() == 3
            assert await page.locator(".broadcast-reaction-card").count() == 3
            assert await page.locator(".broadcast-response-card").count() == 2
            assert await page.locator("[style]").count() == 0
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("viewport", "expected_columns"),
    [
        ({"width": 390, "height": 844}, "1"),
        ({"width": 820, "height": 1180}, "2"),
    ],
)
async def test_broadcast_detail_responsive_actions_and_long_content_fit(
    viewport: dict[str, int],
    expected_columns: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await _load_page(page, delivery_status="failed")

            assert await page.locator(".broadcast-detail-summary").evaluate(
                "element => getComputedStyle(element).gridTemplateColumns.split(' ').length.toString()"
            ) == expected_columns
            assert await page.locator("[data-delivery-status]").count() == 1
            assert await page.locator("[data-delivery-status='failed']").count() == 1
            for control in await page.locator(
                ".broadcast-delivery-filter, .broadcast-reply-reaction-actions .button"
            ).all():
                assert await control.evaluate(
                    "element => element.getBoundingClientRect().height"
                ) >= 44
            assert await page.locator(".broadcast-delivery-card").evaluate(
                "element => element.scrollWidth <= element.clientWidth"
            )
            assert await page.locator(".broadcast-response-card").last.evaluate(
                "element => element.scrollWidth <= element.clientWidth"
            )
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
        finally:
            await browser.close()
