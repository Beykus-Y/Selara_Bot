from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.presenters import build_achievement_rows
from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"


def _raw_rows() -> list[dict[str, object]]:
    return [
        {
            "title": "Первые сто сообщений",
            "description": "Напишите 100 сообщений в любой группе с ботом.",
            "icon": "\U0001f4ac",
            "rarity": "common",
            "scope_label": "глобал",
            "status": "получено",
            "holders_count": 128,
            "holders_percent": 42.31,
            "awarded_at": "16.08.2026 09:00",
        },
        {
            "title": "Легендарный организатор мероприятий сообщества и бессменный модератор года",
            "description": "Очень длинное описание достижения, которое должно переноситься по словам, а не обрезаться и не вылезать за пределы карточки на любом viewport.",
            "icon": "\U0001f3c6",
            "rarity": "legendary",
            "scope_label": "глобал",
            "status": "не получено",
            "holders_count": 3,
            "holders_percent": 0.42,
            "awarded_at": None,
        },
    ]


def _render() -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    rows = build_achievement_rows(_raw_rows())
    return environment.get_template("achievements.html").render(
        page_title="Selara • Достижения",
        page_name="achievements",
        top_links=[{"href": "/", "label": "Главная", "variant": "ghost"}],
        show_logout=True,
        flash=None,
        error=None,
        home_href="/",
        brand_subtitle="бот для Telegram-групп",
        hero_title="Достижения",
        hero_subtitle="Глобальный каталог наград аккаунта.",
        achievement_metrics=[
            {"label": "Открыто", "value": "1", "note": "из 2", "tone": "cyan"},
        ],
        achievement_sections=[{"title": "Глобальные достижения", "rows": rows}],
    )


async def _mount(page, html: str) -> None:
    await page.set_content(html)
    for stylesheet in ("panel.css", "server-ui-foundation.css"):
        await page.add_style_tag(path=str(STATIC_DIR / stylesheet))


def test_build_achievement_rows_marks_unlocked_state() -> None:
    rows = build_achievement_rows(_raw_rows())
    assert rows[0]["unlocked"] is True
    assert rows[0]["status_label"] == "Получено"
    assert rows[1]["unlocked"] is False
    assert rows[1]["status_label"] == "Не открыто"


@pytest.mark.asyncio
async def test_achievements_page_shows_distinct_status_pill_for_locked_and_unlocked() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page, _render())
            rows = page.locator(".achievement-row")
            assert await rows.count() == 2

            unlocked_row = rows.nth(0)
            assert "is-locked" not in (await unlocked_row.get_attribute("class") or "")
            assert "получено" in (await unlocked_row.inner_text()).lower()

            locked_row = rows.nth(1)
            assert "is-locked" in (await locked_row.get_attribute("class") or "")
            assert "не открыто" in (await locked_row.inner_text()).lower()
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
async def test_achievements_page_has_no_horizontal_overflow_with_long_titles(viewport: dict[str, int]) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await _mount(page, _render())
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )

            copy_box = await page.locator(".hero-copy").bounding_box()
            side_box = await page.locator(".hero-side").bounding_box()
            assert copy_box is not None
            assert side_box is not None
            assert not (
                copy_box["x"] < side_box["x"] + side_box["width"]
                and copy_box["x"] + copy_box["width"] > side_box["x"]
                and copy_box["y"] < side_box["y"] + side_box["height"]
                and copy_box["y"] + copy_box["height"] > side_box["y"]
            )
        finally:
            await browser.close()
