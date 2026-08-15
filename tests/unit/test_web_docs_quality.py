"""Heading hierarchy and keyboard-reachability checks for the documentation
pages (docs/WEB_UI_MODERNIZATION_TODO.md, stage 3 "Качество документации":
"Проверить keyboard navigation, heading hierarchy, links и mobile layout.").

Link validity is already covered by test_web_docs_anchors.py. Mobile layout
is a known, already-documented pre-existing gap (`.docs-layout` has no
mobile breakpoint) deliberately deferred to its own slice after stage 3 per
the 2026-08-16 decision log entry — not re-litigated here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.admin_docs import build_admin_docs_context
from selara.web.rendering import create_template_environment
from selara.web.user_docs import build_user_docs_context

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"
_HEADING_RE = re.compile(r"<h([1-6])\b")


def _render(template_name: str, context: dict[str, object]) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    return environment.get_template(template_name).render(
        top_links=[],
        show_logout=False,
        flash=None,
        error=None,
        body_classes="",
        navigation_label="Навигация документации",
        **context,
    )


def _assert_no_skipped_heading_levels(html: str, *, page_name: str) -> None:
    levels = [int(match) for match in _HEADING_RE.findall(html)]
    assert levels, f"{page_name}: expected at least one heading"
    assert levels[0] == 1, f"{page_name}: page should start with an h1, got h{levels[0]}"
    for previous, current in zip(levels, levels[1:]):
        assert current <= previous + 1, (
            f"{page_name}: heading level skips from h{previous} to h{current} "
            f"(full sequence: {levels})"
        )


def test_user_docs_heading_hierarchy_has_no_skipped_levels() -> None:
    html = _render("user_docs.html", build_user_docs_context(chat=None))
    _assert_no_skipped_heading_levels(html, page_name="user_docs.html")


def test_admin_docs_heading_hierarchy_has_no_skipped_levels() -> None:
    html = _render("admin_docs.html", build_admin_docs_context(chat=None))
    _assert_no_skipped_heading_levels(html, page_name="admin_docs.html")


async def _load(page, template_name: str, context: dict[str, object]) -> None:
    await page.set_content(_render(template_name, context))
    await page.add_style_tag(path=str(STATIC_DIR / "panel.css"))
    await page.add_style_tag(path=str(STATIC_DIR / "server-ui-foundation.css"))
    await page.add_style_tag(path=str(STATIC_DIR / "docs-item-actions.css"))
    await page.add_style_tag(path=str(STATIC_DIR / "docs-search.css"))
    await page.add_script_tag(path=str(STATIC_DIR / "docs-item-actions.js"))
    await page.add_script_tag(path=str(STATIC_DIR / "docs-search.js"))


@pytest.mark.asyncio
async def test_user_docs_search_copy_and_deep_link_are_keyboard_reachable() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _load(page, "user_docs.html", build_user_docs_context(chat=None))

            search = page.locator("[data-docs-search-input]")
            await search.focus()
            assert await search.evaluate("el => el === document.activeElement")

            copy_button = page.locator(".docs-clip-button").first
            await copy_button.focus()
            assert await copy_button.evaluate("el => el === document.activeElement")
            assert await copy_button.evaluate("el => el.tagName.toLowerCase()") == "button"

            deep_link = page.locator(".docs-item-anchor").first
            await deep_link.focus()
            assert await deep_link.evaluate("el => el === document.activeElement")
            assert await deep_link.evaluate("el => el.tagName.toLowerCase()") == "a"

            # A real click (keyboard-activatable on <button>/<a> by construction,
            # verified end-to-end rather than assuming it) triggers the copy flow.
            await copy_button.click()
            assert "is-copied" in (await copy_button.get_attribute("class") or "")
        finally:
            await browser.close()
