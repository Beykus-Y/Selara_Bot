from __future__ import annotations

from aiogram.types import Message

from selara.domain.entities import ChatPersonaAssignment, UserSnapshot

_QUOTE_PAIRS: dict[str, str] = {
    '"': '"',
    "'": "'",
    "«": "»",
    "“": "”",
}
_MASCULINE_NAME_ENDINGS_WITH_A: tuple[str, ...] = (
    "ин",
    "ын",
    "ов",
    "ев",
    "ёв",
)
_MASCULINE_NAME_ENDINGS_WITH_YA: tuple[str, ...] = (
    "ий",
    "ой",
    "ай",
    "ей",
    "уй",
    "й",
    "ь",
)
_TRAILING_TARGET_PUNCTUATION = " \t\r\n,.;!?:…"


def collapse_spaces(value: str | None) -> str | None:
    normalized = " ".join((value or "").strip().split())
    return normalized or None


def strip_wrapping_quotes(value: str | None) -> str:
    normalized = (value or "").strip()
    if len(normalized) < 2:
        return normalized
    expected_closer = _QUOTE_PAIRS.get(normalized[0])
    if expected_closer is None or normalized[-1] != expected_closer:
        return normalized
    return normalized[1:-1].strip()


def split_explicit_target_and_tail(raw: str | None) -> tuple[str | None, str | None]:
    value = (raw or "").strip()
    if not value:
        return None, None

    leading = value[0]
    closing = _QUOTE_PAIRS.get(leading)
    if closing is not None:
        closing_index = value.find(closing, 1)
        if closing_index > 0:
            target = strip_wrapping_quotes(value[: closing_index + 1])
            tail = collapse_spaces(value[closing_index + 1 :])
            return target or None, tail

    if "\n" in value:
        first_line, _, remainder = value.partition("\n")
        return strip_wrapping_quotes(first_line) or None, collapse_spaces(remainder)

    parts = value.split(maxsplit=1)
    target = strip_wrapping_quotes(parts[0]) or None
    tail = collapse_spaces(parts[1]) if len(parts) > 1 else None
    return target, tail


async def build_user_snapshot_from_reply_user(activity_repo, *, chat_id: int, user) -> UserSnapshot:
    return UserSnapshot(
        telegram_user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        is_bot=bool(user.is_bot),
        chat_display_name=await activity_repo.get_chat_display_name(chat_id=chat_id, user_id=user.id),
    )


async def build_user_snapshot_from_id(activity_repo, *, chat_id: int, user_id: int) -> UserSnapshot:
    existing = await activity_repo.get_user_snapshot(user_id=user_id)
    chat_display_name = await activity_repo.get_chat_display_name(chat_id=chat_id, user_id=user_id)
    if existing is not None:
        return UserSnapshot(
            telegram_user_id=existing.telegram_user_id,
            username=existing.username,
            first_name=existing.first_name,
            last_name=existing.last_name,
            is_bot=existing.is_bot,
            chat_display_name=chat_display_name or existing.chat_display_name,
        )
    return UserSnapshot(
        telegram_user_id=user_id,
        username=None,
        first_name=None,
        last_name=None,
        is_bot=False,
        chat_display_name=chat_display_name,
    )


def _normalize_persona_lookup(value: str | None) -> str | None:
    normalized = collapse_spaces(strip_wrapping_quotes(value))
    if normalized is None:
        return None
    normalized = normalized.strip(_TRAILING_TARGET_PUNCTUATION)
    normalized = normalized.replace("ё", "е").casefold()
    return normalized or None


def _last_word_variants(word: str) -> set[str]:
    variants = {word}
    if word.endswith("а"):
        variants.add(f"{word[:-1]}у")
    if word.endswith("я"):
        variants.add(f"{word[:-1]}ю")
    if word.endswith(_MASCULINE_NAME_ENDINGS_WITH_A):
        variants.add(f"{word}а")
    if word.endswith(_MASCULINE_NAME_ENDINGS_WITH_YA):
        variants.add(f"{word[:-1]}я")
    return variants


def persona_lookup_matches(query: str | None, stored_label: str | None) -> bool:
    normalized_query = _normalize_persona_lookup(query)
    normalized_stored = _normalize_persona_lookup(stored_label)
    if normalized_query is None or normalized_stored is None:
        return False
    if normalized_query == normalized_stored:
        return True

    query_words = normalized_query.split(" ")
    stored_words = normalized_stored.split(" ")
    if len(query_words) != len(stored_words) or not query_words:
        return False
    if query_words[:-1] != stored_words[:-1]:
        return False

    query_last = query_words[-1]
    stored_last = stored_words[-1]
    return query_last in _last_word_variants(stored_last) or stored_last in _last_word_variants(query_last)


async def resolve_chat_persona_owner(activity_repo, *, chat_id: int, persona_label: str) -> ChatPersonaAssignment | None:
    find_owner = getattr(activity_repo, "find_chat_persona_owner", None)
    if callable(find_owner):
        owner = await find_owner(chat_id=chat_id, persona_label=persona_label)
        if owner is not None:
            return owner

    list_assignments = getattr(activity_repo, "list_chat_persona_assignments", None)
    if not callable(list_assignments):
        return None

    assignments = await list_assignments(chat_id=chat_id)
    matches = [assignment for assignment in assignments if persona_lookup_matches(persona_label, assignment.persona_label)]
    if len(matches) != 1:
        return None
    return matches[0]


async def resolve_chat_target_user(
    message: Message,
    activity_repo,
    *,
    explicit_target: str | None,
    prefer_reply: bool = True,
) -> UserSnapshot | None:
    reply_user = getattr(getattr(message, "reply_to_message", None), "from_user", None)
    if prefer_reply and reply_user is not None:
        return await build_user_snapshot_from_reply_user(activity_repo, chat_id=message.chat.id, user=reply_user)

    normalized_target = collapse_spaces(strip_wrapping_quotes(explicit_target))
    if normalized_target is None:
        return None

    if normalized_target.startswith("@"):
        username_target = await activity_repo.find_chat_user_by_username(chat_id=message.chat.id, username=normalized_target)
        if username_target is not None:
            return username_target
        normalized_target = normalized_target[1:].strip()
        if not normalized_target:
            return None

    if normalized_target.lstrip("-").isdigit():
        return await build_user_snapshot_from_id(
            activity_repo,
            chat_id=message.chat.id,
            user_id=int(normalized_target),
        )

    persona_owner = await resolve_chat_persona_owner(
        activity_repo,
        chat_id=message.chat.id,
        persona_label=normalized_target,
    )
    if persona_owner is None:
        return None
    return persona_owner.user
