from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Deliberately does NOT import selara.web.user_docs here: it now pulls in
# selara.presentation.handlers.settings_common (for the "<setting> по
# настройке" availability badges), which transitively imports
# selara.core.config (pydantic-settings) via chat_settings.py — verified
# against a Jinja2-only venv matching the CI "frontend" job's install step.
# Context is hand-built instead, matching render_admin_docs_fixture.py and
# the other render_*_fixture.py scripts in this directory.
from selara.web.rendering import create_template_environment  # noqa: E402


def render() -> str:
    environment = create_template_environment(
        template_dir=ROOT / "src" / "selara" / "web" / "templates"
    )
    return environment.get_template("user_docs.html").render(
        page_title="Selara • Документация пользователя",
        page_name="user-docs",
        top_links=[{"href": "/app", "label": "Кабинет", "variant": "ghost"}],
        show_logout=False,
        flash=None,
        error=None,
        body_classes="",
        navigation_label="Навигация документации",
        extra_styles=["docs-item-actions.css", "docs-search.css", "docs-responsive.css"],
        extra_scripts=["docs-item-actions.js", "docs-search.js"],
        hero_title="Документация пользователя",
        hero_subtitle="Полная памятка по пользовательским возможностям бота.",
        hero_chips=("команды", "reply-действия", "игры + экономика"),
        origin_chat={"href": "/app", "label": "кабинет"},
        docs_sections=[
            {
                "anchor": "user-docs-economy",
                "title": "Экономика и предметы",
                "summary": "Баланс, ферма, рынок и рост.",
                "items": [
                    {
                        "anchor": "user-docs-economy-item-1",
                        "title": "Панель экономики",
                        "text": "Экономический профиль открывается через /eco.",
                        "badges": ("группа", "ЛС", "Экономика по настройке"),
                        "commands": ("/eco", "/tap"),
                        "triggers": ("баланс", "тап"),
                        "examples": ("/eco global",),
                        "steps": ("Откройте /eco.",),
                        "notes": ("Пример примечания.",),
                    }
                ],
            }
        ],
    )


def main() -> None:
    sys.stdout.write(render())


if __name__ == "__main__":
    main()
