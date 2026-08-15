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
    html = environment.get_template("audit.html").render(
        page_title="Selara audit fixture",
        page_name="audit",
        top_links=[],
        show_logout=True,
        flash=None,
        error=None,
        extra_styles=["audit.css"],
        chat_title="Selara Hub",
        chat_id=-1001,
        chat_section_links=[
            {"href": "/app/chat/-1001", "label": "Обзор", "variant": "ghost"},
            {"href": "/app/chat/-1001/audit", "label": "Журнал", "variant": "primary"},
        ],
        audit_total_count=1,
        audit_shown_count=1,
        audit_system_count=0,
        audit_filters={"q": "", "category": "all", "actor": "all"},
        audit_filter_errors=[],
        audit_load_error=None,
        audit_category_options=[
            {"value": "all", "label": "Все категории"},
            {"value": "settings", "label": "Настройки"},
        ],
        audit_actor_options=[
            {"value": "all", "label": "Все инициаторы"},
            {"value": "users", "label": "Пользователи"},
        ],
        audit_reset_href="/app/chat/-1001/audit",
        audit_groups=[
            {
                "date_label": "15 августа 2026",
                "rows": [
                    {
                        "event_id": 44,
                        "when": "15.08.2026 10:30 UTC",
                        "time_label": "10:30",
                        "action": "web_setting_updated",
                        "action_label": "Изменена настройка",
                        "category_code": "settings",
                        "category_label": "Настройки",
                        "tone": "info",
                        "description": "Режим экономики изменён: global → local.",
                        "actor": "77",
                        "actor_label": "Пользователь 77",
                        "target": "—",
                        "target_label": "Без цели",
                        "has_target": False,
                    }
                ],
            }
        ],
    )
    sys.stdout.write(html)


if __name__ == "__main__":
    main()
