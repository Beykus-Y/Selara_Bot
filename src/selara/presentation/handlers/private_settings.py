"""Thematic group settings opened from the private chat ("Управление группой").

The flat paged menu (pm:as / pm:ae / pm:av / pm:ai) stays reachable as
"Расширенные настройки". This module only adds the grouped entry point; it reuses
the same validators, storage and permission checks as the flat menu, and every
action re-checks admin rights at the moment it runs.
"""

from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from selara.core.chat_settings import CHAT_SETTINGS_KEYS
from selara.core.config import Settings
from selara.domain.entities import ChatSnapshot, UserChatOverview
from selara.presentation.handlers.private_panel import (
    _clear_pending_cfg_input,
    _edit_or_answer,
    _ensure_manage_settings,
    _load_chat_settings,
    _resolve_admin_chat_overview,
    _safe_int,
    _set_pending_cfg_input,
    encode_pm_callback,
)
from selara.presentation.handlers.settings_common import (
    CFG_BOOL_KEYS,
    CFG_ENUM_VALUES,
    apply_setting_update,
    render_setting_editor_text,
    setting_short_ru,
    settings_to_dict,
)
from selara.presentation.navigation.contract import BACK_LABEL, CANCEL_LABEL, HOME_LABEL, safe_callback
from selara.presentation.navigation.settings_groups import (
    SETTINGS_CATEGORIES,
    category_index_for_key,
)

router = Router(name="private_settings")

_ROUTE_PREFIX = "pms"
_LABEL_MAX = 32
_ROW_WIDTH = 2


def _label(text: str) -> str:
    return text if len(text) <= _LABEL_MAX else f"{text[: _LABEL_MAX - 3]}..."


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _chunk_rows(buttons: list[InlineKeyboardButton], width: int) -> list[list[InlineKeyboardButton]]:
    return [buttons[start : start + width] for start in range(0, len(buttons), width)]


def _home_data(chat_id: int) -> str:
    return safe_callback(_ROUTE_PREFIX, "h", str(chat_id))


def _category_data(chat_id: int, category_idx: int) -> str:
    return safe_callback(_ROUTE_PREFIX, "c", str(chat_id), str(category_idx))


def _key_data(chat_id: int, key_idx: int) -> str:
    return safe_callback(_ROUTE_PREFIX, "k", str(chat_id), str(key_idx))


def _value_data(chat_id: int, key_idx: int, token: str) -> str:
    return safe_callback(_ROUTE_PREFIX, "v", str(chat_id), str(key_idx), token)


def _input_data(chat_id: int, key_idx: int) -> str:
    return safe_callback(_ROUTE_PREFIX, "i", str(chat_id), str(key_idx))


def _key_by_index(raw_idx: str | None) -> str | None:
    idx = _safe_int(raw_idx)
    if idx is None or not 0 <= idx < len(CHAT_SETTINGS_KEYS):
        return None
    return CHAT_SETTINGS_KEYS[idx]


def _build_home_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    buttons = [
        _button(category.title, _category_data(chat_id, idx)) for idx, category in enumerate(SETTINGS_CATEGORIES)
    ]
    rows = _chunk_rows(buttons, 1)
    rows.append([_button("🧰 Расширенные настройки", encode_pm_callback("as", chat_id, 0))])
    rows.append([
        _button(BACK_LABEL, encode_pm_callback("ag", chat_id)),
        _button(HOME_LABEL, encode_pm_callback("h")),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_category_keyboard(chat_id: int, category_idx: int) -> InlineKeyboardMarkup:
    category = SETTINGS_CATEGORIES[category_idx]
    buttons = [
        _button(_label(setting_short_ru(key)), _key_data(chat_id, CHAT_SETTINGS_KEYS.index(key)))
        for key in category.settings
    ]
    rows = _chunk_rows(buttons, _ROW_WIDTH)
    rows.append([
        _button(BACK_LABEL, _home_data(chat_id)),
        _button(HOME_LABEL, encode_pm_callback("h")),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_key_keyboard(*, chat_id: int, key: str, current_value: object) -> InlineKeyboardMarkup:
    key_idx = CHAT_SETTINGS_KEYS.index(key)
    category_idx = category_index_for_key(key)
    buttons: list[InlineKeyboardButton] = []
    if key in CFG_BOOL_KEYS:
        current = bool(current_value)
        buttons.append(_button(
            f"true {'✓' if current else ''}".strip(),
            _value_data(chat_id, key_idx, "t"),
        ))
        buttons.append(_button(
            f"false {'✓' if not current else ''}".strip(),
            _value_data(chat_id, key_idx, "f"),
        ))
        rows = _chunk_rows(buttons, 2)
    elif key in CFG_ENUM_VALUES:
        rows = []
        for variant_idx, item in enumerate(CFG_ENUM_VALUES[key]):
            label = {"global": "global (общий)", "local": "local (по группе)"}.get(item, item)
            marker = " ✓" if str(current_value) == item else ""
            rows.append([_button(f"{label}{marker}", _value_data(chat_id, key_idx, f"e{variant_idx}"))])
    else:
        rows = [[_button("⌨️ Ввести значение", _input_data(chat_id, key_idx))]]

    rows.append([_button("↩️ default", _value_data(chat_id, key_idx, "d"))])
    rows.append([
        _button(BACK_LABEL, _category_data(chat_id, category_idx)),
        _button(HOME_LABEL, encode_pm_callback("h")),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_input_keyboard(*, chat_id: int, key: str) -> InlineKeyboardMarkup:
    key_idx = CHAT_SETTINGS_KEYS.index(key)
    return InlineKeyboardMarkup(inline_keyboard=[
        [_button(CANCEL_LABEL, _key_data(chat_id, key_idx))],
    ])


def _render_home_text(overview: UserChatOverview) -> str:
    return (
        "<b>Управление группой</b>\n"
        f"Группа: <b>{escape(overview.chat_title or 'без названия')}</b>\n"
        f"ID: <code>{overview.chat_id}</code>\n\n"
        "Выберите раздел. Изменения применяются сразу.\n"
        "Настроить группу через ИИ можно командой /autocfg в этом чате."
    )


def _render_category_text(chat_id: int, category_idx: int) -> str:
    category = SETTINGS_CATEGORIES[category_idx]
    return (
        f"<b>{escape(category.title)}</b>\n"
        f"{escape(category.summary)}\n\n"
        f"Группа: <code>{chat_id}</code>\n"
        "Выберите настройку."
    )


async def _authorize_group(activity_repo, query: CallbackQuery, chat_id: int) -> UserChatOverview | None:
    overview = await _resolve_admin_chat_overview(activity_repo, user_id=query.from_user.id, chat_id=chat_id)
    if overview is None:
        await query.answer("Группа недоступна", show_alert=True)
        return None
    if not await _ensure_manage_settings(activity_repo, user=query.from_user, chat_id=chat_id):
        await query.answer("Недостаточно прав", show_alert=True)
        return None
    return overview


async def _render_key_screen(query: CallbackQuery, activity_repo, settings: Settings, *, chat_id: int, key: str) -> None:
    current, _ = await _load_chat_settings(activity_repo, settings, chat_id=chat_id)
    current_value = settings_to_dict(current)[key]
    await _edit_or_answer(
        query,
        render_setting_editor_text(chat_id=chat_id, key=key, current_value=current_value),
        _build_key_keyboard(chat_id=chat_id, key=key, current_value=current_value),
    )


@router.callback_query(F.data.startswith(f"{_ROUTE_PREFIX}:"))
async def private_settings_callback(query: CallbackQuery, activity_repo, settings: Settings) -> None:
    if query.from_user is None:
        await query.answer()
        return
    if query.message is None or query.message.chat.type != "private":
        await query.answer("Доступно только в ЛС", show_alert=True)
        return

    parts = (query.data or "").split(":")
    if len(parts) < 3:
        await query.answer("Некорректная кнопка", show_alert=False)
        return
    route = parts[1]
    chat_id = _safe_int(parts[2])
    if chat_id is None:
        await query.answer("Некорректный чат", show_alert=True)
        return

    overview = await _authorize_group(activity_repo, query, chat_id)
    if overview is None:
        return

    # Pending free-text input belongs to the input screen only; any other
    # action leaves that screen, so it must not swallow the next message.
    if route != "i":
        _clear_pending_cfg_input(query.from_user.id)

    if route == "h":
        await _edit_or_answer(query, _render_home_text(overview), _build_home_keyboard(chat_id))
        return

    if route == "c":
        category_idx = _safe_int(parts[3] if len(parts) >= 4 else None)
        if category_idx is None or not 0 <= category_idx < len(SETTINGS_CATEGORIES):
            await query.answer("Некорректный раздел", show_alert=True)
            return
        await _edit_or_answer(
            query,
            _render_category_text(chat_id, category_idx),
            _build_category_keyboard(chat_id, category_idx),
        )
        return

    key = _key_by_index(parts[3] if len(parts) >= 4 else None)
    if key is None:
        await query.answer("Некорректная настройка", show_alert=True)
        return

    if route == "k":
        await _render_key_screen(query, activity_repo, settings, chat_id=chat_id, key=key)
        return

    if route == "i":
        if key in CFG_BOOL_KEYS or key in CFG_ENUM_VALUES:
            await query.answer("Для этой настройки есть готовые варианты", show_alert=True)
            return
        _set_pending_cfg_input(user_id=query.from_user.id, chat_id=chat_id, key=key)
        await _edit_or_answer(
            query,
            (
                "<b>Ожидаю ввод значения</b>\n"
                f"Группа: <code>{chat_id}</code>\n"
                f"Настройка: <b>{escape(setting_short_ru(key))}</b>\n"
                "Отправьте следующее сообщение в ЛС.\n"
                "Для отмены: <code>/cancel</code>."
            ),
            _build_input_keyboard(chat_id=chat_id, key=key),
        )
        return

    if route == "v":
        token = parts[4] if len(parts) >= 5 else ""
        raw_value = _resolve_token(key, token)
        if raw_value is None:
            await query.answer("Неверное значение", show_alert=True)
            return
        current, defaults = await _load_chat_settings(activity_repo, settings, chat_id=chat_id)
        updated_values, error = apply_setting_update(
            key=key,
            raw_value=raw_value,
            current=settings_to_dict(current),
            defaults=settings_to_dict(defaults),
        )
        if error is not None or updated_values is None:
            await query.answer(error or "Не удалось изменить настройку", show_alert=True)
            return
        await activity_repo.upsert_chat_settings(
            chat=ChatSnapshot(
                telegram_chat_id=chat_id,
                chat_type=overview.chat_type,
                title=overview.chat_title,
            ),
            values=updated_values,
        )
        await _render_key_screen(query, activity_repo, settings, chat_id=chat_id, key=key)
        return

    await query.answer("Неизвестное действие", show_alert=False)


def _resolve_token(key: str, token: str) -> str | None:
    """Map a callback token back to the raw value; tokens are indexes, not free text."""
    if token == "d":
        return "default"
    if key in CFG_BOOL_KEYS:
        return {"t": "true", "f": "false"}.get(token)
    if key in CFG_ENUM_VALUES and token.startswith("e"):
        variant_idx = _safe_int(token[1:])
        variants = CFG_ENUM_VALUES[key]
        if variant_idx is not None and 0 <= variant_idx < len(variants):
            return variants[variant_idx]
    return None
