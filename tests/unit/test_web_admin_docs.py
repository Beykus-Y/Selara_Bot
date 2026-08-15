from selara.core.chat_settings import CHAT_SETTINGS_KEYS
from selara.core.roles import SYSTEM_ROLE_TEMPLATES
from selara.web.admin_docs import (
    build_admin_docs_context,
    build_roles_docs,
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


def test_roles_docs_cover_every_system_role_exactly_once_ranked_high_to_low() -> None:
    roles = build_roles_docs()
    seen_codes = [role["code"] for role in roles]
    seen_anchors = [role["anchor"] for role in roles]

    assert sorted(seen_codes) == sorted(template.role_code for template in SYSTEM_ROLE_TEMPLATES)
    assert len(seen_anchors) == len(set(seen_anchors))
    ranks = [role["rank"] for role in roles]
    assert ranks == sorted(ranks, reverse=True)


def test_roles_docs_include_co_owner_with_full_permissions() -> None:
    # Regression guard: docs/bot_docs/roles.md previously omitted co_owner
    # entirely, and admin_docs.py's roles section never listed real role data
    # at all — this now comes straight from core.roles, so it can't drift.
    roles = {role["code"]: role for role in build_roles_docs()}

    assert "co_owner" in roles
    co_owner = roles["co_owner"]
    assert co_owner["title"] == "Совладелец"
    assert co_owner["rank"] == 30
    assert "управление ролями" in co_owner["permissions"]
    assert "шаблоны и кастомные роли" in co_owner["permissions"]


def test_roles_docs_participant_has_no_permissions_listed() -> None:
    roles = {role["code"]: role for role in build_roles_docs()}
    assert roles["participant"]["permissions"] is None


def test_admin_docs_context_includes_roles_docs() -> None:
    context = build_admin_docs_context(chat=None)
    codes = {role["code"] for role in context["roles_docs"]}
    assert codes == {template.role_code for template in SYSTEM_ROLE_TEMPLATES}
