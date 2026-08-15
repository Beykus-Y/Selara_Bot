from pathlib import Path
from typing import Any

from selara.presentation.commands.catalog import build_social_action_docs
from selara.web.rendering import create_template_environment
from selara.web.user_docs import build_user_docs_context


def _flatten_payload(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten_payload(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_payload(item) for item in value)
    return str(value)


def test_user_docs_have_unique_section_anchors() -> None:
    context = build_user_docs_context(chat=None)
    anchors = [section["anchor"] for section in context["docs_sections"]]

    assert anchors
    assert len(anchors) == len(set(anchors))


def test_user_docs_include_origin_chat_when_available() -> None:
    class DummyChat:
        chat_id = -100123
        chat_title = "Test chat"

    context = build_user_docs_context(chat=DummyChat())

    assert context["origin_chat"] == {
        "href": "/app/chat/-100123",
        "label": "Test chat",
    }


def test_user_docs_cover_core_user_features() -> None:
    context = build_user_docs_context(chat=None)
    payload = _flatten_payload(context["docs_sections"])

    assert "/me" in payload
    assert "/game whoami" in payload
    assert "/growth do" in payload
    assert "/family @username" in payload
    assert "/article" in payload
    assert "соблазнить" in payload
    assert "кто сегодня легенда" in payload
    assert "18+ и пикантное" in payload


def test_user_docs_collection_fields_are_not_plain_strings() -> None:
    context = build_user_docs_context(chat=None)

    for section in context["docs_sections"]:
        for item in section["items"]:
            for field_name in ("badges", "commands", "triggers", "examples", "steps", "notes"):
                value = item.get(field_name)
                assert not isinstance(value, str), f"{item['title']}::{field_name} should be a collection, not a string"


def _find_item(context: dict[str, Any], *, title: str) -> dict[str, Any]:
    for section in context["docs_sections"]:
        for item in section["items"]:
            if item["title"] == title:
                return item
    raise AssertionError(f"docs item {title!r} not found")


def test_user_docs_rp_action_lists_match_canonical_catalog_source() -> None:
    # Regression guard for docs/WEB_UI_MODERNIZATION_TODO.md stage 3 "Модель
    # контента": the RP-action trigger lists used to be hand-typed and had
    # drifted (20 real actions were missing entirely, 4 more listed a
    # non-canonical alternate trigger). They must now be derived from
    # selara.presentation.commands.catalog.build_social_action_docs() so the
    # docs page cannot silently fall out of sync with the bot again.
    canonical = build_social_action_docs()
    canonical_non_18 = {action.trigger for action in canonical if not action.is_18_plus}
    canonical_18_plus = {action.trigger for action in canonical if action.is_18_plus}

    context = build_user_docs_context(chat=None)
    non_18_item = _find_item(context, title="Reply-действия без 18+")
    plus_18_item = _find_item(context, title="18+ reply-действия")

    assert set(non_18_item["triggers"]) == canonical_non_18
    assert set(plus_18_item["triggers"]) == canonical_18_plus
    assert len(non_18_item["triggers"]) == len(canonical_non_18)
    assert len(plus_18_item["triggers"]) == len(canonical_18_plus)
    assert not canonical_non_18 & canonical_18_plus


def test_user_docs_items_have_stable_unique_deep_link_anchors() -> None:
    context = build_user_docs_context(chat=None)
    all_anchors: list[str] = []
    for section in context["docs_sections"]:
        for index, item in enumerate(section["items"], start=1):
            expected = f"{section['anchor']}-item-{index}"
            assert item["anchor"] == expected
            all_anchors.append(item["anchor"])

    assert len(all_anchors) == len(set(all_anchors))


def test_user_docs_items_carry_a_lowercased_search_text_covering_their_fields() -> None:
    context = build_user_docs_context(chat=None)
    for section in context["docs_sections"]:
        for item in section["items"]:
            search_text = item["search_text"]
            assert search_text == search_text.lower()
            assert item["title"].lower() in search_text
            for trigger in item["triggers"]:
                assert trigger.lower() in search_text
            for example in item["examples"]:
                assert example.lower() in search_text


def test_user_docs_template_renders_search_box_and_text_attributes() -> None:
    template_dir = Path(__file__).resolve().parents[2] / "src" / "selara" / "web" / "templates"
    environment = create_template_environment(template_dir=template_dir)

    context = build_user_docs_context(chat=None)
    html = environment.get_template("user_docs.html").render(
        top_links=[],
        show_logout=False,
        flash=None,
        error=None,
        **context,
    )

    assert "data-docs-search-input" in html
    assert "data-docs-search-section" in html
    assert 'data-docs-search-text="' in html
    assert "трахнуть" in html.lower()


def test_user_docs_template_renders_copy_buttons_and_deep_links() -> None:
    template_dir = Path(__file__).resolve().parents[2] / "src" / "selara" / "web" / "templates"
    environment = create_template_environment(template_dir=template_dir)

    context = build_user_docs_context(chat=None)
    html = environment.get_template("user_docs.html").render(
        top_links=[],
        show_logout=False,
        flash=None,
        error=None,
        **context,
    )

    assert 'data-copy-text="reply + /pair"' in html
    assert 'class="docs-item-anchor"' in html
    assert "docs-clip-button" in html


def test_user_docs_template_renders_command_lists() -> None:
    template_dir = Path(__file__).resolve().parents[2] / "src" / "selara" / "web" / "templates"
    environment = create_template_environment(template_dir=template_dir)

    context = build_user_docs_context(chat=None)
    html = environment.get_template("user_docs.html").render(
        top_links=[],
        show_logout=False,
        flash=None,
        error=None,
        **context,
    )

    assert "Документация пользователя" in html
    assert "Пары и брак" in html
    assert "/pair @username" in html
    assert "reply + обнять" in html
    assert "кто сегодня легенда" in html
    assert "docs-card-label" in html
    assert ">группа<" in html
    assert ">г<" not in html
