from datetime import datetime, timezone

from selara.core.chat_settings import CHAT_SETTINGS_KEYS, default_chat_settings
from selara.core.config import Settings
from selara.domain.entities import ActivityStats, ChatRoleDefinition, LeaderboardItem
from selara.web.presenters import (
    build_activity_rows,
    build_alias_mode_setting,
    build_leaderboard_rows,
    build_roles,
    build_settings_sections,
    format_datetime,
    permissions_text_ru,
)


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
            "BOT_USERNAME": "selara_test_bot",
        }
    )


def test_format_datetime_is_russian_friendly() -> None:
    value = datetime(2026, 3, 7, 17, 14, tzinfo=timezone.utc)

    assert format_datetime(value) == "07.03.2026 17:14 UTC"


def test_permissions_text_ru_translates_permission_codes() -> None:
    assert permissions_text_ru(["announce", "manage_games"]) == "объявления, управление играми"


def test_build_activity_rows_uses_human_labels() -> None:
    rows = build_activity_rows(
        [
            ActivityStats(
                chat_id=1,
                user_id=1,
                message_count=93,
                last_seen_at=datetime(2026, 3, 7, 17, 14, tzinfo=timezone.utc),
                username="user1",
                first_name=None,
                last_name=None,
            )
        ]
    )

    assert rows[0]["primary"] == "93 сообщений"
    assert rows[0]["secondary"] == "Последняя активность: 07.03.2026 17:14 UTC"


def test_build_leaderboard_rows_uses_human_labels() -> None:
    rows = build_leaderboard_rows(
        [
            LeaderboardItem(
                user_id=1,
                username="user1",
                first_name=None,
                last_name=None,
                activity_value=93,
                karma_value=0,
                hybrid_score=0.300,
                last_seen_at=datetime(2026, 3, 7, 17, 14, tzinfo=timezone.utc),
            )
        ],
        kind="karma",
    )

    assert rows[0]["primary"] == "Карма 0"
    assert rows[0]["secondary"] == "Сообщений: 93 • Активность: 07.03.2026 17:14 UTC"


def test_build_roles_translates_permissions() -> None:
    roles = build_roles(
        [
            ChatRoleDefinition(
                chat_id=1,
                role_code="owner",
                title_ru="Владелец",
                rank=40,
                permissions=("announce", "manage_games", "moderate_users"),
                is_system=True,
            )
        ]
    )

    assert roles[0]["meta"] == "код: owner • ранг: 40"
    assert roles[0]["permissions"] == "объявления, управление играми, модерация пользователей"


def test_build_settings_sections_covers_all_chat_settings_keys() -> None:
    settings = _settings()
    defaults = default_chat_settings(settings)

    sections = build_settings_sections(current=defaults, defaults=defaults, editable=True)

    seen_keys = [
        item["key"]
        for section in sections
        for item in section["items"]
    ]

    assert sorted(seen_keys) == sorted(CHAT_SETTINGS_KEYS)


def test_build_alias_mode_setting_uses_human_labels() -> None:
    item = build_alias_mode_setting(current_mode="aliases_if_exists", editable=True)

    assert item["current_value_display"] == "только алиасы группы"
    assert item["default_value"] == "both"
    assert [option["value"] for option in item["options"]] == ["aliases_if_exists", "both", "standard_only"]
