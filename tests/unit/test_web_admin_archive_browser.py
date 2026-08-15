from datetime import datetime
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.rendering import create_template_environment

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = ROOT / "src/selara/web/templates"
STATIC_DIR = ROOT / "src/selara/web/static"


def _render_archive(*, selected_explicitly: bool) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    return environment.get_template("admin_messages_compact.html").render(
        page_title="Selara test archive",
        page_name="admin_messages_compact",
        top_links=[
            {"href": "/app/admin", "label": "Обзор", "variant": "ghost"},
            {"href": "/app/admin#broadcasts", "label": "Рассылки", "variant": "ghost"},
            {
                "href": "/app/admin/table/messages_compact",
                "label": "История",
                "variant": "subtle",
                "current": True,
            },
        ],
        show_logout=False,
        body_classes="ui-admin-shell",
        navigation_label="Разделы админки",
        flash=None,
        error=None,
        extra_styles=["admin-archive.css"],
        extra_scripts=["admin-archive.js"],
        table_name="messages_compact",
        table_title="Архив сообщений",
        total=4,
        page=1,
        limit=50,
        shown_message_count=3,
        filters_input={"user_id": "", "text": "", "snapshot_kind": ""},
        filter_reset_href="/app/admin/table/messages_compact?chat_id=-100500",
        back_to_chats_href="/app/admin/table/messages_compact",
        previous_page_href=None,
        next_page_href="/app/admin/table/messages_compact?chat_id=-100500&before=test-cursor",
        chat_selected_explicitly=selected_explicitly,
        active_chat={"chat_id": -100500, "label": "Archive Chat", "message_count": 4},
        chat_summaries=[
            {
                "chat_id": -100500,
                "label": "Archive Chat",
                "last_preview": "Последнее сообщение в выбранном чате",
                "last_time": "18:25",
                "snapshot_count": 4,
                "active": True,
                "href": "/app/admin/table/messages_compact?chat_id=-100500",
            },
            {
                "chat_id": -100700,
                "label": "Очень длинное имя тестового чата 🚀 مثال テスト",
                "last_preview": "Фото без доступного локального preview",
                "last_time": "вчера",
                "snapshot_count": 1,
                "active": False,
                "href": "/app/admin/table/messages_compact?chat_id=-100700",
            },
        ],
        rows=[
            {
                "id": 1,
                "date_label": "8 апреля 2026",
                "time_label": "18:24",
                "snapshot_at": datetime(2026, 4, 8, 18, 24),
                "user_id": 202,
                "user_label": "@bob",
                "author_tone": 4,
                "snapshot_kind": "created",
                "message_type": "text",
                "message_type_label": "Текст",
                "message_preview": "Кто уже посмотрел обновление?",
                "has_text": True,
                "reply_preview": None,
                "raw_href": "/app/admin/table/messages?id=1",
            },
            {
                "id": 2,
                "date_label": "8 апреля 2026",
                "time_label": "18:25",
                "snapshot_at": datetime(2026, 4, 8, 18, 25),
                "user_id": 101,
                "user_label": "LongContinuousNameWithoutSpaces1234567890",
                "author_tone": 5,
                "snapshot_kind": "edited",
                "message_type": "text",
                "message_type_label": "Текст",
                "message_preview": "Да, интерфейс стал заметно удобнее.",
                "has_text": True,
                "reply_preview": "#777 @bob: Кто уже посмотрел обновление?",
                "raw_href": "/app/admin/table/messages?id=2",
                "snapshot_count": 2,
                "grouped_with_next": True,
                "edit_history": [
                    {
                        "id": 4,
                        "time_label": "18:24",
                        "snapshot_kind_label": "Исходная версия",
                        "message_preview": "Да, интерфейс уже меняется.",
                        "has_text": True,
                        "raw_href": "/app/admin/table/messages?id=4",
                    }
                ],
            },
            {
                "id": 3,
                "date_label": "8 апреля 2026",
                "time_label": "18:26",
                "snapshot_at": datetime(2026, 4, 8, 18, 26),
                "user_id": 101,
                "user_label": "LongContinuousNameWithoutSpaces1234567890",
                "author_tone": 5,
                "snapshot_kind": "created",
                "message_type": "photo",
                "message_type_label": "Фото",
                "message_preview": "[photo]",
                "has_text": False,
                "reply_preview": None,
                "raw_href": "/app/admin/table/messages?id=3",
                "grouped_with_previous": True,
                "media_info": {
                    "icon": "▧",
                    "title": "Фото",
                    "facts": [
                        "1280×720",
                        "VeryLongContinuousTelegramMediaNameWithoutSpaces1234567890.jpg",
                        "245.0 КБ",
                    ],
                    "preview_href": "data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20width='1280'%20height='720'%3E%3Crect%20width='100%25'%20height='100%25'%20fill='%2358d8ff'/%3E%3C/svg%3E",
                },
            },
        ],
    )


@pytest.mark.asyncio
async def test_archive_desktop_is_split_dialogue_without_database_table() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        try:
            await page.set_content(_render_archive(selected_explicitly=True))
            await page.add_style_tag(path=str(STATIC_DIR / "panel.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "server-ui-foundation.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "admin-archive.css"))
            await page.evaluate(
                """() => {
                    const originalFetch = window.fetch.bind(window);
                    window.archivePhotoFetchCalls = 0;
                    window.fetch = (...args) => {
                        window.archivePhotoFetchCalls += 1;
                        return originalFetch(...args);
                    };
                }"""
            )
            archive_script = STATIC_DIR / "admin-archive.js"
            if archive_script.exists():
                await page.add_script_tag(path=str(archive_script), type="module")

            assert await page.locator(".admin-data-table").count() == 0
            assert await page.locator(".archive-chat-card").count() == 2
            assert await page.locator(".archive-message").count() == 3
            assert await page.locator(".archive-reply").count() == 1
            assert await page.get_by_text("Изменено").count() == 1
            assert await page.locator(".archive-media-fallback").count() == 1
            assert await page.locator(".archive-media-fact").count() == 3
            photo_preview = page.locator("[data-archive-photo-preview]")
            assert await photo_preview.count() == 1
            assert await photo_preview.locator("img").count() == 0
            assert await page.evaluate("window.archivePhotoFetchCalls") == 0
            assert await photo_preview.locator("[data-archive-photo-load]").evaluate(
                "element => element.getBoundingClientRect().height"
            ) >= 44
            await photo_preview.locator("[data-archive-photo-load]").click()
            await photo_preview.locator("img").wait_for(state="visible")
            assert await photo_preview.get_by_text("Фото загружено").is_visible()
            assert await page.evaluate("window.archivePhotoFetchCalls") == 1
            assert await photo_preview.locator("[data-archive-photo-load]").inner_text() == "Скрыть фото"
            await photo_preview.locator("[data-archive-photo-load]").click()
            assert await photo_preview.locator("[data-archive-photo-frame]").is_hidden()
            assert await page.evaluate("window.archivePhotoFetchCalls") == 1
            await photo_preview.locator("[data-archive-photo-load]").click()
            assert await photo_preview.locator("img").is_visible()
            assert await page.evaluate("window.archivePhotoFetchCalls") == 1
            assert await page.locator(".archive-media-card").evaluate(
                "element => element.scrollWidth <= element.clientWidth"
            )
            assert await page.locator(".archive-message.is-continuation").count() == 1
            assert await page.locator(".archive-edit-history").count() == 1
            assert await page.locator(".archive-edit-version").count() == 1
            assert await page.locator('.archive-pagination a[href*="before="]').count() == 1
            assert await page.get_by_text("Показано сообщений: 3").count() == 1
            assert await page.locator(".archive-message-body").nth(1).text_content() == (
                "Да, интерфейс стал заметно удобнее."
            )
            assert await page.locator(".archive-edit-version p").text_content() == (
                "Да, интерфейс уже меняется."
            )
            assert await page.locator(".archive-chat-rail").is_visible()
            assert await page.locator(".archive-conversation").is_visible()
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_archive_photo_preview_shows_recoverable_error_without_broken_image() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        try:
            await page.set_content(_render_archive(selected_explicitly=True))
            await page.add_style_tag(path=str(STATIC_DIR / "panel.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "server-ui-foundation.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "admin-archive.css"))
            preview = page.locator("[data-archive-photo-preview]")
            button = preview.locator("[data-archive-photo-load]")
            await button.evaluate(
                "element => { element.dataset.previewUrl = 'data:text/plain,not-an-image'; }"
            )
            await page.add_script_tag(
                path=str(STATIC_DIR / "admin-archive.js"),
                type="module",
            )

            await button.click()
            await preview.get_by_text("Сервер вернул файл неизвестного типа.").wait_for()

            assert await preview.get_attribute("data-state") == "error"
            assert await button.is_enabled()
            assert await button.inner_text() == "Повторить загрузку"
            assert await preview.locator("img").count() == 0
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_explicitly", [False, True])
async def test_archive_mobile_uses_list_then_conversation_flow(
    selected_explicitly: bool,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        try:
            await page.set_content(_render_archive(selected_explicitly=selected_explicitly))
            await page.add_style_tag(path=str(STATIC_DIR / "panel.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "server-ui-foundation.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "admin-archive.css"))
            archive_script = STATIC_DIR / "admin-archive.js"
            if archive_script.exists():
                await page.add_script_tag(path=str(archive_script), type="module")

            for field_name in ("date_from", "date_to", "message_type", "has_reply"):
                assert await page.locator(f'[name="{field_name}"]').count() == 1
            assert await page.locator(".archive-filters").evaluate(
                "element => element.scrollWidth <= element.clientWidth"
            )
            assert await page.locator(".archive-chat-rail").is_visible() is (not selected_explicitly)
            assert await page.locator(".archive-conversation").is_visible() is selected_explicitly
            assert await page.locator(".archive-mobile-back").is_visible() is selected_explicitly
            assert await page.locator(".archive-chat-card").last.evaluate(
                "element => element.scrollWidth <= element.clientWidth"
            )
            assert await page.locator(".archive-chat-card").first.evaluate(
                "element => getComputedStyle(element).textDecorationLine"
            ) == "none"
            if selected_explicitly:
                edit_history = page.locator(".archive-edit-history")
                assert await edit_history.count() == 1
                assert await edit_history.locator("summary").evaluate(
                    "element => element.getBoundingClientRect().height"
                ) >= 44
                await edit_history.locator("summary").click()
                assert await edit_history.get_by_text("Да, интерфейс уже меняется.").is_visible()
                photo_preview = page.locator("[data-archive-photo-preview]")
                await photo_preview.locator("[data-archive-photo-load]").click()
                await photo_preview.locator("img").wait_for(state="visible")
                assert await photo_preview.evaluate(
                    "element => element.scrollWidth <= element.clientWidth"
                )
                action_geometry = await page.locator(".archive-mobile-actions").evaluate(
                    """actions => {
                        const panel = actions.closest('.archive-panel');
                        const panelRect = panel.getBoundingClientRect();
                        const panelStyle = getComputedStyle(panel);
                        const container = actions.closest('.archive-conversation').getBoundingClientRect();
                        const containerStyle = getComputedStyle(actions.closest('.archive-conversation'));
                        const filterButton = actions.lastElementChild.getBoundingClientRect();
                        const messages = [...document.querySelectorAll('.archive-message')];
                        return {
                            panelContentRight: panelRect.right
                                - parseFloat(panelStyle.borderRightWidth)
                                - parseFloat(panelStyle.paddingRight),
                            layoutRight: actions.closest('.archive-layout').getBoundingClientRect().right,
                            conversationContentRight: container.right
                                - parseFloat(containerStyle.borderRightWidth)
                                - parseFloat(containerStyle.paddingRight),
                            buttonRight: filterButton.right,
                            messageRight: Math.max(
                                ...messages.map(message => message.getBoundingClientRect().right),
                            ),
                        };
                    }"""
                )
                assert action_geometry["layoutRight"] <= action_geometry["panelContentRight"]
                assert action_geometry["buttonRight"] <= action_geometry["conversationContentRight"]
                assert action_geometry["messageRight"] <= action_geometry["conversationContentRight"]
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_archive_tablet_portrait_prioritizes_the_conversation() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 820, "height": 1180})
        try:
            await page.set_content(_render_archive(selected_explicitly=True))
            await page.add_style_tag(path=str(STATIC_DIR / "panel.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "server-ui-foundation.css"))
            await page.add_style_tag(path=str(STATIC_DIR / "admin-archive.css"))

            assert await page.locator(".archive-chat-rail").is_hidden()
            assert await page.locator(".archive-conversation").is_visible()
            assert await page.locator(".archive-message").first.evaluate(
                "element => element.getBoundingClientRect().width"
            ) >= 600
            tablet_filter_columns = await page.locator(".archive-filters").evaluate(
                """filters => {
                    const user = filters.querySelector('[name="user_id"]').getBoundingClientRect();
                    const messageType = filters.querySelector('[name="message_type"]').getBoundingClientRect();
                    return {userLeft: user.left, messageTypeLeft: messageType.left};
                }"""
            )
            assert tablet_filter_columns["messageTypeLeft"] > tablet_filter_columns["userLeft"]
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
        finally:
            await browser.close()
