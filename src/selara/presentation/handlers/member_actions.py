"""Social (RP) actions Selara can take in member mode, like a regular chat participant.

The model only picks an action and a target; the text comes from the same templates users trigger by
hand, 18+ actions are never available to Selara, and the chat's disabled-action list is respected.
"""

from __future__ import annotations

import logging
import random
from html import escape
from typing import Any

from aiogram.types import Message

from selara.presentation.commands.normalizer import normalize_text_command
from selara.presentation.targeting import resolve_chat_target_user

log = logging.getLogger(__name__)

ACTION_TOOL_NAME = "perform_action"
_ASKER_TARGETS = frozenset({"asker", "собеседник", "я", "меня", "мне", "me"})
_HINT_LIMIT = 40

_bot_identity: dict[str, Any] = {}


def action_tool_definition() -> dict:
    return {
        "type": "function",
        "function": {
            "name": ACTION_TOOL_NAME,
            "description": (
                "Совершить безобидное социальное действие в чате от своего имени, как обычный участник "
                "(например «обнять», «погладить», «дать пять», «пощекотать»). Не больше одного за ответ. "
                "В target укажи @username участника или слово asker для собеседника, который к тебе обратился."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Название действия, например «обнять»."},
                    "target": {"type": "string", "description": "@username участника или «asker»."},
                },
                "required": ["action", "target"],
            },
        },
    }


def _catalog():
    # Lazy: text_commands imports group_character, which imports this module.
    from selara.presentation.handlers import text_commands as tc

    return tc


async def _bot_user(bot) -> tuple[int, str] | None:
    if "id" not in _bot_identity:
        try:
            me = await bot.get_me()
        except Exception:
            log.warning("member action: bot identity unavailable", exc_info=True)
            return None
        _bot_identity["id"] = me.id
        _bot_identity["name"] = me.first_name or "Selara"
    return _bot_identity["id"], _bot_identity["name"]


def _allowed_actions(tc, disabled: set[str]) -> dict[str, str]:
    """Normalized trigger -> action key for every action Selara may do."""
    allowed: dict[str, str] = {}
    for trigger, key in tc._SOCIAL_ACTION_ALIASES.items():
        if key in tc._SOCIAL_ACTION_18_PLUS or key in disabled or key not in tc._SOCIAL_ACTION_TEMPLATES:
            continue
        allowed[normalize_text_command(trigger)] = key
    for key, name in tc._SOCIAL_ACTION_CANONICAL.items():
        if key in tc._SOCIAL_ACTION_18_PLUS or key in disabled or key not in tc._SOCIAL_ACTION_TEMPLATES:
            continue
        allowed.setdefault(normalize_text_command(name), key)
    return allowed


async def perform_member_action(
    *, message: Message, bot, activity_repo, arguments: dict, actor_label: str | None
) -> tuple[str, bool]:
    """Send the action text to the chat; returns (result for the model, success)."""
    tc = _catalog()
    try:
        disabled = set(await activity_repo.get_disabled_rp_actions(chat_id=message.chat.id))
    except Exception:
        disabled = set()
    allowed = _allowed_actions(tc, disabled)
    action_text = normalize_text_command(str(arguments.get("action") or ""))
    key = allowed.get(action_text)
    if key is None:
        examples = ", ".join(sorted({tc._SOCIAL_ACTION_CANONICAL.get(k, k) for k in allowed.values()})[:_HINT_LIMIT])
        return f"Такого действия нет или оно недоступно. Примеры: {examples}", False

    raw_target = str(arguments.get("target") or "").strip()
    asker = message.from_user
    if raw_target.casefold() in _ASKER_TARGETS and asker is not None:
        target = await tc._social_action_user_snapshot(message, activity_repo, user=asker)
    else:
        target = await resolve_chat_target_user(message, activity_repo, explicit_target=raw_target, prefer_reply=False)
    if target is None:
        return "Не нашла такого участника в чате. Укажи @username или asker.", False
    if target.is_bot:
        return "Действия применяются только к живым участникам, не к ботам.", False

    identity = await _bot_user(bot)
    if identity is None:
        return "Не удалось выполнить действие.", False
    bot_id, bot_name = identity
    label = escape((actor_label or bot_name).strip() or bot_name)
    actor_mention = f'<a href="tg://user?id={bot_id}">{label}</a>'
    text = random.choice(tc._SOCIAL_ACTION_TEMPLATES[key]).format(
        actor=actor_mention, target=tc._social_action_mention(target)
    )
    try:
        await bot.send_message(
            message.chat.id,
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_to_message_id=message.message_id,
        )
    except Exception:
        log.warning("member action: send failed chat_id=%s", message.chat.id, exc_info=True)
        return "Не удалось отправить действие в чат.", False
    return f"Действие выполнено: {tc._SOCIAL_ACTION_CANONICAL.get(key, key)}. Текст уже отправлен в чат.", True
