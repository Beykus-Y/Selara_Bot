from __future__ import annotations

from pathlib import Path

from jinja2 import StrictUndefined

from selara.web.rendering import create_template_environment

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = ROOT / "src" / "selara" / "web" / "templates"
PANEL_CSS = ROOT / "src" / "selara" / "web" / "static" / "panel.css"
FOUNDATION_CSS = ROOT / "src" / "selara" / "web" / "static" / "server-ui-foundation.css"

EXPECTED_TEMPLATES = {
    "_chat_achievements_tab.html",
    "_chat_audit_and_aliases.html",
    "_chat_overview_tab.html",
    "_chat_settings_panel.html",
    "_chat_triggers_panel.html",
    "_games_dashboard.html",
    "achievements.html",
    "admin.html",
    "admin_broadcast_detail.html",
    "admin_docs.html",
    "admin_edit.html",
    "admin_login.html",
    "admin_messages_compact.html",
    "admin_table.html",
    "audit.html",
    "base.html",
    "chat.html",
    "economy.html",
    "error.html",
    "family.html",
    "feedback.html",
    "games.html",
    "home.html",
    "landing.html",
    "login.html",
    "user_docs.html",
}

INLINE_SCRIPT_TEMPLATES = {
    "games.html",
}


def _template_sources() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(TEMPLATE_DIR.glob("*.html"))
    }


def test_server_ui_template_inventory_is_explicit_and_compiles() -> None:
    sources = _template_sources()
    assert set(sources) == EXPECTED_TEMPLATES

    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    for template_name in sorted(EXPECTED_TEMPLATES):
        environment.get_template(template_name)


def test_full_page_templates_share_the_base_layout() -> None:
    sources = _template_sources()
    standalone_templates = {
        "base.html",
        "_games_dashboard.html",
        "_chat_achievements_tab.html",
        "_chat_audit_and_aliases.html",
        "_chat_overview_tab.html",
        "_chat_settings_panel.html",
        "_chat_triggers_panel.html",
    }

    for template_name, source in sources.items():
        if template_name in standalone_templates:
            continue
        assert source.lstrip().startswith('{% extends "base.html" %}'), template_name


def test_server_ui_does_not_increase_inline_debt() -> None:
    sources = _template_sources()
    inline_style_count = sum(source.count("style=") for source in sources.values())
    script_templates = {
        template_name
        for template_name, source in sources.items()
        if any(
            "<script" in line and "src=" not in line and 'type="application/json"' not in line
            for line in source.splitlines()
        )
    }
    inline_script_lines = sum(
        _count_inline_script_lines(source)
        for source in sources.values()
    )
    panel_css_lines = len(PANEL_CSS.read_text(encoding="utf-8").splitlines())

    assert inline_style_count <= 38
    assert script_templates == INLINE_SCRIPT_TEMPLATES
    assert inline_script_lines <= 1942
    assert panel_css_lines <= 6270


def test_admin_broadcast_uses_packaged_external_javascript() -> None:
    admin_source = (TEMPLATE_DIR / "admin.html").read_text(encoding="utf-8")
    base_source = (TEMPLATE_DIR / "base.html").read_text(encoding="utf-8")
    pyproject_source = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    script_path = ROOT / "src" / "selara" / "web" / "static" / "admin-broadcast.js"

    assert "<script" not in admin_source
    assert "extra_scripts" in base_source
    assert "admin-broadcast.js" in script_path.name
    assert script_path.is_file()
    assert len(script_path.read_text(encoding="utf-8").splitlines()) <= 460
    assert len(
        (ROOT / "src" / "selara" / "web" / "static" / "admin-broadcast.css")
        .read_text(encoding="utf-8")
        .splitlines()
    ) <= 580
    assert "static/*.js" in pyproject_source


def test_admin_archive_uses_page_specific_stylesheet() -> None:
    archive_source = (TEMPLATE_DIR / "admin_messages_compact.html").read_text(encoding="utf-8")
    archive_css = ROOT / "src" / "selara" / "web" / "static" / "admin-archive.css"
    archive_script = ROOT / "src" / "selara" / "web" / "static" / "admin-archive.js"

    assert "admin-data-table" not in archive_source
    assert "archive-layout" in archive_source
    assert archive_css.is_file()
    assert len(archive_css.read_text(encoding="utf-8").splitlines()) <= 920
    assert archive_script.is_file()
    assert len(archive_script.read_text(encoding="utf-8").splitlines()) <= 140


def test_admin_overview_uses_bounded_page_specific_assets() -> None:
    admin_source = (TEMPLATE_DIR / "admin.html").read_text(encoding="utf-8")
    overview_css = ROOT / "src" / "selara" / "web" / "static" / "admin-overview.css"
    overview_script = ROOT / "src" / "selara" / "web" / "static" / "admin-overview.js"

    table_search_script = ROOT / "src" / "selara" / "web" / "static" / "admin-table-search.js"
    table_search_css = ROOT / "src" / "selara" / "web" / "static" / "admin-table-search.css"

    assert "data-admin-overview" in admin_source
    assert overview_css.is_file()
    assert len(overview_css.read_text(encoding="utf-8").splitlines()) <= 520
    assert overview_script.is_file()
    assert len(overview_script.read_text(encoding="utf-8").splitlines()) <= 100
    assert table_search_script.is_file()
    assert len(table_search_script.read_text(encoding="utf-8").splitlines()) <= 60
    assert table_search_css.is_file()
    assert len(table_search_css.read_text(encoding="utf-8").splitlines()) <= 60


def test_user_docs_uses_bounded_copy_and_deep_link_assets() -> None:
    docs_source = (TEMPLATE_DIR / "user_docs.html").read_text(encoding="utf-8")
    docs_actions_css = ROOT / "src" / "selara" / "web" / "static" / "docs-item-actions.css"
    docs_actions_script = ROOT / "src" / "selara" / "web" / "static" / "docs-item-actions.js"

    assert "docs-clip-button" in docs_source
    assert "docs-item-anchor" in docs_source
    assert docs_actions_css.is_file()
    assert len(docs_actions_css.read_text(encoding="utf-8").splitlines()) <= 60
    assert docs_actions_script.is_file()
    assert len(docs_actions_script.read_text(encoding="utf-8").splitlines()) <= 60


def test_user_docs_uses_bounded_search_assets() -> None:
    docs_source = (TEMPLATE_DIR / "user_docs.html").read_text(encoding="utf-8")
    docs_search_css = ROOT / "src" / "selara" / "web" / "static" / "docs-search.css"
    docs_search_script = ROOT / "src" / "selara" / "web" / "static" / "docs-search.js"

    assert "data-docs-search-input" in docs_source
    assert "data-docs-search-card" in docs_source
    assert docs_search_css.is_file()
    assert len(docs_search_css.read_text(encoding="utf-8").splitlines()) <= 40
    assert docs_search_script.is_file()
    assert len(docs_search_script.read_text(encoding="utf-8").splitlines()) <= 60


def test_admin_docs_reuses_the_shared_search_assets() -> None:
    docs_source = (TEMPLATE_DIR / "admin_docs.html").read_text(encoding="utf-8")
    assert "data-docs-search-input" in docs_source
    assert "data-docs-search-card" in docs_source


def test_docs_pages_have_a_mobile_responsive_layout() -> None:
    docs_responsive_css = ROOT / "src" / "selara" / "web" / "static" / "docs-responsive.css"
    assert docs_responsive_css.is_file()
    assert len(docs_responsive_css.read_text(encoding="utf-8").splitlines()) <= 55

    for template_name in ("user_docs.html", "admin_docs.html"):
        docs_source = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
        assert 'class="docs-nav-toggle"' in docs_source


def test_admin_feedback_and_slice_workflow_have_bounded_contracts() -> None:
    feedback_css = ROOT / "src" / "selara" / "web" / "static" / "admin-feedback.css"
    feedback_script = ROOT / "src" / "selara" / "web" / "static" / "admin-feedback.js"
    workflow = ROOT / "docs" / "WEB_UI_SLICE_WORKFLOW.md"

    assert feedback_css.is_file()
    assert len(feedback_css.read_text(encoding="utf-8").splitlines()) <= 420
    assert feedback_script.is_file()
    assert len(feedback_script.read_text(encoding="utf-8").splitlines()) <= 80
    workflow_source = workflow.read_text(encoding="utf-8")
    assert "/tmp/<имя_среза>_screens/" in workflow_source
    assert "/home/beykus/selara_screen/" in workflow_source
    assert "вручную посмотреть" in workflow_source


def test_chat_audit_uses_bounded_page_specific_styles_without_raw_metadata() -> None:
    audit_source = (TEMPLATE_DIR / "audit.html").read_text(encoding="utf-8")
    audit_css = ROOT / "src" / "selara" / "web" / "static" / "audit.css"

    assert "data-audit-page" in audit_source
    assert "meta_json" not in audit_source
    assert audit_css.is_file()
    assert len(audit_css.read_text(encoding="utf-8").splitlines()) <= 620


def test_admin_broadcast_detail_uses_page_specific_stylesheet_without_inline_styles() -> None:
    detail_source = (TEMPLATE_DIR / "admin_broadcast_detail.html").read_text(
        encoding="utf-8"
    )
    detail_css = (
        ROOT / "src" / "selara" / "web" / "static" / "admin-broadcast-detail.css"
    )

    assert "style=" not in detail_source
    assert "broadcast-detail-page" in detail_source
    assert detail_css.is_file()
    assert len(detail_css.read_text(encoding="utf-8").splitlines()) <= 700


def _count_inline_script_lines(source: str) -> int:
    inside_script = False
    count = 0
    for line in source.splitlines():
        if "<script" in line and "src=" not in line and 'type="application/json"' not in line:
            inside_script = True
        if inside_script:
            count += 1
        if "</script>" in line:
            inside_script = False
    return count


def test_base_layout_has_required_semantics() -> None:
    source = (TEMPLATE_DIR / "base.html").read_text(encoding="utf-8")

    assert '<html lang="ru">' in source
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in source
    assert "<title>{{ page_title }}</title>" in source
    assert "server-ui-foundation.css" in source
    assert 'class="skip-link" href="#main-content"' in source
    assert 'id="main-content"' in source
    assert 'tabindex="-1"' in source
    assert '<nav class="top-actions"' in source
    assert "aria-label=" in source
    assert "aria-current" in source
    assert 'aria-live="polite"' in source


def test_foundation_styles_cover_focus_motion_and_admin_mobile_navigation() -> None:
    source = FOUNDATION_CSS.read_text(encoding="utf-8")

    assert ":focus-visible" in source
    assert ".skip-link" in source
    assert "prefers-reduced-motion: reduce" in source
    assert ".ui-admin-shell .top-actions" in source
    assert "overflow-x: auto" in source
    assert len(source.splitlines()) <= 240


def test_admin_login_renders_with_strict_undefined() -> None:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    environment.undefined = StrictUndefined

    html = environment.get_template("admin_login.html").render(
        page_title="Selara test admin login",
        page_name="admin_login",
        top_links=[],
        show_logout=False,
        flash=None,
        error=None,
    )

    assert "Вход для администратора" in html
    assert 'action="/app/admin/login"' in html
    assert 'autocomplete="current-password"' in html


def test_production_miniapp_router_does_not_mount_dormant_admin_pages() -> None:
    router_source = (
        ROOT / "frontend" / "src" / "app" / "router" / "AppRouter.tsx"
    ).read_text(encoding="utf-8")

    assert "@/pages/admin" not in router_source
    assert "@/pages/admin-table" not in router_source
    assert "@/pages/admin-login" not in router_source
    assert "@/pages/admin-broadcast" not in router_source
    assert "@/pages/docs" not in router_source
