from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from selara.web.rendering import create_template_environment

TEMPLATE_DIR = ROOT / "src" / "selara" / "web" / "templates"


def _render_macro(source: str, **context: object) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    template = environment.from_string(
        '{% from "_macros.html" import breadcrumb, status_badge, pagination %}\n' + source
    )
    return template.render(**context)


def test_breadcrumb_renders_a_single_item_with_no_separator() -> None:
    html = _render_macro(
        "{{ breadcrumb(items) }}",
        items=[{"label": "Админка"}],
    )
    assert 'aria-label="Хлебные крошки"' in html
    assert "breadcrumb-sep" not in html
    assert "<span>Админка</span>" in html


def test_breadcrumb_renders_linked_ancestors_and_an_unlinked_current_page() -> None:
    html = _render_macro(
        "{{ breadcrumb(items) }}",
        items=[
            {"href": "/app/admin", "label": "Админка"},
            {"href": "/app/admin/table/users", "label": "users"},
            {"label": "Редактирование"},
        ],
    )
    assert html.count("breadcrumb-sep") == 2
    assert html.count('aria-hidden="true"') == 2
    assert '<a href="/app/admin">Админка</a>' in html
    assert '<a href="/app/admin/table/users">users</a>' in html
    assert "<span>Редактирование</span>" in html
    assert '<a href="' not in html.split("Редактирование")[-1]


def test_status_badge_composes_base_and_toned_classes() -> None:
    for tone, label in (("open", "Открыта"), ("done", "Закрыта")):
        html = _render_macro(
            "{{ status_badge('feedback-status', tone, label) }}",
            tone=tone,
            label=label,
        )
        assert f'class="feedback-status feedback-status-{tone}"' in html
        assert f">{label}<" in html


def test_pagination_hides_previous_link_on_the_first_page() -> None:
    html = _render_macro(
        "{{ pagination(previous_href, next_href, page, total_pages) }}",
        previous_href=None,
        next_href="/app/admin/table/users?page=2",
        page=1,
        total_pages=3,
    )
    assert "← Назад" not in html
    assert 'href="/app/admin/table/users?page=2"' in html
    assert "Страница 1 из 3" in html


def test_pagination_hides_next_link_on_the_last_page() -> None:
    html = _render_macro(
        "{{ pagination(previous_href, next_href, page, total_pages) }}",
        previous_href="/app/admin/table/users?page=2",
        next_href=None,
        page=3,
        total_pages=3,
    )
    assert "Вперёд →" not in html
    assert 'href="/app/admin/table/users?page=2"' in html
    assert "Страница 3 из 3" in html


def test_pagination_shows_both_links_on_a_middle_page() -> None:
    html = _render_macro(
        "{{ pagination(previous_href, next_href, page, total_pages) }}",
        previous_href="/app/admin/table/users?page=1",
        next_href="/app/admin/table/users?page=3",
        page=2,
        total_pages=3,
    )
    assert "← Назад" in html
    assert "Вперёд →" in html
    assert "Страница 2 из 3" in html
