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
        '{% from "_macros.html" import breadcrumb, status_badge, pagination, tabs, confirm_dialog %}\n'
        + source
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


def test_tabs_marks_exactly_one_tab_active_and_uses_the_given_data_attr() -> None:
    html = _render_macro(
        "{{ tabs(items, aria_label, data_attr) }}",
        items=[
            {"value": "mix", "label": "Гибрид", "active": True},
            {"value": "activity", "label": "Сообщения", "active": False},
        ],
        aria_label="Режим рейтинга",
        data_attr="lb-mode",
    )
    assert 'role="tablist"' in html
    assert 'aria-label="Режим рейтинга"' in html
    assert html.count('class="is-active"') == 1
    assert 'aria-selected="true"' in html
    assert 'aria-selected="false"' in html
    assert 'data-lb-mode="mix"' in html
    assert 'data-lb-mode="activity"' in html


def test_confirm_dialog_wraps_the_target_placeholder_in_the_question() -> None:
    html = _render_macro(
        "{{ confirm_dialog(title, prefix, suffix, cancel_label, confirm_label) }}",
        title="Подтверждение удаления",
        prefix="Вы уверены, что хотите удалить запись",
        suffix="? Это действие нельзя отменить.",
        cancel_label="Отмена",
        confirm_label="Удалить",
    )
    assert 'id="delete-dialog"' in html
    assert 'id="delete-form"' in html
    assert 'id="delete-hidden-fields"' in html
    assert "<strong data-delete-target></strong>" in html
    assert "Вы уверены, что хотите удалить запись <strong data-delete-target></strong>? Это действие нельзя отменить." in html
