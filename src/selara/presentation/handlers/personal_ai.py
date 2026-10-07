"""Personal AI in private chats: settings wizard (/ai), reset (/ai_reset) and the dialogue itself."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, Filter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, LinkPreviewOptions, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.ai_character import (
    CHARACTER_PRESETS,
    CUSTOM_PRESET_KEY,
    MAX_ADDRESS_LENGTH,
    MAX_CUSTOM_CHARACTER_LENGTH,
    MAX_DISPLAY_NAME_LENGTH,
    ProfileValidationError,
    preset_title,
    validate_address,
    validate_custom_character,
    validate_display_name,
)
from selara.application.feature_access import (
    AIL_UNIT,
    AccessReason,
    AccessTier,
    FeatureAccessService,
    QuotaScope,
    ail_units_from_cost_usd,
    message_idempotency_key,
)
from selara.application.model_catalog import PROFILE_EMOJI
from selara.application.personal_config import PersonalConfig, PersonalConfigProvider
from selara.application.personal_memory import parse_remember_request
from selara.application.personal_models import (
    PersonalModelChoice,
    choose_from_snapshot,
    format_ail,
    is_profile_key,
    load_snapshot,
    profile_display_name,
    profile_options,
)
from selara.core.config import Settings
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.db.personal_ai_repository import PersonalAiRepository, StoredProfile
from selara.infrastructure.db.telegram_stars import SqlAlchemyUserEntitlementResolver
from selara.infrastructure.db.artifact_repository import ArtifactRepository
from selara.infrastructure.llm.artifact_tools import ArtifactRequestContext
from selara.infrastructure.llm.client import LlmAccountingContext, LlmClient, LlmClientError
from selara.infrastructure.llm.features import AiFeature
from selara.infrastructure.llm.personal_tools import PersonalToolRun
from selara.infrastructure.llm.web_tools import WebToolContext
from selara.infrastructure.llm.personal_ai import (
    MAX_USER_TEXT_LENGTH,
    generate_reply,
    maybe_compress_personal,
    maybe_extract_memories,
)
from selara.presentation.auth import resolve_owner_private_exemption
from selara.presentation.feature_access_messages import (
    ail_insufficient_message,
    daily_reset_text,
    quota_exhausted_message,
)
from selara.presentation.handlers import personal_memory
from selara.presentation.handlers.premium import personal_offer_available
from selara.presentation.handlers.private_panel import _get_pending_admin_input, _get_pending_cfg_input
from selara.presentation.llm_formatting import html_to_plain_text, render_llm_html

log = logging.getLogger(__name__)

router = Router(name="personal_ai")
# Included after text_commands: it only receives private text that no text command recognised.
chat_router = Router(name="personal_ai_chat")

_PENDING_TTL = timedelta(minutes=10)
_UNAVAILABLE_TEXT = "Selara AI в личных сообщениях сейчас недоступен. Попробуйте позже."
_ACCESS_ERROR_TEXT = "⚠️ Проверка доступа временно недоступна. Попробуйте позже."
_LENGTH_TITLES = {"short": "короткие", "medium": "средние", "long": "подробные"}
_INPUT_PROMPTS = {
    "name": f"Пришлите новое имя (до {MAX_DISPLAY_NAME_LENGTH} символов).",
    "custom": f"Опишите характер своими словами (до {MAX_CUSTOM_CHARACTER_LENGTH} символов).",
    "address": f"Как к вам обращаться? Имя или прозвище (до {MAX_ADDRESS_LENGTH} символов).",
}


@dataclass(frozen=True, slots=True)
class _PendingInput:
    field: str
    expires_at: datetime


# In-memory like the other private-panel inputs: losing it on restart only asks the user to press the button again.
_pending_inputs: dict[int, _PendingInput] = {}


def _get_pending_input(user_id: int) -> _PendingInput | None:
    state = _pending_inputs.get(user_id)
    if state is not None and state.expires_at <= datetime.now(timezone.utc):
        _pending_inputs.pop(user_id, None)
        return None
    return state


def _set_pending_input(user_id: int, field: str) -> None:
    _pending_inputs[user_id] = _PendingInput(field=field, expires_at=datetime.now(timezone.utc) + _PENDING_TTL)


# --- filters -----------------------------------------------------------------


class PendingPersonalInputFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        if message.chat.type != "private" or message.from_user is None or not message.text:
            return False
        if message.text.lstrip().startswith("/"):
            return False
        return _get_pending_input(message.from_user.id) is not None


class PersonalChatFilter(Filter):
    """A plain private text that nothing else is waiting for or understands as a command."""

    async def __call__(self, message: Message) -> bool:
        if message.chat.type != "private" or message.from_user is None or message.from_user.is_bot:
            return False
        if getattr(message, "successful_payment", None) is not None:
            return False
        text = message.text
        if not text or not text.strip() or text.lstrip().startswith("/"):
            return False
        user_id = message.from_user.id
        # Expected input of other private flows wins over the dialogue.
        if _get_pending_cfg_input(user_id) is not None or _get_pending_admin_input(user_id) is not None:
            return False
        if _get_pending_input(user_id) is not None:
            return False
        # Text commands are not filtered here: this router sits after text_commands, which hands over
        # (SkipHandler) only the private text it did not recognise itself.
        return True


# --- settings wizard -----------------------------------------------------------


def _cb(action: str, *parts: object) -> str:
    return ":".join(["pai", action, *(str(part) for part in parts)])


@dataclass(frozen=True, slots=True)
class _ModelDeps:
    """What the settings screens need to show and change the model profile; optional in tests."""

    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    personal_config: PersonalConfigProvider
    llm_client: Any = None
    # The server has a web search client wired in (the web tool switch is pointless without it).
    web_available: bool = False

    @property
    def catalog(self):
        return getattr(self.llm_client, "model_catalog", None)

    @property
    def legacy_model(self) -> str:
        return getattr(self.llm_client, "default_model", None) or self.settings.llm_model


def _model_deps(settings, session_factory, personal_config, llm_client, web_search_client=None) -> _ModelDeps | None:
    if settings is None or session_factory is None or personal_config is None:
        return None
    return _ModelDeps(settings, session_factory, personal_config, llm_client, web_available=web_search_client is not None)


def _access_service(session_factory, personal_config: PersonalConfigProvider) -> FeatureAccessService:
    return FeatureAccessService(
        SqlAlchemyFeatureQuotaRepository(session_factory),
        user_entitlement_resolver=SqlAlchemyUserEntitlementResolver(session_factory, personal_config),
        personal_config=personal_config,
    )


def chat_turn_cost_usd(usages) -> Decimal | None:
    """Real cost of the chat turn's successful provider calls, or ``None`` if any of them is unpriced."""
    succeeded = [usage for usage in usages if usage.status == "succeeded"]
    if not succeeded or any(usage.estimated_cost_usd is None for usage in succeeded):
        return None
    return sum((Decimal(usage.estimated_cost_usd) for usage in succeeded), Decimal(0))


def failed_turn_cost_usd(usages) -> Decimal | None:
    """Cost of a turn that produced no answer: what its priced responses cost, zero if the provider only failed.

    Timeouts and dropped connections count as zero too (the provider rarely bills them), so a failure never keeps
    the whole reservation; it settles at the 0.01 AIL minimum. ``None`` (reservation stays) only when a
    response came back without any price.
    """
    answered = [usage for usage in usages if usage.status != "failed"]
    if any(usage.estimated_cost_usd is None for usage in answered):
        return None
    return sum((Decimal(usage.estimated_cost_usd) for usage in answered), Decimal(0))


_TOOLS_RESERVE_HINT = (
    "\n\nЗапрос с инструментами резервирует больше AIL. Их можно выключить: /ai → Поведение и настройки → Инструменты."
)


async def _tool_flags(user_id: int, stored: StoredProfile, choice, deps: _ModelDeps | None) -> tuple[bool, bool] | None:
    """(web, artifacts) for this turn, or ``None`` when it runs without tools.

    Read from the database on every turn: switched off means gone from the next message. Tools need an active
    Selara Personal, the assistant mode (never role play) and a model that supports them.
    """
    if stored.profile.mode != "assistant" or deps is None:
        return None
    web_on = bool(stored.tools_web_enabled and deps.web_available)
    artifacts_on = bool(stored.tools_artifacts_enabled)
    if not (web_on or artifacts_on):
        return None
    capabilities = getattr(getattr(choice, "effective", None), "capabilities", None)
    if capabilities is not None and not capabilities.supports_tools:
        return None
    if not await _has_personal(user_id, deps):
        return None
    return web_on, artifacts_on


async def _settle_chat_turn(
    access_service, *, config, invocation_id, usages, user_id: int, failed: bool = False,
    max_units: Decimal | None = None,
) -> None:
    """Replace the multiplier reservation by the request's actual cost; never fails the answer."""
    if invocation_id is None:
        return
    cost = failed_turn_cost_usd(usages) if failed else chat_turn_cost_usd(usages)
    if cost is None:
        # No provider cost and no catalog price: the reservation (profile multiplier) stays as the charge.
        log.warning("personal_ai: chat turn cost unknown, keeping reservation invocation_id=%s", invocation_id)
        return
    try:
        units = ail_units_from_cost_usd(cost, config.ail_usd_value)
        if max_units is not None:
            # A tool turn is never charged above what its reservation (checked against the balance) covered.
            units = min(units, max_units)
        await access_service.adjust(invocation_id=invocation_id, actual_units=units)
    except Exception:
        log.exception("personal_ai: AIL settlement failed user_id=%s invocation_id=%s", user_id, invocation_id)


async def _model_lines(user_id: int, stored: StoredProfile, deps: _ModelDeps | None) -> tuple[list[str], str]:
    """Model and AIL status lines for /ai, plus the label of the model button."""
    if deps is None:
        return [], "Модель"
    config = await deps.personal_config.get()
    snapshot = await load_snapshot(deps.catalog)
    if not config.ail_enabled:
        # Requests mode answers with the base model whatever was picked earlier; the pick is kept for AIL.
        lines = [f"Модель: {PROFILE_EMOJI['basic']} {escape(profile_display_name(snapshot, 'basic'))}"]
        if stored.model_profile_key != "basic":
            lines.append(
                f"Сохранённый выбор «{escape(profile_display_name(snapshot, stored.model_profile_key))}» "
                "включится вместе с AI Limits."
            )
        else:
            lines.append("Выбор моделей станет доступен после включения AI Limits.")
        return lines, "Модель"
    choice = choose_from_snapshot(snapshot, selected_key=stored.model_profile_key, legacy_model=deps.legacy_model)
    emoji = PROFILE_EMOJI.get(choice.profile_key, "")
    lines = [f"Модель: {emoji} {escape(choice.display_name)}"]
    if choice.fell_back:
        lines.append("Выбранный профиль сейчас недоступен, используется Базовая модель.")
    settles_actual = config.ail_settles_actual_cost
    if settles_actual:
        lines.append(
            f"Резерв на запрос: {format_ail(choice.ail_cost)} AIL (списывается по фактической стоимости ответа)"
        )
    else:
        lines.append(f"Стоимость запроса: {format_ail(choice.ail_cost)} AIL")
    access = _access_service(deps.session_factory, deps.personal_config)
    try:
        summary = await access.get_usage_summary(
            feature=AiFeature.PERSONAL_CHAT,
            chat_id=user_id,
            trigger="telegram_message",
            timezone_name=deps.settings.bot_timezone,
            scope=QuotaScope.user(user_id),
            owner_exempt=resolve_owner_private_exemption(user_id=user_id, admin_user_id=deps.settings.admin_user_id),
        )
    except Exception:
        log.warning("personal_ai: AIL usage summary unavailable user_id=%s", user_id, exc_info=True)
        return lines, f"Модель: {choice.display_name}"
    if summary.unlimited:
        lines.append("Осталось сегодня: без ограничений")
    elif summary.quota_unit == AIL_UNIT:
        lines.append(
            f"Осталось сегодня: {format_ail(summary.quota_remaining)} / {format_ail(summary.quota_limit)} AIL"
            f" (обновится {daily_reset_text(summary.reset_at, timezone_name=deps.settings.bot_timezone)})"
        )
    if settles_actual:
        try:
            last = await access.last_ail_charge(user_id)
        except Exception:
            log.warning("personal_ai: last AIL charge unavailable user_id=%s", user_id, exc_info=True)
            last = None
        if last is not None:
            lines.append(f"Последний запрос: {format_ail(last)} AIL")
    return lines, f"Модель: {choice.display_name}"


def _tools_summary(stored: StoredProfile) -> str:
    parts = [
        f"веб-поиск {'вкл' if stored.tools_web_enabled else 'выкл'}",
        f"артефакты {'вкл' if stored.tools_artifacts_enabled else 'выкл'}",
    ]
    return ", ".join(parts)


def _profile_text(stored: StoredProfile, model_lines: list[str] | None = None) -> str:
    p = stored.profile
    character = escape(p.character_custom) if p.character_preset == CUSTOM_PRESET_KEY and p.character_custom else escape(preset_title(p.character_preset))
    lines = [
        "<b>Моя Selara</b>",
        "",
        f"Имя: <b>{escape(p.display_name)}</b>",
        f"Характер: {character}",
        f"Обращение: {escape(p.address_form) if p.address_form else 'по умолчанию'}, на «{'вы' if p.formality == 'vy' else 'ты'}»",
        f"Ответы: {_LENGTH_TITLES.get(p.reply_length, p.reply_length)}, эмодзи {'да' if p.emoji_enabled else 'нет'}",
        f"Режим: {'ролевая игра' if p.mode == 'roleplay' else 'помощник'}",
        f"Память: {'вкл' if stored.memory_enabled else 'выкл'}, авто-запоминание (только Selara Personal): {'вкл' if stored.auto_memory_enabled else 'выкл'}",
        f"Инструменты (только Selara Personal): {_tools_summary(stored)}",
        *(model_lines or []),
        "",
        "Просто напишите мне сообщение, и я отвечу. /ai_reset — начать диалог заново, /memory — что я о вас помню, "
        "/forget_all — удалить все личные данные.",
    ]
    if p.mode == "roleplay":
        lines.append("В ролевой игре у диалога своя отдельная история; сцену и роли задайте в обычном сообщении.")
    return "\n".join(lines)


def _main_keyboard(stored: StoredProfile, model_label: str = "Модель") -> InlineKeyboardMarkup:
    rev = stored.revision
    builder = InlineKeyboardBuilder()
    builder.button(text="🎭 Персонаж", callback_data=_cb("cat", "character", rev))
    builder.button(text="⚙️ Поведение и настройки", callback_data=_cb("cat", "behavior", rev))
    builder.button(text="🧠 Память", callback_data=_cb("cat", "memory", rev))
    builder.button(text=f"🤖 {model_label}", callback_data=_cb("models", rev))
    builder.button(text="Закрыть", callback_data=_cb("close"))
    builder.adjust(2, 2, 1)
    return builder.as_markup()


_CATEGORIES = ("character", "behavior", "memory", "tools")
_CATEGORY_TITLES = {
    "character": "🎭 Персонаж",
    "behavior": "⚙️ Поведение и настройки",
    "memory": "🧠 Память",
    "tools": "🛠 Инструменты",
}
# Callback value -> profile column for the switches a category screen can flip (``sc`` actions).
_TOOL_SWITCHES = {"tweb": "tools_web_enabled", "tart": "tools_artifacts_enabled"}


@dataclass(frozen=True, slots=True)
class _ToolsView:
    """What the tools screen needs to know about the person and the server."""

    has_personal: bool = False
    web_available: bool = False


def _switch_text(label: str, on: bool, locked: bool) -> str:
    return f"{'🔒 ' if locked else ''}{label}: {'вкл' if on else 'выкл'}"


async def _has_personal(user_id: int, deps: _ModelDeps | None) -> bool:
    """True while the person holds an active Selara Personal entitlement (tools are a paid feature)."""
    if deps is None:
        return False
    if deps.settings.admin_user_id is not None and user_id == deps.settings.admin_user_id:
        return True  # the owner's internal access, like the Mini App
    resolver = SqlAlchemyUserEntitlementResolver(deps.session_factory, deps.personal_config)
    try:
        entitlement = await resolver.resolve(user_id=user_id, feature=AiFeature.PERSONAL_CHAT, trigger="tools_menu")
    except Exception:
        return False
    if entitlement.access_tier != AccessTier.PAID:
        return False
    return entitlement.valid_until is None or entitlement.valid_until > datetime.now(timezone.utc)


async def _tools_view(user_id: int, deps: _ModelDeps | None) -> _ToolsView:
    return _ToolsView(has_personal=await _has_personal(user_id, deps), web_available=bool(deps and deps.web_available))


def _category_screen(
    category: str, stored: StoredProfile, tools: _ToolsView | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    p, rev = stored.profile, stored.revision
    builder = InlineKeyboardBuilder()
    if category == "character":
        text = "\n".join(
            [
                "<b>Персонаж</b>",
                f"Имя: <b>{escape(p.display_name)}</b>",
                "Характер: "
                + (
                    escape(p.character_custom)
                    if p.character_preset == CUSTOM_PRESET_KEY and p.character_custom
                    else escape(preset_title(p.character_preset))
                ),
                f"Обращение: {escape(p.address_form) if p.address_form else 'по умолчанию'}, на «{'вы' if p.formality == 'vy' else 'ты'}»",
            ]
        )
        builder.button(text="Имя", callback_data=_cb("in", "name", rev))
        builder.button(text="Характер", callback_data=_cb("presets", rev))
        builder.button(text="Обращение", callback_data=_cb("in", "address", rev))
        builder.button(
            text=f"Ты/вы: {'вы' if p.formality == 'vy' else 'ты'}",
            callback_data=_cb("sc", category, "formality", "ty" if p.formality == "vy" else "vy", rev),
        )
        sizes: tuple[int, ...] = (2, 2)
    elif category == "behavior":
        text = "\n".join(
            [
                "<b>Поведение и настройки</b>",
                f"Ответы: {_LENGTH_TITLES.get(p.reply_length, p.reply_length)}, эмодзи {'да' if p.emoji_enabled else 'нет'}",
                f"Режим: {'ролевая игра' if p.mode == 'roleplay' else 'помощник'}",
                f"Инструменты: {_tools_summary(stored)}",
            ]
        )
        next_length = {"short": "medium", "medium": "long", "long": "short"}[p.reply_length]
        builder.button(
            text=f"Длина: {_LENGTH_TITLES[p.reply_length]}",
            callback_data=_cb("sc", category, "length", next_length, rev),
        )
        builder.button(
            text=f"Эмодзи: {'вкл' if p.emoji_enabled else 'выкл'}",
            callback_data=_cb("sc", category, "emoji", 0 if p.emoji_enabled else 1, rev),
        )
        builder.button(
            text="Режим: " + ("ролевая игра" if p.mode == "roleplay" else "помощник"),
            callback_data=_cb("sc", category, "mode", "assistant" if p.mode == "roleplay" else "roleplay", rev),
        )
        builder.button(text="🛠 Инструменты", callback_data=_cb("cat", "tools", rev))
        sizes = (2, 1, 1)
    elif category == "memory":
        text = "\n".join(
            [
                "<b>Память</b>",
                f"Память: {'вкл' if stored.memory_enabled else 'выкл'}",
                f"Авто-запоминание (только Selara Personal): {'вкл' if stored.auto_memory_enabled else 'выкл'}",
                "/memory — что я о вас помню, /forget_all — удалить все личные данные.",
            ]
        )
        builder.button(
            text=f"Память: {'вкл' if stored.memory_enabled else 'выкл'}",
            callback_data=_cb("sc", category, "memory", 0 if stored.memory_enabled else 1, rev),
        )
        builder.button(
            text=f"Авто-память (Personal): {'вкл' if stored.auto_memory_enabled else 'выкл'}",
            callback_data=_cb("sc", category, "automemory", 0 if stored.auto_memory_enabled else 1, rev),
        )
        sizes = (1,)
    else:
        view = tools or _ToolsView()
        locked = not view.has_personal
        lines = [
            "<b>Инструменты</b>",
            "Всё выключено, пока вы сами не включите. Инструменты делают ответ дольше и дороже: запрос тратит больше AIL, "
            "но не больше заранее зарезервированного.",
            "• <b>Веб-поиск</b> — ищу в интернете и читаю страницы. После результатов поиска других инструментов в этом запросе нет.",
            "• <b>Артефакты</b> — таблицы, схемы и инфографика картинкой.",
        ]
        if locked:
            lines.append("🔒 Нужна подписка Selara Personal: пока её нет, инструменты не работают (настройки сохраняются).")
        if not view.web_available:
            lines.append("Веб-поиск на этом сервере не подключён.")
        if p.mode == "roleplay":
            lines.append("В ролевой игре инструменты недоступны.")
        text = "\n".join(lines)
        builder.button(
            text=_switch_text("Веб-поиск", stored.tools_web_enabled, locked),
            callback_data=_cb("sc", category, "tweb", 0 if stored.tools_web_enabled else 1, rev),
        )
        builder.button(
            text=_switch_text("Артефакты", stored.tools_artifacts_enabled, locked),
            callback_data=_cb("sc", category, "tart", 0 if stored.tools_artifacts_enabled else 1, rev),
        )
        sizes = (1,)
    builder.button(
        text="Назад", callback_data=_cb("cat", "behavior", rev) if category == "tools" else _cb("home")
    )
    builder.adjust(*sizes, 1)
    return text, builder.as_markup()


async def _models_screen(stored: StoredProfile, deps: _ModelDeps) -> tuple[str, InlineKeyboardMarkup]:
    config = await deps.personal_config.get()
    options = profile_options(await load_snapshot(deps.catalog), legacy_model=deps.legacy_model)
    lines = ["<b>Модель ответа</b>", ""]
    builder = InlineKeyboardBuilder()
    for option in options:
        cost = (
            f"резерв {format_ail(option.ail_multiplier)} AIL"
            if config.ail_settles_actual_cost
            else f"×{format_ail(option.ail_multiplier)} AIL"
        )
        state = "" if option.available else " — сейчас недоступна"
        lines.append(f"{option.emoji} <b>{escape(option.display_name)}</b> — {cost}{state}")
        lines.append(escape(option.description))
        lines.append("")
        if config.ail_enabled and option.available:
            marker = "✅ " if option.profile_key == stored.model_profile_key else ""
            builder.button(
                text=f"{marker}{option.emoji} {option.display_name} · {cost}",
                callback_data=_cb("model", option.profile_key, stored.revision),
            )
    if config.ail_settles_actual_cost:
        lines.append(
            "Каждый запрос списывает из суточного бюджета AI Limits столько AIL, сколько стоил на самом деле: "
            "короткий ответ дешевле, длинный дороже. Для старта запроса на балансе нужен резерв профиля."
        )
    elif config.ail_enabled:
        lines.append("Каждый запрос списывает из суточного бюджета AI Limits столько AIL, сколько стоит модель.")
    else:
        lines.append(
            "Сейчас каждый запрос считается как один из суточного лимита, и ответы даёт базовая модель. "
            "Выбор моделей станет доступен после включения AI Limits."
        )
    builder.button(text="Назад", callback_data=_cb("home"))
    builder.adjust(1)
    return "\n".join(lines).rstrip(), builder.as_markup()


def _presets_keyboard(stored: StoredProfile) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, (title, _) in CHARACTER_PRESETS.items():
        marker = "✅ " if stored.profile.character_preset == key else ""
        builder.button(text=marker + title, callback_data=_cb("preset", key, stored.revision))
    marker = "✅ " if stored.profile.character_preset == CUSTOM_PRESET_KEY else ""
    builder.button(text=marker + "Свой вариант", callback_data=_cb("in", "custom", stored.revision))
    builder.button(text="Назад", callback_data=_cb("home"))
    builder.adjust(1)
    return builder.as_markup()


async def _edit(query: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None) -> None:
    if query.message is None:
        return
    try:
        await query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except TelegramBadRequest:
        # "message is not modified" and stale messages are harmless.
        pass


async def _show_home(
    query: CallbackQuery, repo: PersonalAiRepository, notice: str | None = None, deps: _ModelDeps | None = None
) -> None:
    stored = await repo.get_or_create_profile(query.from_user.id)
    # The usage summary runs in its own transaction: do not keep ours open across it.
    await repo.commit()
    lines, label = await _model_lines(query.from_user.id, stored, deps)
    text = _profile_text(stored, lines)
    if notice:
        text = f"{escape(notice)}\n\n{text}"
    await _edit(query, text, _main_keyboard(stored, label))


def _is_private_callback(query: CallbackQuery) -> bool:
    return query.message is not None and query.message.chat.type == "private" and query.from_user is not None


@router.message(Command("ai"))
async def ai_settings_command(
    message: Message,
    db_session: AsyncSession,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    personal_config: PersonalConfigProvider | None = None,
    llm_client: LlmClient | None = None,
    web_search_client=None,
) -> None:
    if message.chat.type != "private" or message.from_user is None:
        await message.answer("Личные настройки Selara AI доступны в личных сообщениях с ботом: откройте диалог и отправьте /ai.")
        return
    _pending_inputs.pop(message.from_user.id, None)
    repo = PersonalAiRepository(db_session)
    stored = await repo.get_or_create_profile(message.from_user.id)
    await repo.commit()
    lines, label = await _model_lines(
        message.from_user.id, stored, _model_deps(settings, session_factory, personal_config, llm_client, web_search_client)
    )
    await message.answer(_profile_text(stored, lines), parse_mode="HTML", reply_markup=_main_keyboard(stored, label))


@router.message(Command("ai_reset"))
async def ai_reset_command(message: Message, db_session: AsyncSession) -> None:
    if message.chat.type != "private" or message.from_user is None:
        await message.answer("/ai_reset работает только в личных сообщениях с ботом.")
        return
    repo = PersonalAiRepository(db_session)
    stored = await repo.get_or_create_profile(message.from_user.id)
    removed = await repo.reset_thread(user_id=message.from_user.id, thread=stored.profile.thread)
    label = "ролевой игры" if stored.profile.thread == "roleplay" else "диалога"
    await message.answer(f"Готово, история {label} очищена ({removed} сообщ.). Настройки сохранены.")


@router.callback_query(F.data.startswith("pai:"))
async def ai_settings_callback(
    query: CallbackQuery,
    db_session: AsyncSession,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    personal_config: PersonalConfigProvider | None = None,
    llm_client: LlmClient | None = None,
    web_search_client=None,
) -> None:
    if not _is_private_callback(query):
        await query.answer()
        return
    parts = (query.data or "").split(":")
    action, args = (parts[1] if len(parts) > 1 else ""), parts[2:]
    repo = PersonalAiRepository(db_session)
    user_id = query.from_user.id
    deps = _model_deps(settings, session_factory, personal_config, llm_client, web_search_client)

    if action == "close":
        _pending_inputs.pop(user_id, None)
        await query.answer()
        try:
            await query.message.delete()
        except TelegramBadRequest:
            pass
        return
    if action == "home":
        _pending_inputs.pop(user_id, None)
        await query.answer()
        await _show_home(query, repo, deps=deps)
        return

    try:
        revision = int(args[-1])
    except (ValueError, IndexError):
        await query.answer()
        return

    stored = await repo.get_or_create_profile(user_id)
    if stored.revision != revision:
        await query.answer("Настройки уже изменились, показываю актуальные.")
        await _show_home(query, repo, deps=deps)
        return

    changes: dict[str, Any] | None = None
    if action == "cat" and args and args[0] in _CATEGORIES:
        await query.answer()
        await repo.commit()
        text, markup = _category_screen(args[0], stored, await _tools_view(user_id, deps))
        await _edit(query, text, markup)
        return
    if action == "models":
        if deps is None:
            await query.answer("Выбор модели сейчас недоступен.")
            return
        await query.answer()
        await repo.commit()
        text, markup = await _models_screen(stored, deps)
        await _edit(query, text, markup)
        return
    if action == "model" and len(args) == 2:
        # Callback data is user-controlled: only a stable profile key that the current catalog
        # can actually serve is stored, and only while AI Limits price the choice.
        key = args[0]
        if deps is None or not is_profile_key(key) or not await _selectable(key, deps):
            await query.answer("Этот профиль сейчас недоступен.", show_alert=True)
            if deps is not None:
                await repo.commit()
                text, markup = await _models_screen(stored, deps)
                await _edit(query, text, markup)
            return
        changes = {"model_profile_key": key}
    elif action == "presets":
        await query.answer()
        await _edit(query, "<b>Характер</b>\nВыберите готовый вариант или опишите свой.", _presets_keyboard(stored))
        return
    if action == "in" and args and args[0] in _INPUT_PROMPTS:
        await query.answer()
        _set_pending_input(user_id, args[0])
        await _edit(query, escape(_INPUT_PROMPTS[args[0]]) + "\n\nЧтобы отменить, отправьте /ai.", None)
        return
    if action == "preset" and len(args) == 2 and args[0] in CHARACTER_PRESETS:
        changes = {"character_preset": args[0]}
    elif action == "set" and len(args) == 3:
        changes = _parse_switch(args[0], args[1])
        if changes is not None and any(column in changes for column in _TOOL_SWITCHES.values()):
            changes = None  # tool switches live in the category screen, where the Personal check is done
    elif action == "sc" and len(args) == 4 and args[0] in _CATEGORIES:
        category = args[0]
        changes = _parse_switch(args[1], args[2])
        if changes is not None and any(column in changes for column in _TOOL_SWITCHES.values()):
            # Switching a tool on needs Selara Personal; switching it off is always allowed.
            if any(changes.values()) and not await _has_personal(user_id, deps):
                await query.answer("Инструменты доступны с Selara Personal.", show_alert=True)
                await repo.commit()
                text, markup = _category_screen(category, stored, await _tools_view(user_id, deps))
                await _edit(query, text, markup)
                return
        if changes is not None:
            updated = await repo.update_profile(user_id, expected_revision=revision, **changes)
            await query.answer("Сохранено" if updated is not None else "Настройки уже изменились.")
            fresh = updated or await repo.get_or_create_profile(user_id)
            await repo.commit()
            text, markup = _category_screen(category, fresh, await _tools_view(user_id, deps))
            await _edit(query, text, markup)
            return
    if changes is None:
        await query.answer()
        return

    updated = await repo.update_profile(user_id, expected_revision=revision, **changes)
    await query.answer("Сохранено" if updated is not None else "Настройки уже изменились.")
    await _show_home(query, repo, deps=deps)


def _parse_switch(field: str, value: str) -> dict[str, Any] | None:
    """Validate one switch from callback data (user-controlled) into profile column changes."""
    if field == "formality" and value in ("ty", "vy"):
        return {"formality": value}
    if field == "length" and value in ("short", "medium", "long"):
        return {"reply_length": value}
    if field == "emoji" and value in ("0", "1"):
        return {"emoji_enabled": value == "1"}
    if field == "mode" and value in ("assistant", "roleplay"):
        return {"mode": value}
    if field == "memory" and value in ("0", "1"):
        return {"memory_enabled": value == "1"}
    if field == "automemory" and value in ("0", "1"):
        return {"auto_memory_enabled": value == "1"}
    if field in _TOOL_SWITCHES and value in ("0", "1"):
        return {_TOOL_SWITCHES[field]: value == "1"}
    return None


async def _selectable(profile_key: str, deps: _ModelDeps) -> bool:
    if not (await deps.personal_config.get()).ail_enabled:
        return False
    options = profile_options(await load_snapshot(deps.catalog), legacy_model=deps.legacy_model)
    return any(option.profile_key == profile_key and option.available for option in options)


@router.message(PendingPersonalInputFilter())
async def ai_settings_input(
    message: Message,
    db_session: AsyncSession,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    personal_config: PersonalConfigProvider | None = None,
    llm_client: LlmClient | None = None,
    web_search_client=None,
) -> None:
    user_id = message.from_user.id
    state = _get_pending_input(user_id)
    if state is None:
        return
    try:
        if state.field == "name":
            changes: dict[str, Any] = {"display_name": validate_display_name(message.text or "")}
        elif state.field == "custom":
            changes = {"character_preset": CUSTOM_PRESET_KEY, "character_custom": validate_custom_character(message.text or "")}
        else:
            changes = {"address_form": validate_address(message.text or "")}
    except ProfileValidationError as exc:
        await message.answer(f"{exc} Попробуйте ещё раз или отправьте /ai, чтобы отменить.")
        return
    repo = PersonalAiRepository(db_session)
    # Text input is the whole new value, so it may overwrite concurrent button changes of other fields.
    stored = await repo.get_or_create_profile(user_id)
    updated = await repo.update_profile(user_id, expected_revision=stored.revision, **changes)
    _pending_inputs.pop(user_id, None)
    if updated is None:
        await message.answer("Настройки изменились одновременно. Откройте /ai и повторите.")
        return
    await repo.commit()
    lines, label = await _model_lines(user_id, updated, _model_deps(settings, session_factory, personal_config, llm_client, web_search_client))
    await message.answer(
        "Сохранено.\n\n" + _profile_text(updated, lines), parse_mode="HTML", reply_markup=_main_keyboard(updated, label)
    )


# --- dialogue -------------------------------------------------------------------


def _offer_markup(settings: Settings, config, decision) -> InlineKeyboardMarkup | None:
    if decision.access_tier == AccessTier.PAID or not personal_offer_available(settings, config):
        return None
    builder = InlineKeyboardBuilder()
    builder.button(text="Оформить Selara Personal", callback_data="premium:self")
    return builder.as_markup()


# A model that read a web page can be talked into writing a link that carries the person's data out; a preview
# would load that address on Telegram's side without a click, so Personal answers never get one.
_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
ARTIFACT_ANSWER_PLACEHOLDER = "[отправлен артефакт]"


async def _send_answer(message: Message, thinking: Message, text: str) -> None:
    for index, chunk in enumerate(render_llm_html(text)):
        if index == 0:
            try:
                await thinking.edit_text(chunk, parse_mode="HTML", link_preview_options=_NO_PREVIEW)
                continue
            except Exception as exc:
                log.warning("personal_ai: editing answer failed, sending reply: %s", exc)
        try:
            await message.answer(chunk, parse_mode="HTML", link_preview_options=_NO_PREVIEW)
        except TelegramBadRequest:
            await message.answer(html_to_plain_text(chunk), parse_mode=None, link_preview_options=_NO_PREVIEW)
        except TelegramForbiddenError:
            # The user blocked the bot while the model was answering: the turn is already stored and charged.
            log.info("personal_ai: user blocked the bot before the answer was delivered")
            return


# Users already told that their profile is unavailable (in-memory: at worst repeated after a restart).
_fallback_notified: dict[int, str] = {}


async def _notify_fallback_once(message: Message, user_id: int, choice: PersonalModelChoice) -> None:
    if not choice.fell_back:
        _fallback_notified.pop(user_id, None)
        return
    if _fallback_notified.get(user_id) == choice.selected_key:
        return
    _fallback_notified[user_id] = choice.selected_key
    try:
        await message.answer(
            f"Выбранный профиль сейчас недоступен, используется {choice.display_name} модель "
            f"({format_ail(choice.ail_cost)} AIL за запрос)."
        )
    except Exception:
        log.debug("personal_ai: fallback notice not delivered", exc_info=True)


# One turn per user at a time: a second message sent while the first is still being answered would
# pass the cooldown, spend quota and generate from the same stale history.
_inflight_users: set[int] = set()


@chat_router.message(PersonalChatFilter())
async def personal_chat_handler(
    message: Message,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    personal_config: PersonalConfigProvider,
    llm_client: LlmClient | None = None,
    web_search_client=None,
) -> None:
    user_id = message.from_user.id
    fact = parse_remember_request(message.text)
    if fact is not None and await personal_memory.propose_memory(
        message, db_session, session_factory, settings, personal_config, fact, from_phrase=True
    ):
        # "запомни, что ..." is a local action: no model call, no quota, the fact is stored only after confirmation.
        return
    if user_id in _inflight_users:
        await message.answer("⏳ Я ещё отвечаю на предыдущее сообщение. Подожди немного. Квота не потрачена.")
        return
    _inflight_users.add(user_id)
    try:
        await _handle_personal_chat(
            message, db_session, session_factory, settings, personal_config, llm_client, web_search_client
        )
    finally:
        _inflight_users.discard(user_id)


async def _handle_personal_chat(
    message: Message,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    personal_config: PersonalConfigProvider,
    llm_client: LlmClient | None,
    web_search_client=None,
) -> None:
    user = message.from_user
    text = (message.text or "").strip()
    if llm_client is None:
        await message.answer(_UNAVAILABLE_TEXT)
        return
    if len(text) > MAX_USER_TEXT_LENGTH:
        await message.answer(f"Сообщение слишком длинное: до {MAX_USER_TEXT_LENGTH} символов. Квота не потрачена.")
        return

    repo = PersonalAiRepository(db_session)
    last_at = await repo.last_user_message_at(user_id=user.id)
    if last_at is not None:
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last_at).total_seconds()
        if elapsed < settings.llm_cooldown_seconds:
            await message.answer(f"⏳ Слишком часто. Подожди {int(settings.llm_cooldown_seconds - elapsed) + 1} сек.")
            return

    config = await personal_config.get()
    access_service = _access_service(session_factory, personal_config)
    stored = await repo.get_or_create_profile(user.id)
    # Resolved exactly once per turn: the same snapshot gives the AIL cost to the reservation and
    # the physical model to the provider call, whatever the admin changes in between.
    choice = choose_from_snapshot(
        await load_snapshot(getattr(llm_client, "model_catalog", None)),
        selected_key=stored.model_profile_key,
        legacy_model=getattr(llm_client, "default_model", None) or settings.llm_model,
    )
    tool_flags = await _tool_flags(
        user.id, stored, choice, _model_deps(settings, session_factory, personal_config, llm_client, web_search_client)
    )
    tools_active = tool_flags is not None
    reserve_units = choice.ail_cost
    if tools_active and config.ail_settles_actual_cost:
        # Tool turns cost several model calls: reserve a multiple up front (the balance must cover it) and settle
        # to the real cost, never above that reservation.
        reserve_units = choice.ail_cost * settings.personal_tools_reserve_factor
    # The quota service works in its own transactions and also upserts the user row: commit ours first,
    # otherwise a brand-new user's first request would wait on a lock held by this very handler.
    await db_session.commit()
    try:
        # One user message is exactly one request; internal calls below ride the same invocation.
        decision = await access_service.reserve_feature_usage(
            feature=AiFeature.PERSONAL_CHAT,
            chat_id=message.chat.id,
            chat_type="private",
            chat_title=None,
            scope=QuotaScope.user(user.id),
            actor_user_id=user.id,
            actor_is_bot=False,
            trigger="telegram_message",
            timezone_name=settings.bot_timezone,
            idempotency_key=message_idempotency_key(
                feature=AiFeature.PERSONAL_CHAT, chat_id=message.chat.id, source_message_id=message.message_id
            ),
            source_message_id=message.message_id,
            mode=stored.profile.mode,
            owner_exempt=resolve_owner_private_exemption(user_id=user.id, admin_user_id=settings.admin_user_id),
            # Used only if the pool counts AI Limits; in requests mode a message is one request.
            units=reserve_units,
            model_profile=choice.profile_key,
        )
    except Exception:
        log.exception("Personal AI quota reservation failed message_id=%s", message.message_id)
        await message.answer(_ACCESS_ERROR_TEXT)
        return

    if decision.reused:
        await message.answer("Этот запрос уже был обработан. Повторный запуск не выполнялся.")
        return
    ail_mode = decision.quota_unit == AIL_UNIT
    if not decision.allowed:
        if decision.reason == AccessReason.QUOTA_EXHAUSTED and ail_mode:
            # Not enough AIL for this profile: nothing is charged and no provider call is made.
            await message.answer(
                ail_insufficient_message(
                    decision,
                    profile_name=choice.display_name,
                    cost=reserve_units,
                    timezone_name=settings.bot_timezone,
                    can_buy=_offer_markup(settings, config, decision) is not None,
                )
                + (_TOOLS_RESERVE_HINT if tools_active and reserve_units != choice.ail_cost else ""),
                reply_markup=_offer_markup(settings, config, decision),
            )
        elif decision.reason == AccessReason.QUOTA_EXHAUSTED:
            await message.answer(
                quota_exhausted_message(decision, timezone_name=settings.bot_timezone),
                reply_markup=_offer_markup(settings, config, decision),
            )
        else:
            await message.answer("⚠️ Сейчас не удалось разрешить запрос. Попробуйте позже.")
        return
    # Requests mode keeps the original behaviour (legacy model); AIL mode calls the priced snapshot.
    resolved_model = choice.effective if ail_mode else None

    accounting = llm_client.accounting_service if isinstance(llm_client, LlmClient) else None
    invocation_id = decision.invocation_id
    thread = stored.profile.thread

    def _context(feature: AiFeature, stage: str) -> LlmAccountingContext | None:
        if invocation_id is None:
            return None
        return LlmAccountingContext(
            invocation_id=invocation_id,
            feature=feature,
            stage=stage,
            chat_id=message.chat.id,
            actor_user_id=user.id,
            telegram_message_id=message.message_id,
        )

    tool_run = None
    if tool_flags is not None:
        web_on, artifacts_on = tool_flags
        usd_per_ail = config.ail_usd_value
        budget_units = reserve_units if config.ail_settles_actual_cost else choice.ail_cost * settings.personal_tools_reserve_factor
        tool_run = PersonalToolRun(
            web_enabled=web_on,
            artifacts_enabled=artifacts_on,
            web_context=WebToolContext(
                client=web_search_client,
                max_calls=settings.personal_web_max_calls,
                max_results=settings.web_search_max_results,
                max_page_chars=settings.personal_web_page_chars,
            ),
            artifact_context=ArtifactRequestContext(
                repository=ArtifactRepository(db_session),
                renderer_url=settings.artifact_renderer_url,
                chat_id=user.id,
                creator_id=user.id,
                message_id=message.message_id,
            ),
            bot=message.bot,
            total_rounds=settings.personal_tool_rounds,
            cost_budget_usd=Decimal(budget_units) * Decimal(usd_per_ail) if usd_per_ail else None,
        )
    reply_outcome: dict = {}
    outcome = {"status": "failed", "error_category": "handler_error"}
    turn_usages: list = []
    try:
        if ail_mode:
            # Inside the cleanup region: a cancellation here still releases the unused reservation.
            await _notify_fallback_once(message, user.id, choice)
        thinking = await message.answer("⏳ Думаю...")
        try:
            await message.bot.send_chat_action(message.chat.id, "typing")
        except Exception:
            pass
        try:
            answer = await generate_reply(
                llm_client=llm_client,
                repo=repo,
                user_id=user.id,
                profile=stored.profile,
                user_text=text,
                accounting_context=_context(AiFeature.PERSONAL_CHAT, "chat_turn"),
                use_memory=stored.memory_enabled,
                resolved_model=resolved_model,
                usage_sink=turn_usages,
                tool_run=tool_run,
                outcome_sink=reply_outcome,
            )
        except LlmClientError as exc:
            outcome["error_category"] = exc.usages[-1].error_category if exc.usages else "provider_error"
            # A provider error is not charged the full reservation; with no attempt at all the release below runs.
            failed_usages = [*turn_usages, *exc.usages]
            if failed_usages and ail_mode and config.ail_settles_actual_cost and not decision.owner_exempt:
                await _settle_chat_turn(
                    access_service, config=config, invocation_id=invocation_id,
                    usages=failed_usages, user_id=user.id, failed=True,
                    max_units=reserve_units if tools_active else None,
                )
            await thinking.edit_text("⚠️ Не удалось получить ответ от AI. Попробуйте позже.")
            return
        except Exception:
            log.exception("personal_ai: LLM request failed before reaching the provider")
            outcome["error_category"] = "accounting_unavailable"
            await thinking.edit_text("⚠️ Не удалось выполнить запрос. Попробуйте позже.")
            return

        artifact_sent = bool(reply_outcome.get("artifact_sent"))
        if not answer and artifact_sent:
            # An artifact without a caption is still an answer: keep the turn and its charge.
            answer = ARTIFACT_ANSWER_PLACEHOLDER
        if not answer:
            outcome["error_category"] = "empty_answer"
            if ail_mode and config.ail_settles_actual_cost and not decision.owner_exempt:
                await _settle_chat_turn(
                    access_service, config=config, invocation_id=invocation_id,
                    usages=turn_usages, user_id=user.id, failed=True,
                    max_units=reserve_units if tools_active else None,
                )
            await thinking.edit_text("⚠️ AI не дал ответа. Попробуйте переформулировать.")
            return

        # Settle now, before compression and memory extraction: only the chat turn's own cost is charged.
        if ail_mode and config.ail_settles_actual_cost and not decision.owner_exempt:
            await _settle_chat_turn(
                access_service, config=config, invocation_id=invocation_id, usages=turn_usages, user_id=user.id,
                max_units=reserve_units if tools_active else None,
            )
        web_tainted = bool(reply_outcome.get("web_tainted"))
        await repo.add_message(
            user_id=user.id, thread=thread, role="user", content=text, telegram_message_id=message.message_id
        )
        await repo.add_message(
            user_id=user.id, thread=thread, role="assistant", content=answer, web_tainted=web_tainted
        )
        # Persist the turn now and give the connection back before delivery and compression.
        await db_session.commit()
        outcome["status"] = "succeeded"
        outcome["error_category"] = None
        if artifact_sent:
            # The artifact's caption is the answer and is already in the chat.
            try:
                await thinking.delete()
            except Exception:
                log.warning("personal_ai: could not remove the progress message", exc_info=True)
        else:
            await _send_answer(message, thinking, answer)
        try:
            await maybe_compress_personal(
                repo=repo,
                llm_client=llm_client,
                user_id=user.id,
                thread=thread,
                accounting_context=_context(AiFeature.LLM_CONTEXT_COMPRESSION, "context_compression"),
            )
        except Exception:
            log.exception("personal_ai: context compression crashed user_id=%s", user.id)
            # A failed statement leaves the session unusable; the turn itself is already committed.
            await db_session.rollback()
        # Internal operation: rides the same invocation for cost tracking and is never charged to the quota.
        if (
            config.memory_auto_extract
            and stored.memory_enabled
            and stored.auto_memory_enabled
            and decision.access_tier in (AccessTier.PAID, AccessTier.OWNER_INTERNAL)
            and thread == "assistant"
            # A turn that read the web never feeds automatic memory.
            and not web_tainted
        ):
            try:
                await maybe_extract_memories(
                    repo=repo,
                    llm_client=llm_client,
                    user_id=user.id,
                    thread=thread,
                    cursor=stored.memory_extract_cursor,
                    every=config.memory_extract_every,
                    limit=config.memory_paid_limit,
                    accounting_context=_context(AiFeature.PERSONAL_MEMORY_EXTRACT, "memory_extract"),
                )
            except Exception:
                log.exception("personal_ai: memory extraction crashed user_id=%s", user.id)
                await db_session.rollback()
    finally:
        if accounting is not None and invocation_id is not None:
            if outcome["status"] != "succeeded":
                try:
                    await access_service.release_if_no_provider_attempts(
                        invocation_id=invocation_id, reason=outcome["error_category"] or "pre_provider_failure"
                    )
                except Exception:
                    log.exception("Could not release unused personal quota invocation_id=%s", invocation_id)
            try:
                await accounting.finish_invocation_outcome(
                    invocation_id=invocation_id,
                    status=outcome["status"],
                    error_category=outcome["error_category"],
                )
            except Exception:
                log.exception("Could not finalize personal_chat invocation id=%s", invocation_id)
