from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timezone
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from selara.application.use_cases.economy.buy_shop_item import list_shop_offers
from selara.application.use_cases.economy.auction_bid import execute as auction_bid
from selara.application.use_cases.economy.auction_finalize import execute as auction_finalize
from selara.application.use_cases.economy.auction_start import execute as auction_start
from selara.application.use_cases.economy.catalog import (
    CROPS,
    FARM_LEVEL_PLOTS,
    FARM_LEVEL_UPGRADE_COST,
    RECIPES,
    SIZE_TIERS,
    localize_crop_code,
    localize_item_code,
    localize_offer_code,
    localize_scope,
    localize_size_tier,
    normalize_crop_input,
)
from selara.application.use_cases.economy.claim_daily import execute as claim_daily
from selara.application.use_cases.economy.craft import execute as craft_item
from selara.application.use_cases.economy.draw_lottery import execute as draw_lottery
from selara.application.use_cases.economy.growth import STRESS_DECAY_PER_HOUR
from selara.application.use_cases.economy.growth import effective_growth_stress_pct
from selara.application.use_cases.economy.growth import get_profile as get_growth_profile
from selara.application.use_cases.economy.growth import perform_action as perform_growth_action
from selara.application.use_cases.economy.get_dashboard import execute as get_dashboard
from selara.application.use_cases.economy.harvest_all_ready import execute as harvest_all_ready
from selara.application.use_cases.economy.harvest import execute as harvest_crop
from selara.application.use_cases.economy.market_buy_listing import execute as market_buy_listing
from selara.application.use_cases.economy.market_cancel_listing import execute as market_cancel_listing
from selara.application.use_cases.economy.market_create_listing import execute as market_create_listing
from selara.application.use_cases.economy.plant_all_last_crop import execute as plant_all_last_crop
from selara.application.use_cases.economy.plant_crop import execute as plant_crop
from selara.application.use_cases.economy.tap import execute as tap
from selara.application.use_cases.economy.transfer_coins import execute as transfer_coins
from selara.application.use_cases.economy.use_item import execute as use_item
from selara.core.chat_settings import ChatSettings, default_chat_settings
from selara.core.config import Settings
from selara.domain.economy_entities import ChatAuction
from selara.presentation.audit import log_chat_action
from selara.presentation.auth import has_permission

router = Router(name="economy")
_GROUP_CHAT_TYPES = {"group", "supergroup"}
_HTML_DISPLAY_TAGS_RE = re.compile(r"</?(?:b|code)>")
_INVENTORY_PAGE_SIZE = 5
_RUSSIAN_ITEM_ALIASES = {
    "энергетик": "item:energy_drink",
    "гель": "item:growth_gel",
    "гель роста": "item:growth_gel",
    "охлаждение": "item:cooling_pack",
    "охлаждающий пакет": "item:cooling_pack",
    "стимулятор": "item:stimulant_shot",
    "ускорение": "item:fertilizer_fast",
    "удобрение": "item:fertilizer_rich",
    "пестицид": "item:pesticide",
    "страховка": "item:crop_insurance",
    "билет": "item:lottery_ticket",
    "купон": "item:market_fee_coupon",
    "набор": "item:mystery_pack",
    "пицца": "item:pizza",
    "салат": "item:veggie_salad",
    "чипсы": "item:corn_chips",
}
_AUCTION_TASKS: dict[int, asyncio.Task[None]] = {}
_ECONOMY_CLEANUP_DELAY_SECONDS = 20


def _mode_to_short(mode: str) -> str:
    return "l" if mode == "local" else "g"


def _short_to_mode(value: str | None) -> str:
    return "local" if value == "l" else "global"


def _owner_to_short(owner_user_id: int | None) -> str | None:
    if owner_user_id is None:
        return None
    return f"u{owner_user_id}"


def _extract_owner_from_parts(parts: list[str]) -> tuple[list[str], int | None]:
    if not parts:
        return parts, None
    tail = parts[-1]
    if not tail.startswith("u"):
        return parts, None
    raw_id = tail[1:]
    if not raw_id.isdigit():
        return parts, None
    return parts[:-1], int(raw_id)


def _with_owner_suffix(callback_data: str, owner_user_id: int | None) -> str:
    suffix = _owner_to_short(owner_user_id)
    if suffix is None:
        return callback_data
    return f"{callback_data}:{suffix}"


async def _enforce_panel_owner(
    query: CallbackQuery,
    *,
    owner_user_id: int | None,
) -> bool:
    if owner_user_id is None:
        return True
    if query.from_user is None:
        return False
    if query.message is None or query.message.chat.type not in _GROUP_CHAT_TYPES:
        return True
    if owner_user_id == query.from_user.id:
        return True
    await _safe_query_answer(query, "Панель другого игрока, откройте свою через /eco.", show_alert=True)
    return False


def _effective_mode(*, chat_type: str, chat_settings: ChatSettings, mode_hint: str | None) -> str:
    if chat_type in {"group", "supergroup"}:
        return chat_settings.economy_mode
    if mode_hint in {"global", "local"}:
        return mode_hint
    return "global"


def _format_td_seconds(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}с"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}м {sec:02d}с"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}ч {minutes:02d}м"


def _format_size_mm(size_mm: int) -> str:
    return f"{size_mm / 10:.1f}".replace(".", ",")


def _auction_text(auction: ChatAuction, *, leader_label: str | None) -> str:
    current_bid = auction.current_bid if auction.current_bid > 0 else auction.start_price
    leader = leader_label or "пока нет"
    ends_at = auction.ends_at.strftime("%H:%M UTC")
    return (
        "<b>Live-аукцион</b>\n"
        f"<b>Лот:</b> <code>{escape(localize_item_code(auction.item_code))}</code> × <code>{auction.quantity}</code>\n"
        f"<b>Старт:</b> <code>{auction.start_price}</code> | <b>Шаг:</b> <code>{auction.min_increment}</code>\n"
        f"<b>Текущая ставка:</b> <code>{current_bid}</code>\n"
        f"<b>Лидер:</b> <code>{escape(leader)}</code>\n"
        f"<b>Финиш:</b> <code>{ends_at}</code>\n"
        "Ставка: <code>/bid 5000</code> или текстом <code>ставка 5000</code>."
    )


async def _resolve_auction_leader_label(activity_repo, auction: ChatAuction) -> str | None:
    if auction.highest_bid_user_id is None:
        return None
    label = await activity_repo.get_chat_display_name(chat_id=auction.chat_id, user_id=auction.highest_bid_user_id)
    if label:
        return label
    snapshot = await activity_repo.get_user_snapshot(user_id=auction.highest_bid_user_id)
    if snapshot is None:
        return f"user:{auction.highest_bid_user_id}"
    return snapshot.chat_display_name or snapshot.username or snapshot.first_name or f"user:{auction.highest_bid_user_id}"


async def _finalize_auction_task(
    *,
    auction_id: int,
    chat_id: int,
    bot,
    session_factory,
) -> None:
    try:
        while True:
            async with session_factory() as session:
                from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository, SqlAlchemyEconomyRepository

                activity_repo = SqlAlchemyActivityRepository(session)
                economy_repo = SqlAlchemyEconomyRepository(session)
                auction = await economy_repo.get_chat_auction(auction_id=auction_id)
                if auction is None or auction.status != "open":
                    await session.commit()
                    return
                delay = (auction.ends_at - datetime.now(timezone.utc)).total_seconds()
                if delay > 0:
                    await session.commit()
                else:
                    result = await auction_finalize(economy_repo, auction_id=auction_id)
                    if result.auction is not None:
                        leader_label = await _resolve_auction_leader_label(activity_repo, result.auction)
                        text = (
                            "🏁 <b>Аукцион завершён.</b>\n"
                            f"<b>Лот:</b> <code>{escape(localize_item_code(result.auction.item_code))}</code>\n"
                        )
                        if result.winner_user_id is not None and result.auction.current_bid > 0:
                            text += (
                                f"<b>Победитель:</b> <code>{escape(leader_label or str(result.winner_user_id))}</code>\n"
                                f"<b>Ставка:</b> <code>{result.auction.current_bid}</code>"
                            )
                        else:
                            text += "Ставок не было, лот возвращён владельцу."
                        if result.auction.message_id is not None:
                            try:
                                await bot.edit_message_text(
                                    chat_id=chat_id,
                                    message_id=result.auction.message_id,
                                    text=text,
                                    parse_mode="HTML",
                                )
                            except TelegramBadRequest:
                                await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                        else:
                            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                        await log_chat_action(
                            activity_repo,
                            chat_id=chat_id,
                            chat_type="group",
                            chat_title=None,
                            action_code="auction_finalized",
                            description=f"Аукцион #{auction_id} завершён.",
                            actor_user_id=result.auction.seller_user_id,
                            target_user_id=result.winner_user_id,
                            meta_json={"bid": result.auction.current_bid},
                        )
                    await session.commit()
                    return
            await asyncio.sleep(max(1, int(delay)))
    except asyncio.CancelledError:
        return


def _schedule_auction_finalize(*, auction: ChatAuction, chat_id: int, bot, session_factory) -> None:
    existing = _AUCTION_TASKS.pop(auction.id, None)
    if existing is not None:
        existing.cancel()
    _AUCTION_TASKS[auction.id] = asyncio.create_task(
        _finalize_auction_task(
            auction_id=auction.id,
            chat_id=chat_id,
            bot=bot,
            session_factory=session_factory,
        )
    )


def _normalize_crop_input_or_raw(value: str) -> str:
    return normalize_crop_input(value)


def _normalize_item_input(value: str, *, allow_crops: bool = False) -> str:
    normalized = value.strip().lower()
    if not normalized:
        return normalized
    if normalized.startswith(("crop:", "item:", "seed:", "upgrade:")):
        return normalized

    item_alias = _RUSSIAN_ITEM_ALIASES.get(normalized)
    if item_alias is not None:
        return item_alias

    crop_code = normalize_crop_input(normalized)
    if allow_crops and crop_code in CROPS:
        return f"crop:{crop_code}"

    return normalized


def _extract_mode_and_tokens(message: Message, chat_settings: ChatSettings, raw_args: str | None) -> tuple[str, list[str]]:
    tokens = [token for token in (raw_args or "").strip().split() if token]
    mode_hint: str | None = None
    if tokens and tokens[0].lower() in {"global", "local"}:
        mode_hint = tokens[0].lower()
        tokens = tokens[1:]

    mode = _effective_mode(chat_type=message.chat.type, chat_settings=chat_settings, mode_hint=mode_hint)
    return mode, tokens


def growth_action_disabled_text() -> str:
    return "18+ действия отключены в этом чате. Включить: <code>/setcfg actions_18_enabled true</code>."


def growth_action_disabled_plain_text() -> str:
    return "18+ действия отключены в этом чате. Включить: /setcfg actions_18_enabled true."


async def is_growth_action_allowed(
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
    chat_settings: ChatSettings,
    activity_repo,
    economy_repo,
    settings: Settings,
) -> bool:
    if chat_id is not None:
        return bool(chat_settings.actions_18_enabled)

    if economy_mode != "local":
        return bool(chat_settings.actions_18_enabled)

    context_chat_id = await economy_repo.get_private_chat_context(user_id=user_id)
    if context_chat_id is None:
        return bool(chat_settings.actions_18_enabled)

    try:
        saved = await activity_repo.get_chat_settings(chat_id=context_chat_id)
    except Exception:
        saved = None
    if saved is not None:
        return bool(saved.actions_18_enabled)

    return bool(default_chat_settings(settings).actions_18_enabled)


def _dashboard_text(dashboard, *, show_growth: bool = True) -> str:
    account = dashboard.account
    farm = dashboard.farm
    active_slots = FARM_LEVEL_PLOTS.get(farm.farm_level, 2)

    crop_count = 0
    item_count = 0
    for item in dashboard.inventory:
        if item.item_code.startswith("crop:"):
            crop_count += item.quantity
        else:
            item_count += item.quantity

    lines = [
        "<b>Экономика Selara</b>",
        f"<b>Режим:</b> <code>{escape(localize_scope(dashboard.scope.scope_id))}</code>",
        f"<b>Баланс:</b> <code>{account.balance}</code> монет",
        f"<b>Ферма:</b> уровень <code>{farm.farm_level}</code>, размер <code>{escape(localize_size_tier(farm.size_tier))}</code>, слотов <code>{active_slots}</code>",
        f"<b>Серии:</b> тап <code>{account.tap_streak}</code>, daily <code>{account.daily_streak}</code>",
        f"<b>Инвентарь:</b> культуры <code>{crop_count}</code>, предметы <code>{item_count}</code>",
    ]
    if show_growth:
        effective_stress = effective_growth_stress_pct(
            last_growth_at=account.last_growth_at,
            stress_pct=account.growth_stress_pct,
            as_of=datetime.now(timezone.utc),
        )
        lines.insert(
            4,
            f"<b>Рост:</b> <code>{_format_size_mm(account.growth_size_mm)} см</code>, стресс <code>{effective_stress}%</code>, действий <code>{account.growth_actions}</code>",
        )

    if account.last_daily_claimed_at is not None:
        lines.append(f"<b>Последний daily:</b> <code>{account.last_daily_claimed_at.strftime('%d.%m %H:%M UTC')}</code>")

    return "\n".join(lines)


def _farm_text(dashboard) -> str:
    farm = dashboard.farm
    account = dashboard.account
    slots = FARM_LEVEL_PLOTS.get(farm.farm_level, 2)
    lines = [
        "<b>Ферма</b>",
        f"<b>Баланс:</b> <code>{account.balance}</code>",
        f"<b>Уровень:</b> <code>{farm.farm_level}</code> | <b>Размер:</b> <code>{escape(localize_size_tier(farm.size_tier))}</code>",
        f"<b>Последняя культура:</b> <code>{escape(localize_crop_code(farm.last_planted_crop_code))}</code>",
        "",
        "<b>Грядки:</b>",
    ]

    now = datetime.now(timezone.utc)
    by_no = {plot.plot_no: plot for plot in dashboard.plots}
    for no in range(1, slots + 1):
        plot = by_no.get(no)
        if plot is None or plot.crop_code is None:
            lines.append(f"{no}. пусто")
            continue

        if plot.ready_at is None:
            lines.append(f"{no}. {localize_crop_code(plot.crop_code)} (статус: неизвестен)")
            continue

        if plot.ready_at <= now:
            lines.append(f"{no}. {localize_crop_code(plot.crop_code)} ✅ готово к сбору")
            continue

        remain = int((plot.ready_at - now).total_seconds())
        lines.append(f"{no}. {localize_crop_code(plot.crop_code)} ⏳ {_format_td_seconds(remain)}")

    crops: list[str] = []
    seeds: list[str] = []
    items: list[str] = []
    for item in dashboard.inventory:
        if item.quantity <= 0:
            continue
        line = f"- {escape(localize_item_code(item.item_code))}: <code>{item.quantity}</code>"
        if item.item_code.startswith("crop:"):
            crops.append(line)
        elif item.item_code.startswith("seed:"):
            seeds.append(line)
        else:
            items.append(line)

    lines.append("")
    lines.append("<b>Остатки на складе:</b>")
    lines.append("<b>Урожай:</b>")
    lines.extend(crops or ["- пусто"])
    lines.append("")
    lines.append("<b>Семена:</b>")
    lines.extend(seeds or ["- пусто"])
    lines.append("")
    lines.append("<b>Предметы:</b>")
    lines.extend(items or ["- пусто"])
    return "\n".join(lines)


def _inventory_text(dashboard) -> str:
    account = dashboard.account
    crops: list[str] = []
    items: list[str] = []

    for item in dashboard.inventory:
        if item.quantity <= 0:
            continue
        if item.item_code.startswith("crop:"):
            crops.append(f"- {escape(localize_item_code(item.item_code))}: <code>{item.quantity}</code>")
        else:
            items.append(f"- {escape(localize_item_code(item.item_code))}: <code>{item.quantity}</code>")

    lines = [
        "<b>Инвентарь</b>",
        f"<b>Баланс:</b> <code>{account.balance}</code>",
        (
            "<b>Апгрейды:</b> "
            f"спринклер <code>{account.sprinkler_level}</code>, "
            f"перчатка тапа <code>{account.tap_glove_level}</code>, "
            f"стеллаж <code>{account.storage_level}</code>"
        ),
        "",
        "<b>Культуры:</b>",
    ]
    lines.extend(crops or ["- пусто"])
    lines.append("")
    lines.append("<b>Предметы:</b>")
    lines.extend(items or ["- пусто"])
    lines.append("")
    lines.append("Использовать: <code>/inventory use энергетик</code> или <code>/inventory use item:fertilizer_fast 1</code>")
    return "\n".join(lines)


def _shop_text(offers, balance: int, scope_id: str) -> str:
    lines = [
        "<b>Магазин</b>",
        f"<b>Режим экономики:</b> <code>{escape(localize_scope(scope_id))}</code>",
        f"<b>Баланс:</b> <code>{balance}</code>",
        "",
    ]

    if not offers:
        lines.append("Сегодня офферов нет.")
        return "\n".join(lines)

    for idx, offer in enumerate(offers, start=1):
        lines.append(
            f"{idx}. {escape(localize_offer_code(offer.offer_code))} — <code>{offer.price}</code> мон."
        )
    lines.append("")
    lines.append("Покупка: <code>/shop buy &lt;номер_оффера&gt;</code> или кнопкой ниже")
    return "\n".join(lines)


def _lottery_text(dashboard) -> str:
    account = dashboard.account
    today = date.today()
    free_status = "использован" if account.free_lottery_claimed_on == today else "доступен"
    paid_today = account.paid_lottery_used_today if account.paid_lottery_used_on == today else 0

    return "\n".join(
        [
            "<b>Лотерея</b>",
            f"<b>Баланс:</b> <code>{account.balance}</code>",
            f"<b>Бесплатный билет:</b> {free_status}",
            f"<b>Платные билеты сегодня:</b> <code>{paid_today}</code>",
            "",
            "Команды: <code>/lottery free</code>, <code>/lottery paid</code>, <code>/lottery item</code>",
        ]
    )


def _market_text(scope_id: str, listings) -> str:
    lines = [
        "<b>Рынок</b>",
        f"<b>Режим экономики:</b> <code>{escape(localize_scope(scope_id))}</code>",
        "",
    ]
    if not listings:
        lines.append("Открытых лотов нет.")
        lines.append("Создать: <code>/market sell редис 10 12</code>")
        return "\n".join(lines)

    for row in listings:
        lines.append(
            f"#{row.id} | {escape(localize_item_code(row.item_code))} | {row.qty_left}/{row.qty_total} | {row.unit_price}/шт | продавец {row.seller_user_id}"
        )
    lines.append("")
    lines.append("Покупка: <code>/market buy &lt;id&gt; &lt;qty&gt;</code>")
    lines.append("Отмена: <code>/market cancel &lt;id&gt;</code>")
    return "\n".join(lines)


def _craft_text() -> str:
    lines = ["<b>Крафт</b>"]
    for recipe in RECIPES.values():
        ingredients = ", ".join(
            f"{escape(localize_item_code(item_code))} × <code>{qty}</code>"
            for item_code, qty in recipe.ingredients
        )
        lines.append(
            f"• <b>{escape(recipe.title)}</b> → <code>{escape(localize_item_code(recipe.result_item_code))}</code>\n"
            f"  {ingredients}\n"
            f"  {escape(recipe.description)}"
        )
    lines.append("")
    lines.append("Скрафтить: <code>/craft пицца</code>")
    return "\n".join(lines)


def _growth_text(profile) -> str:
    if not profile.accepted:
        return profile.reason or "Не удалось открыть механику роста."

    next_line = "доступно сейчас"
    if profile.next_available_at is not None:
        now = datetime.now(timezone.utc)
        remain = max(1, int((profile.next_available_at - now).total_seconds()))
        next_line = _format_td_seconds(remain)

    stress_bar_fill = max(0, min(10, profile.stress_pct // 10))
    stress_bar = "█" * stress_bar_fill + "·" * (10 - stress_bar_fill)

    return "\n".join(
        [
            "<b>Профиль роста</b>",
            f"<b>Размер:</b> <code>{_format_size_mm(profile.size_mm)} см</code>",
            f"<b>Стресс:</b> <code>{profile.stress_pct}%</code> <code>{stress_bar}</code>",
            f"<b>Всего действий:</b> <code>{profile.actions}</code>",
            f"<b>Баланс:</b> <code>{profile.balance or 0}</code> монет",
            f"<b>Кулдаун:</b> {next_line}",
            f"<b>Пассивный спад:</b> <code>-{STRESS_DECAY_PER_HOUR}%/час</code>",
            "",
            "Команды: <code>/growth</code>, <code>/growth do</code>",
        ]
    )


def _build_dashboard_keyboard(mode: str, *, owner_user_id: int | None) -> InlineKeyboardMarkup:
    m = _mode_to_short(mode)
    builder = InlineKeyboardBuilder()
    builder.button(text="👆 Тап", callback_data=_with_owner_suffix(f"eco:tap:{m}", owner_user_id))
    builder.button(text="🎁 Daily", callback_data=_with_owner_suffix(f"eco:daily:{m}", owner_user_id))
    builder.button(text="🌱 Ферма", callback_data=_with_owner_suffix(f"farm:ov:{m}", owner_user_id))
    builder.button(text="🛒 Магазин", callback_data=_with_owner_suffix(f"shop:ov:{m}", owner_user_id))
    builder.button(text="🎒 Инвентарь", callback_data=_with_owner_suffix(f"inv:ov:{m}", owner_user_id))
    builder.button(text="📏 Рост", callback_data=_with_owner_suffix(f"grw:ov:{m}", owner_user_id))
    builder.button(text="🎲 Лотерея", callback_data=_with_owner_suffix(f"lot:ov:{m}", owner_user_id))
    builder.button(text="📦 Рынок", callback_data=_with_owner_suffix(f"mkt:ov:{m}", owner_user_id))
    builder.button(text="🔄 Обновить", callback_data=_with_owner_suffix(f"eco:dash:{m}", owner_user_id))
    builder.adjust(2, 2, 2, 2, 1)
    return builder.as_markup()


def _build_farm_keyboard(mode: str, dashboard, *, owner_user_id: int | None) -> InlineKeyboardMarkup:
    m = _mode_to_short(mode)
    builder = InlineKeyboardBuilder()

    slots = FARM_LEVEL_PLOTS.get(dashboard.farm.farm_level, 2)
    by_no = {plot.plot_no: plot for plot in dashboard.plots}

    first_empty = None
    for no in range(1, slots + 1):
        plot = by_no.get(no)
        if plot is None or plot.crop_code is None:
            first_empty = no
            break

    if first_empty is not None:
        builder.button(
            text=f"Посадить редис #{first_empty}",
            callback_data=_with_owner_suffix(f"farm:p:{m}:radish:{first_empty}", owner_user_id),
        )
    if first_empty is not None and dashboard.farm.last_planted_crop_code:
        builder.button(
            text=f"Засадить всё: {localize_crop_code(dashboard.farm.last_planted_crop_code)}",
            callback_data=_with_owner_suffix(f"farm:pa:{m}", owner_user_id),
        )

    now = datetime.now(timezone.utc)
    ready_count = 0
    for no in range(1, slots + 1):
        plot = by_no.get(no)
        if plot is None or plot.crop_code is None or plot.ready_at is None:
            continue
        if plot.ready_at <= now:
            ready_count += 1
            builder.button(text=f"Собрать #{no}", callback_data=_with_owner_suffix(f"farm:h:{m}:{no}", owner_user_id))

    if ready_count > 1:
        builder.button(text="Собрать всё", callback_data=_with_owner_suffix(f"farm:ha:{m}", owner_user_id))

    builder.button(text="↩️ Панель", callback_data=_with_owner_suffix(f"eco:dash:{m}", owner_user_id))
    builder.adjust(1)
    return builder.as_markup()


def _build_shop_keyboard(mode: str, offers, *, owner_user_id: int | None) -> InlineKeyboardMarkup:
    m = _mode_to_short(mode)
    builder = InlineKeyboardBuilder()
    for offer in offers[:6]:
        label = offer.title if len(offer.title) <= 16 else f"{offer.title[:13]}..."
        builder.button(
            text=f"Купить {label}",
            callback_data=_with_owner_suffix(f"shop:b:{m}:{offer.offer_code}", owner_user_id),
        )
    builder.button(text="↩️ Панель", callback_data=_with_owner_suffix(f"eco:dash:{m}", owner_user_id))
    builder.adjust(1)
    return builder.as_markup()


def _build_inventory_keyboard(
    mode: str,
    dashboard,
    *,
    owner_user_id: int | None,
    page: int = 0,
) -> InlineKeyboardMarkup:
    m = _mode_to_short(mode)
    builder = InlineKeyboardBuilder()
    items = [item for item in dashboard.inventory if item.item_code.startswith("item:") and item.quantity > 0]
    total_pages = max(1, (len(items) + _INVENTORY_PAGE_SIZE - 1) // _INVENTORY_PAGE_SIZE)
    safe_page = max(0, min(page, total_pages - 1))
    start_index = safe_page * _INVENTORY_PAGE_SIZE
    visible_items = items[start_index : start_index + _INVENTORY_PAGE_SIZE]
    for item in visible_items:
        short = item.item_code.removeprefix("item:")
        label = localize_item_code(item.item_code)
        if len(label) > 14:
            label = f"{label[:11]}..."
        builder.button(
            text=f"Исп. {label}",
            callback_data=_with_owner_suffix(f"inv:u:{m}:{short}:0:{safe_page}", owner_user_id),
        )
    if total_pages > 1:
        prev_page = safe_page - 1 if safe_page > 0 else total_pages - 1
        next_page = safe_page + 1 if safe_page + 1 < total_pages else 0
        builder.button(text="<", callback_data=_with_owner_suffix(f"inv:ov:{m}:{prev_page}", owner_user_id))
        builder.button(
            text=f"{safe_page + 1}/{total_pages}",
            callback_data=_with_owner_suffix(f"inv:ov:{m}:{safe_page}", owner_user_id),
        )
        builder.button(text=">", callback_data=_with_owner_suffix(f"inv:ov:{m}:{next_page}", owner_user_id))
    builder.button(text="↩️ Панель", callback_data=_with_owner_suffix(f"eco:dash:{m}", owner_user_id))
    rows = [1] * len(visible_items)
    if total_pages > 1:
        rows.append(3)
    rows.append(1)
    builder.adjust(*rows)
    return builder.as_markup()


def _build_lottery_keyboard(mode: str, *, owner_user_id: int | None) -> InlineKeyboardMarkup:
    m = _mode_to_short(mode)
    builder = InlineKeyboardBuilder()
    builder.button(text="Бесплатный", callback_data=_with_owner_suffix(f"lot:d:{m}:free", owner_user_id))
    builder.button(text="Платный", callback_data=_with_owner_suffix(f"lot:d:{m}:paid", owner_user_id))
    builder.button(text="Из предмета", callback_data=_with_owner_suffix(f"lot:d:{m}:item", owner_user_id))
    builder.button(text="↩️ Панель", callback_data=_with_owner_suffix(f"eco:dash:{m}", owner_user_id))
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def _build_market_keyboard(mode: str, listings, *, owner_user_id: int | None) -> InlineKeyboardMarkup:
    m = _mode_to_short(mode)
    builder = InlineKeyboardBuilder()
    for listing in listings[:4]:
        builder.button(
            text=f"Купить #{listing.id} x1",
            callback_data=_with_owner_suffix(f"mkt:b:{m}:{listing.id}:1", owner_user_id),
        )
    builder.button(text="🔄 Обновить", callback_data=_with_owner_suffix(f"mkt:ov:{m}", owner_user_id))
    builder.button(text="↩️ Панель", callback_data=_with_owner_suffix(f"eco:dash:{m}", owner_user_id))
    builder.adjust(1)
    return builder.as_markup()


def _build_growth_keyboard(mode: str, *, owner_user_id: int | None) -> InlineKeyboardMarkup:
    m = _mode_to_short(mode)
    builder = InlineKeyboardBuilder()
    builder.button(text="💥 Действие", callback_data=_with_owner_suffix(f"grw:d:{m}", owner_user_id))
    builder.button(text="🎒 Инвентарь", callback_data=_with_owner_suffix(f"inv:ov:{m}", owner_user_id))
    builder.button(text="↩️ Панель", callback_data=_with_owner_suffix(f"eco:dash:{m}", owner_user_id))
    builder.adjust(1)
    return builder.as_markup()


async def _send_dashboard(message: Message, economy_repo, chat_settings: ChatSettings, *, mode: str) -> None:
    if message.from_user is None:
        return

    chat_id = message.chat.id if message.chat.type in {"group", "supergroup"} else None
    dashboard, error = await get_dashboard(
        economy_repo,
        economy_mode=mode,
        chat_id=chat_id,
        user_id=message.from_user.id,
    )
    if dashboard is None:
        await _answer_message(message, error or "Не удалось открыть экономику")
        return

    await _answer_message(
        message,
        _dashboard_text(dashboard, show_growth=chat_settings.actions_18_enabled),
        parse_mode="HTML",
        reply_markup=_build_dashboard_keyboard(mode, owner_user_id=message.from_user.id),
    )


def _plain_from_html(text: str) -> str:
    return _HTML_DISPLAY_TAGS_RE.sub("", text)


async def _answer_message(
    message: Message,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    cleanup_bot: Bot | None = None,
    cleanup_enabled: bool = False,
) -> Message:
    kwargs = {}
    if message.chat.type in _GROUP_CHAT_TYPES:
        kwargs["disable_notification"] = True
    reply = await message.answer(text, parse_mode=parse_mode, reply_markup=reply_markup, **kwargs)
    if cleanup_enabled and cleanup_bot is not None and reply_markup is None:
        _schedule_economy_cleanup(
            cleanup_bot,
            chat_id=message.chat.id,
            message_ids=(message.message_id, reply.message_id),
        )
    return reply


def _schedule_economy_cleanup(bot: Bot, *, chat_id: int, message_ids: tuple[int, ...]) -> None:
    async def _runner() -> None:
        await asyncio.sleep(_ECONOMY_CLEANUP_DELAY_SECONDS)
        for message_id in message_ids:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except TelegramBadRequest:
                continue
            except Exception:
                continue

    asyncio.create_task(_runner())


async def _edit_or_answer(query: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None) -> None:
    if query.message is None:
        await _safe_query_answer(query)
        return

    try:
        await query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        return
    except TelegramBadRequest as exc:
        error_text = str(exc).lower()
        if "message is not modified" in error_text:
            return
        if "can't parse entities" in error_text:
            plain_text = _plain_from_html(text)
            try:
                await query.message.edit_text(plain_text, reply_markup=markup)
                return
            except TelegramBadRequest:
                pass
        if "message to edit not found" in error_text or "message can't be edited" in error_text:
            if query.message.chat.type in _GROUP_CHAT_TYPES:
                await _safe_query_answer(query, "Панель устарела, откройте /eco заново.", show_alert=False)
                return
    except Exception:
        pass

    if query.message.chat.type in _GROUP_CHAT_TYPES:
        await _safe_query_answer(query, "Не удалось обновить панель. Используйте /eco.", show_alert=False)
        return

    try:
        await query.message.answer(text, parse_mode="HTML", reply_markup=markup)
    except TelegramBadRequest:
        await query.message.answer(_plain_from_html(text), reply_markup=markup)


async def _safe_query_answer(query: CallbackQuery, *args, **kwargs) -> None:
    try:
        await query.answer(*args, **kwargs)
    except TelegramBadRequest:
        return


def _parse_mode_from_callback(query: CallbackQuery, index: int, chat_settings: ChatSettings) -> str:
    if not query.data:
        return "global"
    parts, _ = _extract_owner_from_parts(query.data.split(":"))
    if query.message and query.message.chat.type in {"group", "supergroup"}:
        return chat_settings.economy_mode
    if len(parts) <= index:
        return "global"
    return _short_to_mode(parts[index])


@router.message(Command("eco"))
async def eco_command(message: Message, command: CommandObject, economy_repo, chat_settings: ChatSettings) -> None:
    if message.from_user is None:
        return
    if not chat_settings.economy_enabled:
        await _answer_message(message, "Экономика отключена для этого чата.")
        return

    mode, tokens = _extract_mode_and_tokens(message, chat_settings, command.args)

    if message.chat.type == "private" and tokens and tokens[0].isdigit():
        chat_id = int(tokens[0])
        await economy_repo.set_private_chat_context(user_id=message.from_user.id, chat_id=chat_id)
        mode = "local"
        await _answer_message(message, f"Контекст local-режима установлен: chat:{chat_id}")

    await _send_dashboard(message, economy_repo, chat_settings, mode=mode)


@router.message(Command("tap"))
async def tap_command(message: Message, command: CommandObject, bot: Bot, economy_repo, chat_settings: ChatSettings) -> None:
    if message.from_user is None:
        return
    if not chat_settings.economy_enabled:
        await _answer_message(message, "Экономика отключена для этого чата.")
        return

    mode, _ = _extract_mode_and_tokens(message, chat_settings, command.args)
    chat_id = message.chat.id if message.chat.type in {"group", "supergroup"} else None

    result = await tap(
        economy_repo,
        economy_mode=mode,
        chat_id=chat_id,
        user_id=message.from_user.id,
        tap_cooldown_seconds=chat_settings.economy_tap_cooldown_seconds,
    )
    if not result.accepted:
        await _answer_message(message, result.reason or "Тап не выполнен")
        return

    proc = " x4!" if result.proc_x4 else ""
    await _answer_message(
        message,
        f"Тап засчитан: +{result.reward}{proc}. Баланс: {result.new_balance}. Серия: {result.tap_streak}.",
        cleanup_bot=bot,
        cleanup_enabled=chat_settings.cleanup_economy_commands,
    )


@router.message(Command("daily"))
async def daily_command(message: Message, command: CommandObject, bot: Bot, economy_repo, chat_settings: ChatSettings) -> None:
    if message.from_user is None:
        return
    if not chat_settings.economy_enabled:
        await _answer_message(message, "Экономика отключена для этого чата.")
        return

    mode, _ = _extract_mode_and_tokens(message, chat_settings, command.args)
    chat_id = message.chat.id if message.chat.type in {"group", "supergroup"} else None

    result = await claim_daily(
        economy_repo,
        economy_mode=mode,
        chat_id=chat_id,
        user_id=message.from_user.id,
        daily_base_reward=chat_settings.economy_daily_base_reward,
        daily_streak_cap=chat_settings.economy_daily_streak_cap,
    )
    if not result.accepted:
        await _answer_message(message, result.reason or "Daily недоступен")
        return

    bonus_ticket = " +билет" if result.granted_lottery_ticket else ""
    await _answer_message(
        message,
        f"Daily: +{result.reward}{bonus_ticket}. Серия: {result.streak}. Баланс: {result.new_balance}.",
        cleanup_bot=bot,
        cleanup_enabled=chat_settings.cleanup_economy_commands,
    )


@router.message(Command("farm"))
async def farm_command(message: Message, command: CommandObject, bot: Bot, economy_repo, chat_settings: ChatSettings) -> None:
    if message.from_user is None:
        return
    if not chat_settings.economy_enabled:
        await _answer_message(message, "Экономика отключена для этого чата.")
        return

    mode, tokens = _extract_mode_and_tokens(message, chat_settings, command.args)
    chat_id = message.chat.id if message.chat.type in {"group", "supergroup"} else None

    if not tokens:
        dashboard, error = await get_dashboard(economy_repo, economy_mode=mode, chat_id=chat_id, user_id=message.from_user.id)
        if dashboard is None:
            await _answer_message(message, error or "Не удалось открыть ферму")
            return
        await _answer_message(
            message,
            _farm_text(dashboard),
            parse_mode="HTML",
            reply_markup=_build_farm_keyboard(mode, dashboard, owner_user_id=message.from_user.id),
        )
        return

    action = tokens[0].lower()

    if action == "plant":
        if len(tokens) < 2:
            await _answer_message(message, "Формат: /farm plant <культура> [грядка]")
            return
        crop_code = _normalize_crop_input_or_raw(tokens[1])
        plot_no = int(tokens[2]) if len(tokens) >= 3 and tokens[2].isdigit() else None
        result = await plant_crop(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            user_id=message.from_user.id,
            crop_code=crop_code,
            plot_no=plot_no,
        )
        if not result.accepted:
            await _answer_message(message, result.reason or "Не удалось посадить")
            return
        await _answer_message(
            message,
            f"Посадка успешна: {localize_crop_code(result.crop_code)} в грядку #{result.plot_no}. Готово: {result.ready_at}. Баланс: {result.new_balance}",
            cleanup_bot=bot,
            cleanup_enabled=chat_settings.cleanup_economy_commands,
        )
        return

    if action in {"plantall", "plant_all"}:
        crop_code = _normalize_crop_input_or_raw(tokens[1]) if len(tokens) >= 2 else None
        result = await plant_all_last_crop(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            user_id=message.from_user.id,
            crop_code=crop_code,
        )
        if not result.accepted:
            await _answer_message(message, result.reason or "Не удалось засадить грядки")
            return
        crop_label = localize_crop_code(result.crop_code)
        await _answer_message(
            message,
            f"Засажено грядок: {len(result.planted_plots)}. Культура: {crop_label}. Баланс: {result.new_balance}.",
            cleanup_bot=bot,
            cleanup_enabled=chat_settings.cleanup_economy_commands,
        )
        return

    if action == "harvest":
        if len(tokens) < 2 or not tokens[1].isdigit():
            await _answer_message(message, "Формат: /farm harvest <грядка>")
            return
        result = await harvest_crop(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            user_id=message.from_user.id,
            plot_no=int(tokens[1]),
            negative_event_chance_percent=chat_settings.economy_negative_event_chance_percent,
            negative_event_loss_percent=chat_settings.economy_negative_event_loss_percent,
        )
        if not result.accepted:
            await _answer_message(message, result.reason or "Не удалось собрать урожай")
            return
        event_text = f" | событие: {result.event}" if result.event else ""
        await _answer_message(
            message,
            f"Собрано: {result.amount} x {localize_crop_code(result.crop_code)}{event_text}",
            cleanup_bot=bot,
            cleanup_enabled=chat_settings.cleanup_economy_commands,
        )
        return

    if action in {"harvestall", "harvest_all"}:
        result = await harvest_all_ready(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            user_id=message.from_user.id,
            negative_event_chance_percent=chat_settings.economy_negative_event_chance_percent,
            negative_event_loss_percent=chat_settings.economy_negative_event_loss_percent,
        )
        if not result.accepted:
            await _answer_message(message, result.reason or "Нет готовых грядок")
            return
        summary = ", ".join(f"{localize_crop_code(code)} x{qty}" for code, qty in result.crop_totals)
        await _answer_message(
            message,
            f"Собрано со всех готовых грядок: {summary}. Всего: {result.total_amount}.",
            cleanup_bot=bot,
            cleanup_enabled=chat_settings.cleanup_economy_commands,
        )
        return

    if action == "upfarm":
        dashboard, error = await get_dashboard(economy_repo, economy_mode=mode, chat_id=chat_id, user_id=message.from_user.id)
        if dashboard is None:
            await _answer_message(message, error or "Не удалось открыть ферму")
            return

        current = dashboard.farm.farm_level
        next_level = current + 1
        cost = FARM_LEVEL_UPGRADE_COST.get(next_level)
        if cost is None:
            await _answer_message(message, "Ферма уже максимального уровня.")
            return
        if dashboard.account.balance < cost:
            await _answer_message(message, f"Недостаточно монет. Нужно {cost}.")
            return

        await economy_repo.add_balance(account_id=dashboard.account.id, delta=-cost)
        await economy_repo.update_farm_level(account_id=dashboard.account.id, farm_level=next_level)
        await economy_repo.add_ledger(
            account_id=dashboard.account.id,
            direction="out",
            amount=cost,
            reason="farm_upgrade_level",
            meta_json=f'{{"to": {next_level}}}',
        )
        await _answer_message(
            message,
            f"Ферма улучшена до уровня {next_level}.",
            cleanup_bot=bot,
            cleanup_enabled=chat_settings.cleanup_economy_commands,
        )
        return

    if action == "upsize":
        if len(tokens) < 2:
            await _answer_message(message, "Формат: /farm upsize <средний|большой>")
            return
        target = tokens[1].lower()
        target = {"средний": "medium", "большой": "large", "малый": "small"}.get(target, target)
        if target not in SIZE_TIERS:
            await _answer_message(message, "Доступные размеры: medium, large")
            return

        dashboard, error = await get_dashboard(economy_repo, economy_mode=mode, chat_id=chat_id, user_id=message.from_user.id)
        if dashboard is None:
            await _answer_message(message, error or "Не удалось открыть ферму")
            return

        order = {"small": 1, "medium": 2, "large": 3}
        current = dashboard.farm.size_tier
        if order.get(target, 0) <= order.get(current, 0):
            await _answer_message(message, "Этот размер уже активен или ниже текущего.")
            return

        cost = SIZE_TIERS[target].price
        if dashboard.account.balance < cost:
            await _answer_message(message, f"Недостаточно монет. Нужно {cost}.")
            return

        await economy_repo.add_balance(account_id=dashboard.account.id, delta=-cost)
        await economy_repo.update_farm_size_tier(account_id=dashboard.account.id, size_tier=target)
        await economy_repo.add_ledger(
            account_id=dashboard.account.id,
            direction="out",
            amount=cost,
            reason="farm_upgrade_size",
            meta_json=f'{{"to": "{target}"}}',
        )
        await _answer_message(
            message,
            f"Размер фермы обновлён до {localize_size_tier(target)}.",
            cleanup_bot=bot,
            cleanup_enabled=chat_settings.cleanup_economy_commands,
        )
        return

    if action == "sell":
        if len(tokens) < 3 or not tokens[2].isdigit():
            await _answer_message(message, "Формат: /farm sell <культура> <кол-во>")
            return

        crop_code = _normalize_crop_input_or_raw(tokens[1])
        qty = int(tokens[2])
        crop = CROPS.get(crop_code)
        if crop is None:
            await _answer_message(message, "Неизвестная культура")
            return

        dashboard, error = await get_dashboard(economy_repo, economy_mode=mode, chat_id=chat_id, user_id=message.from_user.id)
        if dashboard is None:
            await _answer_message(message, error or "Не удалось открыть ферму")
            return

        item_code = f"crop:{crop_code}"
        stock = await economy_repo.get_inventory_item(account_id=dashboard.account.id, item_code=item_code)
        if stock is None or stock.quantity < qty:
            await _answer_message(message, "Недостаточно культуры в инвентаре")
            return

        revenue = qty * crop.sell_price
        await economy_repo.add_inventory_item(account_id=dashboard.account.id, item_code=item_code, delta=-qty)
        balance = await economy_repo.add_balance(account_id=dashboard.account.id, delta=revenue)
        await economy_repo.add_ledger(
            account_id=dashboard.account.id,
            direction="in",
            amount=revenue,
            reason="crop_sell",
            meta_json=f'{{"crop": "{crop_code}", "qty": {qty}}}',
        )
        await _answer_message(
            message,
            f"Продано {qty} x {crop.title} за {revenue}. Баланс: {balance}",
            cleanup_bot=bot,
            cleanup_enabled=chat_settings.cleanup_economy_commands,
        )
        return

    await _answer_message(message, "Действия: plant, plantall, harvest, harvestall, upfarm, upsize, sell")


@router.message(Command("shop"))
async def shop_command(message: Message, command: CommandObject, bot: Bot, economy_repo, chat_settings: ChatSettings) -> None:
    if message.from_user is None:
        return
    if not chat_settings.economy_enabled:
        await _answer_message(message, "Экономика отключена для этого чата.")
        return

    mode, tokens = _extract_mode_and_tokens(message, chat_settings, command.args)
    chat_id = message.chat.id if message.chat.type in {"group", "supergroup"} else None

    scope, error = await economy_repo.resolve_scope(mode=mode, chat_id=chat_id, user_id=message.from_user.id)
    if scope is None:
        await _answer_message(message, error or "Не удалось открыть магазин")
        return

    dashboard, error = await get_dashboard(economy_repo, economy_mode=mode, chat_id=chat_id, user_id=message.from_user.id)
    if dashboard is None:
        await _answer_message(message, error or "Не удалось открыть магазин")
        return

    offers, _ = await list_shop_offers(
        economy_repo,
        scope=scope,
        user_id=message.from_user.id,
        current_day=date.today(),
    )

    if tokens and tokens[0].lower() == "buy":
        if len(tokens) < 2:
            await _answer_message(message, "Формат: /shop buy <номер_оффера>")
            return
        from selara.application.use_cases.economy.buy_shop_item import execute as buy_shop_item

        selected = tokens[1].strip()
        offer_code = selected
        if selected.isdigit():
            idx = int(selected)
            if idx < 1 or idx > len(offers):
                await _answer_message(message, f"Оффер с номером {idx} не найден.")
                return
            offer_code = offers[idx - 1].offer_code

        result = await buy_shop_item(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            user_id=message.from_user.id,
            offer_code=offer_code,
            current_day=date.today(),
        )
        if not result.accepted:
            await _answer_message(message, result.reason or "Покупка не выполнена")
            return

        await _answer_message(
            message,
            f"Покупка успешна: {result.offer.title if result.offer else '-'} | Баланс: {result.new_balance}",
            cleanup_bot=bot,
            cleanup_enabled=chat_settings.cleanup_economy_commands,
        )
        return

    await _answer_message(message, 
        _shop_text(offers, dashboard.account.balance, scope.scope_id),
        parse_mode="HTML",
        reply_markup=_build_shop_keyboard(mode, offers, owner_user_id=message.from_user.id),
    )


@router.message(Command("inventory"))
async def inventory_command(message: Message, command: CommandObject, bot: Bot, economy_repo, chat_settings: ChatSettings) -> None:
    if message.from_user is None:
        return
    if not chat_settings.economy_enabled:
        await _answer_message(message, "Экономика отключена для этого чата.")
        return

    mode, tokens = _extract_mode_and_tokens(message, chat_settings, command.args)
    chat_id = message.chat.id if message.chat.type in {"group", "supergroup"} else None

    if tokens and tokens[0].lower() == "use":
        if len(tokens) < 2:
            await _answer_message(message, "Формат: /inventory use <предмет> [грядка]")
            return
        item_code = _normalize_item_input(tokens[1])
        plot_no = int(tokens[2]) if len(tokens) > 2 and tokens[2].isdigit() else None
        result = await use_item(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            user_id=message.from_user.id,
            item_code=item_code,
            plot_no=plot_no,
        )
        if not result.accepted:
            await _answer_message(message, result.reason or "Не удалось использовать предмет")
            return
        await _answer_message(
            message,
            result.details or "Предмет применён",
            cleanup_bot=bot,
            cleanup_enabled=chat_settings.cleanup_economy_commands,
        )
        return

    dashboard, error = await get_dashboard(economy_repo, economy_mode=mode, chat_id=chat_id, user_id=message.from_user.id)
    if dashboard is None:
        await _answer_message(message, error or "Не удалось открыть инвентарь")
        return

    await _answer_message(message, 
        _inventory_text(dashboard),
        parse_mode="HTML",
        reply_markup=_build_inventory_keyboard(mode, dashboard, owner_user_id=message.from_user.id, page=0),
    )


@router.message(Command("lottery"))
async def lottery_command(message: Message, command: CommandObject, bot: Bot, economy_repo, chat_settings: ChatSettings) -> None:
    if message.from_user is None:
        return
    if not chat_settings.economy_enabled:
        await _answer_message(message, "Экономика отключена для этого чата.")
        return

    mode, tokens = _extract_mode_and_tokens(message, chat_settings, command.args)
    chat_id = message.chat.id if message.chat.type in {"group", "supergroup"} else None

    ticket_type = "free"
    if tokens:
        ticket_type = tokens[0].lower()

    if ticket_type in {"status", "view"}:
        dashboard, error = await get_dashboard(economy_repo, economy_mode=mode, chat_id=chat_id, user_id=message.from_user.id)
        if dashboard is None:
            await _answer_message(message, error or "Не удалось открыть лотерею")
            return
        await _answer_message(
            message,
            _lottery_text(dashboard),
            parse_mode="HTML",
            reply_markup=_build_lottery_keyboard(mode, owner_user_id=message.from_user.id),
        )
        return

    result = await draw_lottery(
        economy_repo,
        economy_mode=mode,
        chat_id=chat_id,
        user_id=message.from_user.id,
        ticket_type=ticket_type,
        lottery_ticket_price=chat_settings.economy_lottery_ticket_price,
        lottery_paid_daily_limit=chat_settings.economy_lottery_paid_daily_limit,
    )
    if not result.accepted:
        await _answer_message(message, result.reason or "Лотерея недоступна")
        return

    reward_items = (
        ", ".join(f"{localize_item_code(code)} x{qty}" for code, qty in result.item_rewards)
        if result.item_rewards
        else "-"
    )
    await _answer_message(
        message,
        f"Лотерея ({result.ticket_type}): монеты +{result.coin_reward}; предметы: {reward_items}; баланс: {result.new_balance}",
        cleanup_bot=bot,
        cleanup_enabled=chat_settings.cleanup_economy_commands,
    )


@router.message(Command("market"))
async def market_command(message: Message, command: CommandObject, bot: Bot, economy_repo, chat_settings: ChatSettings) -> None:
    if message.from_user is None:
        return
    if not chat_settings.economy_enabled:
        await _answer_message(message, "Экономика отключена для этого чата.")
        return

    mode, tokens = _extract_mode_and_tokens(message, chat_settings, command.args)
    chat_id = message.chat.id if message.chat.type in {"group", "supergroup"} else None

    scope, error = await economy_repo.resolve_scope(mode=mode, chat_id=chat_id, user_id=message.from_user.id)
    if scope is None:
        await _answer_message(message, error or "Не удалось открыть рынок")
        return

    if tokens:
        action = tokens[0].lower()
        if action == "sell":
            if len(tokens) < 4 or not tokens[2].isdigit() or not tokens[3].isdigit():
                await _answer_message(message, "Формат: /market sell <предмет> <кол-во> <цена>")
                return
            normalized_item_code = _normalize_item_input(tokens[1], allow_crops=True)
            result = await market_create_listing(
                economy_repo,
                economy_mode=mode,
                chat_id=chat_id,
                user_id=message.from_user.id,
                item_code=normalized_item_code,
                quantity=int(tokens[2]),
                unit_price=int(tokens[3]),
                market_fee_percent=chat_settings.economy_market_fee_percent,
            )
            if not result.accepted:
                await _answer_message(message, result.reason or "Не удалось создать лот")
                return
            await _answer_message(
                message,
                f"Лот создан: #{result.listing.id}",
                cleanup_bot=bot,
                cleanup_enabled=chat_settings.cleanup_economy_commands,
            )
            return

        if action == "buy":
            if len(tokens) < 3 or not tokens[1].isdigit() or not tokens[2].isdigit():
                await _answer_message(message, "Формат: /market buy <лот_id> <кол-во>")
                return
            result = await market_buy_listing(
                economy_repo,
                economy_mode=mode,
                chat_id=chat_id,
                buyer_user_id=message.from_user.id,
                listing_id=int(tokens[1]),
                quantity=int(tokens[2]),
                seller_tax_percent=chat_settings.economy_transfer_tax_percent,
            )
            if not result.accepted:
                await _answer_message(message, result.reason or "Не удалось купить лот")
                return
            await _answer_message(
                message,
                f"Покупка успешна: лот #{result.listing_id}, кол-во={result.quantity}, цена={result.total_cost}, баланс={result.buyer_balance}",
                cleanup_bot=bot,
                cleanup_enabled=chat_settings.cleanup_economy_commands,
            )
            return

        if action == "cancel":
            if len(tokens) < 2 or not tokens[1].isdigit():
                await _answer_message(message, "Формат: /market cancel <лот_id>")
                return
            result = await market_cancel_listing(
                economy_repo,
                economy_mode=mode,
                chat_id=chat_id,
                seller_user_id=message.from_user.id,
                listing_id=int(tokens[1]),
            )
            if not result.accepted:
                await _answer_message(message, result.reason or "Не удалось отменить лот")
                return
            await _answer_message(
                message,
                f"Лот #{result.listing_id} отменён",
                cleanup_bot=bot,
                cleanup_enabled=chat_settings.cleanup_economy_commands,
            )
            return

    listings = await economy_repo.list_market_open(scope=scope, limit=20)
    await _answer_message(
        message,
        _market_text(scope.scope_id, listings),
        parse_mode="HTML",
        reply_markup=_build_market_keyboard(mode, listings, owner_user_id=message.from_user.id),
    )


@router.message(Command("pay"))
async def pay_command(message: Message, command: CommandObject, bot: Bot, economy_repo, activity_repo, chat_settings: ChatSettings) -> None:
    if message.from_user is None:
        return
    if not chat_settings.economy_enabled:
        await _answer_message(message, "Экономика отключена для этого чата.")
        return

    mode, tokens = _extract_mode_and_tokens(message, chat_settings, command.args)
    chat_id = message.chat.id if message.chat.type in {"group", "supergroup"} else None

    target_user_id: int | None = None
    amount: int | None = None

    if message.reply_to_message is not None and message.reply_to_message.from_user is not None:
        target_user_id = int(message.reply_to_message.from_user.id)
        if tokens and tokens[-1].isdigit():
            amount = int(tokens[-1])

    if target_user_id is None:
        if len(tokens) < 2:
            await _answer_message(message, "Формат: /pay @username 100 | /pay user_id 100 | reply + /pay 100")
            return

        target_raw = tokens[0]
        if tokens[1].isdigit():
            amount = int(tokens[1])
        else:
            await _answer_message(message, "Сумма перевода должна быть числом")
            return

        if target_raw.startswith("@"):
            if chat_id is None:
                await _answer_message(message, "В личке перевод по @username недоступен")
                return
            user = await activity_repo.find_chat_user_by_username(chat_id=chat_id, username=target_raw)
            if user is None:
                await _answer_message(message, "Пользователь с таким username не найден в этом чате")
                return
            target_user_id = user.telegram_user_id
        elif target_raw.isdigit():
            target_user_id = int(target_raw)

    if target_user_id is None or amount is None:
        await _answer_message(message, "Не удалось определить получателя/сумму")
        return

    result = await transfer_coins(
        economy_repo,
        economy_mode=mode,
        chat_id=chat_id,
        sender_user_id=message.from_user.id,
        receiver_user_id=target_user_id,
        amount=amount,
        transfer_daily_limit=chat_settings.economy_transfer_daily_limit,
        transfer_tax_percent=chat_settings.economy_transfer_tax_percent,
    )
    if not result.accepted:
        await _answer_message(message, result.reason or "Перевод отклонён")
        return

    await log_chat_action(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        action_code="coins_transfer",
        description=f"Перевод {result.amount} монет: {message.from_user.id} -> {target_user_id}.",
        actor_user_id=message.from_user.id,
        target_user_id=target_user_id,
        meta_json={"amount": result.amount, "tax": result.tax_amount},
    )
    await _answer_message(
        message,
        f"Перевод выполнен: {result.amount} (налог {result.tax_amount}). Баланс отправителя: {result.sender_balance}",
        cleanup_bot=bot,
        cleanup_enabled=chat_settings.cleanup_economy_commands,
    )


@router.message(Command("craft"))
async def craft_command(message: Message, command: CommandObject, bot: Bot, economy_repo, chat_settings: ChatSettings) -> None:
    if message.from_user is None:
        return
    if not chat_settings.economy_enabled:
        await _answer_message(message, "Экономика отключена для этого чата.")
        return
    if not chat_settings.craft_enabled:
        await _answer_message(message, "Крафт отключён в этом чате.")
        return

    mode, tokens = _extract_mode_and_tokens(message, chat_settings, command.args)
    chat_id = message.chat.id if message.chat.type in {"group", "supergroup"} else None
    if not tokens:
        await _answer_message(message, _craft_text(), parse_mode="HTML")
        return

    result = await craft_item(
        economy_repo,
        economy_mode=mode,
        chat_id=chat_id,
        user_id=message.from_user.id,
        recipe_code=" ".join(tokens),
    )
    if not result.accepted:
        await _answer_message(message, result.reason or "Крафт не выполнен.")
        return
    await _answer_message(
        message,
        f"Скрафчено: <code>{escape(localize_item_code(result.crafted_item_code or ''))}</code> × <code>{result.crafted_quantity}</code>",
        parse_mode="HTML",
        cleanup_bot=bot,
        cleanup_enabled=chat_settings.cleanup_economy_commands,
    )


@router.message(Command("auction"))
async def auction_command(
    message: Message,
    command: CommandObject,
    bot,
    economy_repo,
    activity_repo,
    chat_settings: ChatSettings,
    session_factory,
) -> None:
    if message.from_user is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        await _answer_message(message, "Аукцион доступен только в группе.")
        return
    if not chat_settings.economy_enabled:
        await _answer_message(message, "Экономика отключена для этого чата.")
        return
    if not chat_settings.auctions_enabled:
        await _answer_message(message, "Аукционы отключены в этом чате.")
        return

    raw_args = (command.args or "").strip()
    tokens = [token for token in raw_args.split() if token]
    active = await economy_repo.get_active_chat_auction(chat_id=message.chat.id)
    if active is not None and active.ends_at <= datetime.now(timezone.utc):
        result = await auction_finalize(economy_repo, auction_id=active.id)
        if result.auction is not None:
            leader_label = await _resolve_auction_leader_label(activity_repo, result.auction)
            await _answer_message(
                message,
                (
                    "Аукцион уже завершился.\n"
                    f"Лидер: <code>{escape(leader_label or 'нет')}</code>\n"
                    f"Ставка: <code>{result.auction.current_bid}</code>"
                ),
                parse_mode="HTML",
            )
        active = None

    if not tokens:
        if active is None:
            await _answer_message(
                message,
                "Формат: /auction start <item_code> <qty> <start_price> [minutes]\n"
                "Отмена: /auction cancel\n"
                "Ставка: /bid 5000",
            )
            return
        leader_label = await _resolve_auction_leader_label(activity_repo, active)
        await _answer_message(message, _auction_text(active, leader_label=leader_label), parse_mode="HTML")
        return

    action = tokens[0].lower()
    allowed_manage, _, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="manage_settings",
        bootstrap_if_missing_owner=False,
    )
    if action == "cancel":
        if not allowed_manage:
            await _answer_message(message, "Недостаточно прав для отмены аукциона.")
            return
        if active is None:
            await _answer_message(message, "Активного аукциона нет.")
            return
        result = await auction_finalize(economy_repo, auction_id=active.id, cancel=True)
        if result.auction is None:
            await _answer_message(message, result.reason or "Не удалось отменить аукцион.")
            return
        task = _AUCTION_TASKS.pop(active.id, None)
        if task is not None:
            task.cancel()
        if result.auction.message_id is not None:
            try:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=result.auction.message_id,
                    text="⛔ <b>Аукцион отменён.</b>\nЛот и ставки возвращены владельцам.",
                    parse_mode="HTML",
                )
            except TelegramBadRequest:
                pass
        await log_chat_action(
            activity_repo,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            chat_title=message.chat.title,
            action_code="auction_cancel",
            description=f"Аукцион #{active.id} отменён.",
            actor_user_id=message.from_user.id,
        )
        await _answer_message(message, "Аукцион отменён.")
        return

    if action not in {"start", "sell"}:
        await _answer_message(message, "Формат: /auction start <item_code> <qty> <start_price> [minutes]")
        return
    if not allowed_manage:
        await _answer_message(message, "Недостаточно прав для запуска аукциона.")
        return

    if len(tokens) < 4 or not tokens[2].isdigit() or not tokens[3].isdigit():
        await _answer_message(message, "Формат: /auction start <item_code> <qty> <start_price> [minutes]")
        return

    duration = int(tokens[4]) if len(tokens) >= 5 and tokens[4].isdigit() else chat_settings.auction_duration_minutes
    result = await auction_start(
        economy_repo,
        chat_id=message.chat.id,
        economy_mode=chat_settings.economy_mode,
        seller_user_id=message.from_user.id,
        item_code=_normalize_item_input(tokens[1], allow_crops=True),
        quantity=int(tokens[2]),
        start_price=int(tokens[3]),
        min_increment=chat_settings.auction_min_increment,
        duration_minutes=duration,
    )
    if result.auction is None:
        await _answer_message(message, result.reason or "Не удалось запустить аукцион.")
        return

    leader_label = await _resolve_auction_leader_label(activity_repo, result.auction)
    sent = await message.answer(_auction_text(result.auction, leader_label=leader_label), parse_mode="HTML")
    updated = await economy_repo.update_chat_auction_bid(
        auction_id=result.auction.id,
        current_bid=result.auction.current_bid,
        highest_bid_user_id=result.auction.highest_bid_user_id,
        message_id=sent.message_id,
    )
    if updated is not None:
        _schedule_auction_finalize(auction=updated, chat_id=message.chat.id, bot=bot, session_factory=session_factory)
    await log_chat_action(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        action_code="auction_start",
        description=f"Аукцион запущен: {tokens[1]} × {tokens[2]} со стартом {tokens[3]}.",
        actor_user_id=message.from_user.id,
    )


@router.message(Command("bid"))
async def bid_command(message: Message, command: CommandObject, bot, economy_repo, activity_repo, chat_settings: ChatSettings) -> None:
    if message.from_user is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        await _answer_message(message, "Ставки доступны только в группе.")
        return
    if not chat_settings.economy_enabled or not chat_settings.auctions_enabled:
        await _answer_message(message, "Аукционы сейчас недоступны в этом чате.")
        return
    raw_amount = (command.args or "").strip()
    if not raw_amount.isdigit():
        await _answer_message(message, "Формат: /bid <сумма>")
        return
    active = await economy_repo.get_active_chat_auction(chat_id=message.chat.id)
    if active is None:
        await _answer_message(message, "Активного аукциона нет.")
        return
    if active.ends_at <= datetime.now(timezone.utc):
        result = await auction_finalize(economy_repo, auction_id=active.id)
        leader_label = None if result.auction is None else await _resolve_auction_leader_label(activity_repo, result.auction)
        await _answer_message(
            message,
            f"Аукцион уже завершён. Лидер: <code>{escape(leader_label or 'нет')}</code>",
            parse_mode="HTML",
        )
        return

    result = await auction_bid(
        economy_repo,
        auction_id=active.id,
        bidder_user_id=message.from_user.id,
        bid_amount=int(raw_amount),
    )
    if not result.accepted or result.auction is None:
        await _answer_message(message, result.reason or "Ставка отклонена.")
        return

    leader_label = await _resolve_auction_leader_label(activity_repo, result.auction)
    if result.auction.message_id is not None:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=result.auction.message_id,
                text=_auction_text(result.auction, leader_label=leader_label),
                parse_mode="HTML",
            )
        except TelegramBadRequest:
            pass
    await log_chat_action(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        action_code="auction_bid",
        description=f"Ставка {int(raw_amount)} на аукцион #{result.auction.id}.",
        actor_user_id=message.from_user.id,
        meta_json={"bid": int(raw_amount)},
    )
    await _answer_message(message, f"Ставка принята: <code>{int(raw_amount)}</code>", parse_mode="HTML")


@router.message(Command("growth"))
async def growth_command(message: Message, command: CommandObject, bot: Bot, economy_repo, activity_repo, settings: Settings, chat_settings: ChatSettings) -> None:
    if message.from_user is None:
        return
    if not chat_settings.economy_enabled:
        await _answer_message(message, "Экономика отключена для этого чата.")
        return

    mode, tokens = _extract_mode_and_tokens(message, chat_settings, command.args)
    chat_id = message.chat.id if message.chat.type in {"group", "supergroup"} else None

    should_perform = bool(tokens) and tokens[0].lower() in {"do", "d", "act", "go", "дрочить", "подрочить"}
    if should_perform:
        allowed = await is_growth_action_allowed(
            economy_mode=mode,
            chat_id=chat_id,
            user_id=message.from_user.id,
            chat_settings=chat_settings,
            activity_repo=activity_repo,
            economy_repo=economy_repo,
            settings=settings,
        )
        if not allowed:
            await _answer_message(message, growth_action_disabled_text(), parse_mode="HTML")
            return

        result = await perform_growth_action(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            user_id=message.from_user.id,
        )
        if result.accepted:
            delta_sign = "+" if result.size_delta_mm >= 0 else ""
            status = "неудача" if result.fumble else "успех"
            await _answer_message(
                message,
                (
                    f"Рост: {status}. Размер {delta_sign}{_format_size_mm(result.size_delta_mm)} см, "
                    f"стресс +{result.stress_delta_pct}%, монеты +{result.reward}."
                ),
                cleanup_bot=bot,
                cleanup_enabled=chat_settings.cleanup_economy_commands,
            )
        else:
            await _answer_message(message, result.reason or "Сейчас действие недоступно.")

    profile = await get_growth_profile(
        economy_repo,
        economy_mode=mode,
        chat_id=chat_id,
        user_id=message.from_user.id,
    )
    await _answer_message(
        message,
        _growth_text(profile),
        parse_mode="HTML",
        reply_markup=_build_growth_keyboard(mode, owner_user_id=message.from_user.id),
    )


@router.callback_query(F.data.startswith("eco:"))
async def eco_callback(query: CallbackQuery, economy_repo, chat_settings: ChatSettings) -> None:
    if query.data is None or query.from_user is None:
        await _safe_query_answer(query)
        return
    if not chat_settings.economy_enabled:
        await _safe_query_answer(query, "Экономика отключена для этого чата.", show_alert=True)
        return

    parts, owner_user_id = _extract_owner_from_parts(query.data.split(":"))
    if not await _enforce_panel_owner(query, owner_user_id=owner_user_id):
        return
    if len(parts) < 3:
        await _safe_query_answer(query, "Некорректная кнопка", show_alert=False)
        return

    panel_owner_user_id = owner_user_id or query.from_user.id
    action = parts[1]
    mode = _parse_mode_from_callback(query, 2, chat_settings)
    chat_id = query.message.chat.id if query.message and query.message.chat.type in {"group", "supergroup"} else None

    if action == "tap":
        result = await tap(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            user_id=query.from_user.id,
            tap_cooldown_seconds=chat_settings.economy_tap_cooldown_seconds,
        )
        await _safe_query_answer(query, result.reason or (f"+{result.reward}" if result.accepted else "Ошибка"), show_alert=False)

    if action == "daily":
        result = await claim_daily(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            user_id=query.from_user.id,
            daily_base_reward=chat_settings.economy_daily_base_reward,
            daily_streak_cap=chat_settings.economy_daily_streak_cap,
        )
        await _safe_query_answer(query, result.reason or (f"+{result.reward}" if result.accepted else "Ошибка"), show_alert=False)

    dashboard, error = await get_dashboard(
        economy_repo,
        economy_mode=mode,
        chat_id=chat_id,
        user_id=query.from_user.id,
    )
    if dashboard is None:
        await _safe_query_answer(query, error or "Не удалось открыть панель", show_alert=True)
        return

    await _edit_or_answer(
        query,
        _dashboard_text(dashboard, show_growth=chat_settings.actions_18_enabled),
        _build_dashboard_keyboard(mode, owner_user_id=panel_owner_user_id),
    )
    await _safe_query_answer(query)


@router.callback_query(F.data.startswith("farm:"))
async def farm_callback(query: CallbackQuery, economy_repo, chat_settings: ChatSettings) -> None:
    if query.data is None or query.from_user is None:
        await _safe_query_answer(query)
        return
    if not chat_settings.economy_enabled:
        await _safe_query_answer(query, "Экономика отключена для этого чата.", show_alert=True)
        return

    parts, owner_user_id = _extract_owner_from_parts(query.data.split(":"))
    if not await _enforce_panel_owner(query, owner_user_id=owner_user_id):
        return
    if len(parts) < 3:
        await _safe_query_answer(query, "Некорректная кнопка", show_alert=False)
        return

    panel_owner_user_id = owner_user_id or query.from_user.id
    action = parts[1]
    mode = _parse_mode_from_callback(query, 2, chat_settings)
    chat_id = query.message.chat.id if query.message and query.message.chat.type in {"group", "supergroup"} else None

    if action == "p" and len(parts) >= 5 and parts[4].isdigit():
        crop = parts[3]
        plot_no = int(parts[4])
        result = await plant_crop(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            user_id=query.from_user.id,
            crop_code=crop,
            plot_no=plot_no,
        )
        if not result.accepted:
            await _safe_query_answer(query, result.reason or "Ошибка посадки", show_alert=True)
        else:
            await _safe_query_answer(query, "Посадка выполнена", show_alert=False)

    if action == "pa":
        result = await plant_all_last_crop(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            user_id=query.from_user.id,
        )
        if not result.accepted:
            await _safe_query_answer(query, result.reason or "Ошибка посадки", show_alert=True)
        else:
            await _safe_query_answer(query, f"Засажено грядок: {len(result.planted_plots)}", show_alert=False)

    if action == "h" and len(parts) >= 4 and parts[3].isdigit():
        result = await harvest_crop(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            user_id=query.from_user.id,
            plot_no=int(parts[3]),
            negative_event_chance_percent=chat_settings.economy_negative_event_chance_percent,
            negative_event_loss_percent=chat_settings.economy_negative_event_loss_percent,
        )
        if not result.accepted:
            await _safe_query_answer(query, result.reason or "Ошибка сбора", show_alert=True)
        else:
            await _safe_query_answer(query, f"Собрано {result.amount}", show_alert=False)

    if action == "ha":
        result = await harvest_all_ready(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            user_id=query.from_user.id,
            negative_event_chance_percent=chat_settings.economy_negative_event_chance_percent,
            negative_event_loss_percent=chat_settings.economy_negative_event_loss_percent,
        )
        if not result.accepted:
            await _safe_query_answer(query, result.reason or "Нет готовых грядок", show_alert=True)
        else:
            await _safe_query_answer(query, f"Собрано всего: {result.total_amount}", show_alert=False)

    dashboard, error = await get_dashboard(economy_repo, economy_mode=mode, chat_id=chat_id, user_id=query.from_user.id)
    if dashboard is None:
        await _safe_query_answer(query, error or "Не удалось открыть ферму", show_alert=True)
        return

    await _edit_or_answer(
        query,
        _farm_text(dashboard),
        _build_farm_keyboard(mode, dashboard, owner_user_id=panel_owner_user_id),
    )
    await _safe_query_answer(query)


@router.callback_query(F.data.startswith("shop:"))
async def shop_callback(query: CallbackQuery, economy_repo, chat_settings: ChatSettings) -> None:
    if query.data is None or query.from_user is None:
        await _safe_query_answer(query)
        return
    if not chat_settings.economy_enabled:
        await _safe_query_answer(query, "Экономика отключена для этого чата.", show_alert=True)
        return

    parts, owner_user_id = _extract_owner_from_parts(query.data.split(":"))
    if not await _enforce_panel_owner(query, owner_user_id=owner_user_id):
        return
    if len(parts) < 3:
        await _safe_query_answer(query, "Некорректная кнопка", show_alert=False)
        return

    panel_owner_user_id = owner_user_id or query.from_user.id
    action = parts[1]
    mode = _parse_mode_from_callback(query, 2, chat_settings)
    chat_id = query.message.chat.id if query.message and query.message.chat.type in {"group", "supergroup"} else None

    scope, error = await economy_repo.resolve_scope(mode=mode, chat_id=chat_id, user_id=query.from_user.id)
    if scope is None:
        await _safe_query_answer(query, error or "Ошибка scope", show_alert=True)
        return

    if action == "b" and len(parts) >= 4:
        offer_code = parts[3]
        from selara.application.use_cases.economy.buy_shop_item import execute as buy_shop_item

        result = await buy_shop_item(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            user_id=query.from_user.id,
            offer_code=offer_code,
            current_day=date.today(),
        )
        await _safe_query_answer(query, result.reason or "Покупка выполнена", show_alert=not result.accepted)

    dashboard, error = await get_dashboard(economy_repo, economy_mode=mode, chat_id=chat_id, user_id=query.from_user.id)
    if dashboard is None:
        await _safe_query_answer(query, error or "Ошибка", show_alert=True)
        return

    offers, _ = await list_shop_offers(
        economy_repo,
        scope=scope,
        user_id=query.from_user.id,
        current_day=date.today(),
    )
    await _edit_or_answer(
        query,
        _shop_text(offers, dashboard.account.balance, scope.scope_id),
        _build_shop_keyboard(mode, offers, owner_user_id=panel_owner_user_id),
    )
    await _safe_query_answer(query)


@router.callback_query(F.data.startswith("inv:"))
async def inventory_callback(query: CallbackQuery, economy_repo, chat_settings: ChatSettings) -> None:
    if query.data is None or query.from_user is None:
        await _safe_query_answer(query)
        return
    if not chat_settings.economy_enabled:
        await _safe_query_answer(query, "Экономика отключена для этого чата.", show_alert=True)
        return

    parts, owner_user_id = _extract_owner_from_parts(query.data.split(":"))
    if not await _enforce_panel_owner(query, owner_user_id=owner_user_id):
        return
    if len(parts) < 3:
        await _safe_query_answer(query, "Некорректная кнопка", show_alert=False)
        return

    panel_owner_user_id = owner_user_id or query.from_user.id
    action = parts[1]
    mode = _parse_mode_from_callback(query, 2, chat_settings)
    chat_id = query.message.chat.id if query.message and query.message.chat.type in {"group", "supergroup"} else None
    page = 0
    if action == "ov" and len(parts) >= 4 and parts[3].isdigit():
        page = int(parts[3])

    if action == "u" and len(parts) >= 5:
        item_code = f"item:{parts[3]}"
        plot_no = int(parts[4]) if parts[4].isdigit() and parts[4] != "0" else None
        if len(parts) >= 6 and parts[5].isdigit():
            page = int(parts[5])
        result = await use_item(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            user_id=query.from_user.id,
            item_code=item_code,
            plot_no=plot_no,
        )
        await _safe_query_answer(query, result.reason or result.details or "Готово", show_alert=not result.accepted)

    dashboard, error = await get_dashboard(economy_repo, economy_mode=mode, chat_id=chat_id, user_id=query.from_user.id)
    if dashboard is None:
        await _safe_query_answer(query, error or "Ошибка", show_alert=True)
        return

    await _edit_or_answer(
        query,
        _inventory_text(dashboard),
        _build_inventory_keyboard(mode, dashboard, owner_user_id=panel_owner_user_id, page=page),
    )
    await _safe_query_answer(query)


@router.callback_query(F.data.startswith("grw:"))
async def growth_callback(query: CallbackQuery, economy_repo, activity_repo, settings: Settings, chat_settings: ChatSettings) -> None:
    if query.data is None or query.from_user is None:
        await _safe_query_answer(query)
        return
    if not chat_settings.economy_enabled:
        await _safe_query_answer(query, "Экономика отключена для этого чата.", show_alert=True)
        return

    parts, owner_user_id = _extract_owner_from_parts(query.data.split(":"))
    if not await _enforce_panel_owner(query, owner_user_id=owner_user_id):
        return
    if len(parts) < 3:
        await _safe_query_answer(query, "Некорректная кнопка", show_alert=False)
        return

    panel_owner_user_id = owner_user_id or query.from_user.id
    action = parts[1]
    mode = _parse_mode_from_callback(query, 2, chat_settings)
    chat_id = query.message.chat.id if query.message and query.message.chat.type in {"group", "supergroup"} else None

    if action == "d":
        allowed = await is_growth_action_allowed(
            economy_mode=mode,
            chat_id=chat_id,
            user_id=query.from_user.id,
            chat_settings=chat_settings,
            activity_repo=activity_repo,
            economy_repo=economy_repo,
            settings=settings,
        )
        if not allowed:
            await _safe_query_answer(query, growth_action_disabled_plain_text(), show_alert=True)
            return

        result = await perform_growth_action(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            user_id=query.from_user.id,
        )
        if result.accepted:
            delta_sign = "+" if result.size_delta_mm >= 0 else ""
            await _safe_query_answer(
                query,
                f"{delta_sign}{_format_size_mm(result.size_delta_mm)} см, +{result.reward} мон.",
                show_alert=False,
            )
        else:
            await _safe_query_answer(query, result.reason or "Сейчас недоступно", show_alert=True)

    profile = await get_growth_profile(
        economy_repo,
        economy_mode=mode,
        chat_id=chat_id,
        user_id=query.from_user.id,
    )
    await _edit_or_answer(
        query,
        _growth_text(profile),
        _build_growth_keyboard(mode, owner_user_id=panel_owner_user_id),
    )
    await _safe_query_answer(query)


@router.callback_query(F.data.startswith("lot:"))
async def lottery_callback(query: CallbackQuery, economy_repo, chat_settings: ChatSettings) -> None:
    if query.data is None or query.from_user is None:
        await _safe_query_answer(query)
        return
    if not chat_settings.economy_enabled:
        await _safe_query_answer(query, "Экономика отключена для этого чата.", show_alert=True)
        return

    parts, owner_user_id = _extract_owner_from_parts(query.data.split(":"))
    if not await _enforce_panel_owner(query, owner_user_id=owner_user_id):
        return
    if len(parts) < 3:
        await _safe_query_answer(query, "Некорректная кнопка", show_alert=False)
        return

    panel_owner_user_id = owner_user_id or query.from_user.id
    action = parts[1]
    mode = _parse_mode_from_callback(query, 2, chat_settings)
    chat_id = query.message.chat.id if query.message and query.message.chat.type in {"group", "supergroup"} else None

    if action == "d" and len(parts) >= 4:
        ticket = parts[3]
        result = await draw_lottery(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            user_id=query.from_user.id,
            ticket_type=ticket,
            lottery_ticket_price=chat_settings.economy_lottery_ticket_price,
            lottery_paid_daily_limit=chat_settings.economy_lottery_paid_daily_limit,
        )
        if not result.accepted:
            await _safe_query_answer(query, result.reason or "Ошибка лотереи", show_alert=True)
        else:
            await _safe_query_answer(query, f"Выигрыш: +{result.coin_reward}", show_alert=False)

    dashboard, error = await get_dashboard(economy_repo, economy_mode=mode, chat_id=chat_id, user_id=query.from_user.id)
    if dashboard is None:
        await _safe_query_answer(query, error or "Ошибка", show_alert=True)
        return

    await _edit_or_answer(
        query,
        _lottery_text(dashboard),
        _build_lottery_keyboard(mode, owner_user_id=panel_owner_user_id),
    )
    await _safe_query_answer(query)


@router.callback_query(F.data.startswith("mkt:"))
async def market_callback(query: CallbackQuery, economy_repo, chat_settings: ChatSettings) -> None:
    if query.data is None or query.from_user is None:
        await _safe_query_answer(query)
        return
    if not chat_settings.economy_enabled:
        await _safe_query_answer(query, "Экономика отключена для этого чата.", show_alert=True)
        return

    parts, owner_user_id = _extract_owner_from_parts(query.data.split(":"))
    if not await _enforce_panel_owner(query, owner_user_id=owner_user_id):
        return
    if len(parts) < 3:
        await _safe_query_answer(query, "Некорректная кнопка", show_alert=False)
        return

    panel_owner_user_id = owner_user_id or query.from_user.id
    action = parts[1]
    mode = _parse_mode_from_callback(query, 2, chat_settings)
    chat_id = query.message.chat.id if query.message and query.message.chat.type in {"group", "supergroup"} else None

    if action == "b" and len(parts) >= 5 and parts[3].isdigit() and parts[4].isdigit():
        result = await market_buy_listing(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            buyer_user_id=query.from_user.id,
            listing_id=int(parts[3]),
            quantity=int(parts[4]),
            seller_tax_percent=chat_settings.economy_transfer_tax_percent,
        )
        await _safe_query_answer(query, result.reason or "Покупка выполнена", show_alert=not result.accepted)

    if action == "c" and len(parts) >= 4 and parts[3].isdigit():
        result = await market_cancel_listing(
            economy_repo,
            economy_mode=mode,
            chat_id=chat_id,
            seller_user_id=query.from_user.id,
            listing_id=int(parts[3]),
        )
        await _safe_query_answer(query, result.reason or "Лот отменён", show_alert=not result.accepted)

    scope, error = await economy_repo.resolve_scope(mode=mode, chat_id=chat_id, user_id=query.from_user.id)
    if scope is None:
        await _safe_query_answer(query, error or "Ошибка", show_alert=True)
        return

    listings = await economy_repo.list_market_open(scope=scope, limit=20)
    await _edit_or_answer(
        query,
        _market_text(scope.scope_id, listings),
        _build_market_keyboard(mode, listings, owner_user_id=panel_owner_user_id),
    )
    await _safe_query_answer(query)
