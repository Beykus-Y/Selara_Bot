from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Unlike render_admin_docs_fixture.py, build_user_docs_context() has no
# transitive dependency on selara.core.config (pydantic-settings) — verified
# against a Jinja2-only venv matching the CI "frontend" job's install step —
# so it's safe to call directly here instead of hand-building the context.
from selara.web.rendering import create_template_environment  # noqa: E402
from selara.web.user_docs import build_user_docs_context  # noqa: E402


def render() -> str:
    environment = create_template_environment(
        template_dir=ROOT / "src" / "selara" / "web" / "templates"
    )
    context = build_user_docs_context(chat=None)
    return environment.get_template("user_docs.html").render(
        top_links=[{"href": "/app", "label": "Кабинет", "variant": "ghost"}],
        show_logout=False,
        flash=None,
        error=None,
        body_classes="",
        navigation_label="Навигация документации",
        extra_styles=["docs-item-actions.css", "docs-search.css"],
        extra_scripts=["docs-item-actions.js", "docs-search.js"],
        **context,
    )


def main() -> None:
    sys.stdout.write(render())


if __name__ == "__main__":
    main()
