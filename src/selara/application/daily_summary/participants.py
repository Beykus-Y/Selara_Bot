from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatMemberInfo:
    user_id: int
    is_active_member: bool
    username: str | None = None
    display_name: str | None = None
    persona_label: str | None = None


def _active_member_label(member: ChatMemberInfo, *, persona_enabled: bool) -> str:
    if persona_enabled and member.persona_label:
        return member.persona_label
    if member.display_name:
        return member.display_name
    if member.username:
        return f"@{member.username}"
    return f"user#{member.user_id}"


def build_participant_directory(
    members: list[ChatMemberInfo],
    *,
    persona_enabled: bool,
) -> dict[int, str]:
    """Live snapshot of displayable names for CURRENTLY active members only.

    A departed member is not included at all, regardless of any persona/name they
    once had -- this is the only source the writer stage sees for "who's who",
    which is what keeps a former member from ever being named in the summary.
    """
    return {
        member.user_id: _active_member_label(member, persona_enabled=persona_enabled)
        for member in members
        if member.is_active_member
    }
