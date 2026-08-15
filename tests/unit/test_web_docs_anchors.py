"""Automated anchor-uniqueness and internal-link validity checks for the
documentation pages (docs/WEB_UI_MODERNIZATION_TODO.md, stage 3 "Модель
контента": "Добавить автоматическую проверку уникальности anchors и
внутренних ссылок.").

A duplicate id makes `#anchor` navigation land on the wrong element (browsers
scroll to the *first* match), and a dangling internal link silently does
nothing when clicked — neither is visible from just reading the template, so
this is enforced structurally against the real rendered output instead.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.web.admin_docs import build_admin_docs_context
from selara.web.presenters import build_settings_sections
from selara.web.rendering import create_template_environment
from selara.web.user_docs import build_user_docs_context

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
_INTERNAL_HREF_RE = re.compile(r"^#([\w-]+)$")


class _AnchorCollector(HTMLParser):
    """Collects every element `id` and every same-page `href="#..."` target."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.internal_hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        element_id = attr_map.get("id")
        if element_id:
            self.ids.append(element_id)
        href = attr_map.get("href")
        if href:
            match = _INTERNAL_HREF_RE.match(href)
            if match:
                self.internal_hrefs.append(match.group(1))


def _render(template_name: str, context: dict[str, object]) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    return environment.get_template(template_name).render(
        top_links=[],
        show_logout=False,
        flash=None,
        error=None,
        body_classes="",
        navigation_label="Навигация документации",
        **context,
    )


def _assert_anchors_are_sound(html: str, *, page_name: str) -> None:
    collector = _AnchorCollector()
    collector.feed(html)

    seen: set[str] = set()
    duplicates = set()
    for element_id in collector.ids:
        if element_id in seen:
            duplicates.add(element_id)
        seen.add(element_id)
    assert not duplicates, f"{page_name}: duplicate id attributes: {sorted(duplicates)}"

    dangling = {target for target in collector.internal_hrefs if target not in seen}
    assert not dangling, f"{page_name}: internal links point to missing anchors: {sorted(dangling)}"

    # A page with internal links but zero collected ids would trivially pass
    # the checks above without proving anything — guard against that.
    assert collector.ids, f"{page_name}: expected at least one id attribute in the rendered page"
    assert collector.internal_hrefs, f"{page_name}: expected at least one internal #-link in the rendered page"


def test_admin_docs_anchors_are_unique_and_internal_links_resolve() -> None:
    html = _render("admin_docs.html", build_admin_docs_context(chat=None))
    _assert_anchors_are_sound(html, page_name="admin_docs.html")


def test_user_docs_anchors_are_unique_and_internal_links_resolve() -> None:
    html = _render("user_docs.html", build_user_docs_context(chat=None))
    _assert_anchors_are_sound(html, page_name="user_docs.html")


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
            "BOT_USERNAME": "selara_test_bot",
        }
    )


def test_setting_field_doc_links_resolve_on_the_admin_docs_page() -> None:
    # Regression guard for docs/WEB_UI_MODERNIZATION_TODO.md stage 3
    # "Администраторская документация": "Ссылки из полей админки ведут сразу
    # к релевантному anchor." chat.html's setting cards link to
    # "{admin_docs_url}#{item.doc_anchor}" (build_settings_sections in
    # presenters.py); this proves every doc_anchor it generates is a real id
    # on the rendered admin_docs.html page, not just that both sides happen
    # to call the same setting_anchor() function.
    defaults = default_chat_settings(_settings())
    sections = build_settings_sections(current=defaults, defaults=defaults, editable=True)
    doc_anchors = {item["doc_anchor"] for section in sections for item in section["items"]}
    assert doc_anchors

    admin_docs_html = _render("admin_docs.html", build_admin_docs_context(chat=None))
    collector = _AnchorCollector()
    collector.feed(admin_docs_html)
    real_ids = set(collector.ids)

    missing = doc_anchors - real_ids
    assert not missing, f"setting doc_anchor targets missing from admin_docs.html: {sorted(missing)}"
