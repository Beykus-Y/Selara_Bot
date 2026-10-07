"""Mini App settings of Selara in one group: call names, character and member-mode switches.

The same rules as the ``/selara`` command (name validation and conflicts, the call-name limit by
Selara AI, character validation) behind a JSON API; authorization stays with the route.
"""

from __future__ import annotations

from selara.application.ai_character import ProfileValidationError
from selara.application.ai_character.group import (
    GROUP_CUSTOM_PRESET,
    GROUP_PRESETS,
    call_name_limit,
    active_call_names,
    normalize_call_name,
    validate_call_name,
    validate_group_custom_character,
)
from selara.core.config import Settings
from selara.infrastructure.db.chat_ai_character_repository import ChatAiCharacterRepository, GroupCharacterError
from selara.presentation.handlers.group_character import (
    _limits,
    chat_has_selara_ai,
    invalidate_call_names,
    name_conflict,
)

_SWITCHES = {
    "member_mode": "member_mode_enabled",
    "history": "member_history_access",
    "actions": "member_actions_enabled",
}
_TRUE = {"1", "true", "on", "yes"}


async def build_selara_settings_payload(
    *, db_session, chat_id: int, session_factory, settings: Settings, can_manage: bool
) -> dict:
    repo = ChatAiCharacterRepository(db_session)
    character = await repo.get_character(chat_id=chat_id)
    names = await repo.list_names(chat_id=chat_id)
    paid = await chat_has_selara_ai(session_factory, chat_id=chat_id, settings=settings)
    active = {name.name_norm for name in active_call_names(names, paid=paid)}
    limits = _limits(settings)
    daily, per_actor = (limits.paid_daily, limits.paid_per_actor) if paid else (limits.free_daily, limits.free_per_actor)
    return {
        "chat_id": chat_id,
        "can_manage": can_manage,
        "paid": paid,
        "names": [
            {
                "display": name.name_display,
                "norm": name.name_norm,
                "is_primary": name.is_primary,
                "active": name.name_norm in active,
            }
            for name in names
        ],
        "name_limit": call_name_limit(paid),
        "character": {
            "preset": character.character_preset,
            "custom": character.character_custom,
            "presets": [{"key": key, "title": title} for key, (title, _) in GROUP_PRESETS.items()],
            "custom_key": GROUP_CUSTOM_PRESET,
        },
        "member_mode": character.member_mode_enabled,
        "history": character.member_history_access,
        "actions": character.member_actions_enabled,
        "limits": {"daily": daily, "per_actor": per_actor},
    }


async def apply_selara_action(
    *,
    db_session,
    activity_repo,
    chat_id: int,
    actor_id: int,
    action: str,
    value: str,
    session_factory,
    settings: Settings,
) -> tuple[bool, str]:
    """Apply one admin action; returns (ok, message). Validation problems are ``ok=False`` results."""
    repo = ChatAiCharacterRepository(db_session)
    try:
        if action == "add_name":
            display, norm = validate_call_name(value)
            conflict = await name_conflict(norm, chat_id=chat_id, activity_repo=activity_repo, db_session=db_session)
            if conflict is not None:
                return False, conflict
            paid = await chat_has_selara_ai(session_factory, chat_id=chat_id, settings=settings)
            added = await repo.add_name(chat_id=chat_id, display=display, norm=norm, actor_user_id=actor_id, paid=paid)
            await repo.commit()
            message = f"Кличка «{added.name_display}» добавлена."
        elif action == "remove_name":
            removed = await repo.remove_name(chat_id=chat_id, norm=normalize_call_name(value))
            await repo.commit()
            if removed is None:
                return False, "Такой клички нет."
            message = f"Кличка «{removed.name_display}» удалена."
        elif action == "set_primary":
            primary = await repo.set_primary(chat_id=chat_id, norm=normalize_call_name(value))
            await repo.commit()
            if primary is None:
                return False, "Такой клички нет."
            message = f"Основная кличка — «{primary.name_display}»."
        elif action == "set_preset":
            if value not in GROUP_PRESETS:
                return False, "Нет такого пресета."
            await repo.update_character(
                chat_id=chat_id, actor_user_id=actor_id, character_preset=value, character_custom=None
            )
            await repo.commit()
            message = "Характер сохранён."
        elif action == "set_custom":
            custom = validate_group_custom_character(value)
            await repo.update_character(
                chat_id=chat_id, actor_user_id=actor_id, character_preset=GROUP_CUSTOM_PRESET, character_custom=custom
            )
            await repo.commit()
            message = "Свой характер сохранён."
        elif action in _SWITCHES:
            await repo.update_character(
                chat_id=chat_id, actor_user_id=actor_id, **{_SWITCHES[action]: value.strip().lower() in _TRUE}
            )
            await repo.commit()
            message = "Настройка сохранена."
        elif action == "reset_history":
            cleared = await repo.reset_history(chat_id=chat_id)
            await repo.commit()
            message = f"Разговор с участниками забыт ({cleared} сообщений)."
        else:
            return False, "Неизвестное действие."
    except (ProfileValidationError, GroupCharacterError) as exc:
        await repo.commit()
        return False, str(exc)
    invalidate_call_names(chat_id)
    return True, message
