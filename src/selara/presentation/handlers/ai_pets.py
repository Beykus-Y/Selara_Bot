"""AI pets in group chats: creation, card, mechanics and the item shop.

Replies are templates — no LLM call happens here (talking to a pet is a later
stage). AI features of a pet will depend on the owner's Selara Personal; the
mechanics in this module work regardless of any subscription.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from selara.application.ai_pets import mechanics as m
from selara.core.chat_settings import ChatSettings
from selara.core.config import Settings
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.ai_pets import ActionResult, AiPetService, PetDomainError, PetView
from selara.presentation.auth import has_permission
from selara.presentation.formatters import format_user_link

logger = logging.getLogger(__name__)

router = Router(name="ai_pets")

_GROUP_TYPES = {"group", "supergroup"}
CALLBACK_PREFIX = "aipet:"
PETS_DISABLED_TEXT = (
    "AI-питомцы в этом чате выключены. Админ может включить их: <code>/setcfg pets_enabled true</code>.\n"
    "Ролевое «стать питомцем» теперь — <code>/bepet</code>."
)
_FEED_WORDS = {"покормить", "кормить", "накормить"}
_ACTION_BUTTONS: tuple[tuple[str, str], ...] = (
    ("pat", "🤚 Погладить"),
    ("play", "🎾 Поиграть"),
    ("feed", "🍖 Покормить"),
    ("shop", "🛍 Магазин"),
    ("tease", "😜 Подразнить"),
    ("hurt", "💢 Обидеть"),
)


# ----- parsing ------------------------------------------------------------------


def parse_pet_text(text: str) -> tuple[str, str | None] | None:
    """Parse «пет», «петы» and «пет <действие> [имя]»; anything else is not ours."""
    words = " ".join((text or "").split())
    lowered = words.casefold()
    if lowered == "петы":
        return "list", None
    if lowered == "пет":
        return "panel", None
    if not lowered.startswith("пет "):
        return None
    rest = words[4:].strip()
    verb, _, name = rest.partition(" ")
    verb = verb.casefold()
    name = name.strip() or None
    if verb in _FEED_WORDS:
        return "feed", name
    if verb in m.ACTION_ALIASES:
        return m.ACTION_ALIASES[verb], name
    if verb in {"магазин", "лавка"}:
        return "shop", name
    return None


def is_bare_pet_command(message: Message) -> bool:
    """``/pet`` alone opens the AI pet; ``/pet @user`` or a reply is the old role-play request."""
    raw = (message.text or "").strip()
    has_args = len(raw.split(maxsplit=1)) > 1
    reply = message.reply_to_message
    replied_to_person = reply is not None and reply.from_user is not None and not reply.from_user.is_bot
    return not has_args and not replied_to_person


# ----- helpers ------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today(settings: Settings | None, now: datetime) -> date:
    name = getattr(settings, "bot_timezone", None) or "UTC"
    try:
        zone = ZoneInfo(name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    return now.astimezone(zone).date()


def _service(db_session, economy_repo) -> AiPetService:
    return AiPetService(db_session, economy_repo)


def _user_snapshot(user) -> UserSnapshot:
    return UserSnapshot(
        telegram_user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        is_bot=bool(user.is_bot),
    )


def _actor_link(user) -> str:
    label = " ".join(part for part in (user.first_name, user.last_name) if part) or (user.username or str(user.id))
    return format_user_link(user_id=user.id, label=label)


async def _owner_label(activity_repo, *, chat_id: int, user_id: int) -> str:
    try:
        label = await activity_repo.get_chat_display_name(chat_id=chat_id, user_id=user_id)
    except Exception:  # display only; never fail the command over a name
        label = None
    return format_user_link(user_id=user_id, label=label or "хозяин")


def pet_keyboard(pet_id: int) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=title,
            callback_data=f"{CALLBACK_PREFIX}{'shop' if key == 'shop' else 'a'}:{pet_id}" + ("" if key == "shop" else f":{key}"),
        )
        for key, title in _ACTION_BUTTONS
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i : i + 2] for i in range(0, len(buttons), 2)])


def render_card(pet: PetView, *, owner_label: str, viewer_affinity: int | None, top: list[tuple[str, int]] | None = None) -> str:
    level, into, needed = m.level_progress(pet.xp)
    lines = [
        f"{pet.emoji} <b>{escape(pet.name)}</b> — {escape(pet.species_title)}, ур. {level} ({into}/{needed} XP)",
        f"Хозяин: {owner_label}",
        f"Настроение: {pet.mood} · Сытость: {pet.satiety} · Энергия: {pet.energy}",
        f"Сейчас {m.mood_label(pet.mood)}.",
    ]
    if pet.traits:
        lines.append("Характер: " + ", ".join(escape(m.TRAITS.get(key, key)) for key in pet.traits))
    if viewer_affinity is not None:
        lines.append(f"К вам: {m.affinity_label(viewer_affinity)}")
    if top:
        lines.append("Любимцы: " + ", ".join(f"{label} ({m.affinity_label(value)})" for label, value in top))
    if pet.status == "dormant":
        lines.append("💤 Спит.")
    return "\n".join(lines)


def _effects_line(applied: dict[str, int]) -> str:
    names = (("mood", "настроение"), ("satiety", "сытость"), ("energy", "энергия"), ("affinity", "отношение"), ("xp", "XP"))
    parts = [f"{title} {applied[key]:+d}" for key, title in names if applied.get(key)]
    return f"({', '.join(parts)})" if parts else ""


def render_result(result: ActionResult, *, event_type: str, actor_link: str) -> str:
    pet = result.pet
    assert pet is not None
    item_title = result.item.title.lower() if result.item is not None else None
    lines = [f"{actor_link}: {escape(m.reply_text(event_type, name=pet.name, species_key=pet.species_key, item=item_title))}"]
    effects = _effects_line(result.applied)
    if effects:
        lines.append(effects)
    if result.item is not None and result.item.price:
        balance = f", баланс: {result.new_balance}" if result.new_balance is not None else ""
        lines.append(f"Потрачено {result.item.price} монет{balance}.")
    if result.leveled_up_to is not None:
        lines.append(f"🎉 {escape(pet.name)} достигает {result.leveled_up_to} уровня!")
        if result.leveled_up_to == m.TRAVEL_UNLOCK_LEVEL:
            lines.append("Открыты путешествия — они появятся в одном из следующих обновлений.")
    return "\n".join(lines)


async def _pets_allowed(message: Message, chat_settings: ChatSettings) -> bool:
    if message.chat.type not in _GROUP_TYPES:
        await message.answer("Питомцы живут в группах. Откройте /pet в чате, где он живёт.")
        return False
    if not chat_settings.pets_enabled:
        await message.answer(PETS_DISABLED_TEXT, parse_mode="HTML")
        return False
    return True


async def _send_card(message: Message, pet: PetView, *, activity_repo, service: AiPetService, viewer_id: int) -> None:
    owner_label = await _owner_label(activity_repo, chat_id=message.chat.id, user_id=pet.owner_user_id)
    affinity = await service.relation_affinity(pet_id=pet.id, chat_id=message.chat.id, user_id=viewer_id)
    top_rows = await service.top_relations(pet_id=pet.id, chat_id=message.chat.id)
    top = [
        (await _owner_label(activity_repo, chat_id=message.chat.id, user_id=user_id), value)
        for user_id, value in top_rows
        if value >= 25
    ]
    await message.answer(
        render_card(pet, owner_label=owner_label, viewer_affinity=affinity, top=top),
        parse_mode="HTML",
        reply_markup=pet_keyboard(pet.id) if pet.status == "active" else None,
    )


# ----- panel and lifecycle ------------------------------------------------------


async def show_panel(message: Message, *, activity_repo, db_session, economy_repo, chat_settings: ChatSettings, name: str | None = None) -> None:
    if message.from_user is None:
        return
    service = _service(db_session, economy_repo)
    now = _now()
    if message.chat.type not in _GROUP_TYPES:
        own = await service.get_owner_pet(owner_user_id=message.from_user.id, now=now)
        if own is None:
            await message.answer(
                "У вас пока нет AI-питомца. Заведите его в группе, где включены питомцы: "
                "<code>/pet_new кот Мурка</code> (нужна Selara Personal).",
                parse_mode="HTML",
            )
            return
        state = "спит 💤" if own.status == "dormant" else "живёт в группе"
        await message.answer(
            f"{own.emoji} <b>{escape(own.name)}</b>, ур. {own.level} — {state}. Карточка и действия — /pet в той группе.",
            parse_mode="HTML",
        )
        return
    if not await _pets_allowed(message, chat_settings):
        return
    pet = await service.find_chat_pet(chat_id=message.chat.id, actor_user_id=message.from_user.id, name=name)
    if pet is None:
        pets = await service.list_chat_pets(chat_id=message.chat.id)
        if pets:
            await message.answer(_render_list(pets) + "\n\nКарточка питомца: <code>пет имя</code> или <code>/pet_shop имя</code>.", parse_mode="HTML")
        else:
            await message.answer(
                "В этом чате пока нет питомцев. Завести своего: <code>/pet_new кот Мурка</code> "
                "(виды: собака, кот, паук, дракон, человек или своё слово; нужна Selara Personal).",
                parse_mode="HTML",
            )
        return
    pet = await service.current_view(pet_id=pet.id, now=now) or pet
    await _send_card(message, pet, activity_repo=activity_repo, service=service, viewer_id=message.from_user.id)


def _render_list(pets: list[PetView]) -> str:
    lines = ["<b>Питомцы этого чата</b>"]
    for pet in pets:
        lines.append(f"{pet.emoji} {escape(pet.name)} — {escape(pet.species_title)}, ур. {pet.level}")
    return "\n".join(lines)


@router.message(Command("pet"), is_bare_pet_command)
async def pet_panel_command(message: Message, activity_repo, db_session, economy_repo, chat_settings: ChatSettings) -> None:
    await show_panel(message, activity_repo=activity_repo, db_session=db_session, economy_repo=economy_repo, chat_settings=chat_settings)


@router.message(Command("pets"))
async def pets_list_command(message: Message, db_session, economy_repo, chat_settings: ChatSettings) -> None:
    if not await _pets_allowed(message, chat_settings):
        return
    pets = await _service(db_session, economy_repo).list_chat_pets(chat_id=message.chat.id)
    if not pets:
        await message.answer("В этом чате пока нет питомцев. Завести: <code>/pet_new кот Мурка</code>.", parse_mode="HTML")
        return
    await message.answer(_render_list(pets), parse_mode="HTML")


@router.message(Command("pet_new"))
async def pet_new_command(message: Message, command: CommandObject, activity_repo, db_session, economy_repo, chat_settings: ChatSettings) -> None:
    if message.from_user is None or not await _pets_allowed(message, chat_settings):
        return
    species_raw, _, name_raw = (command.args or "").strip().partition(" ")
    if not species_raw or not name_raw.strip():
        await message.answer(
            "Формат: <code>/pet_new &lt;вид&gt; &lt;имя&gt;</code>, например <code>/pet_new кот Мурка</code>.\n"
            "Виды: собака, кот, паук, дракон, человек — или своё слово до 40 символов.",
            parse_mode="HTML",
        )
        return
    service = _service(db_session, economy_repo)
    try:
        pet = await service.create_pet(
            owner=_user_snapshot(message.from_user),
            chat=ChatSnapshot(telegram_chat_id=message.chat.id, chat_type=message.chat.type, title=message.chat.title),
            species_raw=species_raw,
            name_raw=name_raw,
            now=_now(),
        )
    except (m.PetValidationError, PetDomainError) as exc:
        await message.answer(escape(str(exc)), parse_mode="HTML")
        return
    await message.answer(
        f"{pet.emoji} У вас появился питомец: <b>{escape(pet.name)}</b>!\n"
        f"Выберите до {m.MAX_TRAITS} черт характера: <code>/pet_traits игривый, ласковый</code>\n"
        f"Доступны: {escape(', '.join(m.TRAITS.values()))}.",
        parse_mode="HTML",
    )
    await _send_card(message, pet, activity_repo=activity_repo, service=service, viewer_id=message.from_user.id)


@router.message(Command("pet_traits"))
async def pet_traits_command(message: Message, command: CommandObject, db_session, economy_repo) -> None:
    if message.from_user is None:
        return
    try:
        traits = m.parse_traits(command.args or "")
        pet = await _service(db_session, economy_repo).set_traits(owner_user_id=message.from_user.id, traits=traits)
    except (m.PetValidationError, PetDomainError) as exc:
        hint = f"\nДоступны: {', '.join(m.TRAITS.values())}." if not (command.args or "").strip() else ""
        await message.answer(escape(str(exc) + hint), parse_mode="HTML")
        return
    await message.answer(
        f"{pet.emoji} Характер {escape(pet.name)}: " + escape(", ".join(m.TRAITS[key] for key in pet.traits)) + ".",
        parse_mode="HTML",
    )


@router.message(Command("pet_release"))
async def pet_release_command(message: Message, db_session, economy_repo) -> None:
    if message.from_user is None:
        return
    pet = await _service(db_session, economy_repo).get_owner_pet(owner_user_id=message.from_user.id)
    if pet is None:
        await message.answer("У вас нет питомца.")
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Отпустить навсегда", callback_data=f"{CALLBACK_PREFIX}rel:{pet.id}"),
                InlineKeyboardButton(text="Оставить", callback_data=f"{CALLBACK_PREFIX}keep:{pet.id}"),
            ]
        ]
    )
    await message.answer(
        f"Отпустить {escape(pet.name)}? Уровень, отношения и история пропадут, вернуть питомца будет нельзя.",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def _admin_allowed(message: Message, activity_repo) -> bool:
    user = message.from_user
    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        is_bot=bool(user.is_bot),
        permission="manage_settings",
        bootstrap_if_missing_owner=False,
    )
    if not allowed:
        await message.answer("Усыплять и будить чужих питомцев могут админы с правом настройки чата.")
    return allowed


async def _sleep_command(message: Message, command: CommandObject, activity_repo, db_session, economy_repo, *, asleep: bool) -> None:
    if message.from_user is None:
        return
    if message.chat.type not in _GROUP_TYPES:
        await message.answer("Команда доступна только в группе.")
        return
    name = (command.args or "").strip()
    if not name:
        await message.answer(f"Формат: <code>/{'pet_sleep' if asleep else 'pet_wake'} имя</code>", parse_mode="HTML")
        return
    if not await _admin_allowed(message, activity_repo):
        return
    try:
        pet = await _service(db_session, economy_repo).set_sleep(
            chat_id=message.chat.id, name=name, actor_user_id=message.from_user.id, asleep=asleep
        )
    except PetDomainError as exc:
        await message.answer(escape(str(exc)), parse_mode="HTML")
        return
    text = f"💤 {escape(pet.name)} уснул(а) и не реагирует на действия." if asleep else f"☀️ {escape(pet.name)} проснулся(ась)!"
    await message.answer(text, parse_mode="HTML")


@router.message(Command("pet_sleep"))
async def pet_sleep_command(message: Message, command: CommandObject, activity_repo, db_session, economy_repo) -> None:
    await _sleep_command(message, command, activity_repo, db_session, economy_repo, asleep=True)


@router.message(Command("pet_wake"))
async def pet_wake_command(message: Message, command: CommandObject, activity_repo, db_session, economy_repo) -> None:
    await _sleep_command(message, command, activity_repo, db_session, economy_repo, asleep=False)


# ----- shop ---------------------------------------------------------------------


async def _send_shop(message: Message, pet: PetView, *, service: AiPetService, chat_settings: ChatSettings) -> None:
    if not chat_settings.economy_enabled:
        await message.answer("Экономика в этом чате выключена, магазин питомцев недоступен.")
        return
    items = await service.list_items()
    if not items:
        await message.answer("Магазин питомцев пока пуст.")
        return
    rows: list[list[InlineKeyboardButton]] = []
    lines = [f"🛍 <b>Магазин для {escape(pet.name)}</b> — покупка сразу применяется:"]
    for item in items:
        locked = item.min_level > pet.level
        icon = "🍖" if item.kind == "food" else "🎾"
        suffix = f" — с {item.min_level} ур." if locked else ""
        lines.append(f"{icon} {escape(item.title)}: {item.price} монет{suffix}")
        if not locked:
            rows.append(
                [InlineKeyboardButton(text=f"{icon} {item.title} · {item.price}", callback_data=f"{CALLBACK_PREFIX}buy:{pet.id}:{item.code}")]
            )
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


@router.message(Command("pet_shop"))
async def pet_shop_command(message: Message, command: CommandObject, db_session, economy_repo, chat_settings: ChatSettings) -> None:
    if message.from_user is None or not await _pets_allowed(message, chat_settings):
        return
    service = _service(db_session, economy_repo)
    pet = await service.find_chat_pet(chat_id=message.chat.id, actor_user_id=message.from_user.id, name=(command.args or "").strip() or None)
    if pet is None:
        await message.answer("Не нашёл питомца. Укажите имя: <code>/pet_shop Мурка</code>.", parse_mode="HTML")
        return
    await _send_shop(message, pet, service=service, chat_settings=chat_settings)


# ----- actions (text and buttons) -----------------------------------------------


async def _run_action(
    *,
    service: AiPetService,
    pet_id: int,
    chat_id: int,
    user,
    action: str,
    idempotency_key: str,
    chat_settings: ChatSettings,
    settings: Settings | None,
    item_code: str | None = None,
) -> tuple[ActionResult, str]:
    now = _now()
    today = _today(settings, now)
    if action in m.ACTIONS:
        result = await service.perform_action(
            pet_id=pet_id,
            chat_id=chat_id,
            actor_user_id=user.id,
            action_key=action,
            idempotency_key=idempotency_key,
            today=today,
            now=now,
        )
        return result, action
    if not chat_settings.economy_enabled:
        return ActionResult(status="economy_unavailable", message="Экономика в этом чате выключена: кормить и покупать игрушки нельзя."), action
    result = await service.use_item(
        pet_id=pet_id,
        chat_id=chat_id,
        actor=_user_snapshot(user),
        item_code=item_code,
        kind="food" if item_code is None else None,
        idempotency_key=idempotency_key,
        economy_mode=chat_settings.economy_mode,
        today=today,
        now=now,
    )
    event_type = m.ITEM_EVENT_TYPES.get(result.item.kind, "feed") if result.item is not None else "feed"
    return result, event_type


async def ai_pet_text_command(
    message: Message,
    parsed: tuple[str, str | None],
    *,
    activity_repo,
    db_session,
    economy_repo,
    chat_settings: ChatSettings,
    settings: Settings | None,
) -> None:
    """Entry point for «пет ...» text commands, called by the text command dispatcher."""
    kind, name = parsed
    if message.from_user is None:
        return
    if kind == "panel":
        await show_panel(message, activity_repo=activity_repo, db_session=db_session, economy_repo=economy_repo, chat_settings=chat_settings)
        return
    if not await _pets_allowed(message, chat_settings):
        return
    service = _service(db_session, economy_repo)
    if kind == "list":
        pets = await service.list_chat_pets(chat_id=message.chat.id)
        await message.answer(_render_list(pets) if pets else "В этом чате пока нет питомцев.", parse_mode="HTML")
        return
    pet = await service.find_chat_pet(chat_id=message.chat.id, actor_user_id=message.from_user.id, name=name)
    if pet is None:
        await message.answer("Не нашёл питомца. Укажите имя: <code>пет погладить Мурка</code>.", parse_mode="HTML")
        return
    if kind == "shop":
        await _send_shop(message, pet, service=service, chat_settings=chat_settings)
        return
    result, event_type = await _run_action(
        service=service,
        pet_id=pet.id,
        chat_id=message.chat.id,
        user=message.from_user,
        action=kind,
        idempotency_key=f"ai_pet:msg:{message.chat.id}:{message.message_id}",
        chat_settings=chat_settings,
        settings=settings,
    )
    if result.status == "duplicate":
        return
    if result.status != "ok":
        await message.answer(escape(result.message), parse_mode="HTML")
        return
    await message.answer(render_result(result, event_type=event_type, actor_link=_actor_link(message.from_user)), parse_mode="HTML")


@router.callback_query(F.data.startswith(CALLBACK_PREFIX))
async def ai_pet_callback(query: CallbackQuery, activity_repo, db_session, economy_repo, chat_settings: ChatSettings, settings: Settings | None = None) -> None:
    data = query.data or ""
    parts = data[len(CALLBACK_PREFIX) :].split(":")
    message = query.message
    if message is None or query.from_user is None or len(parts) < 2 or not parts[1].isdigit():
        await query.answer("Кнопка устарела.")
        return
    kind, pet_id = parts[0], int(parts[1])
    service = _service(db_session, economy_repo)

    if kind in {"rel", "keep"}:
        pet = await service.get_pet(pet_id)
        if pet is None or pet.owner_user_id != query.from_user.id:
            await query.answer("Это не ваш питомец.", show_alert=True)
            return
        if kind == "keep":
            await query.answer(f"{pet.name} остаётся с вами.")
            return
        if pet.status == "released":
            await query.answer("Питомец уже отпущен.")
            return
        await service.release(owner_user_id=query.from_user.id, chat_id=message.chat.id if message.chat.type in _GROUP_TYPES else None)
        await query.answer()
        await message.answer(f"{pet.emoji} {escape(pet.name)} ушёл(ла) на свободу. Можно завести нового: /pet_new.", parse_mode="HTML")
        return

    if message.chat.type not in _GROUP_TYPES or not chat_settings.pets_enabled:
        await query.answer("Питомцы в этом чате выключены.", show_alert=True)
        return

    if kind == "shop":
        pet = await service.get_pet(pet_id)
        if pet is None or pet.status != "active" or pet.current_chat_id != message.chat.id:
            await query.answer("Этого питомца здесь нет.", show_alert=True)
            return
        await query.answer()
        await _send_shop(message, pet, service=service, chat_settings=chat_settings)
        return

    if kind == "a" and len(parts) == 3 and (parts[2] in m.ACTIONS or parts[2] == "feed"):
        action, item_code = parts[2], None
    elif kind == "buy" and len(parts) == 3:
        action, item_code = "item", parts[2]
    else:
        await query.answer("Кнопка устарела.")
        return

    result, event_type = await _run_action(
        service=service,
        pet_id=pet_id,
        chat_id=message.chat.id,
        user=query.from_user,
        action=action,
        idempotency_key=f"ai_pet:cb:{query.id}",
        chat_settings=chat_settings,
        settings=settings,
        item_code=item_code,
    )
    if result.status == "duplicate":
        await query.answer()
        return
    if result.status != "ok":
        await query.answer(result.message[:200] or "Не получилось.", show_alert=True)
        return
    await query.answer()
    await message.answer(render_result(result, event_type=event_type, actor_link=_actor_link(query.from_user)), parse_mode="HTML")
