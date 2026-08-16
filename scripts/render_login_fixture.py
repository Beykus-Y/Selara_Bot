from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from selara.web.rendering import create_template_environment  # noqa: E402


def render(*, error: str | None = None, flash: str | None = None) -> str:
    environment = create_template_environment(
        template_dir=ROOT / "src" / "selara" / "web" / "templates"
    )
    return environment.get_template("login.html").render(
        page_title="Selara login fixture",
        page_name="login",
        top_links=[{"href": "/", "label": "Главная", "variant": "ghost"}],
        show_logout=False,
        flash=flash,
        error=error,
        home_href="/",
        brand_subtitle="бот для Telegram-групп",
        bot_username="selara_test_bot",
        bot_dm_url="https://t.me/selara_test_bot",
        body_classes="ui-login",
        navigation_label="Навигация страницы входа",
        extra_scripts=["login-form.js"],
    )


def main() -> None:
    sys.stdout.write(render())


if __name__ == "__main__":
    main()
