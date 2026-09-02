from __future__ import annotations

from selara.application.daily_summary.participants import ChatMemberInfo
from selara.application.daily_summary.sanitize import (
    build_alias_index,
    build_author_display_tokens,
    redact_known_aliases,
    redact_text_mentions,
)


def test_active_member_token_matches_participant_directory() -> None:
    members = [
        ChatMemberInfo(user_id=1, is_active_member=True, display_name="Vasya", persona_label="Кот"),
    ]

    tokens = build_author_display_tokens(members, persona_enabled=True)

    assert tokens[1] == "Кот"


def test_inactive_member_gets_stable_anonymous_token_not_their_name() -> None:
    members = [
        ChatMemberInfo(user_id=1, is_active_member=True, display_name="Vasya"),
        ChatMemberInfo(user_id=2, is_active_member=False, display_name="Petya", persona_label="Лис"),
    ]

    tokens = build_author_display_tokens(members, persona_enabled=True)

    assert tokens[1] == "Vasya"
    assert tokens[2] not in ("Petya", "Лис")
    assert tokens[2].startswith("Участник #")


def test_anonymous_token_is_stable_across_two_calls_for_the_same_run_input() -> None:
    members = [
        ChatMemberInfo(user_id=5, is_active_member=False, display_name="Petya"),
        ChatMemberInfo(user_id=9, is_active_member=False, display_name="Kolya"),
    ]

    first = build_author_display_tokens(members, persona_enabled=True)
    second = build_author_display_tokens(members, persona_enabled=True)

    assert first == second
    # distinct departed users never collide on the same token
    assert first[5] != first[9]


def test_alias_index_only_covers_inactive_members() -> None:
    members = [
        ChatMemberInfo(user_id=1, is_active_member=True, username="vasya", display_name="Vasya"),
        ChatMemberInfo(user_id=2, is_active_member=False, username="petya", display_name="Petya", persona_label="Лис"),
    ]

    index = build_alias_index(members)

    assert "vasya" not in index
    assert index["petya"] == 2
    assert index["лис"] == 2


def test_redact_known_aliases_replaces_username_and_persona_mentions() -> None:
    members = [
        ChatMemberInfo(user_id=2, is_active_member=False, username="petya", display_name="Petya", persona_label="Лис"),
    ]
    index = build_alias_index(members)
    tokens = {2: "Участник #1"}

    text = "А Лис вчера опять сервер сломал, да и @petya такой же"
    redacted = redact_known_aliases(text, alias_index=index, tokens=tokens)

    assert "Лис" not in redacted
    assert "@petya" not in redacted
    assert redacted.count("Участник #1") == 2


def test_redact_known_aliases_does_not_touch_unrelated_words() -> None:
    members = [
        ChatMemberInfo(user_id=2, is_active_member=False, username="vas", display_name="Vas"),
    ]
    index = build_alias_index(members)
    tokens = {2: "Участник #1"}

    # "vas" is a substring of "vasya" but must not match as a whole-word alias
    text = "Vasya пришёл в чат"
    redacted = redact_known_aliases(text, alias_index=index, tokens=tokens)

    assert redacted == text


def test_redact_text_mentions_replaces_exact_span_by_offset() -> None:
    tokens = {2: "Участник #1"}
    text = "Спроси у Пети про это"
    # "Пети" starts at offset 9, length 4 (utf-16 code units == len() here, ascii-safe test)
    entities = [(9, 4, 2)]

    redacted = redact_text_mentions(text, entities=entities, tokens=tokens)

    assert redacted == "Спроси у Участник #1 про это"


def test_redact_text_mentions_ignores_entities_for_unknown_users() -> None:
    tokens = {2: "Участник #1"}
    text = "Спроси у Коли про это"
    entities = [(9, 4, 999)]  # user 999 has no token -> not a departed member we track

    redacted = redact_text_mentions(text, entities=entities, tokens=tokens)

    assert redacted == text
