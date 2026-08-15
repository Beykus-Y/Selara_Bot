from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from selara.web.admin_docs import build_admin_docs_context  # noqa: E402
from selara.web.rendering import create_template_environment  # noqa: E402


def render() -> str:
    environment = create_template_environment(
        template_dir=ROOT / "src" / "selara" / "web" / "templates"
    )
    context = build_admin_docs_context(chat=None)
    return environment.get_template("admin_docs.html").render(
        top_links=[{"href": "/app", "label": "Кабинет", "variant": "ghost"}],
        show_logout=False,
        flash=None,
        error=None,
        body_classes="",
        navigation_label="Навигация документации",
        **context,
    )


def main() -> None:
    sys.stdout.write(render())


if __name__ == "__main__":
    main()
