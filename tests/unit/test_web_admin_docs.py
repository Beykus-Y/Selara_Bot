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


def test_admin_docs_covers_broadcasts_and_maintenance() -> None:
    # Regression guard for docs/WEB_UI_MODERNIZATION_TODO.md stage 3
    # "Администраторская документация": "Настройки чата, роли, permissions,
    # aliases, triggers, рассылки и обслуживание." — рассылки и обслуживание
    # были единственными двумя темами из этого списка, для которых вообще не
    # было ни одной секции в admin_docs.py.
    context = build_admin_docs_context(chat=None)
    titles = {section["title"] for section in context["docs_sections"]}
    assert "Рассылки" in titles
    assert "Обслуживание" in titles


def test_admin_docs_items_carry_a_lowercased_search_text_covering_their_fields() -> None:
    context = build_admin_docs_context(chat=None)
    for section in context["docs_sections"]:
        for item in section["items"]:
            search_text = item["search_text"]
            assert search_text == search_text.lower()
            assert item["title"].lower() in search_text
            assert item["text"].lower() in search_text
            for example in item.get("examples", ()):
                assert example.lower() in search_text
            for note in item.get("notes", ()):
                assert note.lower() in search_text


def test_admin_docs_search_text_is_deterministic_and_holds_no_protected_data() -> None:
    # Regression guard for docs/WEB_UI_MODERNIZATION_TODO.md stage 3
    # "Качество документации": "Search index строится детерминированно и не
    # содержит защищённые данные." docs_sections is a static, hand-written
    # tuple with no request-specific input beyond the optional `chat` used
    # only for the unrelated "origin_chat" link, so two independent builds
    # must be byte-identical and never mention a real chat_id/title.
    first = build_admin_docs_context(chat=None)
    second = build_admin_docs_context(chat=None)
    assert first["docs_sections"] == second["docs_sections"]

    from selara.domain.entities import UserChatOverview

    chat = UserChatOverview(
        chat_id=-100123456789,
        chat_type="group",
        chat_title="SECRET-CHAT-TITLE",
        bot_role="owner",
        message_count=None,
        last_seen_at=None,
    )
    with_chat = build_admin_docs_context(chat=chat)
    assert with_chat["docs_sections"] == first["docs_sections"]
    for section in with_chat["docs_sections"]:
        for item in section["items"]:
            assert "secret-chat-title" not in item["search_text"]
            assert "-100123456789" not in item["search_text"]


def test_admin_docs_settings_workflow_includes_erroneous_examples() -> None:
    # Regression guard for docs/WEB_UI_MODERNIZATION_TODO.md stage 3
    # "Администраторская документация": "Примеры включают ошибочные варианты
    # и способы исправления." Errors quoted verbatim from
    # core.chat_settings.parse_chat_setting_value, not invented.
    context = build_admin_docs_context(chat=None)
    items = {
        item["title"]: item
        for section in context["docs_sections"]
        for item in section["items"]
    }
    examples = items["Подсказки по формату"]["examples"]
    assert any("Значение должно быть целым числом" in example for example in examples)
    assert any(example.startswith("❌") for example in examples)
    assert any(example.startswith("✅") for example in examples)


def test_admin_docs_broadcasts_and_maintenance_note_telegram_limits() -> None:
    # Regression guard for docs/WEB_UI_MODERNIZATION_TODO.md stage 3
    # "Администраторская документация": "Отображать ограничения Telegram
    # рядом с соответствующими возможностями." Facts verified against real
    # code: admin-broadcast.js's 10 MB client-side photo guard and
    # infrastructure/backup.py's BACKUP_CHUNK_SIZE_BYTES chunking.
    context = build_admin_docs_context(chat=None)
    items = {
        item["title"]: item
        for section in context["docs_sections"]
        for item in section["items"]
    }
    assert any("10 МБ" in note for note in items["Текст и медиа"]["notes"])
    assert any("50 МБ" in note for note in items["Ручной запрос backup"]["notes"])


def test_admin_docs_context_includes_roles_docs() -> None:
    context = build_admin_docs_context(chat=None)
    codes = {role["code"] for role in context["roles_docs"]}
    assert codes == {template.role_code for template in SYSTEM_ROLE_TEMPLATES}
