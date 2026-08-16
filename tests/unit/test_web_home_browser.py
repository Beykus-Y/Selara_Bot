from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"


def _render_home(*, admin_groups=None, activity_groups=None) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    return environment.get_template("home.html").render(
        page_title="Selara • Кабинет",
        page_name="home",
        top_links=[{"href": "/", "label": "Главная", "variant": "ghost"}],
        show_logout=True,
        flash=None,
        error=None,
        home_href="/",
        brand_subtitle="бот для Telegram-групп",
        hero_title="С возвращением, Ilya",
        hero_subtitle="Ваши группы, экономика и статистика собраны в одном месте.",
        metrics=[
            {"label": "Админ-группы", "value": "3", "note": "с правами управления", "tone": "cyan"},
            {"label": "Недавние группы", "value": "5", "note": "активность за 7 дней", "tone": "violet"},
        ],
        admin_groups=admin_groups
        if admin_groups is not None
        else [
            {"href": "/app/chats/1", "title": "Selara Community", "meta": "482 участника", "badge": "owner"},
            {"href": "/app/chats/2", "title": "Playtest Lounge", "meta": "98 участников", "badge": "admin"},
        ],
        activity_groups=activity_groups
        if activity_groups is not None
        else [
            {"href": "/app/chats/3", "title": "Друзья по чату", "meta": "активность сегодня", "badge": "member"},
        ],
        global_dashboard={
            "title": "Общая экономика",
            "empty_text": None,
            "rows": [{"title": "Баланс", "meta": "монеты", "value": "1 240"}],
        },
        security_items=[
            {"title": "Только код из Telegram", "text": "Бот выдаёт код по /login в личке."},
        ],
    )


async def _mount(page, html: str) -> None:
    await page.set_content(html)
    for stylesheet in ("panel.css", "server-ui-foundation.css"):
        await page.add_style_tag(path=str(STATIC_DIR / stylesheet))


def _rects_overlap(a: dict, b: dict) -> bool:
    return a["x"] < b["x"] + b["width"] and a["x"] + a["width"] > b["x"] and a["y"] < b["y"] + b["height"] and a["y"] + a["height"] > b["y"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "viewport",
    [
        {"width": 1440, "height": 900},
        {"width": 390, "height": 844},
        {"width": 820, "height": 1180},
    ],
)
async def test_home_hero_copy_and_side_chips_never_overlap(viewport: dict[str, int]) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await _mount(page, _render_home())

            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )

            copy_box = await page.locator(".hero-copy").bounding_box()
            side_box = await page.locator(".hero-side").bounding_box()
            assert copy_box is not None
            assert side_box is not None
            assert not _rects_overlap(copy_box, side_box)
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_home_shows_empty_states_when_no_groups() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page, _render_home(admin_groups=[], activity_groups=[]))
            assert "Пока нет групп" in await page.content()
            assert "Данные появятся после активности" in await page.content()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_home_group_cards_are_clickable_quick_links() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page, _render_home())
            link = page.locator('a.list-card[href="/app/chats/1"]')
            assert await link.count() == 1
        finally:
            await browser.close()
