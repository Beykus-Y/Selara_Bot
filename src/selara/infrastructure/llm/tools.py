from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot

from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.llm_repository import LlmRepository

log = logging.getLogger(__name__)

import os
_BOT_DOCS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "docs", "bot_docs"))


@dataclass
class ToolCall:
    name: str
    arguments: dict
    call_id: str


@dataclass
class ToolResult:
    call_id: str
    name: str
    result_text: str
    action_description: str
    undo_payload: dict | None = None
    success: bool = True
    db_action_id: int | None = None


@dataclass
class ToolDefinition:
    name: str
    schema: dict
    executor: Callable
    status_text: str = ""


_TOOL_REGISTRY: dict[str, ToolDefinition] = {}


def register_tool(name: str, schema: dict, status_text: str = "") -> Callable:
    def decorator(fn: Callable) -> Callable:
        _TOOL_REGISTRY[name] = ToolDefinition(
            name=name,
            schema={"type": "function", "function": {**schema, "name": name}},
            executor=fn,
            status_text=status_text,
        )
        return fn
    return decorator


def get_tool_definitions() -> list[dict]:
    return [t.schema for t in _TOOL_REGISTRY.values()]


def get_tool_status(name: str, arguments: dict) -> str:
    definition = _TOOL_REGISTRY.get(name)
    if not definition or not definition.status_text:
        return ""
    try:
        return definition.status_text.format(**arguments)
    except (KeyError, ValueError):
        return definition.status_text


async def execute_tool(call: ToolCall, **ctx: Any) -> ToolResult:
    definition = _TOOL_REGISTRY.get(call.name)
    if definition is None:
        return ToolResult(
            call_id=call.call_id,
            name=call.name,
            result_text=json.dumps({"error": f"Неизвестный инструмент: {call.name}"}),
            action_description="",
            success=False,
        )
    try:
        result = await definition.executor(call, **ctx)
        log.info("llm tool %s ok: success=%s db_action_id=%s undo=%s",
                 call.name, result.success, result.db_action_id, result.undo_payload is not None)
        return result
    except Exception as exc:
        log.exception("llm tool %s failed: %s", call.name, exc)
        return ToolResult(
            call_id=call.call_id,
            name=call.name,
            result_text=json.dumps({"error": str(exc)}),
            action_description=f"Ошибка инструмента {call.name}",
            success=False,
        )


async def _resolve_target(
    target: str,
    *,
    chat_id: int,
    activity_repo: Any,
) -> UserSnapshot | None:
    if not target:
        return None
    stripped = target.lstrip("@").strip()
    if not stripped:
        return None

    if stripped.isdigit():
        user_id = int(stripped)
        from sqlalchemy import select
        from selara.infrastructure.db.models import UserModel, UserChatActivityModel
        stmt = (
            select(UserModel)
            .join(UserChatActivityModel, UserChatActivityModel.user_id == UserModel.telegram_user_id)
            .where(
                UserChatActivityModel.chat_id == chat_id,
                UserModel.telegram_user_id == user_id,
            )
            .limit(1)
        )
        row = (await activity_repo._session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            return UserSnapshot(
                telegram_user_id=int(row.telegram_user_id),
                username=row.username,
                first_name=row.first_name,
                last_name=row.last_name,
                is_bot=bool(row.is_bot),
            )
        return None

    user = await activity_repo.find_chat_user_by_username(chat_id=chat_id, username=stripped)
    if user is not None:
        return user

    assignment = await activity_repo.find_chat_persona_owner(chat_id=chat_id, persona_label=stripped)
    if assignment is not None:
        return assignment.user

    return None


def _ok(call_id: str, name: str, data: dict, description: str, undo: dict | None = None) -> ToolResult:
    return ToolResult(
        call_id=call_id,
        name=name,
        result_text=json.dumps(data, ensure_ascii=False, default=str),
        action_description=description,
        undo_payload=undo,
        success=True,
    )


def _err(call_id: str, name: str, msg: str) -> ToolResult:
    return ToolResult(
        call_id=call_id,
        name=name,
        result_text=json.dumps({"error": msg}),
        action_description="",
        success=False,
    )


@register_tool(
    "grant_rest",
    schema={
        "description": (
            "Выдать рест пользователю. Рест — официальный период, в течение которого человек освобождён "
            "от нормы сообщений в неделю (не мьют, не бан — просто исключение из рейтинга активности). "
            "Указывай duration_days в днях."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "@username, образ или user_id"},
                "duration_days": {"type": "integer", "description": "Длительность в днях (мин. 1)"},
                "reason": {"type": "string", "description": "Причина"},
            },
            "required": ["target", "duration_days"],
        },
    },
    status_text="Выдаю рест {target}...",
)
async def _exec_grant_rest(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    actor_snapshot: UserSnapshot,
    activity_repo: Any,
    llm_repo: LlmRepository,
    **_: Any,
) -> ToolResult:
    target_str = call.arguments.get("target", "")
    duration_days = max(1, int(call.arguments.get("duration_days", 1)))
    reason = call.arguments.get("reason", "")

    target = await _resolve_target(target_str, chat_id=chat_snapshot.telegram_chat_id, activity_repo=activity_repo)
    if target is None:
        return _err(call.call_id, call.name, f"Пользователь '{target_str}' не найден в чате.")

    await activity_repo.grant_rest(
        chat=chat_snapshot,
        actor=actor_snapshot,
        target=target,
        duration_days=duration_days,
    )

    action = await llm_repo.add_admin_action(
        chat_id=chat_snapshot.telegram_chat_id,
        admin_user_id=actor_snapshot.telegram_user_id,
        tool_name=call.name,
        action_description=f"Рест {target_str} на {duration_days}д",
        undo_payload={"tool": "revoke_rest", "target_user_id": target.telegram_user_id, "chat_id": chat_snapshot.telegram_chat_id},
    )
    result = _ok(
        call.call_id, call.name,
        {"ok": True, "target": target_str, "duration_days": duration_days},
        f"Рест {target_str} на {duration_days}д",
        undo={"tool": "revoke_rest", "target_user_id": target.telegram_user_id, "chat_id": chat_snapshot.telegram_chat_id},
    )
    result.db_action_id = action.id
    return result


@register_tool(
    "revoke_rest",
    schema={
        "description": "Снять рест (отпуск от нормы активности) с пользователя.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "@username, образ или user_id"},
            },
            "required": ["target"],
        },
    },
    status_text="Снимаю рест с {target}...",
)
async def _exec_revoke_rest(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    actor_snapshot: UserSnapshot,
    activity_repo: Any,
    llm_repo: LlmRepository,
    **_: Any,
) -> ToolResult:
    target_str = call.arguments.get("target", "")
    target = await _resolve_target(target_str, chat_id=chat_snapshot.telegram_chat_id, activity_repo=activity_repo)
    if target is None:
        return _err(call.call_id, call.name, f"Пользователь '{target_str}' не найден в чате.")

    state = await activity_repo.revoke_rest(
        chat=chat_snapshot,
        actor=actor_snapshot,
        target=target,
    )
    if state is None:
        return _err(call.call_id, call.name, f"У {target_str} нет активного реста.")

    action = await llm_repo.add_admin_action(
        chat_id=chat_snapshot.telegram_chat_id,
        admin_user_id=actor_snapshot.telegram_user_id,
        tool_name=call.name,
        action_description=f"Рест снят с {target_str}",
        undo_payload=None,
    )
    result = _ok(
        call.call_id, call.name,
        {"ok": True, "target": target_str},
        f"Рест снят с {target_str}",
    )
    result.db_action_id = action.id
    return result


@register_tool(
    "warn_user",
    schema={
        "description": "Выдать предупреждение (варн) пользователю. 3 варна = автобан.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "@username, образ или user_id"},
                "reason": {"type": "string", "description": "Причина"},
            },
            "required": ["target"],
        },
    },
    status_text="Выдаю предупреждение {target}...",
)
async def _exec_warn_user(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    actor_snapshot: UserSnapshot,
    activity_repo: Any,
    llm_repo: LlmRepository,
    **_: Any,
) -> ToolResult:
    target_str = call.arguments.get("target", "")
    reason = call.arguments.get("reason", "")

    target = await _resolve_target(target_str, chat_id=chat_snapshot.telegram_chat_id, activity_repo=activity_repo)
    if target is None:
        return _err(call.call_id, call.name, f"Пользователь '{target_str}' не найден в чате.")

    result_state = await activity_repo.apply_moderation_action(
        chat=chat_snapshot,
        actor=actor_snapshot,
        target=target,
        action="warn",
        reason=reason or None,
    )

    action = await llm_repo.add_admin_action(
        chat_id=chat_snapshot.telegram_chat_id,
        admin_user_id=actor_snapshot.telegram_user_id,
        tool_name=call.name,
        action_description=f"Варн {target_str}",
        undo_payload={"tool": "unwarn", "target_user_id": target.telegram_user_id, "chat_id": chat_snapshot.telegram_chat_id},
    )
    result = _ok(
        call.call_id, call.name,
        {"ok": True, "target": target_str, "warn_count": result_state.state.warn_count, "auto_ban": result_state.auto_ban_triggered},
        f"Варн {target_str}",
        undo={"tool": "unwarn", "target_user_id": target.telegram_user_id, "chat_id": chat_snapshot.telegram_chat_id},
    )
    result.db_action_id = action.id
    return result


@register_tool(
    "ban_user",
    schema={
        "description": "Забанить пользователя в чате.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "@username, образ или user_id"},
                "reason": {"type": "string", "description": "Причина"},
            },
            "required": ["target"],
        },
    },
    status_text="Баню {target}...",
)
async def _exec_ban_user(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    actor_snapshot: UserSnapshot,
    activity_repo: Any,
    llm_repo: LlmRepository,
    bot: Bot,
    **_: Any,
) -> ToolResult:
    target_str = call.arguments.get("target", "")
    reason = call.arguments.get("reason", "")

    target = await _resolve_target(target_str, chat_id=chat_snapshot.telegram_chat_id, activity_repo=activity_repo)
    if target is None:
        return _err(call.call_id, call.name, f"Пользователь '{target_str}' не найден в чате.")

    await activity_repo.apply_moderation_action(
        chat=chat_snapshot,
        actor=actor_snapshot,
        target=target,
        action="ban",
        reason=reason or None,
    )
    try:
        await bot.ban_chat_member(
            chat_id=chat_snapshot.telegram_chat_id,
            user_id=target.telegram_user_id,
        )
    except Exception as exc:
        log.warning("llm ban_user: Telegram ban failed for %d: %s", target.telegram_user_id, exc)

    action = await llm_repo.add_admin_action(
        chat_id=chat_snapshot.telegram_chat_id,
        admin_user_id=actor_snapshot.telegram_user_id,
        tool_name=call.name,
        action_description=f"Бан {target_str}",
        undo_payload={"tool": "unban", "target_user_id": target.telegram_user_id, "chat_id": chat_snapshot.telegram_chat_id},
    )
    result = _ok(
        call.call_id, call.name,
        {"ok": True, "target": target_str},
        f"Бан {target_str}",
        undo={"tool": "unban", "target_user_id": target.telegram_user_id, "chat_id": chat_snapshot.telegram_chat_id},
    )
    result.db_action_id = action.id
    return result


@register_tool(
    "unwarn_user",
    schema={
        "description": "Снять предупреждение (варн) с пользователя.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "@username, образ или user_id"},
                "reason": {"type": "string", "description": "Причина"},
            },
            "required": ["target"],
        },
    },
    status_text="Снимаю варн с {target}...",
)
async def _exec_unwarn_user(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    actor_snapshot: UserSnapshot,
    activity_repo: Any,
    llm_repo: LlmRepository,
    **_: Any,
) -> ToolResult:
    target_str = call.arguments.get("target", "")
    reason = call.arguments.get("reason", "")
    target = await _resolve_target(target_str, chat_id=chat_snapshot.telegram_chat_id, activity_repo=activity_repo)
    if target is None:
        return _err(call.call_id, call.name, f"Пользователь '{target_str}' не найден.")

    result_state = await activity_repo.apply_moderation_action(
        chat=chat_snapshot, actor=actor_snapshot, target=target,
        action="unwarn", reason=reason or None,
    )
    action = await llm_repo.add_admin_action(
        chat_id=chat_snapshot.telegram_chat_id,
        admin_user_id=actor_snapshot.telegram_user_id,
        tool_name=call.name,
        action_description=f"Снят варн у {target_str}",
        undo_payload=None,
    )
    result = _ok(
        call.call_id, call.name,
        {"ok": True, "target": target_str, "warn_count": result_state.state.warn_count},
        f"Снят варн у {target_str}",
    )
    result.db_action_id = action.id
    return result


@register_tool(
    "unban_user",
    schema={
        "description": "Разбанить пользователя в чате.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "@username, образ или user_id"},
                "reason": {"type": "string", "description": "Причина"},
            },
            "required": ["target"],
        },
    },
    status_text="Разбаниваю {target}...",
)
async def _exec_unban_user(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    actor_snapshot: UserSnapshot,
    activity_repo: Any,
    llm_repo: LlmRepository,
    bot: Bot,
    **_: Any,
) -> ToolResult:
    target_str = call.arguments.get("target", "")
    reason = call.arguments.get("reason", "")
    target = await _resolve_target(target_str, chat_id=chat_snapshot.telegram_chat_id, activity_repo=activity_repo)
    if target is None:
        return _err(call.call_id, call.name, f"Пользователь '{target_str}' не найден.")

    await activity_repo.apply_moderation_action(
        chat=chat_snapshot, actor=actor_snapshot, target=target,
        action="unban", reason=reason or None,
    )
    try:
        await bot.unban_chat_member(
            chat_id=chat_snapshot.telegram_chat_id,
            user_id=target.telegram_user_id,
        )
    except Exception as exc:
        log.warning("llm unban_user: Telegram unban failed for %d: %s", target.telegram_user_id, exc)

    action = await llm_repo.add_admin_action(
        chat_id=chat_snapshot.telegram_chat_id,
        admin_user_id=actor_snapshot.telegram_user_id,
        tool_name=call.name,
        action_description=f"Разбан {target_str}",
        undo_payload=None,
    )
    result = _ok(call.call_id, call.name, {"ok": True, "target": target_str}, f"Разбан {target_str}")
    result.db_action_id = action.id
    return result


@register_tool(
    "apply_pred",
    schema={
        "description": (
            "Выдать пред (предупреждение, мягче варна). "
            "3 преда автоматически конвертируются в варн."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "@username, образ или user_id"},
                "reason": {"type": "string", "description": "Причина"},
            },
            "required": ["target"],
        },
    },
    status_text="Выдаю пред {target}...",
)
async def _exec_apply_pred(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    actor_snapshot: UserSnapshot,
    activity_repo: Any,
    llm_repo: LlmRepository,
    **_: Any,
) -> ToolResult:
    target_str = call.arguments.get("target", "")
    reason = call.arguments.get("reason", "")
    target = await _resolve_target(target_str, chat_id=chat_snapshot.telegram_chat_id, activity_repo=activity_repo)
    if target is None:
        return _err(call.call_id, call.name, f"Пользователь '{target_str}' не найден.")

    result_state = await activity_repo.apply_moderation_action(
        chat=chat_snapshot, actor=actor_snapshot, target=target,
        action="pred", reason=reason or None,
    )
    action = await llm_repo.add_admin_action(
        chat_id=chat_snapshot.telegram_chat_id,
        admin_user_id=actor_snapshot.telegram_user_id,
        tool_name=call.name,
        action_description=f"Пред {target_str}",
        undo_payload={"tool": "unpred", "target_user_id": target.telegram_user_id, "chat_id": chat_snapshot.telegram_chat_id},
    )
    result = _ok(
        call.call_id, call.name,
        {
            "ok": True, "target": target_str,
            "pending_preds": result_state.state.pending_preds,
            "auto_warn_triggered": result_state.auto_warns_added > 0,
        },
        f"Пред {target_str}",
        undo={"tool": "unpred", "target_user_id": target.telegram_user_id, "chat_id": chat_snapshot.telegram_chat_id},
    )
    result.db_action_id = action.id
    return result


@register_tool(
    "remove_pred",
    schema={
        "description": "Снять пред с пользователя.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "@username, образ или user_id"},
                "reason": {"type": "string", "description": "Причина"},
            },
            "required": ["target"],
        },
    },
    status_text="Снимаю пред с {target}...",
)
async def _exec_remove_pred(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    actor_snapshot: UserSnapshot,
    activity_repo: Any,
    llm_repo: LlmRepository,
    **_: Any,
) -> ToolResult:
    target_str = call.arguments.get("target", "")
    reason = call.arguments.get("reason", "")
    target = await _resolve_target(target_str, chat_id=chat_snapshot.telegram_chat_id, activity_repo=activity_repo)
    if target is None:
        return _err(call.call_id, call.name, f"Пользователь '{target_str}' не найден.")

    result_state = await activity_repo.apply_moderation_action(
        chat=chat_snapshot, actor=actor_snapshot, target=target,
        action="unpred", reason=reason or None,
    )
    action = await llm_repo.add_admin_action(
        chat_id=chat_snapshot.telegram_chat_id,
        admin_user_id=actor_snapshot.telegram_user_id,
        tool_name=call.name,
        action_description=f"Снят пред у {target_str}",
        undo_payload=None,
    )
    result = _ok(
        call.call_id, call.name,
        {"ok": True, "target": target_str, "pending_preds": result_state.state.pending_preds},
        f"Снят пред у {target_str}",
    )
    result.db_action_id = action.id
    return result


@register_tool(
    "get_user_info",
    schema={
        "description": (
            "Получить информацию о пользователе: роль, варны, рест (официальный отпуск от нормы активности), образ."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "@username, образ или user_id"},
            },
            "required": ["target"],
        },
    },
    status_text="Смотрю информацию о {target}...",
)
async def _exec_get_user_info(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    activity_repo: Any,
    **_: Any,
) -> ToolResult:
    target_str = call.arguments.get("target", "")
    target = await _resolve_target(target_str, chat_id=chat_snapshot.telegram_chat_id, activity_repo=activity_repo)
    if target is None:
        return _err(call.call_id, call.name, f"Пользователь '{target_str}' не найден в чате.")

    chat_id = chat_snapshot.telegram_chat_id
    mod_state = await activity_repo.get_moderation_state(chat_id=chat_id, user_id=target.telegram_user_id)
    rest_state = await activity_repo.get_active_rest_state(chat_id=chat_id, user_id=target.telegram_user_id)
    bot_role = await activity_repo.get_bot_role(chat_id=chat_id, user_id=target.telegram_user_id)

    info: dict = {
        "user_id": target.telegram_user_id,
        "username": target.username,
        "first_name": target.first_name,
        "display_name": target.chat_display_name,
        "bot_role": str(bot_role) if bot_role else "participant",
        "moderation": {
            "warn_count": mod_state.warn_count if mod_state else 0,
            "pending_preds": mod_state.pending_preds if mod_state else 0,
            "is_banned": mod_state.is_banned if mod_state else False,
            "last_reason": mod_state.last_reason if mod_state else None,
            "total_warns": mod_state.total_warns if mod_state else 0,
            "total_preds": mod_state.total_preds if mod_state else 0,
            "total_bans": mod_state.total_bans if mod_state else 0,
        },
        "rest": {
            "active": rest_state is not None,
            "expires_at": str(rest_state.expires_at) if rest_state else None,
        },
    }
    return _ok(call.call_id, call.name, info, f"Информация о {target_str}")


@register_tool(
    "list_active_rests",
    schema={
        "description": "Список активных рестов в чате (пользователи, временно освобождённые от нормы активности).",
        "parameters": {"type": "object", "properties": {}},
    },
    status_text="Загружаю список активных рестов...",
)
async def _exec_list_active_rests(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    activity_repo: Any,
    **_: Any,
) -> ToolResult:
    entries = await activity_repo.list_active_rest_entries(chat_id=chat_snapshot.telegram_chat_id)
    data = [
        {
            "user_id": e.user.telegram_user_id,
            "username": e.user.username,
            "display_name": e.user.chat_display_name,
            "expires_at": str(e.expires_at),
        }
        for e in entries
    ]
    return _ok(call.call_id, call.name, {"rests": data, "count": len(data)}, f"Список рестов ({len(data)})")


@register_tool(
    "get_audit_log",
    schema={
        "description": "Последние действия модерации в чате.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Количество записей (по умолчанию 20)", "default": 20},
            },
        },
    },
    status_text="Читаю журнал модерации...",
)
async def _exec_get_audit_log(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    activity_repo: Any,
    **_: Any,
) -> ToolResult:
    limit = int(call.arguments.get("limit", 20))
    entries = await activity_repo.list_audit_logs(chat_id=chat_snapshot.telegram_chat_id, limit=min(limit, 100))
    data = [
        {
            "action": e.action_code,
            "description": e.description,
            "actor_id": e.actor_user_id,
            "target_id": e.target_user_id,
            "created_at": str(e.created_at),
        }
        for e in entries
    ]
    return _ok(call.call_id, call.name, {"log": data}, "Журнал действий")


@register_tool(
    "get_chat_stats",
    schema={
        "description": "Статистика чата: участники, активность.",
        "parameters": {"type": "object", "properties": {}},
    },
    status_text="Считаю статистику чата...",
)
async def _exec_get_chat_stats(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    activity_repo: Any,
    **_: Any,
) -> ToolResult:
    from sqlalchemy import select, func as sqlfunc
    from selara.infrastructure.db.models import UserChatActivityModel, UserChatRestStateModel

    now = datetime.now(timezone.utc)
    chat_id = chat_snapshot.telegram_chat_id

    total_stmt = select(sqlfunc.count()).where(
        UserChatActivityModel.chat_id == chat_id,
        UserChatActivityModel.is_active_member.is_(True),
    )
    total = (await activity_repo._session.execute(total_stmt)).scalar_one()

    active_stmt = select(sqlfunc.count()).where(
        UserChatActivityModel.chat_id == chat_id,
        UserChatActivityModel.is_active_member.is_(True),
        UserChatActivityModel.message_count > 0,
    )
    active = (await activity_repo._session.execute(active_stmt)).scalar_one()

    rested_stmt = select(sqlfunc.count()).where(
        UserChatRestStateModel.chat_id == chat_id,
        UserChatRestStateModel.expires_at > now,
    )
    rested = (await activity_repo._session.execute(rested_stmt)).scalar_one()

    return _ok(call.call_id, call.name, {
        "total_members": total,
        "active_members": active,
        "currently_rested": rested,
    }, "Статистика чата")


@register_tool(
    "set_rank",
    schema={
        "description": "Изменить роль пользователя в боте.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "@username, образ или user_id"},
                "rank": {"type": "string", "description": "participant | junior_admin | senior_admin | owner"},
            },
            "required": ["target", "rank"],
        },
    },
    status_text="Меняю роль {target}...",
)
async def _exec_set_rank(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    actor_snapshot: UserSnapshot,
    activity_repo: Any,
    llm_repo: LlmRepository,
    **_: Any,
) -> ToolResult:
    target_str = call.arguments.get("target", "")
    rank = call.arguments.get("rank", "participant")

    target = await _resolve_target(target_str, chat_id=chat_snapshot.telegram_chat_id, activity_repo=activity_repo)
    if target is None:
        return _err(call.call_id, call.name, f"Пользователь '{target_str}' не найден в чате.")

    previous_role = await activity_repo.get_bot_role(chat_id=chat_snapshot.telegram_chat_id, user_id=target.telegram_user_id)
    previous_rank = str(previous_role) if previous_role else "participant"

    await activity_repo.set_bot_role(
        chat=chat_snapshot,
        target=target,
        role=rank,
        assigned_by_user_id=actor_snapshot.telegram_user_id,
    )

    action = await llm_repo.add_admin_action(
        chat_id=chat_snapshot.telegram_chat_id,
        admin_user_id=actor_snapshot.telegram_user_id,
        tool_name=call.name,
        action_description=f"Роль {target_str}: {previous_rank} → {rank}",
        undo_payload={"tool": "set_rank", "target_user_id": target.telegram_user_id, "previous_rank": previous_rank, "chat_id": chat_snapshot.telegram_chat_id},
    )
    result = _ok(
        call.call_id, call.name,
        {"ok": True, "target": target_str, "previous_rank": previous_rank, "new_rank": rank},
        f"Роль {target_str}: {previous_rank} → {rank}",
        undo={"tool": "set_rank", "target_user_id": target.telegram_user_id, "previous_rank": previous_rank, "chat_id": chat_snapshot.telegram_chat_id},
    )
    result.db_action_id = action.id
    return result


@register_tool(
    "get_top",
    schema={
        "description": (
            "Топ участников чата по активности (messages) или карме (karma). "
            "Поддерживает периоды: all_time, 7d, 30d."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": "Режим: activity (сообщения) | karma (репутация)",
                    "enum": ["activity", "karma"],
                    "default": "activity",
                },
                "period": {
                    "type": "string",
                    "description": "Период: all_time | 7d | 30d",
                    "enum": ["all_time", "7d", "30d"],
                    "default": "all_time",
                },
                "limit": {
                    "type": "integer",
                    "description": "Количество мест (по умолчанию 10, макс 50)",
                    "default": 10,
                },
            },
        },
    },
    status_text="Строю топ {mode} за {period}...",
)
async def _exec_get_top(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    activity_repo: Any,
    **_: Any,
) -> ToolResult:
    from datetime import timedelta
    from selara.domain.entities import LeaderboardPeriod

    mode_str = call.arguments.get("mode", "activity")
    period_str = call.arguments.get("period", "all_time")
    limit = min(int(call.arguments.get("limit", 10)), 50)

    now = datetime.now(timezone.utc)
    period_map: dict[str, tuple[LeaderboardPeriod, datetime | None]] = {
        "all_time": ("all", None),
        "7d": ("7d", now - timedelta(days=7)),
        "30d": ("month", now - timedelta(days=30)),
    }
    lb_period, since = period_map.get(period_str, ("all", None))

    if mode_str == "karma":
        karma_weight, activity_weight, lb_mode = 1.0, 0.0, "karma"
    else:
        karma_weight, activity_weight, lb_mode = 0.0, 1.0, "activity"

    items = await activity_repo.get_leaderboard(
        chat_id=chat_snapshot.telegram_chat_id,
        mode=lb_mode,
        period=lb_period,
        since=since,
        limit=limit,
        karma_weight=karma_weight,
        activity_weight=activity_weight,
    )

    top = [
        {
            "rank": i + 1,
            "user_id": item.user_id,
            "username": f"@{item.username}" if item.username else None,
            "first_name": item.first_name,
            "display_name": item.chat_display_name,
            "messages": item.activity_value,
        }
        for i, item in enumerate(items)
    ]
    return _ok(
        call.call_id, call.name,
        {"mode": mode_str, "period": period_str, "top": top},
        f"Топ {mode_str} за {period_str} ({len(top)} мест)",
    )


@register_tool(
    "list_personas",
    schema={
        "description": (
            "Список всех образов (персонажей) в чате: кто какой образ носит. "
            "Используй когда нужно найти пользователя по образу или вывести все образы."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    status_text="Загружаю список образов чата...",
)
async def _exec_list_personas(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    activity_repo: Any,
    **_: Any,
) -> ToolResult:
    assignments = await activity_repo.list_chat_persona_assignments(
        chat_id=chat_snapshot.telegram_chat_id,
    )
    data = [
        {
            "persona": a.persona_label,
            "user_id": a.user.telegram_user_id,
            "username": f"@{a.user.username}" if a.user.username else None,
            "first_name": a.user.first_name,
            "display_name": a.user.chat_display_name,
        }
        for a in assignments
    ]
    return _ok(
        call.call_id, call.name,
        {"personas": data, "count": len(data)},
        f"Образы чата ({len(data)})",
    )


@register_tool(
    "grant_persona",
    schema={
        "description": "Назначить образ (персонажа) пользователю в чате.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "@username или user_id"},
                "label": {"type": "string", "description": "Название образа (например: Дракон, Маг)"},
            },
            "required": ["target", "label"],
        },
    },
    status_text="Назначаю образ {label} пользователю {target}...",
)
async def _exec_grant_persona(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    actor_snapshot: UserSnapshot,
    activity_repo: Any,
    llm_repo: LlmRepository,
    **_: Any,
) -> ToolResult:
    target_str = call.arguments.get("target", "")
    label = call.arguments.get("label", "").strip()
    if not label:
        return _err(call.call_id, call.name, "Название образа не указано.")

    target = await _resolve_target(target_str, chat_id=chat_snapshot.telegram_chat_id, activity_repo=activity_repo)
    if target is None:
        return _err(call.call_id, call.name, f"Пользователь '{target_str}' не найден.")

    stored_label = await activity_repo.set_chat_persona_label(
        chat=chat_snapshot,
        user=target,
        persona_label=label,
        granted_by_user_id=actor_snapshot.telegram_user_id,
    )
    action = await llm_repo.add_admin_action(
        chat_id=chat_snapshot.telegram_chat_id,
        admin_user_id=actor_snapshot.telegram_user_id,
        tool_name=call.name,
        action_description=f"Образ [{stored_label}] → {target_str}",
        undo_payload={"tool": "revoke_persona", "target_user_id": target.telegram_user_id, "chat_id": chat_snapshot.telegram_chat_id},
    )
    result = _ok(
        call.call_id, call.name,
        {"ok": True, "target": target_str, "label": stored_label},
        f"Образ [{stored_label}] назначен {target_str}",
        undo={"tool": "revoke_persona", "target_user_id": target.telegram_user_id, "chat_id": chat_snapshot.telegram_chat_id},
    )
    result.db_action_id = action.id
    return result


@register_tool(
    "revoke_persona",
    schema={
        "description": "Снять образ (персонажа) с пользователя.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "@username, образ или user_id"},
            },
            "required": ["target"],
        },
    },
    status_text="Снимаю образ с {target}...",
)
async def _exec_revoke_persona(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    actor_snapshot: UserSnapshot,
    activity_repo: Any,
    llm_repo: LlmRepository,
    **_: Any,
) -> ToolResult:
    target_str = call.arguments.get("target", "")
    target = await _resolve_target(target_str, chat_id=chat_snapshot.telegram_chat_id, activity_repo=activity_repo)
    if target is None:
        return _err(call.call_id, call.name, f"Пользователь '{target_str}' не найден.")

    current_label = await activity_repo.get_chat_persona_label(
        chat_id=chat_snapshot.telegram_chat_id, user_id=target.telegram_user_id,
    )
    removed = await activity_repo.clear_chat_persona_label(
        chat_id=chat_snapshot.telegram_chat_id, user_id=target.telegram_user_id,
    )
    if not removed:
        return _err(call.call_id, call.name, f"У {target_str} нет образа.")

    action = await llm_repo.add_admin_action(
        chat_id=chat_snapshot.telegram_chat_id,
        admin_user_id=actor_snapshot.telegram_user_id,
        tool_name=call.name,
        action_description=f"Образ [{current_label}] снят с {target_str}",
        undo_payload=None,
    )
    result = _ok(
        call.call_id, call.name,
        {"ok": True, "target": target_str, "removed_label": current_label},
        f"Образ [{current_label}] снят с {target_str}",
    )
    result.db_action_id = action.id
    return result


@register_tool(
    "list_members",
    schema={
        "description": (
            "Список участников чата с именами, никами, ролью и активностью. "
            "Используй когда нужно узнать кто есть в чате или найти пользователя по имени."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Максимум записей, сортировка по активности (по умолчанию 50, макс 200)",
                    "default": 50,
                },
            },
        },
    },
    status_text="Загружаю список участников...",
)
async def _exec_list_members(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    activity_repo: Any,
    **_: Any,
) -> ToolResult:
    from sqlalchemy import select, outerjoin
    from selara.infrastructure.db.models import UserChatActivityModel, UserChatBotRoleModel, UserModel

    limit = min(int(call.arguments.get("limit", 50)), 200)
    chat_id = chat_snapshot.telegram_chat_id

    stmt = (
        select(
            UserModel.telegram_user_id,
            UserModel.username,
            UserModel.first_name,
            UserChatActivityModel.persona_label,
            UserChatActivityModel.message_count,
            UserChatBotRoleModel.role.label("bot_role"),
        )
        .join(UserModel, UserModel.telegram_user_id == UserChatActivityModel.user_id)
        .outerjoin(
            UserChatBotRoleModel,
            (UserChatBotRoleModel.chat_id == chat_id)
            & (UserChatBotRoleModel.user_id == UserChatActivityModel.user_id),
        )
        .where(
            UserChatActivityModel.chat_id == chat_id,
            UserChatActivityModel.is_active_member.is_(True),
        )
        .order_by(UserChatActivityModel.message_count.desc())
        .limit(limit)
    )

    rows = (await activity_repo._session.execute(stmt)).all()
    members = [
        {
            "user_id": r.telegram_user_id,
            "username": f"@{r.username}" if r.username else None,
            "first_name": r.first_name,
            "bot_role": r.bot_role or "participant",
            "persona": r.persona_label,
            "message_count": r.message_count,
        }
        for r in rows
    ]
    return _ok(
        call.call_id, call.name,
        {"members": members, "total": len(members)},
        f"Список участников ({len(members)})",
    )


@register_tool(
    "lookup_glossary",
    schema={
        "description": (
            "Найти значение термина или сленга в словаре чата. "
            "Используй перед тем как применять незнакомые понятия (рест, варн, образ и т.д.)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "Термин или слово для поиска"},
            },
            "required": ["term"],
        },
    },
    status_text="Ищу в словаре: {term}...",
)
async def _exec_lookup_glossary(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    llm_repo: LlmRepository,
    **_: Any,
) -> ToolResult:
    term = call.arguments.get("term", "").strip()
    if not term:
        return _err(call.call_id, call.name, "Термин не указан.")

    row = await llm_repo.lookup_glossary_term(chat_id=chat_snapshot.telegram_chat_id, term=term)
    if row is None:
        return _ok(call.call_id, call.name, {"found": False, "term": term}, f"Термин '{term}' не найден")
    return _ok(call.call_id, call.name, {"found": True, "term": row.term, "definition": row.definition}, f"Термин '{term}'")


@register_tool(
    "add_to_glossary",
    schema={
        "description": (
            "Добавить или обновить термин в словаре чата. "
            "Записывай туда специфичный сленг, правила чата, значения команд — всё что может понадобиться в будущем."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "Термин (будет нормализован в нижний регистр)"},
                "definition": {"type": "string", "description": "Определение или описание термина"},
            },
            "required": ["term", "definition"],
        },
    },
    status_text="Записываю в словарь: {term}...",
)
async def _exec_add_to_glossary(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    llm_repo: LlmRepository,
    **_: Any,
) -> ToolResult:
    term = call.arguments.get("term", "").strip()
    definition = call.arguments.get("definition", "").strip()
    if not term or not definition:
        return _err(call.call_id, call.name, "Термин и определение обязательны.")

    row = await llm_repo.upsert_glossary_term(
        chat_id=chat_snapshot.telegram_chat_id,
        term=term,
        definition=definition,
    )
    return _ok(
        call.call_id, call.name,
        {"ok": True, "term": row.term, "definition": row.definition},
        f"Словарь: '{row.term}' записан",
    )


@register_tool(
    "get_history",
    schema={
        "description": "Получить историю предыдущих обращений к AI-ассистенту за период.",
        "parameters": {
            "type": "object",
            "properties": {
                "period_start": {"type": "string", "description": "Начало периода ISO 8601"},
                "period_end": {"type": "string", "description": "Конец периода ISO 8601"},
            },
            "required": ["period_start", "period_end"],
        },
    },
    status_text="Читаю историю за {period_start} — {period_end}...",
)
async def _exec_get_history(
    call: ToolCall,
    *,
    chat_snapshot: ChatSnapshot,
    llm_repo: LlmRepository,
    **_: Any,
) -> ToolResult:
    try:
        period_start = datetime.fromisoformat(call.arguments["period_start"].replace("Z", "+00:00"))
        period_end = datetime.fromisoformat(call.arguments["period_end"].replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        return _err(call.call_id, call.name, f"Неверный формат дат: {exc}")

    summaries = await llm_repo.get_summaries_in_range(
        chat_id=chat_snapshot.telegram_chat_id,
        period_start=period_start,
        period_end=period_end,
    )
    raw_msgs = await llm_repo.get_all_messages_in_range(
        chat_id=chat_snapshot.telegram_chat_id,
        period_start=period_start,
        period_end=period_end,
    )

    result_parts: list[str] = []
    for s in summaries:
        result_parts.append(f"[Сводка {s.period_start} — {s.period_end}]: {s.content}")
    for m in raw_msgs:
        result_parts.append(f"[{m.role}] {m.created_at}: {m.content[:200]}")

    return _ok(
        call.call_id, call.name,
        {"history": result_parts},
        f"История за {call.arguments.get('period_start')} — {call.arguments.get('period_end')}",
    )


@register_tool(
    "list_bot_docs",
    schema={
        "description": "Получить список доступных технических документов и руководств по возможностям и работе AI-ассистента.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    status_text="Загружаю список руководств...",
)
async def _exec_list_bot_docs(
    call: ToolCall,
    **_: Any,
) -> ToolResult:
    if not os.path.exists(_BOT_DOCS_DIR):
        return _ok(call.call_id, call.name, {"docs": [], "count": 0}, "Документов нет.")

    docs_list = []
    for filename in sorted(os.listdir(_BOT_DOCS_DIR)):
        if filename.endswith(".md"):
            filepath = os.path.join(_BOT_DOCS_DIR, filename)
            title = filename
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line.startswith("#"):
                        title = first_line.lstrip("#").strip()
            except Exception:
                pass
            docs_list.append({"filename": filename, "title": title})

    return _ok(
        call.call_id, call.name,
        {"docs": docs_list, "count": len(docs_list)},
        f"Список документов ({len(docs_list)})",
    )


@register_tool(
    "read_bot_doc",
    schema={
        "description": "Прочитать содержимое конкретного технического документа или руководства.",
        "parameters": {
            "type": "object",
            "properties": {
                "doc_name": {"type": "string", "description": "Имя файла документа (например: moderation.md)"},
            },
            "required": ["doc_name"],
        },
    },
    status_text="Читаю документ {doc_name}...",
)
async def _exec_read_bot_doc(
    call: ToolCall,
    **_: Any,
) -> ToolResult:
    doc_name = call.arguments.get("doc_name", "").strip()
    if not doc_name:
        return _err(call.call_id, call.name, "Имя документа не указано.")

    # Защита от path traversal
    normalized_name = os.path.basename(doc_name)
    if normalized_name != doc_name or doc_name.startswith("..") or "/" in doc_name or "\\" in doc_name:
        return _err(call.call_id, call.name, "Недопустимый путь к файлу.")

    filepath = os.path.join(_BOT_DOCS_DIR, normalized_name)
    if not os.path.exists(filepath):
        return _err(call.call_id, call.name, f"Документ '{doc_name}' не найден.")

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as exc:
        return _err(call.call_id, call.name, f"Не удалось прочитать документ: {exc}")

    return _ok(
        call.call_id, call.name,
        {"doc_name": doc_name, "content": content},
        f"Документ {doc_name} прочитан",
    )
