from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from selara.web.rendering import create_template_environment  # noqa: E402


def render(*, selected_explicitly: bool = True) -> str:
    environment = create_template_environment(
        template_dir=ROOT / "src" / "selara" / "web" / "templates"
    )
    return environment.get_template("admin_messages_compact.html").render(
        page_title="Selara archive UI fixture",
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
            {"href": "/app/admin#database", "label": "База данных", "variant": "ghost"},
        ],
        show_logout=True,
        logout_action="/app/admin/logout",
        body_classes="ui-admin-shell",
        navigation_label="Разделы админки",
        flash=None,
        error=None,
        extra_styles=["admin-archive.css"],
        extra_scripts=["admin-archive.js"],
        table_name="messages_compact",
        table_title="Архив сообщений",
        total=5,
        page=1,
        limit=50,
        shown_message_count=4,
        filters_input={"user_id": "", "text": "", "snapshot_kind": ""},
        filter_reset_href="/app/admin/table/messages_compact?chat_id=-100500",
        back_to_chats_href="/app/admin/table/messages_compact",
        previous_page_href=None,
        next_page_href="/app/admin/table/messages_compact?chat_id=-100500&before=test-cursor",
        chat_selected_explicitly=selected_explicitly,
        active_chat={"chat_id": -100500, "label": "Archive Chat", "message_count": 5},
        chat_summaries=[
            {
                "chat_id": -100500,
                "label": "Archive Chat",
                "last_preview": "Фото без автоматической загрузки",
                "last_time": "18:26",
                "snapshot_count": 5,
                "active": True,
                "href": "/app/admin/table/messages_compact?chat_id=-100500",
            },
            {
                "chat_id": -100700,
                "label": "Очень длинное имя тестового чата 🚀 مثال テスト",
                "last_preview": "Сообщение с длинным названием и безопасным переносом",
                "last_time": "17:40",
                "snapshot_count": 12,
                "active": False,
                "href": "/app/admin/table/messages_compact?chat_id=-100700",
            },
            {
                "chat_id": -100900,
                "label": "Чат команды Selara",
                "last_preview": "Кто уже посмотрел обновление?",
                "last_time": "вчера",
                "snapshot_count": 8,
                "active": False,
                "href": "/app/admin/table/messages_compact?chat_id=-100900",
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
                "message_preview": "Кто уже посмотрел большое обновление Selara?",
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
                "user_label": "Alice",
                "author_tone": 5,
                "snapshot_kind": "edited",
                "message_type": "text",
                "message_type_label": "Текст",
                "message_preview": "Да, новая админка стала заметно понятнее. Особенно нравится preview перед отправкой.",
                "has_text": True,
                "reply_preview": "#777 @bob: Кто уже посмотрел большое обновление Selara?",
                "raw_href": "/app/admin/table/messages?id=2",
                "snapshot_count": 2,
                "grouped_with_next": True,
                "edit_history": [
                    {
                        "id": 5,
                        "time_label": "18:24",
                        "snapshot_kind_label": "Исходный снимок",
                        "message_preview": "Да, новая админка стала заметно понятнее.",
                        "has_text": True,
                        "raw_href": "/app/admin/table/messages?id=5",
                    }
                ],
            },
            {
                "id": 3,
                "date_label": "8 апреля 2026",
                "time_label": "18:26",
                "snapshot_at": datetime(2026, 4, 8, 18, 26),
                "user_id": 101,
                "user_label": "Alice",
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
                    "facts": ["1280×720", "245.0 КБ"],
                    "preview_href": "data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20width='1280'%20height='720'%3E%3Cdefs%3E%3ClinearGradient%20id='g'%3E%3Cstop%20stop-color='%23142b49'/%3E%3Cstop%20offset='1'%20stop-color='%2358d8ff'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect%20width='100%25'%20height='100%25'%20fill='url(%23g)'/%3E%3C/svg%3E",
                },
            },
            {
                "id": 4,
                "date_label": "9 апреля 2026",
                "time_label": "09:03",
                "snapshot_at": datetime(2026, 4, 9, 9, 3),
                "user_id": 404,
                "user_label": "LongContinuousNameWithoutSpaces1234567890",
                "author_tone": 1,
                "snapshot_kind": "created",
                "message_type": "text",
                "message_type_label": "Текст",
                "message_preview": "Проверка нового дня, длинного автора и устойчивой компоновки сообщения.",
                "has_text": True,
                "reply_preview": None,
                "raw_href": "/app/admin/table/messages?id=4",
            },
        ],
    )


def main() -> None:
    sys.stdout.write(render())


if __name__ == "__main__":
    main()
