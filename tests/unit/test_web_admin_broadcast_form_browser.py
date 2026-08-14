from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"


def _render_admin_page() -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    return environment.get_template("admin.html").render(
        page_title="Selara test admin",
        page_name="admin",
        top_links=[],
        show_logout=False,
        flash=None,
        error=None,
        admin_user_id=77,
        open_feedback_count=0,
        broadcast_active_days=3,
        recent_active_chat_count=1,
        recent_active_chats=[
            {
                "chat_id": -1001001,
                "title": "Test chat",
                "last_activity_at": "сейчас",
                "checked": True,
            }
        ],
        recent_broadcasts=[],
        feedback_requests=[],
        table_sections=[],
    )


@pytest.mark.asyncio
async def test_server_admin_broadcast_composer_handles_photo_and_reactions() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(_render_admin_page())
            form = page.locator("[data-broadcast-form]")
            photo_field = form.locator("[data-broadcast-photo-field]")
            photo_input = form.locator("[data-broadcast-photo-input]")
            reactions_toggle = form.locator("[data-broadcast-reactions-toggle]")
            reaction_controls = form.locator("[data-broadcast-reaction-controls]")

            assert await form.get_attribute("enctype") == "multipart/form-data"
            assert await photo_field.is_hidden()
            assert not await photo_input.evaluate("input => input.required")

            await form.locator('input[name="media_mode"][value="photo"]').check()
            assert await photo_field.is_visible()
            assert await photo_input.evaluate("input => input.required")
            await photo_input.set_input_files(
                files=[{"name": "notice.png", "mimeType": "image/png", "buffer": b"not-a-real-png"}]
            )
            assert await form.locator("[data-broadcast-photo-preview]").is_visible()

            await form.locator('input[name="media_mode"][value="text"]').check()
            assert await photo_field.is_hidden()
            assert await photo_input.input_value() == ""

            assert await reaction_controls.is_hidden()
            await reactions_toggle.check()
            assert await reaction_controls.is_visible()
            assert await form.locator("[data-broadcast-reaction-row]").count() == 2
            await form.locator("[data-broadcast-add-reaction]").click()
            assert await form.locator("[data-broadcast-reaction-row]").count() == 3
            await form.locator("[data-broadcast-remove-reaction]").last.click()
            assert await form.locator("[data-broadcast-reaction-row]").count() == 2

            await form.locator("[data-broadcast-body]").fill("Важная новость")
            await form.evaluate(
                """form => form.addEventListener('submit', event => {
                    event.preventDefault();
                    window.submittedBroadcastBody = form.querySelector('[data-broadcast-compiled-body]').value;
                })"""
            )
            await form.locator('button[type="submit"]').click()

            compiled = await page.evaluate("window.submittedBroadcastBody")
            assert compiled == (
                "Важная новость\n\n"
                "[reactions]\n"
                "👍 = Всё понятно\n"
                "👎 = Не согласен\n"
                "[/reactions]"
            )
        finally:
            await browser.close()
