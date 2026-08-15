from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from selara.web.rendering import create_template_environment  # noqa: E402


@dataclass
class FixtureUser:
    """Stand-in for UserModel — avoids pulling SQLAlchemy into the minimal
    frontend CI job, which only installs Jinja2 to render HTML fixtures."""

    telegram_user_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    is_bot: bool
    subscription_exempt: bool
    updated_at: datetime

TOP_LINKS = [
    {"href": "/app/admin", "label": "Обзор", "variant": "ghost"},
    {"href": "/app/admin#broadcasts", "label": "Рассылки", "variant": "ghost"},
    {"href": "/app/admin/table/messages_compact", "label": "История", "variant": "ghost"},
    {"href": "/app/admin#database", "label": "База данных", "variant": "subtle", "current": True},
]


def _users(count: int) -> list[FixtureUser]:
    rows = []
    for index in range(count):
        rows.append(
            FixtureUser(
                telegram_user_id=100 + index,
                username=(
                    "ОченьДлинноеИмяПользователяБезПробеловForTestOverflow1234567890"
                    if index == 0
                    else f"user{index}"
                ),
                first_name="Алиса" if index == 0 else f"User{index}",
                last_name=None,
                is_bot=index % 2 == 0,
                subscription_exempt=index % 3 == 0,
                updated_at=datetime(2026, 4, 8, 18, 24, tzinfo=timezone.utc),
            )
        )
    return rows


def render_table(*, empty: bool = False) -> str:
    environment = create_template_environment(
        template_dir=ROOT / "src" / "selara" / "web" / "templates"
    )
    rows = [] if empty else _users(3)
    columns = ["telegram_user_id", "username", "first_name", "is_bot", "subscription_exempt", "updated_at"]
    row_entries = [
        {
            "row": row,
            "pk_query": f"telegram_user_id={row.telegram_user_id}",
            "delete_label": f"Пользователи: telegram_user_id={row.telegram_user_id}",
        }
        for row in rows
    ]
    total = 0 if empty else 120
    return environment.get_template("admin_table.html").render(
        page_title="Selara admin table fixture",
        page_name="admin_table",
        top_links=TOP_LINKS,
        show_logout=True,
        logout_action="/app/admin/logout",
        body_classes="ui-admin-shell",
        navigation_label="Разделы админки",
        flash=None,
        error=None,
        extra_styles=["admin-table.css"],
        extra_scripts=["admin-table.js"],
        table_name="users",
        table_title="Пользователи",
        columns=columns,
        row_entries=row_entries,
        page=1,
        total=total,
        limit=50,
        filters_input={},
        reference_labels={},
        previous_page_href=None,
        next_page_href="/app/admin/table/users?page=2" if total > 50 else None,
    )


def render_edit() -> str:
    environment = create_template_environment(
        template_dir=ROOT / "src" / "selara" / "web" / "templates"
    )
    row = _users(1)[0]
    columns = [
        (col, getattr(row, col))
        for col in ("telegram_user_id", "username", "first_name", "is_bot", "updated_at")
    ]
    return environment.get_template("admin_edit.html").render(
        page_title="Selara admin edit fixture",
        page_name="admin_edit",
        top_links=TOP_LINKS,
        show_logout=True,
        logout_action="/app/admin/logout",
        body_classes="ui-admin-shell",
        navigation_label="Разделы админки",
        flash=None,
        error=None,
        extra_styles=["admin-table.css"],
        extra_scripts=["admin-table.js"],
        table_name="users",
        table_title="Пользователи",
        record_id="telegram_user_id=100",
        pk_fields=[("telegram_user_id", row.telegram_user_id)],
        primary_key_columns=["telegram_user_id"],
        columns=columns,
        reference_labels={},
    )


def main() -> None:
    sys.stdout.write(render_table())


if __name__ == "__main__":
    main()
