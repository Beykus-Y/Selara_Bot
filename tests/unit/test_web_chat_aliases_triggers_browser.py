"""Regression guards for chat.html's alias/trigger forms (aliases/triggers
sub-slice of the chat.html multi-part Etap 4 item, see
docs/WEB_UI_MODERNIZATION_TODO.md).

These forms are plain (non-fetch) POSTs, unlike the settings-save forms
handled by chat-settings.js — deleting an alias or smart-trigger was a
single unconfirmed click with no double-submit guard at all.
"""

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"
CHAT_ID = 123


def _render() -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    return environment.get_template("chat.html").render(
        page_title="Selara", page_name="chat", top_links=[], show_logout=True,
        flash=None, error=None,
        chat_title="Selara Community", hero_subtitle="x", chat_id=CHAT_ID,
        chat_section_links=[
            {"href": f"/app/chat/{CHAT_ID}", "label": "Обзор", "variant": "subtle"},
            {"href": f"/app/chat/{CHAT_ID}", "label": "Настройки", "variant": "ghost"},
        ],
        metrics=[], dashboard_panels=[], access_rows=[], roles=[], command_rules=[],
        leaderboards=[], trigger_template_quick_rows=[], trigger_template_examples=[],
        trigger_template_docs_url="/docs", audit_rows=[],
        can_manage_settings=True, manage_settings_tone="ok", manage_settings_note="x",
        active_tab="settings",
        settings_sections=[],
        alias_mode_setting={
            "key": "alias_mode", "title": "t", "description": "d", "current_value": "both",
            "default_value": "both", "current_value_display": "x", "default_value_display": "x",
            "hint": "h", "editable": True, "input_kind": "select",
            "options": [{"value": "both", "label": "x", "selected": True}], "doc_href": "#x",
        },
        alias_source_options=[{"value": "топ", "label": "топ"}],
        aliases=[{"alias": "топ100", "command": "/top", "source": "топ"}],
        triggers=[
            {
                "id": 7, "keyword": "привет", "match_type": "exact", "match_type_label": "Точное",
                "preview": "p", "response_text": "r", "media_file_id": "", "media_type": "",
            }
        ],
        admin_docs_url="/app/docs/admin",
        achievement_sections=[],
        extra_scripts=["chat-overview.js", "chat-settings.js", "chat-plain-forms.js"],
    )


async def _mount(page) -> None:
    await page.set_content(_render())
    for stylesheet in ("panel.css", "server-ui-foundation.css"):
        await page.add_style_tag(path=str(STATIC_DIR / stylesheet))
    await page.add_script_tag(path=str(STATIC_DIR / "chat-plain-forms.js"), type="module")


@pytest.mark.asyncio
async def test_deleting_an_alias_requires_confirmation() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page)
            await page.route(f"**/app/chat/{CHAT_ID}/aliases", lambda route: route.abort())

            dialogs: list[str] = []

            async def handle_dialog(dialog):
                dialogs.append(dialog.message)
                await dialog.dismiss()

            page.on("dialog", handle_dialog)

            delete_button = page.locator('button[name="action"][value="delete"]').first
            await delete_button.click()
            await page.wait_for_timeout(80)

            assert len(dialogs) == 1
            assert "топ100" in dialogs[0]
            # Dismissed -> button must not stay permanently disabled from a
            # submit that was actually cancelled.
            assert not await delete_button.is_disabled()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_confirming_alias_delete_disables_submit_buttons() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page)

            async def handle_delete(route):
                # Never resolves within the test — keeps the button in its
                # disabled "in-flight" state so the check below can't race.
                pass

            await page.route(f"**/app/chat/{CHAT_ID}/aliases", handle_delete)
            page.on("dialog", lambda dialog: dialog.accept())

            delete_button = page.locator('button[name="action"][value="delete"]').first
            update_button = page.locator('button[name="action"][value="save"]').first
            await delete_button.click()
            await page.wait_for_timeout(120)

            assert await delete_button.is_disabled()
            assert await update_button.is_disabled()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_deleting_a_trigger_requires_confirmation_with_keyword() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page)
            await page.route(f"**/app/chat/{CHAT_ID}/triggers", lambda route: route.abort())

            dialogs: list[str] = []

            async def handle_dialog(dialog):
                dialogs.append(dialog.message)
                await dialog.dismiss()

            page.on("dialog", handle_dialog)

            delete_button = page.locator('button[name="action"][value="delete"]').nth(1)
            await delete_button.click()
            await page.wait_for_timeout(80)

            assert len(dialogs) == 1
            assert "привет" in dialogs[0]
        finally:
            await browser.close()
