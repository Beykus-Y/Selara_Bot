from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"
SAMPLE_PHOTO = Path(__file__).resolve().parents[2] / "src/selara/images/citata.png"


def _render_admin_page(*, with_history: bool = False) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    return environment.get_template("admin.html").render(
        page_title="Selara test admin",
        page_name="admin",
        top_links=[
            {"href": "/app/admin", "label": "Обзор", "variant": "subtle", "current": True},
            {"href": "/app/admin#broadcasts", "label": "Рассылки", "variant": "ghost"},
            {"href": "/app/admin/table/messages_compact", "label": "История", "variant": "ghost"},
            {"href": "/app/admin#database", "label": "База данных", "variant": "ghost"},
        ],
        show_logout=False,
        body_classes="ui-admin-shell",
        navigation_label="Разделы админки",
        flash=None,
        error=None,
        extra_styles=["admin-overview.css", "admin-feedback.css", "admin-broadcast.css", "admin-table-search.css"],
        extra_scripts=["admin-overview.js", "admin-feedback.js", "admin-broadcast.js"],
        admin_user_id=77,
        open_feedback_count=0,
        attention_broadcast_count=0,
        attention_summary="0 задач требуют внимания",
        recent_broadcast_count=1 if with_history else 0,
        admin_table_count=0,
        broadcast_active_days=3,
        recent_active_chat_count=2,
        broadcast_audience_status="2 чата доступны",
        recent_active_chats=[
            {
                "chat_id": -1001001,
                "title": "Test chat",
                "last_activity_at": "сейчас",
                "checked": True,
            },
            {
                "chat_id": -1001002,
                "title": "Second long chat",
                "last_activity_at": "вчера",
                "checked": True,
            },
        ],
        recent_broadcasts=(
            [
                {
                    "id": 42,
                    "created_at": "сегодня, 12:05",
                    "body_preview": "Большое обновление Selara уже доступно.",
                    "target_count": 3,
                    "sent_count": 2,
                    "failed_count": 1,
                    "reply_count": 4,
                    "reaction_count": 9,
                    "delivery_percent": 67,
                    "status_label": "Выполнено частично",
                    "status_tone": "warn",
                }
            ]
            if with_history
            else []
        ),
        feedback_requests=[],
        feedback_status="all",
        feedback_filter_error=None,
        table_sections=[],
    )


async def _goto_admin_page(page, *, with_history: bool = False) -> None:
    """Navigate to a fake same-origin URL serving the admin page.

    A plain `page.set_content()` leaves the page at `about:blank`, which has an
    opaque origin — relative `fetch()` calls (used by the real broadcast submit
    flow) fail immediately with a URL-parse TypeError before any request is even
    dispatched, so `page.route()` interception never sees them. Serving the
    same markup from a routed fake HTTP origin gives `fetch("/api/...")` a real
    base to resolve against, so it can be intercepted normally.
    """
    html = _render_admin_page(with_history=with_history)

    async def serve_page(route):
        await route.fulfill(status=200, content_type="text/html", body=html)

    await page.route("http://selara.test/admin", serve_page)
    await page.goto("http://selara.test/admin")


@pytest.mark.asyncio
@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
async def test_server_admin_broadcast_history_card_is_operational_and_responsive(
    viewport: dict[str, int],
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await page.set_content(_render_admin_page(with_history=True))
            await page.add_style_tag(path=str(STATIC_DIR / "panel.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "server-ui-foundation.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "admin-overview.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "admin-feedback.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "admin-broadcast.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "admin-table-search.css"))

            card = page.locator(".broadcast-history-card")
            assert await card.count() == 1
            assert await card.get_by_text("Выполнено частично").is_visible()
            assert await card.locator("progress").get_attribute("value") == "2"
            assert await card.get_by_text("9 реакций").is_visible()
            assert await card.evaluate("element => element.scrollWidth <= element.clientWidth")
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_server_admin_broadcast_composer_handles_photo_and_reactions() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await _goto_admin_page(page)
            await page.add_script_tag(path=str(STATIC_DIR / "admin-broadcast.js"), type="module")
            form = page.locator("[data-broadcast-form]")
            photo_field = form.locator("[data-broadcast-photo-field]")
            photo_input = form.locator("[data-broadcast-photo-input]")
            photo_picker = form.locator(".broadcast-file-picker")
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
            assert await form.locator("[data-broadcast-photo-name]").inner_text() == "notice.png"
            assert "is-selected" in (await photo_picker.get_attribute("class") or "")
            assert await form.locator("[data-broadcast-photo-preview]").count() == 0

            await form.locator('input[name="media_mode"][value="text"]').check()
            assert await photo_field.is_hidden()
            assert await photo_input.input_value() == ""
            assert await form.locator("[data-broadcast-photo-name]").inner_text() == "Файл не выбран"
            assert "is-selected" not in (await photo_picker.get_attribute("class") or "")

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
                    if (form.dataset.submitting !== 'true') return;
                    window.submittedBroadcastBody = form.querySelector('[data-broadcast-compiled-body]').value;
                })"""
            )

            async def handle_send(route):
                await route.fulfill(
                    status=400,
                    content_type="application/json",
                    body='{"ok": false, "message": "не важно для этого теста"}',
                )

            await page.route("**/api/admin/broadcasts/send", handle_send)

            await form.locator('button[type="submit"]').click()
            confirm_dialog = form.locator("[data-broadcast-confirm-dialog]")
            assert await confirm_dialog.is_visible()
            await confirm_dialog.locator("[data-broadcast-confirm-submit]").click()

            compiled = await page.evaluate("window.submittedBroadcastBody")
            assert compiled == (
                "Важная новость\n\n"
                "[reactions]\n"
                "👍 = Всё понятно\n"
                "👎 = Не согласен\n"
                "[/reactions]"
            )
            assert await form.locator("[data-broadcast-submit]").is_disabled()
            assert await form.locator("[data-broadcast-submit]").inner_text() == "Отправка…"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_server_admin_broadcast_preview_is_live_and_sanitized() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(_render_admin_page())
            await page.add_script_tag(path=str(STATIC_DIR / "admin-broadcast.js"), type="module")
            form = page.locator("[data-broadcast-form]")
            preview = page.locator("[data-broadcast-live-preview]")

            assert await preview.locator("[data-broadcast-preview-empty]").is_visible()
            await form.locator("[data-broadcast-body]").fill(
                '<b>Важная новость</b>\n<img src=x onerror="window.previewXss = true">'
            )
            assert await preview.locator("strong").inner_text() == "Важная новость"
            assert await preview.locator("img:not([data-broadcast-preview-photo])").count() == 0
            assert not await page.evaluate("Boolean(window.previewXss)")
            assert await preview.locator("[data-broadcast-preview-empty]").is_hidden()

            await form.locator("[data-broadcast-reactions-toggle]").check()
            reaction_preview = preview.locator("[data-broadcast-preview-reactions]")
            assert await reaction_preview.is_visible()
            assert "👍 — Всё понятно" in await reaction_preview.inner_text()
            assert "👎 — Не согласен" in await reaction_preview.inner_text()

            await form.locator('input[name="media_mode"][value="photo"]').check()
            await form.locator("[data-broadcast-photo-input]").set_input_files(
                files=[{"name": "notice.png", "mimeType": "image/png", "buffer": b"preview-image"}]
            )
            assert await preview.locator("[data-broadcast-preview-photo]").is_visible()
            assert await preview.locator("[data-broadcast-preview-mode]").inner_text() == "Фото с подписью"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_server_admin_broadcast_confirmation_can_cancel_and_submit_once() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await _goto_admin_page(page)
            await page.add_script_tag(path=str(STATIC_DIR / "admin-broadcast.js"), type="module")
            form = page.locator("[data-broadcast-form]")
            submit = form.locator("[data-broadcast-submit]")
            dialog = form.locator("[data-broadcast-confirm-dialog]")
            await form.locator("[data-broadcast-body]").fill("Проверка перед отправкой")

            request_count = {"value": 0}

            async def handle_send(route):
                request_count["value"] += 1
                await route.fulfill(
                    status=400,
                    content_type="application/json",
                    body='{"ok": false, "message": "Telegram временно недоступен.", "field": null}',
                )

            await page.route("**/api/admin/broadcasts/send", handle_send)

            await submit.click()
            assert await dialog.is_visible()
            assert await dialog.locator("[data-broadcast-confirm-count]").inner_text() == "2 чата"
            assert "Проверка перед отправкой" in await dialog.locator("[data-broadcast-confirm-preview]").inner_text()
            assert not await submit.is_disabled()

            await dialog.locator("[data-broadcast-confirm-cancel]").click()
            assert await dialog.is_hidden()
            assert await submit.evaluate("element => element === document.activeElement")
            assert request_count["value"] == 0

            await submit.click()
            await dialog.locator("[data-broadcast-confirm-submit]").click()
            assert await dialog.is_hidden()

            general_error = form.locator("[data-broadcast-form-error]")
            await general_error.wait_for(state="visible")
            assert await general_error.inner_text() == "Telegram временно недоступен."
            assert request_count["value"] == 1
            assert not await submit.is_disabled()
            assert await submit.inner_text() == "Отправить в активные чаты"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_server_admin_broadcast_server_error_binds_to_message_block_and_keeps_state() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await _goto_admin_page(page)
            await page.add_script_tag(path=str(STATIC_DIR / "admin-broadcast.js"), type="module")
            form = page.locator("[data-broadcast-form]")
            submit = form.locator("[data-broadcast-submit]")
            dialog = form.locator("[data-broadcast-confirm-dialog]")
            body = form.locator("[data-broadcast-body]")
            await body.fill("<b>незакрытый тег")

            async def handle_send(route):
                await route.fulfill(
                    status=400,
                    content_type="application/json",
                    body=(
                        '{"ok": false, "field": "message", '
                        '"message": "Некорректный Telegram HTML. Закройте все теги."}'
                    ),
                )

            await page.route("**/api/admin/broadcasts/send", handle_send)

            await submit.click()
            await dialog.locator("[data-broadcast-confirm-submit]").click()

            message_error = form.locator("[data-broadcast-message-error]")
            await message_error.wait_for(state="visible")
            assert await message_error.inner_text() == "Некорректный Telegram HTML. Закройте все теги."
            assert await form.locator("[data-broadcast-media-error]").is_hidden()
            assert await form.locator("[data-broadcast-audience-error]").is_hidden()
            assert await form.locator("[data-broadcast-form-error]").is_hidden()
            # No page reload happened — the composed text is still in the field.
            assert await body.input_value() == "<b>незакрытый тег"
            assert not await submit.is_disabled()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_server_admin_broadcast_server_error_binds_to_media_block() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await _goto_admin_page(page)
            await page.add_script_tag(path=str(STATIC_DIR / "admin-broadcast.js"), type="module")
            form = page.locator("[data-broadcast-form]")
            submit = form.locator("[data-broadcast-submit]")
            dialog = form.locator("[data-broadcast-confirm-dialog]")
            await form.locator("[data-broadcast-body]").fill("Фото без файла")
            await form.locator('input[name="media_mode"][value="photo"]').check()
            # The photo input has a native `required` attribute in this mode; this test
            # exercises the server-side error path, so bypass native constraint validation
            # (which would otherwise silently block the submit event before it fires).
            await form.evaluate("form => { form.noValidate = true; }")

            async def handle_send(route):
                await route.fulfill(
                    status=400,
                    content_type="application/json",
                    body='{"ok": false, "field": "media", "message": "Для режима с фотографией выберите файл."}',
                )

            await page.route("**/api/admin/broadcasts/send", handle_send)

            await submit.click()
            await dialog.locator("[data-broadcast-confirm-submit]").click()

            media_error = form.locator("[data-broadcast-media-error]")
            await media_error.wait_for(state="visible")
            assert await media_error.inner_text() == "Для режима с фотографией выберите файл."
            assert await form.locator("[data-broadcast-message-error]").is_hidden()
            assert await form.locator("[data-broadcast-form-error]").is_hidden()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_server_admin_broadcast_audience_search_and_inline_validation() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(_render_admin_page())
            await page.add_script_tag(path=str(STATIC_DIR / "admin-broadcast.js"), type="module")
            form = page.locator("[data-broadcast-form]")
            selected_count = form.locator("[data-broadcast-selected-count]")

            assert await selected_count.inner_text() == "Выбрано: 2 из 2"
            await form.locator("[data-broadcast-target-search]").fill("second")
            assert await form.locator("[data-broadcast-target-item]:visible").count() == 1
            assert "Second long chat" in await form.locator("[data-broadcast-target-item]:visible").inner_text()

            await form.locator('[data-broadcast-toggle="none"]').click()
            assert await selected_count.inner_text() == "Выбрано: 0 из 2"
            await form.locator("[data-broadcast-body]").fill("Проверка")
            await form.locator("[data-broadcast-submit]").click()

            audience_error = form.locator("[data-broadcast-audience-error]")
            assert await audience_error.is_visible()
            assert await audience_error.inner_text() == "Выберите хотя бы один чат для рассылки."
            assert await form.locator("[data-broadcast-form-error]").is_hidden()
            assert not await form.locator("[data-broadcast-submit]").is_disabled()

            await form.locator('[data-broadcast-toggle="all"]').click()
            assert await selected_count.inner_text() == "Выбрано: 2 из 2"
            assert await audience_error.is_hidden()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_server_admin_shell_is_keyboard_and_mobile_safe() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        try:
            await page.set_content(_render_admin_page())
            await page.add_style_tag(path=str(STATIC_DIR / "panel.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "server-ui-foundation.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "admin-overview.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "admin-feedback.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "admin-broadcast.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "admin-table-search.css"))
            await page.add_script_tag(path=str(STATIC_DIR / "admin-broadcast.js"), type="module")

            has_page_overflow = await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
            assert not has_page_overflow
            assert await page.locator(".top-actions").evaluate(
                "element => getComputedStyle(element).overflowX"
            ) == "auto"
            assert await page.locator('.top-actions [aria-current="page"]').inner_text() == "Обзор"

            await page.keyboard.press("Tab")
            assert await page.evaluate("document.activeElement.classList.contains('skip-link')")
            assert await page.locator(".skip-link").bounding_box() is not None

            form = page.locator("[data-broadcast-form]")
            await form.locator("[data-broadcast-body]").fill("Мобильная проверка")
            await form.locator("[data-broadcast-submit]").click()
            dialog = form.locator("[data-broadcast-confirm-dialog]")
            assert await dialog.is_visible()
            bounds = await dialog.bounding_box()
            assert bounds is not None
            assert bounds["x"] >= 0
            assert bounds["x"] + bounds["width"] <= 390
            assert bounds["height"] <= 844
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
            await page.keyboard.press("Escape")
            assert await dialog.is_hidden()
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("viewport", "minimum_preview_width", "minimum_composer_height"),
    [
        ({"width": 1440, "height": 900}, 300, 190),
        ({"width": 390, "height": 844}, 310, 210),
    ],
)
async def test_broadcast_confirm_and_preview_fit_supported_viewports(
    viewport: dict[str, int],
    minimum_preview_width: int,
    minimum_composer_height: int,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await page.set_content(_render_admin_page())
            await page.add_style_tag(path=str(STATIC_DIR / "panel.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "server-ui-foundation.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "admin-overview.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "admin-feedback.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "admin-broadcast.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "admin-table-search.css"))
            await page.add_script_tag(path=str(STATIC_DIR / "admin-broadcast.js"), type="module")

            form = page.locator("[data-broadcast-form]")
            await form.locator("[data-broadcast-body]").fill(
                "<b>Большое обновление Selara</b>\n\n"
                "Постоянный preview, фото, реакции и безопасное подтверждение.\n\n"
                "<blockquote>Проверьте сообщение перед публикацией.</blockquote>"
            )
            await form.locator('input[name="media_mode"][value="photo"]').check()
            await form.locator("[data-broadcast-photo-input]").set_input_files(SAMPLE_PHOTO)
            await form.locator("[data-broadcast-reactions-toggle]").check()
            await page.locator("[data-broadcast-preview-photo]").evaluate(
                "image => image.decode()"
            )

            preview_width = await page.locator("[data-broadcast-preview-message]").evaluate(
                "element => element.getBoundingClientRect().width"
            )
            assert preview_width >= minimum_preview_width
            composer_height = await form.locator("[data-broadcast-body]").evaluate(
                "element => element.getBoundingClientRect().height"
            )
            assert composer_height >= minimum_composer_height

            await form.locator("[data-broadcast-submit]").click()
            dialog = form.locator("[data-broadcast-confirm-dialog]")
            await dialog.wait_for(state="visible")
            geometry = await dialog.evaluate(
                """dialog => {
                    const bounds = dialog.getBoundingClientRect();
                    const title = dialog.querySelector('[data-broadcast-confirm-count]')
                        .closest('.broadcast-confirm-copy').getBoundingClientRect();
                    const actions = dialog.querySelector('.broadcast-confirm-actions')
                        .getBoundingClientRect();
                    return {
                        scrollTop: dialog.scrollTop,
                        dialogTop: bounds.top,
                        dialogBottom: bounds.bottom,
                        titleTop: title.top,
                        actionsBottom: actions.bottom,
                        viewportHeight: window.innerHeight,
                    };
                }"""
            )
            assert geometry["scrollTop"] == 0
            assert geometry["dialogTop"] >= 0
            assert geometry["dialogBottom"] <= geometry["viewportHeight"]
            assert geometry["titleTop"] >= geometry["dialogTop"]
            assert geometry["actionsBottom"] <= geometry["dialogBottom"]
            assert await dialog.locator("[data-broadcast-confirm-cancel]").evaluate(
                "element => element === document.activeElement"
            )
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
        finally:
            await browser.close()
