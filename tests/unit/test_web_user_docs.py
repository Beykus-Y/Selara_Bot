from pathlib import Path
from typing import Any

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
