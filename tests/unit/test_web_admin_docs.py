from selara.core.chat_settings import CHAT_SETTINGS_KEYS
from selara.web.admin_docs import (
    build_admin_docs_context,
    build_settings_docs_sections,
    trigger_match_type_label_ru,
)


def test_trigger_match_type_label_is_localized() -> None:
    assert trigger_match_type_label_ru("exact") == "Точное совпадение"
    assert trigger_match_type_label_ru("contains") == "Содержит фразу"
    assert trigger_match_type_label_ru("starts_with") == "Начинается с"


def test_settings_docs_cover_all_known_keys_once() -> None:
    sections = build_settings_docs_sections()
    seen_keys: list[str] = []
    seen_anchors: list[str] = []

    for section in sections:
        for item in section["items"]:
            seen_keys.append(item["key"])
            seen_anchors.append(item["anchor"])

    assert sorted(seen_keys) == sorted(CHAT_SETTINGS_KEYS)
    assert len(seen_anchors) == len(set(seen_anchors))


def test_admin_docs_context_includes_trigger_template_variables() -> None:
    context = build_admin_docs_context(chat=None)
    groups = context["trigger_template_variable_groups"]

    flattened = {
        item["token"]
        for group in groups
        for item in group["items"]
    }

    assert "{user}" in flattened
    assert "{reply_user}" in flattened
    assert "{args}" in flattened
