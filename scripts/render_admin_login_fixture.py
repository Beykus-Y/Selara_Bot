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
    return environment.get_template("admin_login.html").render(
        page_title="Selara admin login fixture",
        page_name="admin_login",
        top_links=[{"href": "/", "label": "На главную", "variant": "ghost"}],
        show_logout=False,
        flash=flash,
        error=error,
        body_classes="ui-admin-login",
        navigation_label="Навигация страницы входа",
        extra_scripts=["admin-login.js"],
    )


def main() -> None:
    sys.stdout.write(render())


if __name__ == "__main__":
    main()
