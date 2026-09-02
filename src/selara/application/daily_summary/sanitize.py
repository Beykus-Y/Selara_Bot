from __future__ import annotations

import re

from selara.application.daily_summary.participants import ChatMemberInfo, build_participant_directory


def build_author_display_tokens(
    members: list[ChatMemberInfo],
    *,
    persona_enabled: bool,
) -> dict[int, str]:
    """Per-run author token for every member seen in the analysed window.

    Active members map to the same label the writer stage will see (persona or
    display name). A departed member NEVER maps to their real name/username/persona
    here -- they get a stable-within-this-run anonymous token instead, numbered
    deterministically (sorted by user_id) so two calls with the same input agree,
    without the number itself being a cross-run fingerprint of who they were.
    """
    tokens = dict(build_participant_directory(members, persona_enabled=persona_enabled))
    inactive_ids = sorted(member.user_id for member in members if not member.is_active_member)
    for index, user_id in enumerate(inactive_ids, start=1):
        tokens[user_id] = f"Участник #{index}"
    return tokens


def build_alias_index(members: list[ChatMemberInfo]) -> dict[str, int]:
    """Known, unambiguous aliases of departed members: username/persona/display name.

    Only inactive members are indexed -- an active member's own name is meant to be
    visible, this index exists solely to catch OTHER people's messages that mention
    a departed member by one of these exact, deterministic identifiers.
    """
    index: dict[str, int] = {}
    for member in members:
        if member.is_active_member:
            continue
        for alias in (member.username, member.persona_label, member.display_name):
            if not alias:
                continue
            key = alias.strip().casefold()
            if key:
                index[key] = member.user_id
    return index


def redact_known_aliases(text: str, *, alias_index: dict[str, int], tokens: dict[int, str]) -> str:
    """Deterministically replace exact alias mentions of departed members.

    Intentionally does NOT try to guess that a bare word like "Вася" in free text
    refers to a specific person -- that is unreliable and would mangle unrelated
    text. It only strips whole-word, case-insensitive matches of known, unique
    identifiers (username, persona label, display name), each optionally preceded
    by "@" for username-style mentions.
    """
    if not alias_index:
        return text

    aliases_by_length = sorted(alias_index, key=len, reverse=True)
    pattern = re.compile(
        r"@?\b(" + "|".join(re.escape(alias) for alias in aliases_by_length) + r")\b",
        re.IGNORECASE,
    )

    def _replace(match: re.Match[str]) -> str:
        user_id = alias_index.get(match.group(1).casefold())
        if user_id is None:
            return match.group(0)
        return tokens.get(user_id, match.group(0))

    return pattern.sub(_replace, text)


def redact_text_mentions(
    text: str,
    *,
    entities: list[tuple[int, int, int]],
    tokens: dict[int, str],
) -> str:
    """Replace exact (offset, length) spans that Telegram tagged as a text_mention.

    A text_mention entity carries the mentioned user's id directly -- a reliable,
    non-heuristic signal, unlike guessing from surrounding words. Only spans whose
    user_id has a known token (i.e. is a departed member tracked this run) are
    touched; everything else is left untouched.
    """
    result = text
    for offset, length, user_id in sorted(entities, key=lambda entity: entity[0], reverse=True):
        token = tokens.get(user_id)
        if token is None:
            continue
        result = result[:offset] + token + result[offset + length :]
    return result
