from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Deliberately does NOT import selara.web.admin_docs / selara.core.* here:
# the CI "frontend" job only installs Jinja2 for this fixture family (see
# .github/workflows/ci.yml, "Install server UI fixture renderer"), not the
# full backend dependency chain (pydantic-settings etc.) that admin_docs.py
# pulls in transitively. Context is hand-built instead, matching the other
# render_*_fixture.py scripts in this directory.
from selara.web.rendering import create_template_environment  # noqa: E402


def render() -> str:
    environment = create_template_environment(
        template_dir=ROOT / "src" / "selara" / "web" / "templates"
    )
    return environment.get_template("admin_docs.html").render(
        page_title="Selara • Документация администратора",
        page_name="admin-docs",
        top_links=[{"href": "/app", "label": "Кабинет", "variant": "ghost"}],
        show_logout=False,
        flash=None,
        error=None,
        body_classes="",
        navigation_label="Навигация документации",
        hero_title="Документация администратора",
        hero_subtitle="Полная справка по веб-панели: от входа и ролей до настроек, алиасов, смарт-триггеров, аудита и игровых разделов.",
        origin_chat={"href": "/app", "label": "кабинет"},
        extra_styles=["docs-search.css"],
        extra_scripts=["docs-search.js"],
        docs_sections=[
            {
                "anchor": "docs-roles",
                "title": "Роли и ранги команд",
                "summary": "Управление тем, кто и что может делать внутри группы.",
                "items": [
                    {
                        "title": "Роли бота",
                        "text": "Раздел ролей показывает все доступные роли, их ранг и набор прав.",
                        "examples": ("❌ Пример ошибки", "✅ Пример исправления"),
                        "notes": ("Ограничение Telegram: пример заметки.",),
                        "search_text": "роли бота раздел ролей показывает все доступные роли, их ранг и набор прав.",
                    },
                ],
            },
        ],
        trigger_match_types=[
            {"code": "exact", "label": "Точное совпадение", "description": "Полное совпадение сообщения."},
        ],
        trigger_template_variable_groups=[
            {
                "title": "Отправитель",
                "items": [
                    {
                        "token": "{user}",
                        "label": "Отправитель упоминанием",
                        "description": "HTML-упоминание автора.",
                        "availability": "смарт-триггеры и RP",
                        "aliases": "{actor}, {sender}",
                    }
                ],
            }
        ],
        settings_docs_sections=[
            {
                "anchor": "settings-group-1",
                "title": "Общие",
                "items": [
                    {
                        "anchor": "setting-example_key",
                        "key": "example_key",
                        "title": "Пример настройки",
                        "description": "Описание примера настройки для фикстуры.",
                        "value_hint": "true/false",
                    }
                ],
            }
        ],
        # Mirrors the real SYSTEM_ROLE_TEMPLATES shape from selara.core.roles
        # (kept in sync by hand — this fixture only needs to exercise the
        # template's rendering path for HTMLHint, not be a live data source).
        roles_docs=[
            {"anchor": "role-owner", "code": "owner", "title": "Владелец", "rank": 40, "permissions": [
                "управление ролями", "управление настройками", "управление играми",
                "модерация пользователей", "объявления", "доступ к командам", "шаблоны и кастомные роли",
            ]},
            {"anchor": "role-co_owner", "code": "co_owner", "title": "Совладелец", "rank": 30, "permissions": [
                "управление ролями", "управление настройками", "управление играми",
                "модерация пользователей", "объявления", "доступ к командам", "шаблоны и кастомные роли",
            ]},
            {"anchor": "role-senior_admin", "code": "senior_admin", "title": "Старший админ", "rank": 20, "permissions": [
                "управление играми", "модерация пользователей", "объявления",
            ]},
            {"anchor": "role-junior_admin", "code": "junior_admin", "title": "Мл. админ", "rank": 10, "permissions": [
                "объявления",
            ]},
            {"anchor": "role-participant", "code": "participant", "title": "Участник", "rank": 0, "permissions": None},
        ],
    )


def main() -> None:
    sys.stdout.write(render())


if __name__ == "__main__":
    main()
