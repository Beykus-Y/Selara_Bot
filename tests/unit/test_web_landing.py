"""Regression guards for landing.html (docs/WEB_UI_MODERNIZATION_TODO.md
Этап 4: "landing.html: ясное позиционирование, CTA, performance и mobile
layout.").

Found while working this slice: the "реакций" stat in the hero stats strip
was a hardcoded "117+" in the template, not derived from any context value —
and the real count (SOCIAL_COMMAND_KEY_TO_ACTION) is 118, so it was also
just wrong, not only unmaintainable. build_landing_context() separately
computed the correct live count into a "signal_cards" field that the
template never rendered at all (along with hero_eyebrow/overview_text/
overview_pills — dead output, verified unused across every template).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.presentation.commands.catalog import SOCIAL_COMMAND_KEY_TO_ACTION
from selara.presentation.game_state import GAME_DEFINITIONS
from selara.web.presenters import build_landing_context
from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"


def _context() -> dict[str, object]:
    return build_landing_context(
        bot_username="selara_test_bot",
        bot_dm_url="https://t.me/selara_test_bot",
        user=None,
        flash=None,
        error=None,
    )


def _render() -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    context = _context()
    return environment.get_template("landing.html").render(
        top_links=[],
        show_logout=False,
        body_classes="",
        navigation_label="Основная навигация",
        **context,
    )


def test_landing_reply_action_count_matches_the_real_catalog() -> None:
    html = _render()
    real_count = len(SOCIAL_COMMAND_KEY_TO_ACTION)
    assert f"{real_count}+" in html
    assert "реакций" in html


def test_landing_game_mode_count_matches_reality() -> None:
    html = _render()
    assert f"{len(GAME_DEFINITIONS)}+" in html
    assert "режимов" in html


def test_landing_context_has_no_dead_unrendered_fields() -> None:
    # hero_eyebrow/signal_cards/overview_text/overview_pills were computed by
    # build_landing_context() but never referenced by landing.html or any
    # other template — verified by grep before removing them.
    context = _context()
    for dead_key in ("hero_eyebrow", "signal_cards", "overview_text", "overview_pills"):
        assert dead_key not in context, f"{dead_key} should have been removed as dead output"


# NOTE: build_landing_context() builds 6 feature_cards, but landing.html's
# template only ever renders cards[1], cards[2], and a merge of cards[3]+
# cards[4] into one "+"-kicker card — cards[0] ("Команды для обычного
# участника") and cards[5] ("Управление группой") are silently dropped.
# Left unresolved (not a test) pending Ilya's call on whether the landing
# page should show all 6 categories or 3 curated ones was a deliberate
# design choice — see the 2026-08-16 decision log entry.


@pytest.mark.asyncio
async def test_landing_page_has_no_horizontal_overflow_on_any_viewport() -> None:
    html = _render()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for viewport in ({"width": 1440, "height": 900}, {"width": 820, "height": 1100}, {"width": 390, "height": 900}):
                page = await browser.new_page(viewport=viewport)
                await page.set_content(html)
                await page.add_style_tag(path=str(STATIC_DIR / "panel.css"))
                await page.add_style_tag(path=str(STATIC_DIR / "server-ui-foundation.css"))
                overflow = await page.evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth"
                )
                assert not overflow, f"landing.html overflows horizontally at {viewport}"
                await page.close()
        finally:
            await browser.close()
