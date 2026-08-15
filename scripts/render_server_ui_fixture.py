from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from selara.web.rendering import create_template_environment  # noqa: E402


def main() -> None:
    environment = create_template_environment(
        template_dir=ROOT / "src" / "selara" / "web" / "templates"
    )
    html = environment.get_template("admin.html").render(
        page_title="Selara server UI fixture",
        page_name="admin",
        top_links=[
            {
                "href": "/app/admin",
                "label": "Обзор",
                "variant": "subtle",
                "current": True,
            },
            {
                "href": "/app/admin#broadcasts",
                "label": "Рассылки",
                "variant": "ghost",
            },
            {
                "href": "/app/admin/table/messages_compact",
                "label": "История",
                "variant": "ghost",
            },
            {
                "href": "/app/admin#database",
                "label": "База данных",
                "variant": "ghost",
            },
        ],
        show_logout=True,
        logout_action="/app/admin/logout",
        body_classes="ui-admin-shell",
        navigation_label="Разделы админки",
        flash=None,
        error=None,
        extra_styles=["admin-overview.css", "admin-feedback.css", "admin-broadcast.css"],
        extra_scripts=["admin-overview.js", "admin-feedback.js", "admin-broadcast.js"],
        admin_user_id=77,
        open_feedback_count=1,
        attention_broadcast_count=1,
        attention_summary="2 задачи требуют внимания",
        recent_broadcast_count=1,
        admin_table_count=47,
        broadcast_active_days=3,
        recent_active_chat_count=2,
        broadcast_audience_status="2 чата доступны",
        recent_active_chats=[
            {
                "chat_id": -1001001,
                "title": "Длинное имя тестового чата 🚀",
                "last_activity_at": "сегодня, 03:00",
                "checked": True,
            },
            {
                "chat_id": -1001002,
                "title": "Second chat مثال テスト",
                "last_activity_at": "вчера, 21:15",
                "checked": False,
            },
        ],
        recent_broadcasts=[
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
        ],
        feedback_requests=[],
        feedback_status="all",
        feedback_filter_error=None,
        table_sections=[],
    )
    sys.stdout.write(html)


if __name__ == "__main__":
    main()
