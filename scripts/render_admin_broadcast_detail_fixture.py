from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from selara.web.rendering import create_template_environment  # noqa: E402


def render(*, delivery_status: str = "all") -> str:
    environment = create_template_environment(
        template_dir=ROOT / "src" / "selara" / "web" / "templates"
    )
    delivery_filters = [
        {
            "value": "all",
            "label": "Все",
            "count": 3,
            "href": "/app/admin/broadcasts/42#deliveries",
            "current": delivery_status == "all",
        },
        {
            "value": "sent",
            "label": "Доставлено",
            "count": 1,
            "href": "/app/admin/broadcasts/42?delivery_status=sent#deliveries",
            "current": delivery_status == "sent",
        },
        {
            "value": "failed",
            "label": "Ошибки",
            "count": 1,
            "href": "/app/admin/broadcasts/42?delivery_status=failed#deliveries",
            "current": delivery_status == "failed",
        },
        {
            "value": "pending",
            "label": "Ожидают",
            "count": 1,
            "href": "/app/admin/broadcasts/42?delivery_status=pending#deliveries",
            "current": delivery_status == "pending",
        },
    ]
    all_deliveries = [
        {
            "chat_id": -1001001,
            "chat_title": "Selara News",
            "last_activity_at": "сегодня, 12:00",
            "status_code": "sent",
            "status_label": "Доставлено",
            "status_tone": "ok",
            "telegram_message_id": 9101,
            "reply_count": 2,
            "reaction_mode": "native",
            "reaction_mode_label": "Нативные реакции",
            "bot_member_status": "administrator",
            "sent_at": "сегодня, 12:05",
            "error_text": None,
        },
        {
            "chat_id": -1001002,
            "chat_title": "VeryLongContinuousTelegramChatNameWithoutSpaces1234567890",
            "last_activity_at": "вчера, 21:40",
            "status_code": "failed",
            "status_label": "Ошибка",
            "status_tone": "warn",
            "telegram_message_id": None,
            "reply_count": 0,
            "reaction_mode": "none",
            "reaction_mode_label": "Без реакций",
            "bot_member_status": "member",
            "sent_at": "—",
            "error_text": "Telegram API: CHAT_WRITE_FORBIDDEN",
        },
        {
            "chat_id": -1001003,
            "chat_title": "Команда продукта مثال テスト",
            "last_activity_at": "сегодня, 08:10",
            "status_code": "pending",
            "status_label": "Ожидание",
            "status_tone": "muted",
            "telegram_message_id": None,
            "reply_count": 0,
            "reaction_mode": "none",
            "reaction_mode_label": "Режим определится при отправке",
            "bot_member_status": None,
            "sent_at": "—",
            "error_text": None,
        },
    ]
    visible_deliveries = (
        all_deliveries
        if delivery_status == "all"
        else [item for item in all_deliveries if item["status_code"] == delivery_status]
    )
    configured_reactions = [
        {
            "chat_title": "Selara News",
            "user_label": "@alice",
            "emoji": "❤️",
            "reaction_type": "emoji",
            "reaction_value": "❤",
            "reaction_label": "❤️",
            "source": "native",
            "option_key": "r1",
            "option_label": "Всё понравилось",
            "reacted_at": "сегодня, 12:10",
        }
    ]
    other_reactions = [
        {
            "chat_title": "Selara News",
            "user_label": "LongContinuousUserNameWithoutSpaces1234567890",
            "emoji": "✨",
            "reaction_type": "custom_emoji",
            "reaction_value": "5368324170671202286",
            "reaction_label": "Custom emoji 5368324170671202286",
            "source": "native",
            "option_key": None,
            "option_label": "",
            "reacted_at": "сегодня, 12:11",
        }
    ]
    other_reaction_counts = [
        {
            "chat_title": "Selara News",
            "emoji": "⭐",
            "reaction_type": "paid",
            "reaction_value": "paid",
            "reaction_label": "Платная реакция",
            "option_key": None,
            "option_label": "",
            "count": 3,
            "observed_at": "сегодня, 12:12",
        }
    ]
    return environment.get_template("admin_broadcast_detail.html").render(
        page_title="Selara broadcast detail fixture",
        page_name="admin_broadcast_detail",
        top_links=[
            {"href": "/app/admin", "label": "Обзор", "variant": "ghost"},
            {
                "href": "/app/admin#broadcasts",
                "label": "Рассылки",
                "variant": "subtle",
                "current": True,
            },
            {
                "href": "/app/admin/table/messages_compact",
                "label": "История",
                "variant": "ghost",
            },
        ],
        show_logout=True,
        logout_action="/app/admin/logout",
        body_classes="ui-admin-shell",
        navigation_label="Разделы админки",
        flash=None,
        error=None,
        extra_styles=["admin-broadcast-detail.css"],
        broadcast={
            "id": 42,
            "created_at": "15 августа 2026, 12:05",
            "body": "Большое обновление Selara уже доступно.",
            "message_preview_html": "<b>Большое обновление Selara</b> уже доступно.",
            "media_type": "photo",
            "media_type_label": "Фото",
            "reaction_options": [
                {"key": "r1", "emoji": "❤️", "label": "Всё понравилось"},
                {"key": "r2", "emoji": "👀", "label": "Изучу позже"},
            ],
            "active_since_days": 3,
            "target_count": 3,
            "sent_count": 1,
            "failed_count": 1,
            "pending_count": 1,
            "reply_count": 2,
            "reaction_count": 2,
            "anonymous_reaction_total": 3,
            "delivery_percent": 33,
            "status_label": "Выполнено частично",
            "status_tone": "warn",
        },
        deliveries=visible_deliveries,
        shown_delivery_count=len(visible_deliveries),
        delivery_status=delivery_status,
        delivery_filters=delivery_filters,
        delivery_filter_error=None,
        replies=[
            {
                "id": 7,
                "chat_title": "Selara News",
                "user_label": "@alice",
                "sent_at": "сегодня, 12:15",
                "message_type": "text",
                "telegram_message_id": 9201,
                "preview": "Спасибо! Особенно понравилась новая история сообщений.",
                "bot_reaction_emoji": "❤",
                "bot_reaction_display": "Прочитано",
                "bot_reaction_updated_by_user_id": 77,
                "bot_reaction_updated_at": "сегодня, 12:20",
            },
            {
                "id": 8,
                "chat_title": "VeryLongContinuousTelegramChatNameWithoutSpaces1234567890",
                "user_label": "مستخدم طويل テスト",
                "sent_at": "сегодня, 12:17",
                "message_type": "photo",
                "telegram_message_id": 9202,
                "preview": "[photo]",
                "bot_reaction_emoji": None,
                "bot_reaction_display": None,
                "bot_reaction_updated_by_user_id": None,
                "bot_reaction_updated_at": None,
            },
        ],
        reactions=configured_reactions + other_reactions,
        anonymous_reaction_counts=other_reaction_counts,
        configured_reactions=configured_reactions,
        other_reactions=other_reactions,
        configured_reaction_counts=[],
        other_reaction_counts=other_reaction_counts,
    )


def main() -> None:
    sys.stdout.write(render())


if __name__ == "__main__":
    main()
