"""Owner commands to grant and revoke subscriptions: ``/grant_sub``, ``/revoke_sub``, ``/grants``.

Registered on the payment router (like ``/stars_refund``): owner only, private chat only, own DB sessions.
"""

from __future__ import annotations

import logging
from html import escape

from aiogram import Bot
from aiogram.types import Message

from selara.application.entitlement_grants import (
    GRANT_USAGE,
    GrantCommand,
    GrantError,
    REVOKE_USAGE,
    format_until,
    parse_grant_command,
    parse_revoke_command,
    target_id_from_text,
)
from selara.core.config import Settings
from selara.infrastructure.db.entitlement_grants import EntitlementGrantService, GrantOutcome

logger = logging.getLogger(__name__)

OWNER_ONLY_TEXT = "Команда доступна только владельцу бота в личном чате."
_SCOPE_TITLE = {"user": "Selara Personal", "chat": "Selara AI для группы"}
_ACTION_TITLE = {"grant": "выдано", "extend": "продлено", "revoke": "отключено", "shorten": "сокращено"}


def _is_owner_private(message: Message, settings: Settings) -> bool:
    return (
        message.chat.type == "private"
        and message.from_user is not None
        and settings.admin_user_id is not None
        and message.from_user.id == settings.admin_user_id
    )


def _args(message: Message) -> str:
    parts = (message.text or "").split(maxsplit=1)
    return parts[1] if len(parts) == 2 else ""


async def _resolve_target(service: EntitlementGrantService, command: GrantCommand) -> int:
    target_id = target_id_from_text(command.target)
    if target_id is not None:
        return target_id
    if command.scope == "user" and command.target.startswith("@"):
        resolved = await service.resolve_username(command.target)
        if resolved is not None:
            return resolved
        raise GrantError("invalid_target", "Пользователь с таким @username не найден: укажите числовой id.")
    raise GrantError("invalid_target", "Нужен числовой id (для группы он отрицательный).")


def _notice_line(notified: bool | None) -> str:
    if notified is True:
        return "\nПолучатель уведомлён."
    if notified is False:
        return "\nУведомить получателя не удалось (возможно, он не открывал бота): операция выполнена."
    return ""


def render_outcome(outcome: GrantOutcome, *, notified: bool | None, timezone_name: str) -> str:
    until = format_until(outcome.valid_until, timezone_name) if outcome.valid_until else "—"
    status = "действует" if outcome.status == "active" else "отключена"
    head = f"{_ACTION_TITLE[outcome.action].capitalize()}: {_SCOPE_TITLE[outcome.scope]}, id <code>{outcome.target_id}</code>."
    if outcome.duplicate:
        head = "Эта операция уже выполнена (повторный запуск ничего не изменил).\n" + head
    text = f"{head}\nСтатус: {status}, до {escape(until)}."
    if outcome.paid_recently and outcome.action in ("grant", "extend"):
        text += "\nУ цели есть недавний платёж Stars: выдача добавляется к оплаченному сроку."
    return text + _notice_line(notified)


async def grant_subscription_command(message: Message, bot: Bot, session_factory, settings: Settings) -> None:
    if message.chat.type != "private" or message.from_user is None:
        return
    if not _is_owner_private(message, settings):
        await message.answer(OWNER_ONLY_TEXT)
        return
    service = EntitlementGrantService(session_factory, admin_user_id=settings.admin_user_id)
    try:
        command = parse_grant_command(_args(message))
        target_id = await _resolve_target(service, command)
        outcome, notified = await service.grant_and_notify(
            notify=True,
            send_notice=_notice_sender(bot),
            timezone_name=settings.bot_timezone,
            scope=command.scope,
            target_id=target_id,
            days=command.days,
            reason=command.reason,
            idempotency_key=f"cmd:{message.chat.id}:{message.message_id}",
            actor_user_id=message.from_user.id,
            source="command",
        )
    except GrantError as exc:
        await message.answer(exc.message if exc.code == "usage" else escape(exc.message), parse_mode="HTML")
        return
    except Exception:
        logger.exception("Subscription grant command failed")
        await message.answer("Не удалось выполнить выдачу. Повторите позже.")
        return
    await message.answer(render_outcome(outcome, notified=notified, timezone_name=settings.bot_timezone), parse_mode="HTML")


async def revoke_subscription_command(message: Message, bot: Bot, session_factory, settings: Settings) -> None:
    if message.chat.type != "private" or message.from_user is None:
        return
    if not _is_owner_private(message, settings):
        await message.answer(OWNER_ONLY_TEXT)
        return
    service = EntitlementGrantService(session_factory, admin_user_id=settings.admin_user_id)
    try:
        command = parse_revoke_command(_args(message))
        target_id = await _resolve_target(service, command)
        outcome, notified = await service.revoke_and_notify(
            notify=True,
            send_notice=_notice_sender(bot),
            timezone_name=settings.bot_timezone,
            scope=command.scope,
            target_id=target_id,
            mode="cancel_all" if command.days is None else "shorten",
            days=command.days,
            reason=command.reason,
            idempotency_key=f"cmd:{message.chat.id}:{message.message_id}",
            actor_user_id=message.from_user.id,
            source="command",
        )
    except GrantError as exc:
        await message.answer(exc.message if exc.code == "usage" else escape(exc.message), parse_mode="HTML")
        return
    except Exception:
        logger.exception("Subscription revoke command failed")
        await message.answer("Не удалось выполнить отзыв. Повторите позже.")
        return
    text = render_outcome(outcome, notified=notified, timezone_name=settings.bot_timezone)
    if outcome.action == "revoke":
        text += "\nОплаченные Stars этой операцией не возвращаются."
    await message.answer(text, parse_mode="HTML")


async def list_grants_command(message: Message, session_factory, settings: Settings) -> None:
    if message.chat.type != "private" or message.from_user is None:
        return
    if not _is_owner_private(message, settings):
        await message.answer(OWNER_ONLY_TEXT)
        return
    rows = await EntitlementGrantService(session_factory, admin_user_id=settings.admin_user_id).recent(limit=10)
    if not rows:
        await message.answer("Выдач и отзывов пока не было.")
        return
    lines = ["<b>Последние выдачи и отзывы</b>"]
    for row in rows:
        target = f"{'чат' if row['scope'] == 'chat' else 'польз.'} <code>{row['target_id']}</code>"
        if row["target_title"]:
            target += f" ({escape(row['target_title'][:40])})"
        days = f" {row['delta_days']:g} дн." if row["delta_days"] else ""
        lines.append(
            f"#{row['id']} {_ACTION_TITLE[row['action']]}{days} — {target}; {escape(row['reason'][:60])}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


def _notice_sender(bot: Bot):
    async def send(chat_id: int, text: str) -> bool:
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            logger.warning("Subscription notice was not delivered chat_id=%s", chat_id)
            return False
        return True

    return send


__all__ = [
    "GRANT_USAGE",
    "REVOKE_USAGE",
    "grant_subscription_command",
    "list_grants_command",
    "revoke_subscription_command",
]
