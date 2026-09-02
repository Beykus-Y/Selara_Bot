from __future__ import annotations

from selara.application.daily_summary.participants import ChatMemberInfo, build_participant_directory


def test_active_member_uses_persona_when_enabled() -> None:
    members = [
        ChatMemberInfo(user_id=1, is_active_member=True, username="vasya", display_name="Vasya", persona_label="Кот"),
    ]

    directory = build_participant_directory(members, persona_enabled=True)

    assert directory == {1: "Кот"}


def test_active_member_falls_back_to_display_name_without_persona() -> None:
    members = [
        ChatMemberInfo(user_id=1, is_active_member=True, username="vasya", display_name="Vasya", persona_label="Кот"),
    ]

    directory = build_participant_directory(members, persona_enabled=False)

    assert directory == {1: "Vasya"}


def test_active_member_falls_back_to_username_without_display_name() -> None:
    members = [
        ChatMemberInfo(user_id=1, is_active_member=True, username="vasya", display_name=None, persona_label=None),
    ]

    directory = build_participant_directory(members, persona_enabled=True)

    assert directory == {1: "@vasya"}


def test_inactive_member_is_excluded_from_directory_entirely() -> None:
    # even though this user has a persona label, a departed member must never appear
    members = [
        ChatMemberInfo(user_id=1, is_active_member=True, username="vasya", display_name="Vasya", persona_label=None),
        ChatMemberInfo(user_id=2, is_active_member=False, username="petya", display_name="Petya", persona_label="Лис"),
    ]

    directory = build_participant_directory(members, persona_enabled=True)

    assert directory == {1: "Vasya"}
    assert 2 not in directory
