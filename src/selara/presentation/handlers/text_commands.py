import asyncio
import json
import logging
import random
import re
import hashlib
import time
from os.path import basename
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from html import escape
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ChosenInlineResult,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)
from sqlalchemy.exc import SQLAlchemyError

from selara.application.use_cases.gacha import (
    GACHA_CURRENCY_PER_COIN_RATE,
    GACHA_DEFAULT_CURRENCY_PURCHASE_AMOUNT,
    GachaUseCaseError,
    buy_currency_with_coins as buy_gacha_currency_with_coins,
    give_card as give_gacha_card,
    get_profile as get_gacha_profile,
    purchase_pull as purchase_gacha_pull,
    pull_card as pull_gacha_card,
    reset_cooldown as reset_gacha_cooldown,
    sell_pull as sell_gacha_pull,
)
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error
from selara.core.chat_settings import ChatSettings
from selara.core.config import Settings
from selara.domain.entities import ChatSnapshot, ChatTextAlias, UserSnapshot
from selara.domain.value_objects import display_name_from_parts
from selara.presentation.auth import get_role_label_ru, has_command_access, has_permission
from selara.presentation.commands.access import parse_command_rank_phrase, resolve_command_key_input
from selara.presentation.commands.catalog import (
    COMMAND_KEYS_WITH_TAIL,
    SOCIAL_COMMAND_KEY_TO_ACTION,
    SOCIAL_TRIGGER_TO_COMMAND_KEY,
    match_builtin_command,
    resolve_builtin_command_key,
)
from selara.presentation.commands.normalizer import normalize_text_command
from selara.presentation.commands.resolver import TextCommandResolutionError, resolve_text_command
from selara.presentation.game_state import GAME_STORE
from selara.presentation.handlers.common import safe_callback_answer as _safe_callback_answer
from selara.presentation.handlers.economy import (
    auction_command as economy_auction_command,
    bid_command as economy_bid_command,
    craft_command as economy_craft_command,
    daily_command as economy_daily_command,
    eco_command as economy_eco_command,
    farm_command as economy_farm_command,
    growth_command as economy_growth_command,
    inventory_command as economy_inventory_command,
    lottery_command as economy_lottery_command,
    market_command as economy_market_command,
    pay_command as economy_pay_command,
    shop_command as economy_shop_command,
    tap_command as economy_tap_command,
)
from selara.presentation.handlers.game.router import game_command as game_slash_command
from selara.presentation.handlers.game.router import role_command as game_role_command
from selara.presentation.handlers.game.router import start_command as game_start_command
from selara.presentation.handlers.help import send_help
from selara.presentation.handlers.chat_assistant import (
    adopt_command as family_adopt_command,
    family_command as family_tree_command,
    manage_chat_gate_command,
    match_chat_trigger,
    match_custom_social_action,
    pet_command as family_pet_command,
    rpadd_command as custom_rp_add_command,
    send_chat_trigger,
    send_custom_social_action,
    settrigger_command as smart_trigger_set_command,
    title_command as title_prefix_command,
)
from selara.presentation.handlers.relationships import (
    breakup_command as relationship_breakup_command,
    care_command as relationship_care_command,
    date_command as relationship_date_command,
    divorce_command as relationship_divorce_command,
    flirt_command as relationship_flirt_command,
    gift_command as relationship_gift_command,
    love_command as relationship_love_command,
    marriage_status_command as relationship_marriage_status_command,
    marriages_command as relationship_marriages_command,
    marry_command as relationship_marry_command,
    pair_command as relationship_pair_command,
    relation_command as relationship_relation_command,
    surprise_command as relationship_surprise_command,
    support_command as relationship_support_command,
    vow_command as relationship_vow_command,
)
from selara.presentation.handlers.stats import (
    _resolve_stats_target_user,
    achievements_command,
    send_inactive_members,
    award_text_command,
    remove_award_reply_text_command,
    send_last_seen,
    send_me_stats,
    send_rep_stats,
    send_user_stats,
    set_about_text_command,
    send_top_stats,
    should_include_hybrid_top_keyboard,
)
from selara.presentation.formatters import format_user_link, preferred_mention_label_from_parts
from selara.presentation.handlers.settings_common import settings_to_dict
from selara.presentation.db_recovery import safe_rollback

router = Router(name="text_commands")
logger = logging.getLogger(__name__)

_ANNOUNCE_PATTERN = re.compile(r"^\s*объява\b(?P<body>[\s\S]*)$", re.IGNORECASE)
_NAMING_PATTERN = re.compile(r"^\s*(?:нейминг|/naming(?:@[A-Za-z0-9_]+)?)\b(?P<body>[\s\S]*)$", re.IGNORECASE)
_PROFILE_ABOUT_PATTERN = re.compile(r"^\s*добавить\s+о\s+себе\b(?P<body>[\s\S]*)$", re.IGNORECASE)
_PROFILE_AWARD_PATTERN = re.compile(r"^\s*наградить\b(?P<body>[\s\S]*)$", re.IGNORECASE)
_PROFILE_AWARD_REMOVE_PATTERN = re.compile(r"^\s*снять\s+награду\b(?P<body>[\s\S]*)$", re.IGNORECASE)
_SMART_TRIGGER_LEARN_PATTERN = re.compile(r"^\s*!?научить\b(?P<body>[\s\S]*)$", re.IGNORECASE)
_CUSTOM_RP_ADD_PATTERN = re.compile(r"^\s*!?добавить_действие\b(?P<body>[\s\S]*)$", re.IGNORECASE)
_ZHMYH_PATTERN = re.compile(r"^жмых(?:\s+(?P<level>\d+))?$")
_MAX_MESSAGE_LEN_SAFE = 3900
_ANNOUNCE_MENTION_CHUNK_SIZE = 5
_ZHMYH_FILENAME = "zhmyh.jpg"
_ZHMYH_MAX_SIDE = 1600
_ZHMYH_MIN_LEVEL = 1
_ZHMYH_MAX_LEVEL = 6
_ZHMYH_DEFAULT_LEVEL = 3
_INLINE_PM_CALLBACK_PREFIX = "ipm:"
_INLINE_PM_COOLDOWN_SECONDS = 10
_INLINE_PM_ALERT_CHUNK_LEN = 180
_INLINE_PM_COOLDOWN_CACHE_TTL_SECONDS = max(60, _INLINE_PM_COOLDOWN_SECONDS * 6)
_INLINE_PM_MENTION_RE = re.compile(r"^@([A-Za-z0-9_]{3,32})$")
_INLINE_PM_MENTION_SCAN_RE = re.compile(r"@([A-Za-z0-9_]{3,32})")
_INLINE_PM_BUTTON_TEXT = "Прочитать"
_INLINE_PM_EMPTY_RESULT: list[InlineQueryResultArticle] = []
_SHIPPER_TEMPLATES: tuple[str, ...] = (
    "💞 <b>Рандом шипперим!</b>\n{first} + {second}\n<i>Любите друг друга и берегите. Мур.</i>",
    "💘 <b>Сегодня судьба решила так:</b>\n{first} и {second}\n<i>Официально милота дня.</i>",
    "🫶 <b>Шипперим на удачу:</b>\n{first} × {second}\n<i>Не спорьте, это канон.</i>",
    "💓 <b>Совместимость поймана:</b>\n{first} + {second}\n<i>Искры уже летят.</i>",
    "🌟 <b>Романтический рандом:</b>\n{first} и {second}\n<i>Берегите этот мэтч.</i>",
    "🔥 <b>Шип дня объявлен:</b>\n{first} + {second}\n<i>Пусть чат одобряет.</i>",
    "🎀 <b>Купидон не промахнулся:</b>\n{first} и {second}\n<i>Будьте лапочками.</i>",
    "💗 <b>Сводим сердца:</b>\n{first} + {second}\n<i>Теперь вы официальная пара мемов.</i>",
)
_TODAY_RANDOMIZER_PATTERN = re.compile(
    r"^\s*кто\s+сегодня\b(?P<body>[\s\S]*)$",
    re.IGNORECASE,
)
_TODAY_RANDOMIZER_MAX_LEN = 96
_TODAY_RANDOMIZER_TEMPLATES: tuple[str, ...] = (
    "🎲 | Судьба решила, что сегодня {target} {predicate}.",
    "🔮 | Карты показали: сегодня {target} {predicate}.",
    "🪄 | Магия чата выбрала: сегодня {target} {predicate}.",
    "📣 | Официальное решение дня: сегодня {target} {predicate}.",
    "🃏 | Рандом безжалостен: сегодня {target} {predicate}.",
    "🌚 | Вселенная шепнула, что сегодня {target} {predicate}.",
)
_DAILY_ARTICLE_COMMAND_RE = re.compile(
    r"^\s*/article(?:@[A-Za-z0-9_]+)?\s*$",
    re.IGNORECASE,
)
_DAILY_ARTICLE_TEMPLATES: tuple[str, ...] = (
    "🤷‍♂️ Сегодня {user} приговаривается к статье <b>{code}</b>. {title}",
    "⚖️ Суд постановил: сегодня для {user} действует статья <b>{code}</b>. {title}",
    "📜 На сегодняшнем заседании {user} получает статью <b>{code}</b>. {title}",
    "🚨 Рандом правосудия решил: сегодня у {user} статья <b>{code}</b>. {title}",
    "🧾 Судьба открыла протокол: {user} проходит по статье <b>{code}</b>. {title}",
    "🔨 Присяжные мемов постановили: сегодня {user} живёт по статье <b>{code}</b>. {title}",
)
_DAILY_ARTICLE_OUTROS: tuple[str, ...] = (
    "Апелляцию можно подать завтра.",
    "Приговор обжалованию сегодня не подлежит.",
    "Мемный адвокат уже в пути.",
    "Смягчающие обстоятельства: хороший вайб и смешные сообщения.",
    "До смены суток статья закреплена именно за вами.",
    "Завтра будет новое заседание судьбы.",
)

_SOCIAL_ACTION_ALIASES: dict[str, str] = {
    trigger: SOCIAL_COMMAND_KEY_TO_ACTION[command_key]
    for trigger, command_key in SOCIAL_TRIGGER_TO_COMMAND_KEY.items()
}
_SOCIAL_ACTION_CANONICAL: dict[str, str] = {
    "slap": "шлепнуть",
    "kill": "убить",
    "fuck": "трахнуть",
    "seduce": "соблазнить",
    "makeout": "засосать",
    "night": "провести ночь с",
    "hit": "ударить",
    "hug": "обнять",
    "kiss": "поцеловать",
    "handshake": "пожать руку",
    "highfive": "дать пять",
    "pat": "погладить",
    "tickle": "пощекотать",
    "poke": "ткнуть",
    "wink": "подмигнуть",
    "dance": "потанцевать",
    "bow": "поклониться",
    "cheer": "подбодрить",
    "treat": "угостить",
    "praise": "похвалить",
    "fistbump": "дать кулак",
}
_INLINE_RP_QUERY_ACTIONS: dict[str, str] = {
    **_SOCIAL_ACTION_ALIASES,
    **{action_key: action_key for action_key in _SOCIAL_ACTION_CANONICAL},
}
_SOCIAL_ACTION_18_PLUS: set[str] = {"fuck", "seduce", "makeout", "night"}
_SOCIAL_ACTION_REPLICA_TEMPLATES: tuple[str, ...] = (
    "💬 С репликой: «{replica}»",
    "🗣 И добавил(а): «{replica}»",
    "🎙 При этом сказал(а): «{replica}»",
    "✨ И шепнул(а): «{replica}»",
)
_SOCIAL_ACTION_TEMPLATES: dict[str, tuple[str, ...]] = {
    "slap": (
        "👏 | {actor} шлёпнул(а) {target}.",
        "👏 | {actor} влепил(а) сочный шлепок {target}.",
        "👏 | {actor} оставил(а) звонкий шлепок {target}.",
        "👏 | {actor} шлёпнул(а) {target} и сделал(а) вид, что так и было надо.",
    ),
    "kill": (
        "💀 | {actor} виртуально убил(а) {target}. Без последствий, только рофл.",
        "💀 | {actor} устранил(а) {target} в альтернативной вселенной.",
        "💀 | {actor} эпично «убил(а)» {target} в рамках шутки.",
        "💀 | {actor} отправил(а) {target} на респаун.",
    ),
    "fuck": (
        "🔥 | {actor} страстно трахнул(а) {target}.",
        "🔥 | {actor} сорвал(а) все тормоза с {target}.",
        "🔥 | {actor} устроил(а) 18+ эпизод с {target}.",
        "🔥 | {actor} очень близко «подружился(ась)» с {target}.",
    ),
    "seduce": (
        "😈 | {actor} соблазнил(а) {target} одним только взглядом.",
        "😈 | {actor} опасно приблизился(ась) к {target}, и дальше всё пошло слишком гладко.",
        "😈 | {actor} устроил(а) для {target} мастер-класс по искушению.",
        "😈 | {actor} поймал(а) {target} в очень пикантный вайб.",
    ),
    "makeout": (
        "💋 | {actor} засосал(а) {target} так, что чат сделал вид, будто ничего не видел.",
        "💋 | {actor} устроил(а) с {target} слишком жаркий поцелуй.",
        "💋 | {actor} потерял(а) чувство меры рядом с {target}.",
        "💋 | {actor} и {target} явно переборщили с химией момента.",
    ),
    "night": (
        "🌙 | {actor} провёл(вела) с {target} очень насыщенную ночь.",
        "🌙 | {actor} исчез(ла) с {target} до утра, а детали оставил(а) за кадром.",
        "🌙 | {actor} организовал(а) с {target} ночной эпизод уровня «лучше не спрашивать».",
        "🌙 | {actor} и {target} решили, что ночь создана для взрослых приключений.",
    ),
    "hit": (
        "👊 | {actor} ударил(а) {target}.",
        "👊 | {actor} выдал(а) точный удар по {target}.",
        "👊 | {actor} приложил(а) {target} по всем правилам боевика.",
        "👊 | {actor} врезал(а) {target}, и чат это запомнил.",
    ),
    "hug": (
        "🤗 | {actor} обнял(а) {target}.",
        "🤗 | {actor} крепко обнял(а) {target}.",
        "🤗 | {actor} укутал(а) {target} в обнимашки.",
        "🤗 | {actor} подарил(а) {target} самое тёплое объятие.",
    ),
    "kiss": (
        "😘 | {actor} поцеловал(а) {target}.",
        "😘 | {actor} чмокнул(а) {target}.",
        "😘 | {actor} оставил(а) поцелуй для {target}.",
        "😘 | {actor} нежно поцеловал(а) {target}.",
    ),
    "handshake": (
        "🤝 | {actor} пожал(а) руку {target}.",
        "🤝 | {actor} крепко пожал(а) руку {target}.",
        "🤝 | {actor} обменялся(ась) с {target} уверенным рукопожатием.",
        "🤝 | {actor} заключил(а) с {target} мир в формате рукопожатия.",
    ),
    "highfive": (
        "🖐 | {actor} дал(а) пять {target}.",
        "🖐 | {actor} звонко отбил(а) пять с {target}.",
        "🖐 | {actor} поймал(а) ладонь {target} на идеальную пятюню.",
        "🖐 | {actor} синхронно дал(а) пять с {target}.",
    ),
    "pat": (
        "🫳 | {actor} погладил(а) {target} по голове.",
        "🫳 | {actor} мягко погладил(а) {target}.",
        "🫳 | {actor} ласково провёл(вела) рукой по волосам {target}.",
        "🫳 | {actor} подарил(а) {target} минутку заботы и поглаживаний.",
    ),
    "tickle": (
        "😄 | {actor} пощекотал(а) {target}.",
        "😄 | {actor} устроил(а) щекотную атаку на {target}.",
        "😄 | {actor} заставил(а) {target} смеяться от щекотки.",
        "😄 | {actor} нашёл(нашла) слабое место и пощекотал(а) {target}.",
    ),
    "poke": (
        "👉 | {actor} ткнул(а) {target}.",
        "👉 | {actor} аккуратно потыкал(а) {target}.",
        "👉 | {actor} привлёк(ла) внимание {target} лёгким тычком.",
        "👉 | {actor} проверил(а), на месте ли {target}, и ткнул(а) его(её).",
    ),
    "wink": (
        "😉 | {actor} подмигнул(а) {target}.",
        "😉 | {actor} отправил(а) {target} хитрое подмигивание.",
        "😉 | {actor} поймал(а) взгляд {target} и подмигнул(а).",
        "😉 | {actor} молча подмигнул(а) {target}: всё под контролем.",
    ),
    "dance": (
        "💃 | {actor} потанцевал(а) с {target}.",
        "💃 | {actor} пригласил(а) {target} на танец.",
        "💃 | {actor} закружил(а) {target} в ритме чата.",
        "💃 | {actor} и {target} поймали общий вайб и потанцевали.",
    ),
    "bow": (
        "🙇 | {actor} поклонился(ась) {target}.",
        "🙇 | {actor} сделал(а) уважительный поклон перед {target}.",
        "🙇 | {actor} торжественно поклонился(ась) {target}.",
        "🙇 | {actor} склонил(а) голову перед {target} в знак уважения.",
    ),
    "cheer": (
        "📣 | {actor} подбодрил(а) {target}.",
        "📣 | {actor} крикнул(а) {target}: «Ты справишься!»",
        "📣 | {actor} зарядил(а) {target} мотивацией.",
        "📣 | {actor} поддержал(а) {target} и добавил(а) уверенности.",
    ),
    "treat": (
        "🍰 | {actor} угостил(а) {target} вкусняшкой.",
        "🍰 | {actor} принёс(ла) {target} угощение.",
        "🍰 | {actor} поделился(ась) десертом с {target}.",
        "🍰 | {actor} устроил(а) мини-праздник и угостил(а) {target}.",
    ),
    "praise": (
        "🏅 | {actor} похвалил(а) {target}.",
        "🏅 | {actor} отметил(а), что {target} сегодня на высоте.",
        "🏅 | {actor} сказал(а) {target} заслуженные тёплые слова.",
        "🏅 | {actor} выдал(а) {target} честную и приятную похвалу.",
    ),
    "fistbump": (
        "👊🤜🤛 | {actor} отбил(а) кулачок с {target}.",
        "👊🤜🤛 | {actor} дал(а) кулак {target}.",
        "👊🤜🤛 | {actor} и {target} сошлись на крепком кулачке.",
        "👊🤜🤛 | {actor} обменялся(ась) с {target} бодрым фистбампом.",
    ),
}

_INLINE_PM_LAST_SENT_AT: dict[int, datetime] = {}
_INLINE_PM_ALERT_PAGE_TTL_SECONDS = 1800
_INLINE_PM_ALERT_PAGE: dict[tuple[str, int], tuple[int, datetime]] = {}
_INLINE_PM_BOT_USERNAME_CACHE: str | None = None
_INLINE_PM_PENDING_TTL_SECONDS = 1800
_INLINE_PM_HISTORY_LIMIT = 10
_INLINE_PM_RESULTS_LIMIT = 20
_INLINE_RP_PENDING_TTL_SECONDS = 1800
_INLINE_RP_HISTORY_LIMIT = 12


@dataclass(frozen=True)
class _InlinePrivatePendingMessage:
    sender_id: int
    receiver_ids: tuple[int, ...]
    receiver_usernames: tuple[str, ...]
    text: str
    created_at: datetime


_INLINE_PM_PENDING: dict[str, _InlinePrivatePendingMessage] = {}
_INLINE_RP_PENDING: dict[str, tuple[int, UserSnapshot, datetime]] = {}
_INLINE_RP_RECENT_TARGETS: dict[int, list[UserSnapshot]] = {}
_GACHA_CALLBACK_PREFIX = "gacha:"
_GACHA_PAID_PULL_PRICE = 160
_GACHA_CURRENCY_PURCHASE_AMOUNT = GACHA_DEFAULT_CURRENCY_PURCHASE_AMOUNT
_GACHA_COIN_EXCHANGE_RATE = GACHA_CURRENCY_PER_COIN_RATE
_GACHA_SUBSCRIPTION_CHANNEL = "@SelaraBot_Chanel"
_GACHA_SUBSCRIPTION_CHANNEL_URL = "https://t.me/SelaraBot_Chanel"
_GACHA_SUBSCRIPTION_CACHE_TTL = 600
_gacha_subscription_cache: dict[int, tuple[bool, float]] = {}
_GACHA_BANNER_LABELS: dict[str, str] = {
    "genshin": "Геншин",
    "hsr": "HSR",
}


@dataclass(frozen=True)
class _CustomAliasMatch:
    alias: ChatTextAlias
    tail_raw: str


@dataclass(frozen=True)
class _InlinePrivatePayload:
    receiver_usernames: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class _InlineRpPayload:
    action_key: str
    search_text: str


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

async def _safe_inline_query_answer(
    inline_query: InlineQuery,
    results: list[InlineQueryResultArticle],
    *,
    cache_time: int = 0,
    is_personal: bool = True,
) -> None:
    try:
        await inline_query.answer(results, cache_time=cache_time, is_personal=is_personal)
    except TelegramBadRequest as exc:
        error_text = str(exc).lower()
        if "query is too old" in error_text or "query id is invalid" in error_text:
            return
        raise


async def _get_bot_username(bot: Bot) -> str:
    global _INLINE_PM_BOT_USERNAME_CACHE
    if _INLINE_PM_BOT_USERNAME_CACHE is not None:
        return _INLINE_PM_BOT_USERNAME_CACHE

    me = await bot.get_me()
    _INLINE_PM_BOT_USERNAME_CACHE = (me.username or "").strip().lower()
    return _INLINE_PM_BOT_USERNAME_CACHE


def _is_uuid_string(value: str) -> bool:
    try:
        UUID(value)
        return True
    except (ValueError, TypeError):
        return False


def _inline_private_callback_data(message_id: str) -> str:
    return f"{_INLINE_PM_CALLBACK_PREFIX}{message_id}"


def _parse_inline_private_callback_data(data: str | None) -> str | None:
    if data is None or not data.startswith(_INLINE_PM_CALLBACK_PREFIX):
        return None
    candidate = data[len(_INLINE_PM_CALLBACK_PREFIX) :].strip()
    if not _is_uuid_string(candidate):
        return None
    return candidate


def _parse_inline_private_payload(raw_query: str, *, bot_username: str) -> _InlinePrivatePayload | None:
    tokens = [token for token in raw_query.split() if token]
    if not tokens:
        return None

    bot_username_norm = bot_username.lstrip("@").lower()
    seen_usernames: set[str] = set()
    receiver_usernames: list[str] = []
    text_tokens: list[str] = []

    for token in tokens:
        match = _INLINE_PM_MENTION_RE.fullmatch(token)
        if match is None:
            text_tokens.append(token)
            continue

        username = match.group(1).lower()
        if bot_username_norm and username == bot_username_norm:
            continue
        if username not in seen_usernames:
            seen_usernames.add(username)
            receiver_usernames.append(username)

    text = " ".join(text_tokens).strip()
    if not text:
        return None
    return _InlinePrivatePayload(receiver_usernames=tuple(receiver_usernames), text=text)


def _parse_inline_rp_payload(raw_query: str) -> _InlineRpPayload | None:
    tokens = [token for token in raw_query.split() if token]
    if not tokens:
        return None

    if tokens[0].lower() == "rp":
        tokens = tokens[1:]
        if not tokens:
            return None

    for trigger, action_key in sorted(
        _INLINE_RP_QUERY_ACTIONS.items(),
        key=lambda item: (-len(item[0].split()), -len(item[0])),
    ):
        trigger_width = len(trigger.split())
        if len(tokens) < trigger_width:
            continue
        prefix = normalize_text_command(" ".join(tokens[:trigger_width]))
        if prefix != trigger:
            continue
        return _InlineRpPayload(
            action_key=action_key,
            search_text=" ".join(tokens[trigger_width:]).strip(),
        )
    return None


async def _resolve_inline_private_receivers(
    activity_repo,
    *,
    sender_user_id: int,
    receiver_usernames: tuple[str, ...],
) -> list[UserSnapshot]:
    if not receiver_usernames:
        return []

    receivers: list[UserSnapshot] = []
    seen_receiver_ids: set[int] = set()
    for username in receiver_usernames:
        snapshot = await activity_repo.find_shared_group_user_by_username(
            sender_user_id=sender_user_id,
            username=username,
        )
        if snapshot is None:
            continue
        if snapshot.telegram_user_id == sender_user_id:
            continue
        if snapshot.telegram_user_id in seen_receiver_ids:
            continue
        seen_receiver_ids.add(snapshot.telegram_user_id)
        receivers.append(snapshot)
    return receivers


def _inline_private_username_mention(username: str) -> str:
    normalized = username.lstrip("@").strip().lower()
    return f"@{normalized}"


def _inline_private_preview_label(*, username: str, resolved_user: UserSnapshot | None) -> str:
    mention = _inline_private_username_mention(username)
    if resolved_user is None:
        return mention

    chat_display_name = (resolved_user.chat_display_name or "").strip()
    if chat_display_name and chat_display_name.lower() != mention.lower():
        return f"{chat_display_name} ({mention})"
    return mention


def _inline_private_history_label(user: UserSnapshot) -> str:
    chat_display_name = (user.chat_display_name or "").strip()
    if chat_display_name:
        return chat_display_name
    return display_name_from_parts(
        user_id=user.telegram_user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        chat_display_name=None,
    )


def _inline_private_history_mention(user: UserSnapshot) -> str:
    if user.username:
        return f"@{user.username}"
    label = _inline_private_history_label(user)
    return f'<a href="tg://user?id={user.telegram_user_id}">{escape(label)}</a>'


def _inline_rp_result_title(*, action_key: str, target: UserSnapshot) -> str:
    canonical = _SOCIAL_ACTION_CANONICAL.get(action_key, action_key)
    target_label = _social_action_display_name(target)
    if len(target_label) > 32:
        target_label = f"{target_label[:29]}..."
    return f"{canonical} {target_label}"


def _inline_rp_result_description(*, action_key: str, target: UserSnapshot) -> str:
    canonical = _SOCIAL_ACTION_CANONICAL.get(action_key, action_key)
    return f"{canonical} -> {_social_action_display_name(target)}"


def _inline_rp_render_message(*, action_key: str, actor: UserSnapshot, target: UserSnapshot) -> str:
    templates = _SOCIAL_ACTION_TEMPLATES.get(action_key) or ("{actor} {target}",)
    seed = f"{action_key}:{actor.telegram_user_id}:{target.telegram_user_id}"
    template = templates[int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16) % len(templates)]
    return template.format(actor=_social_action_mention(actor), target=_social_action_mention(target))


def _inline_rp_matches_search(user: UserSnapshot, *, search_text: str) -> bool:
    normalized = normalize_text_command(search_text)
    if not normalized:
        return True
    haystack = " ".join(
        str(part).lower()
        for part in (
            user.username,
            user.first_name,
            user.last_name,
            user.chat_display_name,
            user.telegram_user_id,
        )
        if part is not None
    )
    return normalized in haystack


async def _resolve_inline_rp_targets(
    activity_repo,
    *,
    sender_user_id: int,
    search_text: str,
) -> list[UserSnapshot]:
    candidates: list[UserSnapshot] = []
    seen_user_ids: set[int] = set()
    normalized_search = normalize_text_command(search_text)

    if normalized_search.startswith("@"):
        explicit = await activity_repo.find_shared_group_user_by_username(
            sender_user_id=sender_user_id,
            username=normalized_search,
        )
        if explicit is not None and explicit.telegram_user_id != sender_user_id:
            seen_user_ids.add(explicit.telegram_user_id)
            candidates.append(explicit)

    recent_targets = _INLINE_RP_RECENT_TARGETS.get(sender_user_id, [])
    for user in recent_targets:
        if user.telegram_user_id == sender_user_id or user.telegram_user_id in seen_user_ids:
            continue
        if not _inline_rp_matches_search(user, search_text=search_text):
            continue
        seen_user_ids.add(user.telegram_user_id)
        candidates.append(user)

    for user in await activity_repo.list_recent_inline_private_receivers(
        sender_user_id=sender_user_id,
        limit=_INLINE_RP_HISTORY_LIMIT,
    ):
        if user.telegram_user_id == sender_user_id or user.telegram_user_id in seen_user_ids:
            continue
        if not _inline_rp_matches_search(user, search_text=search_text):
            continue
        seen_user_ids.add(user.telegram_user_id)
        candidates.append(user)

    return candidates[:_INLINE_RP_HISTORY_LIMIT]


def _inline_private_usernames_preview(usernames: tuple[str, ...]) -> str:
    preview = ", ".join(_inline_private_username_mention(username) for username in usernames)
    if len(preview) > 120:
        return f"{preview[:117]}..."
    return preview


def _inline_private_group_message(*, mentions: list[str]) -> str:
    return f"📩 Личное сообщение для пользователей: {', '.join(mentions)}"


def _inline_private_result_title(receiver_usernames: tuple[str, ...]) -> str:
    count = len(receiver_usernames)
    if count <= 0:
        return "Отправить личное сообщение"

    if count == 1:
        target = _inline_private_username_mention(receiver_usernames[0])
        if len(target) > 32:
            target = f"{target[:29]}..."
        return f"Отправить пользователю {target}"

    return f"Отправить {count}-м пользователям"


def _inline_private_history_result_title(user: UserSnapshot) -> str:
    target = _inline_private_history_label(user)
    if len(target) > 32:
        target = f"{target[:29]}..."
    return f"Отправить пользователю {target}"


def _build_inline_private_target_usernames(receiver_usernames: tuple[str, ...]) -> list[tuple[str, ...]]:
    if not receiver_usernames:
        return []

    targets: list[tuple[str, ...]] = [receiver_usernames]
    for username in receiver_usernames:
        single = (username,)
        if single not in targets:
            targets.append(single)
    return targets


def _extract_inline_private_usernames(message_text: str) -> set[str]:
    if not message_text:
        return set()
    return {match.group(1).lower() for match in _INLINE_PM_MENTION_SCAN_RE.finditer(message_text)}


def _cleanup_inline_private_pending(*, now: datetime) -> None:
    expired_ids = [
        message_id
        for message_id, pending in _INLINE_PM_PENDING.items()
        if (now - pending.created_at).total_seconds() > _INLINE_PM_PENDING_TTL_SECONDS
    ]
    for message_id in expired_ids:
        _INLINE_PM_PENDING.pop(message_id, None)

    expired_sender_ids = [
        sender_id
        for sender_id, last_sent_at in _INLINE_PM_LAST_SENT_AT.items()
        if (now - last_sent_at).total_seconds() > _INLINE_PM_COOLDOWN_CACHE_TTL_SECONDS
    ]
    for sender_id in expired_sender_ids:
        _INLINE_PM_LAST_SENT_AT.pop(sender_id, None)

    expired_page_keys = [
        page_key
        for page_key, (_, updated_at) in _INLINE_PM_ALERT_PAGE.items()
        if (now - updated_at).total_seconds() > _INLINE_PM_ALERT_PAGE_TTL_SECONDS
    ]
    for page_key in expired_page_keys:
        _INLINE_PM_ALERT_PAGE.pop(page_key, None)

    expired_rp_ids = [
        result_id
        for result_id, (_, _, created_at) in _INLINE_RP_PENDING.items()
        if (now - created_at).total_seconds() > _INLINE_RP_PENDING_TTL_SECONDS
    ]
    for result_id in expired_rp_ids:
        _INLINE_RP_PENDING.pop(result_id, None)


def _split_inline_private_text(text: str, *, max_len: int = _INLINE_PM_ALERT_CHUNK_LEN) -> list[str]:
    normalized = " ".join(text.split()).strip()
    if not normalized:
        return []

    words = normalized.split(" ")
    chunks: list[str] = []
    current = ""
    for word in words:
        if not word:
            continue

        if len(word) > max_len:
            if current:
                chunks.append(current)
                current = ""
            for idx in range(0, len(word), max_len):
                chunks.append(word[idx : idx + max_len])
            continue

        if not current:
            current = word
            continue

        candidate = f"{current} {word}"
        if len(candidate) <= max_len:
            current = candidate
            continue

        chunks.append(current)
        current = word

    if current:
        chunks.append(current)
    return chunks


def _inline_private_cooldown_left_seconds(*, sender_user_id: int, now: datetime) -> int:
    last_sent_at = _INLINE_PM_LAST_SENT_AT.get(sender_user_id)
    if last_sent_at is None:
        return 0
    delta = now - last_sent_at
    if delta >= timedelta(seconds=_INLINE_PM_COOLDOWN_SECONDS):
        return 0
    return max(1, int(_INLINE_PM_COOLDOWN_SECONDS - delta.total_seconds()))


def _mark_inline_private_sent(*, sender_user_id: int, now: datetime) -> None:
    _INLINE_PM_LAST_SENT_AT[sender_user_id] = now


def _match_custom_alias(text: str, aliases: list[ChatTextAlias]) -> _CustomAliasMatch | None:
    if not text or not aliases:
        return None

    ordered = sorted(aliases, key=lambda item: (len(item.alias_text_norm), item.alias_text_norm), reverse=True)
    for alias in ordered:
        parts = [part for part in alias.alias_text_norm.split(" ") if part]
        if not parts:
            continue
        pattern = r"^\s*" + r"\s+".join(re.escape(part) for part in parts) + r"(?:\s+(?P<tail>[\s\S]*))?\s*$"
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        tail_raw = (match.group("tail") or "").strip()
        return _CustomAliasMatch(alias=alias, tail_raw=tail_raw)
    return None


def _rewrite_text_with_alias(match: _CustomAliasMatch) -> str:
    source = match.alias.source_trigger_norm
    effective_command_key = resolve_builtin_command_key(source) or match.alias.command_key
    if match.tail_raw and effective_command_key in COMMAND_KEYS_WITH_TAIL:
        return f"{source} {match.tail_raw}"
    return source


def _apply_alias_mode_to_text(
    *,
    text: str,
    mode: str,
    aliases: list[ChatTextAlias],
) -> str | None:
    if not aliases:
        return text

    if mode == "standard_only":
        return text

    matched_alias = _match_custom_alias(text, aliases)
    if matched_alias is not None:
        return _rewrite_text_with_alias(matched_alias)

    if mode != "aliases_if_exists":
        return text

    builtin_match = match_builtin_command(text)
    if builtin_match is None:
        return text

    commands_with_aliases = {alias.command_key for alias in aliases}
    if builtin_match.command_key in commands_with_aliases:
        return None
    return text


async def _answer_quiet(message: Message, text: str, **kwargs) -> None:
    if message.chat.type in {"group", "supergroup"}:
        kwargs.setdefault("disable_notification", True)
    await message.answer(text, **kwargs)


def _gacha_banner_label(banner: str) -> str:
    return _GACHA_BANNER_LABELS.get((banner or "").strip().lower(), banner)


def _gacha_currency_label(banner: str) -> str:
    if (banner or "").strip().lower() == "hsr":
        return "Звездный нефрит"
    return "Примогемы"


def _gacha_currency_button_label(banner: str) -> str:
    if (banner or "").strip().lower() == "hsr":
        return f"+{_GACHA_CURRENCY_PURCHASE_AMOUNT} нефрита"
    return f"+{_GACHA_CURRENCY_PURCHASE_AMOUNT} примогемов"


def _gacha_rank_label(banner: str) -> str:
    if (banner or "").strip().lower() == "hsr":
        return "Уровень освоения"
    return "Ранг приключений"


def _gacha_economy_mode(*, chat_type: str, chat_settings: ChatSettings) -> str:
    if chat_type in {"group", "supergroup"}:
        return chat_settings.economy_mode
    return "global"


def _gacha_economy_chat_id(*, chat_type: str, chat_id: int) -> int | None:
    if chat_type in {"group", "supergroup"}:
        return chat_id
    return None


async def _is_subscribed_to_channel(bot: Bot, user_id: int) -> bool:
    cached = _gacha_subscription_cache.get(user_id)
    if cached is not None:
        subscribed, ts = cached
        if time.monotonic() - ts < _GACHA_SUBSCRIPTION_CACHE_TTL:
            return subscribed
    try:
        member = await bot.get_chat_member(chat_id=_GACHA_SUBSCRIPTION_CHANNEL, user_id=user_id)
        subscribed = member.status not in {"left", "kicked"}
    except Exception:
        subscribed = True
    _gacha_subscription_cache[user_id] = (subscribed, time.monotonic())
    return subscribed


async def _require_channel_subscription(bot: Bot, message: Message, user_id: int) -> bool:
    if await _is_subscribed_to_channel(bot, user_id):
        return True
    await message.answer(
        f'Для использования гачи нужно подписаться на наш канал: '
        f'<a href="{_GACHA_SUBSCRIPTION_CHANNEL_URL}">SelaraBot Chanel</a>',
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    return False


async def _require_channel_subscription_callback(bot: Bot, query: CallbackQuery, user_id: int) -> bool:
    if await _is_subscribed_to_channel(bot, user_id):
        return True
    await query.answer(
        f"Для гачи нужно подписаться на канал {_GACHA_SUBSCRIPTION_CHANNEL}",
        show_alert=True,
    )
    return False


async def _load_gacha_coin_balance(
    economy_repo,
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
) -> int | None:
    scope, error = await resolve_scope_or_error(
        economy_repo,
        economy_mode=economy_mode,
        chat_id=chat_id,
        user_id=user_id,
    )
    if scope is None:
        return None
    account, _ = await get_account_or_error(economy_repo, scope=scope, user_id=user_id)
    return account.balance


def _gacha_image_filename(image_url: str) -> str:
    candidate = basename(urlsplit(image_url).path.strip())
    if candidate:
        return candidate
    return "gacha-card.jpg"


async def _fetch_gacha_image_file(image_url: str, *, timeout_seconds: float) -> BufferedInputFile:
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        response = await client.get(image_url)
        response.raise_for_status()
    return BufferedInputFile(response.content, filename=_gacha_image_filename(image_url))


def _gacha_escape_message_html(text: str) -> str:
    return "\n".join(escape(line) for line in text.splitlines())


def _render_gacha_pull_html(*, banner: str, response, owner_user_id: int) -> str:
    header = f"<b>🎴 {_gacha_banner_label(banner)}</b>"
    if not response.message:
        return header

    if response.card is None:
        return f"{header}\n\n{_gacha_escape_message_html(response.message)}"

    lines = response.message.splitlines()
    if lines:
        first_line = lines[0]
        if ": " in first_line:
            prefix, _ = first_line.rsplit(": ", 1)
            lines[0] = f"{escape(prefix)}: {format_user_link(user_id=owner_user_id, label=response.card.name)}"
        else:
            lines[0] = escape(first_line)
    if len(lines) > 1:
        lines[1:] = [escape(line) for line in lines[1:]]
    return f"{header}\n\n" + "\n".join(lines)


def _gacha_buy_callback_data(*, banner: str, owner_user_id: int) -> str:
    return f"{_GACHA_CALLBACK_PREFIX}buy:{banner}:u{owner_user_id}"


def _gacha_currency_buy_callback_data(*, banner: str, amount: int, owner_user_id: int) -> str:
    return f"{_GACHA_CALLBACK_PREFIX}currency:{banner}:{amount}:u{owner_user_id}"


def _gacha_sell_callback_data(*, banner: str, pull_id: int, owner_user_id: int) -> str:
    return f"{_GACHA_CALLBACK_PREFIX}sell:{banner}:{pull_id}:u{owner_user_id}"


def _parse_gacha_callback_data(data: str | None) -> tuple[str | None, str | None, int | None, int | None, int | None]:
    if data is None or not data.startswith(_GACHA_CALLBACK_PREFIX):
        return None, None, None, None, None
    parts = data.split(":")
    if len(parts) == 4 and parts[1] == "buy" and parts[3].startswith("u") and parts[3][1:].isdigit():
        return "buy", parts[2], None, int(parts[3][1:]), None
    if (
        len(parts) == 5
        and parts[1] == "currency"
        and parts[3].isdigit()
        and parts[4].startswith("u")
        and parts[4][1:].isdigit()
    ):
        return "currency", parts[2], None, int(parts[4][1:]), int(parts[3])
    if len(parts) == 5 and parts[1] == "sell" and parts[3].isdigit() and parts[4].startswith("u") and parts[4][1:].isdigit():
        return "sell", parts[2], int(parts[3]), int(parts[4][1:]), None
    return None, None, None, None, None


def _build_gacha_sell_markup(*, banner: str, pull_id: int, owner_user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Продать",
                    callback_data=_gacha_sell_callback_data(banner=banner, pull_id=pull_id, owner_user_id=owner_user_id),
                )
            ]
        ]
    )


def _build_gacha_info_markup(*, owner_user_id: int, banners: list[str]) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    for banner in banners:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Крутка • {_gacha_banner_label(banner)}",
                    callback_data=_gacha_buy_callback_data(banner=banner, owner_user_id=owner_user_id),
                ),
                InlineKeyboardButton(
                    text=_gacha_currency_button_label(banner),
                    callback_data=_gacha_currency_buy_callback_data(
                        banner=banner,
                        amount=_GACHA_CURRENCY_PURCHASE_AMOUNT,
                        owner_user_id=owner_user_id,
                    ),
                ),
            ]
        )
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_gacha_pull_markup(*, response, banner: str, owner_user_id: int) -> InlineKeyboardMarkup | None:
    if response.sell_offer is None or response.pull_id is None:
        return None
    return _build_gacha_sell_markup(banner=banner, pull_id=response.pull_id, owner_user_id=owner_user_id)


def _format_gacha_recent_pull(entry) -> str:
    if entry is None:
        return "нет"
    pulled_at = getattr(entry, "pulled_at", "")
    if isinstance(pulled_at, str):
        timestamp = pulled_at.replace("T", " ")[:16]
    else:
        timestamp = str(pulled_at)
    return f"{escape(entry.card_name)} • <code>{escape(timestamp)}</code>"


def _render_gacha_info_section(*, banner: str, response) -> str:
    recent = response.recent_pulls[0] if response.recent_pulls else None
    return "\n".join(
        [
            f"<b>{_gacha_banner_label(banner)}</b>",
            f"🧭 {_gacha_rank_label(banner)}: <code>{response.player.adventure_rank}</code> ({response.player.xp_into_rank}/{response.player.xp_for_next_rank})",
            f"🌟 Очки: <code>{response.player.total_points}</code>",
            f"💠 {_gacha_currency_label(banner)}: <code>{response.player.total_primogems}</code>",
            f"🗂 Карты: <code>{response.unique_cards}</code> • копии <code>{response.total_copies}</code>",
            f"🕘 Последняя: {_format_gacha_recent_pull(recent)}",
        ]
    )


async def _build_gacha_info_view(
    settings: Settings,
    economy_repo,
    *,
    user_id: int,
    economy_mode: str,
    chat_id: int | None,
) -> tuple[str, InlineKeyboardMarkup | None]:
    banners = ("genshin", "hsr")
    results = await asyncio.gather(
        *(get_gacha_profile(settings, user_id=user_id, banner=banner) for banner in banners),
        return_exceptions=True,
    )
    coin_balance = await _load_gacha_coin_balance(
        economy_repo,
        economy_mode=economy_mode,
        chat_id=chat_id,
        user_id=user_id,
    )

    sections: list[str] = [
        "<b>Гача инфо</b>",
        f"💸 Платная крутка: <code>{_GACHA_PAID_PULL_PRICE}</code> валюты баннера",
        f"💱 Обмен: <code>1</code> валюты = <code>{_GACHA_COIN_EXCHANGE_RATE}</code> монет",
    ]
    if coin_balance is not None:
        sections.append(f"🪙 Монеты бота: <code>{coin_balance}</code>")
    available_banners: list[str] = []
    errors: list[str] = []
    for banner, result in zip(banners, results, strict=True):
        if isinstance(result, Exception):
            error_text = result.message if isinstance(result, GachaUseCaseError) else str(result)
            errors.append(f"❌ {escape(_gacha_banner_label(banner))}: {escape(error_text)}")
            continue
        available_banners.append(banner)
        sections.extend(["", _render_gacha_info_section(banner=banner, response=result)])

    if not available_banners:
        if errors:
            return "\n".join(errors), None
        return "Не удалось загрузить гача-статистику.", None

    if errors:
        sections.extend(["", *errors])
    return "\n".join(sections), _build_gacha_info_markup(owner_user_id=user_id, banners=available_banners)


async def _deliver_gacha_pull_response(message: Message, settings: Settings, *, banner: str, response, owner_user_id: int) -> None:
    rendered_message = _render_gacha_pull_html(banner=banner, response=response, owner_user_id=owner_user_id)
    reply_markup = _build_gacha_pull_markup(response=response, banner=banner, owner_user_id=owner_user_id)
    if response.card is None or not response.card.image_url:
        await _answer_quiet(
            message,
            rendered_message,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
        return

    try:
        photo = await _fetch_gacha_image_file(
            response.card.image_url,
            timeout_seconds=settings.gacha_timeout_seconds,
        )
        await message.answer_photo(
            photo=photo,
            caption=rendered_message,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_notification=message.chat.type in {"group", "supergroup"},
        )
    except (httpx.HTTPError, TelegramBadRequest) as exc:
        logger.warning(
            "Gacha image delivery failed, falling back to text",
            extra={
                "chat_id": getattr(message.chat, "id", None),
                "user_id": owner_user_id,
                "banner": banner,
                "image_url": response.card.image_url,
                "error": str(exc),
            },
        )
        await _answer_quiet(
            message,
            rendered_message,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )


async def _send_gacha_pull(message: Message, settings: Settings, *, banner: str) -> None:
    if message.from_user is None:
        return

    try:
        response = await pull_gacha_card(
            settings,
            user_id=message.from_user.id,
            username=message.from_user.username,
            banner=banner,
        )
    except GachaUseCaseError as exc:
        await _answer_quiet(message, exc.message)
        return

    await _deliver_gacha_pull_response(
        message,
        settings,
        banner=banner,
        response=response,
        owner_user_id=message.from_user.id,
    )


async def _send_gacha_profile(message: Message, settings: Settings, *, banner: str) -> None:
    if message.from_user is None:
        return

    try:
        response = await get_gacha_profile(
            settings,
            user_id=message.from_user.id,
            banner=banner,
        )
    except GachaUseCaseError as exc:
        await _answer_quiet(message, exc.message)
        return

    await _answer_quiet(message, response.message, disable_web_page_preview=True)


async def _send_gacha_info(message: Message, settings: Settings, economy_repo, chat_settings: ChatSettings) -> None:
    if message.from_user is None:
        return

    text, reply_markup = await _build_gacha_info_view(
        settings,
        economy_repo,
        user_id=message.from_user.id,
        economy_mode=_gacha_economy_mode(chat_type=message.chat.type, chat_settings=chat_settings),
        chat_id=_gacha_economy_chat_id(chat_type=message.chat.type, chat_id=message.chat.id),
    )
    await _answer_quiet(message, text, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)


async def _resolve_gacha_skip_target(
    message: Message,
    activity_repo,
    *,
    target_username: str | None,
) -> tuple[int | None, str | None]:
    if message.from_user is None:
        return None, "Не удалось определить отправителя команды."

    # If target specified via @username, resolve via activity repository
    if target_username:
        if message.chat.type not in {"group", "supergroup"}:
            return None, "Сброс кулдауна по @username работает только в группе."

        snapshot = await activity_repo.find_shared_group_user_by_username(
            sender_user_id=message.from_user.id,
            username=target_username,
        )
        if snapshot is None:
            return None, f"Не удалось найти пользователя {target_username}."

        return snapshot.telegram_user_id, None

    # If command was issued as a reply, use the replied-to user as the target
    if message.reply_to_message is not None and message.reply_to_message.from_user is not None:
        return message.reply_to_message.from_user.id, None

    # Default to sender
    return message.from_user.id, None


async def _ensure_gacha_admin_user(
    message: Message,
    settings: Settings,
    *,
    denied_message: str,
) -> bool:
    admin_user_id = settings.gacha_admin_user_id
    if admin_user_id is None:
        await _answer_quiet(message, "Команда недоступна: не настроен GACHA_ADMIN_USER_ID.")
        return False
    if message.from_user is None or message.from_user.id != admin_user_id:
        await _answer_quiet(message, denied_message)
        return False
    return True


def _format_gacha_toggle_duration(seconds: int) -> str:
    if seconds % 86400 == 0:
        n = seconds // 86400
        return f"{n} д."
    if seconds % 3600 == 0:
        n = seconds // 3600
        return f"{n} ч."
    n = seconds // 60
    return f"{n} мин."


async def _manage_gacha_toggle(
    message: Message,
    activity_repo,
    settings: Settings,
    chat_settings: ChatSettings,
    *,
    command_key: str,
    duration_seconds: int | None,
) -> None:
    if message.from_user is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return

    user_id = message.from_user.id
    is_env_admin = (
        (settings.gacha_admin_user_id is not None and user_id == settings.gacha_admin_user_id)
        or (settings.admin_user_id is not None and user_id == settings.admin_user_id)
    )

    if not is_env_admin:
        allowed, _, _ = await has_permission(
            activity_repo,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            chat_title=message.chat.title,
            user_id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            is_bot=bool(message.from_user.is_bot),
            permission="manage_settings",
            bootstrap_if_missing_owner=False,
        )
        if not allowed:
            return

    enable = command_key == "gacha_on"
    restore_at: datetime | None = None
    if duration_seconds is not None:
        restore_at = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)

    chat = ChatSnapshot(
        telegram_chat_id=message.chat.id,
        chat_type=message.chat.type,
        title=message.chat.title,
    )
    values = settings_to_dict(chat_settings)
    values["gacha_enabled"] = enable
    values["gacha_restore_at"] = restore_at
    await activity_repo.upsert_chat_settings(chat=chat, values=values)

    state_label = "включена" if enable else "выключена"
    if duration_seconds is not None:
        duration_label = _format_gacha_toggle_duration(duration_seconds)
        restore_label = "выключится" if enable else "включится"
        await message.answer(f"Гача {state_label} на {duration_label}, затем {restore_label} автоматически.")
    else:
        await message.answer(f"Гача {state_label}.")


async def _check_and_maybe_restore_gacha(
    message: Message,
    activity_repo,
    chat_settings: ChatSettings,
) -> ChatSettings:
    """If a timed toggle has expired, flip gacha_enabled back and clear restore_at."""
    if chat_settings.gacha_restore_at is None:
        return chat_settings
    if datetime.now(timezone.utc) < chat_settings.gacha_restore_at:
        return chat_settings
    new_enabled = not chat_settings.gacha_enabled
    chat = ChatSnapshot(
        telegram_chat_id=message.chat.id,
        chat_type=message.chat.type,
        title=message.chat.title,
    )
    values = settings_to_dict(chat_settings)
    values["gacha_enabled"] = new_enabled
    values["gacha_restore_at"] = None
    return await activity_repo.upsert_chat_settings(chat=chat, values=values)


async def _send_gacha_skip(
    message: Message,
    activity_repo,
    settings: Settings,
    *,
    banner: str,
    target_username: str | None,
) -> None:
    if message.from_user is None:
        return

    if not await _ensure_gacha_admin_user(
        message,
        settings,
        denied_message="Недостаточно прав для сброса кулдауна гачи.",
    ):
        return

    target_user_id, error = await _resolve_gacha_skip_target(
        message,
        activity_repo,
        target_username=target_username,
    )
    if error is not None:
        await _answer_quiet(message, error)
        return
    if target_user_id is None:
        await _answer_quiet(message, "Не удалось определить пользователя для сброса кулдауна.")
        return

    try:
        response = await reset_gacha_cooldown(
            settings,
            user_id=target_user_id,
            banner=banner,
        )
    except GachaUseCaseError as exc:
        await _answer_quiet(message, exc.message)
        return

    await _answer_quiet(message, response.message)


@router.message(Command("gachagive"))
async def gachagive_command(
    message: Message,
    command: CommandObject,
    bot: Bot,
    activity_repo,
    settings: Settings,
) -> None:
    if message.from_user is None:
        return

    if not await _require_channel_subscription(bot, message, message.from_user.id):
        return

    raw = (command.args or "").strip()
    if not raw and message.reply_to_message is None:
        await _answer_quiet(message, 'Формат: /gachagive <code> @username (или reply)')
        return

    parts = raw.split()
    code = parts[0] if parts else None
    target_username = parts[1] if len(parts) > 1 else None

    if not code:
        await _answer_quiet(message, 'Укажите код карты: /gachagive <code> @username (или reply)')
        return

    if not await _ensure_gacha_admin_user(
        message,
        settings,
        denied_message="Недостаточно прав для выдачи карты в гаче.",
    ):
        return

    target_user_id, error = await _resolve_gacha_skip_target(
        message,
        activity_repo,
        target_username=target_username,
    )
    if error is not None:
        await _answer_quiet(message, error)
        return
    if target_user_id is None:
        await _answer_quiet(message, "Не удалось определить пользователя для выдачи карты.")
        return

    try:
        response = await give_gacha_card(settings, user_id=target_user_id, banner=None, code=code)
    except GachaUseCaseError as exc:
        await _answer_quiet(message, exc.message)
        return

    await _answer_quiet(message, response.message)


def _command_object_from_args(raw_args: str | None):
    value = (raw_args or "").strip()
    return SimpleNamespace(args=value or None)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _menu_owner_suffix(owner_user_id: int | None) -> str:
    return f":u{owner_user_id}" if owner_user_id is not None else ""


def _menu_callback(screen: str, *, owner_user_id: int | None) -> str:
    return f"menu:{screen}{_menu_owner_suffix(owner_user_id)}"


def _parse_menu_callback(data: str | None) -> tuple[str | None, int | None]:
    if not data or not data.startswith("menu:"):
        return None, None
    parts = data.split(":")
    if len(parts) < 2:
        return None, None
    owner_user_id = None
    if parts[-1].startswith("u") and parts[-1][1:].isdigit():
        owner_user_id = int(parts[-1][1:])
        parts = parts[:-1]
    return ":".join(parts[1:]) or None, owner_user_id


def _menu_keyboard(
    *,
    screen: str,
    owner_user_id: int | None,
    mode: str,
    web_base_url: str | None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    mode_short = "l" if mode == "local" else "g"
    if screen == "main":
        builder.button(text="💰 Экономика", callback_data=_menu_callback("economy", owner_user_id=owner_user_id))
        builder.button(text="🎮 Игры", callback_data=_menu_callback("games", owner_user_id=owner_user_id))
        builder.button(text="💞 Отношения", callback_data=_menu_callback("relations", owner_user_id=owner_user_id))
        builder.adjust(1)
        return builder.as_markup()

    if screen == "economy":
        builder.button(text="Панель", callback_data=f"eco:dash:{mode_short}{_menu_owner_suffix(owner_user_id)}")
        builder.button(text="Ферма", callback_data=f"farm:ov:{mode_short}{_menu_owner_suffix(owner_user_id)}")
        builder.button(text="Рынок", callback_data=f"mkt:ov:{mode_short}{_menu_owner_suffix(owner_user_id)}")
        builder.button(text="Магазин", callback_data=f"shop:ov:{mode_short}{_menu_owner_suffix(owner_user_id)}")
        builder.button(text="⬅️ Назад", callback_data=_menu_callback("main", owner_user_id=owner_user_id))
        builder.adjust(2, 2, 1)
        return builder.as_markup()

    if screen == "games":
        if web_base_url:
            builder.button(text="🌐 Игровой центр", url=f"{web_base_url}/app/games")
        builder.button(text="⬅️ Назад", callback_data=_menu_callback("main", owner_user_id=owner_user_id))
        builder.adjust(1)
        return builder.as_markup()

    if screen == "relations":
        if web_base_url:
            builder.button(text="🌐 Профиль", url=f"{web_base_url}/app")
        builder.button(text="⬅️ Назад", callback_data=_menu_callback("main", owner_user_id=owner_user_id))
        builder.adjust(1)
        return builder.as_markup()

    builder.button(text="⬅️ Назад", callback_data=_menu_callback("main", owner_user_id=owner_user_id))
    return builder.as_markup()


def _menu_text(*, screen: str, mode: str) -> str:
    if screen == "economy":
        return (
            "<b>/menu • Экономика</b>\n"
            f"Режим: <code>{escape(mode)}</code>\n"
            "Здесь собраны быстрые кнопки к панели, ферме, рынку и магазину без лишних slash-команд."
        )
    if screen == "games":
        return (
            "<b>/menu • Игры</b>\n"
            "Создание лобби осталось в Telegram: используйте <code>/game whoami</code>, <code>/game spy</code>, "
            "<code>/game mafia</code> или откройте веб-центр игр."
        )
    if screen == "relations":
        return (
            "<b>/menu • Отношения</b>\n"
            "Быстрые точки входа: <code>мои отношения</code>, <code>мой брак</code>, <code>браки</code>, "
            "<code>предложить встречаться</code>, <code>предложить брак</code>, <code>/family</code> "
            "и reply-действия вроде <code>обнять</code>."
        )
    return (
        "<b>/menu</b>\n"
        "Один вход вместо россыпи slash-команд. Выберите раздел ниже."
    )


def _extract_zhmyh_level(text: str) -> tuple[bool, int | None, str | None]:
    normalized = normalize_text_command(text)
    if not normalized:
        return False, None, None

    first_token = normalized.split(" ", maxsplit=1)[0]
    if first_token != "жмых":
        return False, None, None

    match = _ZHMYH_PATTERN.fullmatch(normalized)
    if match is None:
        return True, None, "Формат: <code>жмых</code> или <code>жмых 1..6</code>"

    raw_level = match.group("level")
    if raw_level is None:
        return True, _ZHMYH_DEFAULT_LEVEL, None

    level = int(raw_level)
    if not _ZHMYH_MIN_LEVEL <= level <= _ZHMYH_MAX_LEVEL:
        return True, None, "Уровень жмыха должен быть от 1 до 6. Пример: <code>жмых 4</code>"

    return True, level, None


def _extract_social_action(text: str) -> str | None:
    action_key, _mass_target, _replica = _extract_social_action_target_request(text)
    return action_key


def _extract_social_action_request(text: str) -> tuple[str | None, str | None]:
    raw_text = (text or "").strip()
    normalized = normalize_text_command(text)
    if not normalized or normalized.startswith("/"):
        return None, None

    for trigger in sorted(_SOCIAL_ACTION_ALIASES, key=len, reverse=True):
        pattern = (
            r"^\s*"
            + r"\s+".join(re.escape(part) for part in trigger.split())
            + r"(?:[?!.,:;…]+)?(?:\s+(?P<tail>[\s\S]*))?\s*$"
        )
        match = re.match(pattern, raw_text, flags=re.IGNORECASE)
        if match is None:
            continue
        tail_raw = " ".join(((match.group("tail") or "")).split()).strip() or None
        return _SOCIAL_ACTION_ALIASES[trigger], tail_raw
    return None, None


def _extract_social_action_target_request(text: str) -> tuple[str | None, bool, str | None]:
    action_key, tail = _extract_social_action_request(text)
    if action_key is None:
        return None, False, None
    if not tail:
        return action_key, False, None

    match = re.match(r"^(?P<marker>\S+)(?:[?!.,:;…]+)?(?:\s+(?P<rest>[\s\S]*))?\s*$", tail)
    if match is None:
        return action_key, False, tail

    marker = normalize_text_command(match.group("marker") or "")
    if marker not in {"всех", "всем"}:
        return action_key, False, tail

    replica = " ".join(((match.group("rest") or "")).split()).strip() or None
    return action_key, True, replica


def _build_social_action_replica_line(replica: str) -> str:
    template = random.choice(_SOCIAL_ACTION_REPLICA_TEMPLATES)
    return template.format(replica=escape(replica))


def _build_social_action_mass_messages(
    *,
    template: str,
    actor_mention: str,
    target_mentions: list[str],
    replica: str | None,
) -> list[str]:
    if not target_mentions:
        return []

    replica_line = _build_social_action_replica_line(replica) if replica else None
    messages: list[str] = []
    current_mentions: list[str] = []

    for mention in target_mentions:
        candidate_mentions = current_mentions + [mention]
        candidate = template.format(actor=actor_mention, target=", ".join(candidate_mentions))
        if replica_line:
            candidate = f"{candidate}\n{replica_line}"
        if current_mentions and len(candidate) > _MAX_MESSAGE_LEN_SAFE:
            current = template.format(actor=actor_mention, target=", ".join(current_mentions))
            if replica_line:
                current = f"{current}\n{replica_line}"
            messages.append(current)
            current_mentions = [mention]
            continue
        current_mentions = candidate_mentions

    if current_mentions:
        current = template.format(actor=actor_mention, target=", ".join(current_mentions))
        if replica_line:
            current = f"{current}\n{replica_line}"
        messages.append(current)
    return messages


def _extract_today_randomizer_predicate(text: str) -> tuple[bool, str | None, str | None]:
    normalized = normalize_text_command(text)
    if not normalized or normalized.startswith("/"):
        return False, None, None

    match = _TODAY_RANDOMIZER_PATTERN.fullmatch(text)
    if match is None:
        return False, None, None

    raw_body = (match.group("body") or "").strip()
    raw_body = raw_body.strip(" \t\r\n?!.,;:…-")
    predicate = re.sub(r"\s+", " ", raw_body)
    if not predicate:
        return True, None, "Формат: <code>кто сегодня легенда</code>"
    if len(predicate) > _TODAY_RANDOMIZER_MAX_LEN:
        return True, None, f"Фраза слишком длинная. Оставьте до {_TODAY_RANDOMIZER_MAX_LEN} символов."
    if predicate[:1].isalpha():
        predicate = predicate[:1].lower() + predicate[1:]
    return True, predicate, None


def _is_daily_article_command(text: str) -> bool:
    normalized = normalize_text_command(text)
    if normalized in {"моя статья", "статья"}:
        return True
    return _DAILY_ARTICLE_COMMAND_RE.fullmatch(text) is not None


def _stable_rng(*parts: object) -> random.Random:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    seed = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return random.Random(seed)


def _load_daily_articles() -> tuple[tuple[str, str], ...]:
    path = Path(__file__).resolve().parents[1] / "daily_articles.json"
    if not path.exists():
        return ()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to load daily articles from %s", path)
        return ()

    if not isinstance(raw, list):
        return ()

    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip()
        title = str(item.get("title", "")).strip()
        if not code or not title:
            continue
        dedupe_key = (code, title.casefold())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        result.append((code, title))
    return tuple(result)


DAILY_ARTICLES: tuple[tuple[str, str], ...] = _load_daily_articles()


def _fallback_subject_bbox(width: int, height: int) -> tuple[int, int, int, int]:
    pad_x = max(2, int(width * 0.18))
    pad_y = max(2, int(height * 0.18))
    left = pad_x
    top = pad_y
    right = max(left + 8, width - pad_x)
    bottom = max(top + 8, height - pad_y)
    right = min(width, right)
    bottom = min(height, bottom)
    return left, top, right, bottom


def _estimate_subject_bbox(image, *, resampling) -> tuple[int, int, int, int]:
    from PIL import ImageFilter, ImageOps

    width, height = image.size
    if width < 48 or height < 48:
        return _fallback_subject_bbox(width, height)

    probe = image.convert("L")
    probe.thumbnail((192, 192), resampling.BILINEAR)
    probe = ImageOps.autocontrast(probe.filter(ImageFilter.FIND_EDGES))
    probe_w, probe_h = probe.size
    values = list(probe.getdata())
    if not values:
        return _fallback_subject_bbox(width, height)

    mean_value = sum(values) / len(values)
    threshold = int(_clamp(mean_value * 1.35 + 18.0, 24.0, 245.0))

    total_weight = 0.0
    weighted_x = 0.0
    weighted_y = 0.0
    for idx, value in enumerate(values):
        weight = float(value - threshold)
        if weight <= 0:
            continue
        x = idx % probe_w
        y = idx // probe_w
        total_weight += weight
        weighted_x += x * weight
        weighted_y += y * weight

    if total_weight <= 0:
        return _fallback_subject_bbox(width, height)

    center_x = weighted_x / total_weight
    center_y = weighted_y / total_weight

    variance_x = 0.0
    variance_y = 0.0
    for idx, value in enumerate(values):
        weight = float(value - threshold)
        if weight <= 0:
            continue
        x = idx % probe_w
        y = idx // probe_w
        variance_x += (x - center_x) * (x - center_x) * weight
        variance_y += (y - center_y) * (y - center_y) * weight

    spread_x = max(probe_w * 0.12, (variance_x / total_weight) ** 0.5 * 2.2)
    spread_y = max(probe_h * 0.12, (variance_y / total_weight) ** 0.5 * 2.2)
    left = int((center_x - spread_x) * width / probe_w)
    top = int((center_y - spread_y) * height / probe_h)
    right = int((center_x + spread_x) * width / probe_w)
    bottom = int((center_y + spread_y) * height / probe_h)

    left = int(_clamp(left, 0, width - 2))
    top = int(_clamp(top, 0, height - 2))
    right = int(_clamp(right, left + 2, width))
    bottom = int(_clamp(bottom, top + 2, height))

    min_box_w = max(24, int(width * 0.14))
    min_box_h = max(24, int(height * 0.14))
    if (right - left) < min_box_w:
        center = (left + right) // 2
        left = int(_clamp(center - min_box_w // 2, 0, width - min_box_w))
        right = min(width, left + min_box_w)
    if (bottom - top) < min_box_h:
        center = (top + bottom) // 2
        top = int(_clamp(center - min_box_h // 2, 0, height - min_box_h))
        bottom = min(height, top + min_box_h)

    return left, top, right, bottom


def _mesh_transform(image, *, quad: tuple[float, float, float, float, float, float, float, float], resample, fillcolor):
    from PIL import Image

    mesh_mode = Image.Transform.MESH if hasattr(Image, "Transform") else Image.MESH
    mesh = [((0, 0, image.width, image.height), quad)]
    try:
        return image.transform((image.width, image.height), mesh_mode, mesh, resample=resample, fillcolor=fillcolor)
    except TypeError:
        return image.transform((image.width, image.height), mesh_mode, mesh, resample=resample)


def _deform_subject(image, *, bbox: tuple[int, int, int, int], level: int, resampling):
    from PIL import Image, ImageDraw, ImageFilter

    left, top, right, bottom = bbox
    subject = image.crop((left, top, right, bottom))
    if subject.width < 16 or subject.height < 16:
        return image

    strength = (level - _ZHMYH_MIN_LEVEL) / (_ZHMYH_MAX_LEVEL - _ZHMYH_MIN_LEVEL)
    fillcolor = subject.getpixel((subject.width // 2, subject.height // 2))
    target_w = max(16, int(subject.width * (1.04 + strength * 0.48)))
    target_h = max(16, int(subject.height * (0.98 - strength * 0.24)))
    subject = subject.resize((target_w, target_h), resampling.LANCZOS)
    subject = subject.rotate(
        angle=(level - _ZHMYH_DEFAULT_LEVEL) * 2.2,
        resample=resampling.BICUBIC,
        expand=True,
        fillcolor=fillcolor,
    )

    bend = max(2, int(min(subject.size) * (0.012 + strength * 0.078)))
    subject = _mesh_transform(
        subject,
        quad=(
            float(bend),
            0.0,
            0.0,
            float(subject.height - bend),
            float(subject.width - bend),
            float(subject.height),
            float(subject.width),
            float(bend),
        ),
        resample=resampling.BICUBIC,
        fillcolor=fillcolor,
    )

    mask = Image.new("L", subject.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, subject.width - 1, subject.height - 1), fill=245)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(2, int(min(subject.size) * 0.045))))

    center_x = (left + right) // 2
    center_y = (top + bottom) // 2
    paste_x = center_x - subject.width // 2
    paste_y = center_y - subject.height // 2

    crop_left = max(0, -paste_x)
    crop_top = max(0, -paste_y)
    crop_right = max(0, paste_x + subject.width - image.width)
    crop_bottom = max(0, paste_y + subject.height - image.height)
    if crop_left + crop_right >= subject.width or crop_top + crop_bottom >= subject.height:
        return image

    if crop_left or crop_top or crop_right or crop_bottom:
        subject = subject.crop((crop_left, crop_top, subject.width - crop_right, subject.height - crop_bottom))
        mask = mask.crop((crop_left, crop_top, mask.width - crop_right, mask.height - crop_bottom))
        paste_x += crop_left
        paste_y += crop_top

    image.paste(subject, (paste_x, paste_y), mask)
    return image


def _expand_bbox(
    bbox: tuple[int, int, int, int],
    *,
    width: int,
    height: int,
    pad_x_ratio: float,
    pad_y_ratio: float,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = bbox
    box_w = max(1, right - left)
    box_h = max(1, bottom - top)
    pad_x = int(box_w * pad_x_ratio)
    pad_y = int(box_h * pad_y_ratio)
    out_left = int(_clamp(left - pad_x, 0, width - 2))
    out_top = int(_clamp(top - pad_y, 0, height - 2))
    out_right = int(_clamp(right + pad_x, out_left + 2, width))
    out_bottom = int(_clamp(bottom + pad_y, out_top + 2, height))
    return out_left, out_top, out_right, out_bottom


def _mesh_warp_region(region, *, level: int, resampling, seed: int):
    from PIL import Image

    width, height = region.size
    if width < 20 or height < 20:
        return region

    strength = (level - _ZHMYH_MIN_LEVEL) / (_ZHMYH_MAX_LEVEL - _ZHMYH_MIN_LEVEL)
    grid = 3 + level
    step_x = width / grid
    step_y = height / grid
    rng = random.Random(seed)
    amp_x = width * (0.008 + 0.038 * strength)
    amp_y = height * (0.008 + 0.038 * strength)

    points: list[list[tuple[float, float]]] = []
    for gy in range(grid + 1):
        row: list[tuple[float, float]] = []
        for gx in range(grid + 1):
            x = gx * step_x
            y = gy * step_y
            if gx in {0, grid} or gy in {0, grid}:
                row.append((x, y))
                continue

            nx = abs(gx / grid - 0.5) * 2.0
            ny = abs(gy / grid - 0.5) * 2.0
            center_gain = max(0.15, 1.0 - (nx + ny) * 0.55)
            dx = (rng.random() * 2.0 - 1.0) * amp_x * center_gain
            dy = (rng.random() * 2.0 - 1.0) * amp_y * center_gain
            row.append(
                (
                    _clamp(x + dx, 0.0, float(width - 1)),
                    _clamp(y + dy, 0.0, float(height - 1)),
                )
            )
        points.append(row)

    mesh: list[tuple[tuple[int, int, int, int], tuple[float, float, float, float, float, float, float, float]]] = []
    for gy in range(grid):
        for gx in range(grid):
            x0 = int(round(gx * step_x))
            y0 = int(round(gy * step_y))
            x1 = int(round((gx + 1) * step_x))
            y1 = int(round((gy + 1) * step_y))
            if x1 <= x0 or y1 <= y0:
                continue

            p00 = points[gy][gx]
            p01 = points[gy + 1][gx]
            p11 = points[gy + 1][gx + 1]
            p10 = points[gy][gx + 1]
            mesh.append(
                (
                    (x0, y0, x1, y1),
                    (
                        p00[0],
                        p00[1],
                        p01[0],
                        p01[1],
                        p11[0],
                        p11[1],
                        p10[0],
                        p10[1],
                    ),
                )
            )

    mesh_mode = Image.Transform.MESH if hasattr(Image, "Transform") else Image.MESH
    fillcolor = region.getpixel((width // 2, height // 2))
    try:
        return region.transform((width, height), mesh_mode, mesh, resample=resampling.BICUBIC, fillcolor=fillcolor)
    except TypeError:
        return region.transform((width, height), mesh_mode, mesh, resample=resampling.BICUBIC)


def _apply_object_internal_distort(image, *, bbox: tuple[int, int, int, int], level: int, resampling):
    from PIL import Image, ImageDraw, ImageFilter

    strength = (level - _ZHMYH_MIN_LEVEL) / (_ZHMYH_MAX_LEVEL - _ZHMYH_MIN_LEVEL)
    width, height = image.size
    region_bbox = _expand_bbox(
        bbox,
        width=width,
        height=height,
        pad_x_ratio=0.35 + 0.30 * strength,
        pad_y_ratio=0.28 + 0.24 * strength,
    )
    left, top, right, bottom = region_bbox

    edge_guard = max(6, int(min(width, height) * 0.03))
    if width > edge_guard * 2 + 6 and height > edge_guard * 2 + 6:
        left = max(edge_guard, left)
        top = max(edge_guard, top)
        right = min(width - edge_guard, right)
        bottom = min(height - edge_guard, bottom)
        if right - left < 20 or bottom - top < 20:
            return image

    region_bbox = (left, top, right, bottom)
    region = image.crop(region_bbox)
    warped = _mesh_warp_region(
        region,
        level=level,
        resampling=resampling,
        seed=(width * 92821) ^ (height * 68917) ^ (level * 31337),
    )
    if level >= 4:
        warped = _mesh_warp_region(
            warped,
            level=max(_ZHMYH_MIN_LEVEL, level - 1),
            resampling=resampling,
            seed=(width * 1699) ^ (height * 1297) ^ (level * 911),
        )

    region_w, region_h = region.size
    feather = max(6, int(min(region_w, region_h) * (0.06 + 0.05 * strength)))
    radius = max(8, int(min(region_w, region_h) * (0.14 + 0.08 * strength)))
    mask = Image.new("L", (region_w, region_h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((1, 1, region_w - 2, region_h - 2), radius=radius, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=feather))

    result = image.copy()
    result.paste(warped, (left, top), mask)
    return result


def _chroma_smear(image, *, level: int, resampling):
    from PIL import Image

    width, height = image.size
    if width < 20 or height < 20:
        return image

    y, cb, cr = image.convert("YCbCr").split()
    div = 2 + level
    small_w = max(8, width // div)
    small_h = max(8, height // div)
    cb = cb.resize((small_w, small_h), resampling.BILINEAR).resize((width, height), resampling.NEAREST)
    cr = cr.resize((max(8, small_w - level), max(8, small_h - level)), resampling.BILINEAR).resize(
        (width, height),
        resampling.NEAREST,
    )
    return Image.merge("YCbCr", (y, cb, cr)).convert("RGB")


def _jpeg_recompress(image, *, quality: int):
    from PIL import Image

    buffer = BytesIO()
    try:
        image.save(buffer, format="JPEG", quality=quality, subsampling=2)
    except Exception:
        return image
    buffer.seek(0)
    with Image.open(buffer) as reopened:
        return reopened.convert("RGB")


def _save_zhmyh_image_bytes(image, *, level: int) -> bytes:
    output = BytesIO()
    try:
        image.save(output, format="JPEG", quality=max(6, 22 - level * 2), subsampling=2)
        return output.getvalue()
    except Exception:
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()


def _make_zhmyh_image(source: bytes, *, level: int) -> bytes:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    level = int(_clamp(level, _ZHMYH_MIN_LEVEL, _ZHMYH_MAX_LEVEL))
    strength = (level - _ZHMYH_MIN_LEVEL) / (_ZHMYH_MAX_LEVEL - _ZHMYH_MIN_LEVEL)
    resampling = Image.Resampling if hasattr(Image, "Resampling") else Image

    with Image.open(BytesIO(source)) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGB")

    if max(image.size) > _ZHMYH_MAX_SIDE:
        image.thumbnail((_ZHMYH_MAX_SIDE, _ZHMYH_MAX_SIDE), resampling.LANCZOS)

    try:
        subject_bbox = _estimate_subject_bbox(image, resampling=resampling)
        image = _deform_subject(image, bbox=subject_bbox, level=level, resampling=resampling)
        image = _apply_object_internal_distort(image, bbox=subject_bbox, level=level, resampling=resampling)
    except Exception:
        logger.exception("Zhmyh advanced geometry stage failed")

    crush_divisor = int(round(1.5 + level * 0.85))
    crushed_w = max(24, image.width // crush_divisor)
    crushed_h = max(24, image.height // crush_divisor)
    image = image.resize((crushed_w, crushed_h), resampling.BILINEAR).resize((image.width, image.height), resampling.NEAREST)

    poster_bits = max(3, 7 - (level // 2))
    image = ImageOps.posterize(image, bits=poster_bits)
    image = ImageEnhance.Contrast(image).enhance(1.05 + 0.55 * strength)
    image = ImageEnhance.Color(image).enhance(1.0 + 0.55 * strength)
    image = ImageEnhance.Sharpness(image).enhance(1.1 + 2.0 * strength)
    image = image.filter(ImageFilter.MedianFilter(size=3 if level <= 3 else 5))
    image = _chroma_smear(image, level=level, resampling=resampling)

    try:
        noise = Image.effect_noise(image.size, 8 + level * 3).convert("L")
        noise = ImageOps.autocontrast(noise)
        noise_rgb = Image.merge("RGB", (noise, noise, noise))
        image = Image.blend(image, noise_rgb, alpha=float(_clamp(0.03 + level * 0.02, 0.03, 0.15)))
    except Exception:
        logger.exception("Zhmyh noise stage failed")

    image = _jpeg_recompress(image, quality=max(12, 52 - level * 6))
    image = _jpeg_recompress(image, quality=max(8, 36 - level * 5))

    return _save_zhmyh_image_bytes(image, level=level)


def _extract_announcement_body(text: str) -> tuple[str | None, str | None]:
    match = _ANNOUNCE_PATTERN.match(text)
    if match is None:
        return None, None

    body = (match.group("body") or "").strip()
    if not body:
        return None, "Формат: <code>объява \"текст объявления\"</code>"

    if body[0] in {'"', "«"}:
        closing = '"' if body[0] == '"' else "»"
        if len(body) < 2 or body[-1] != closing:
            return None, "Закройте кавычки в объявлении."
        body = body[1:-1]

    body = body.strip()
    if not body:
        return None, "Текст объявления пустой."
    return body, None


def _extract_naming_value(text: str) -> tuple[bool, str | None, str | None]:
    match = _NAMING_PATTERN.match(text)
    if match is None:
        return False, None, None

    body = (match.group("body") or "").strip()
    if not body:
        return True, None, None

    if body[0] in {'"', "«"}:
        closing = '"' if body[0] == '"' else "»"
        if len(body) < 2 or body[-1] != closing:
            return True, None, "Закройте кавычки в нейминге."
        body = body[1:-1]

    normalized = " ".join(body.split()).strip()
    if not normalized:
        return True, None, "Имя для нейминга пустое."
    if len(normalized) < 2:
        return True, None, "Имя должно быть не короче 2 символов."
    if len(normalized) > 32:
        return True, None, "Имя должно быть не длиннее 32 символов."

    return True, normalized, None


def _extract_optional_quoted_text(
    raw_body: str,
    *,
    empty_error: str,
    unclosed_error: str,
) -> tuple[str | None, str | None]:
    body = (raw_body or "").strip()
    if not body:
        return None, empty_error

    if body[0] in {'"', "«"}:
        closing = '"' if body[0] == '"' else "»"
        if len(body) < 2 or body[-1] != closing:
            return None, unclosed_error
        body = body[1:-1]

    normalized = " ".join(body.split()).strip()
    if not normalized:
        return None, empty_error
    return normalized, None


def _extract_profile_about_text(text: str) -> tuple[bool, str | None, str | None]:
    match = _PROFILE_ABOUT_PATTERN.match(text)
    if match is None:
        return False, None, None

    value, error = _extract_optional_quoted_text(
        (match.group("body") or "").strip(),
        empty_error='Формат: <code>добавить о себе "текст"</code>',
        unclosed_error='Закройте кавычки в команде <code>добавить о себе</code>.',
    )
    return True, value, error


def _extract_award_request(text: str) -> tuple[bool, str | None, str | None, str | None]:
    match = _PROFILE_AWARD_PATTERN.match(text)
    if match is None:
        return False, None, None, None

    body = (match.group("body") or "").strip()
    target_token = None
    title_body = body
    if body:
        parts = body.split(maxsplit=1)
        first_token = parts[0]
        if first_token.startswith("@") or first_token.lstrip("-").isdigit():
            target_token = first_token
            title_body = parts[1] if len(parts) > 1 else ""

    value, error = _extract_optional_quoted_text(
        title_body.strip(),
        empty_error=(
            'Формат: reply на сообщение или <code>наградить @username текст награды</code>'
        ),
        unclosed_error='Закройте кавычки в команде <code>наградить</code>.',
    )
    return True, target_token, value, error


def _extract_reply_award_title(text: str) -> tuple[bool, str | None, str | None]:
    matched, _target_token, value, error = _extract_award_request(text)
    return matched, value, error


def _extract_award_remove_index(text: str) -> tuple[bool, int | None, str | None]:
    match = _PROFILE_AWARD_REMOVE_PATTERN.match(text)
    if match is None:
        return False, None, None

    body = " ".join(((match.group("body") or "").split())).strip()
    if not body or not body.isdigit():
        return True, None, 'Формат: reply на сообщение бота и <code>снять награду 2</code>.'

    award_index = int(body)
    if award_index <= 0:
        return True, None, "Номер награды должен быть положительным."
    return True, award_index, None


def _is_reply_profile_lookup(message: Message, text: str) -> bool:
    normalized = normalize_text_command(text)
    if normalized != "кто ты":
        return False
    if message.reply_to_message is None or message.reply_to_message.from_user is None:
        return False
    return not bool(message.reply_to_message.from_user.is_bot)


def _is_naming_reset(value: str) -> bool:
    return value.lower() in {"сброс", "сбросить", "очистить", "удалить", "reset", "off", "none", "выкл"}


def _build_announcement_messages(*, body: str, mentions: list[str]) -> list[str]:
    if not mentions:
        return []

    messages: list[str] = []
    current_mentions: list[str] = []
    for mention in mentions:
        candidate_mentions = current_mentions + [mention]
        candidate = f"{body}\n\n{' '.join(candidate_mentions)}"
        if current_mentions and (
            len(candidate_mentions) > _ANNOUNCE_MENTION_CHUNK_SIZE or len(candidate) > _MAX_MESSAGE_LEN_SAFE
        ):
            messages.append(f"{body}\n\n{' '.join(current_mentions)}")
            current_mentions = [mention]
            continue
        current_mentions = candidate_mentions

    if current_mentions:
        messages.append(f"{body}\n\n{' '.join(current_mentions)}")
    return messages


async def _announcement_human_name(message: Message, bot: Bot, user: UserSnapshot) -> str:
    label = preferred_mention_label_from_parts(
        user_id=user.telegram_user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        chat_display_name=user.chat_display_name,
    )
    if label != f"user:{user.telegram_user_id}":
        return label

    try:
        member = await bot.get_chat_member(message.chat.id, user.telegram_user_id)
    except Exception:
        member = None

    if member is not None and member.user is not None:
        refreshed = preferred_mention_label_from_parts(
            user_id=member.user.id,
            username=member.user.username,
            first_name=member.user.first_name,
            last_name=member.user.last_name,
        )
        if refreshed != f"user:{user.telegram_user_id}":
            return refreshed

    return f"Участник {str(user.telegram_user_id)[-4:]}"


def _shipper_base_name(user: UserSnapshot) -> str:
    return display_name_from_parts(
        user_id=user.telegram_user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        chat_display_name=user.chat_display_name,
    )


async def _shipper_human_name(message: Message, bot: Bot, user: UserSnapshot) -> str:
    label = _shipper_base_name(user)
    if label != f"user:{user.telegram_user_id}":
        return label

    try:
        member = await bot.get_chat_member(message.chat.id, user.telegram_user_id)
    except Exception:
        member = None

    if member is not None and member.user is not None:
        refreshed = display_name_from_parts(
            user_id=member.user.id,
            username=member.user.username,
            first_name=member.user.first_name,
            last_name=member.user.last_name,
        )
        if refreshed != f"user:{user.telegram_user_id}":
            return refreshed

    return f"Участник {str(user.telegram_user_id)[-4:]}"


def _shipper_member_mention(*, user_id: int, label: str) -> str:
    return f'<a href="tg://user?id={user_id}">{escape(label)}</a>'


def _social_action_display_name(user: UserSnapshot) -> str:
    return display_name_from_parts(
        user_id=user.telegram_user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        chat_display_name=user.chat_display_name,
    )


def _social_action_mention(user: UserSnapshot) -> str:
    return f'<a href="tg://user?id={user.telegram_user_id}">{escape(_social_action_display_name(user))}</a>'


async def _social_action_user_snapshot(message: Message, activity_repo, *, user) -> UserSnapshot:
    try:
        chat_display_name = await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=user.id)
    except Exception:
        chat_display_name = None
    return UserSnapshot(
        telegram_user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        is_bot=bool(user.is_bot),
        chat_display_name=chat_display_name,
    )


async def _send_social_action(message: Message, activity_repo, chat_settings: ChatSettings, *, action_key: str) -> None:
    canonical = _SOCIAL_ACTION_CANONICAL.get(action_key, "действие")
    _, mass_target, replica = _extract_social_action_target_request(message.text or message.caption or "")
    if message.chat.type not in {"group", "supergroup"}:
        await _answer_quiet(message, "Эта команда работает только в группе.")
        return

    if message.from_user is None:
        return

    if action_key in _SOCIAL_ACTION_18_PLUS and not chat_settings.actions_18_enabled:
        await _answer_quiet(
            message,
            "18+ действия отключены в этом чате. Включить: <code>/setcfg actions_18_enabled true</code>.",
            parse_mode="HTML",
        )
        return

    actor = await _social_action_user_snapshot(message, activity_repo, user=message.from_user)
    actor_mention = _social_action_mention(actor)
    template = random.choice(_SOCIAL_ACTION_TEMPLATES[action_key])

    if mass_target:
        try:
            recipients = await activity_repo.get_announcement_recipients(chat_id=message.chat.id)
        except Exception:
            recipients = []

        target_mentions = [
            _social_action_mention(target)
            for target in recipients
            if not target.is_bot and target.telegram_user_id != message.from_user.id
        ]
        if not target_mentions:
            await _answer_quiet(message, "Пока некого обрабатывать командой для всех.")
            return

        for response_text in _build_social_action_mass_messages(
            template=template,
            actor_mention=actor_mention,
            target_mentions=target_mentions,
            replica=replica,
        ):
            await _answer_quiet(
                message,
                response_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        return

    if message.reply_to_message is None or message.reply_to_message.from_user is None:
        await _answer_quiet(
            message,
            f"Сделайте reply на сообщение участника и напишите <code>{canonical}</code>.",
            parse_mode="HTML",
        )
        return

    target_user = message.reply_to_message.from_user
    if target_user.id == message.from_user.id:
        await _answer_quiet(message, "Нужно ответить на сообщение другого участника.")
        return
    if bool(target_user.is_bot):
        await _answer_quiet(message, "Нужно выбрать живого участника, а не бота.")
        return

    target = await _social_action_user_snapshot(message, activity_repo, user=target_user)
    response_text = template.format(actor=actor_mention, target=_social_action_mention(target))
    if replica:
        response_text = f"{response_text}\n{_build_social_action_replica_line(replica)}"
    await _answer_quiet(
        message,
        response_text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def _build_shipper_text(message: Message, bot: Bot, first: UserSnapshot, second: UserSnapshot) -> str:
    first_name = await _shipper_human_name(message, bot, first)
    second_name = await _shipper_human_name(message, bot, second)
    first_label = _shipper_member_mention(user_id=first.telegram_user_id, label=first_name)
    second_label = _shipper_member_mention(user_id=second.telegram_user_id, label=second_name)
    template = random.choice(_SHIPPER_TEMPLATES)
    return template.format(
        first=first_label,
        second=second_label,
    )


async def _send_random_shipper(message: Message, activity_repo, bot: Bot) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await _answer_quiet(message, "Шипперим работает только в группе.")
        return

    recipients = await activity_repo.get_announcement_recipients(chat_id=message.chat.id)
    pool = [user for user in recipients if not user.is_bot]
    if len(pool) < 2:
        await _answer_quiet(message, "Нужно минимум 2 активных участника в чате для шипперинга.")
        return

    if len(pool) > 100:
        pool = pool[:100]

    first, second = random.sample(pool, k=2)
    await _answer_quiet(
        message,
        await _build_shipper_text(message, bot, first, second),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def _send_today_randomizer(message: Message, activity_repo, bot: Bot, *, predicate: str) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await _answer_quiet(message, "Эта фраза работает только в группе.")
        return

    try:
        recipients = await activity_repo.get_announcement_recipients(chat_id=message.chat.id)
    except Exception:
        recipients = []

    pool = [user for user in recipients if not user.is_bot]
    if message.from_user is not None and not bool(message.from_user.is_bot):
        if all(user.telegram_user_id != message.from_user.id for user in pool):
            pool.append(await _social_action_user_snapshot(message, activity_repo, user=message.from_user))

    if not pool:
        await _answer_quiet(message, "Пока некого выбирать. Напишите после того, как в чате появятся участники.")
        return

    chosen = random.choice(pool[:100])
    chosen_name = await _shipper_human_name(message, bot, chosen)
    chosen_mention = _shipper_member_mention(user_id=chosen.telegram_user_id, label=chosen_name)
    template = random.choice(_TODAY_RANDOMIZER_TEMPLATES)
    await _answer_quiet(
        message,
        template.format(target=chosen_mention, predicate=escape(predicate)),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def _send_daily_article(message: Message, activity_repo) -> None:
    if message.from_user is None:
        return
    if not DAILY_ARTICLES:
        await _answer_quiet(message, "Каталог статей дня пока пуст. Добавьте записи в daily_articles.json.")
        return

    actor = await _social_action_user_snapshot(message, activity_repo, user=message.from_user)
    actor_mention = _social_action_mention(actor)
    today_key = datetime.now().astimezone().date().isoformat()
    rng = _stable_rng("daily_article", message.from_user.id, today_key)
    code, title = rng.choice(DAILY_ARTICLES)
    template = rng.choice(_DAILY_ARTICLE_TEMPLATES)
    outro = rng.choice(_DAILY_ARTICLE_OUTROS)
    await _answer_quiet(
        message,
        f"{template.format(user=actor_mention, code=escape(code), title=escape(title))}\n<i>{escape(outro)}</i>",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def _enforce_command_access(message: Message, activity_repo, *, command_key: str) -> bool:
    if message.chat.type not in {"group", "supergroup"}:
        return True
    if message.from_user is None:
        return False

    allowed, actor_role_code, required_role_code, _ = await has_command_access(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        command_key=command_key,
        bootstrap_if_missing_owner=False,
    )
    if allowed:
        return True

    actor_label = await get_role_label_ru(activity_repo, chat_id=message.chat.id, role_code=actor_role_code)
    required_label = await get_role_label_ru(activity_repo, chat_id=message.chat.id, role_code=required_role_code)
    await message.answer(
        (
            f"Недостаточно прав для команды <code>{escape(command_key)}</code>.\n"
            f"Ваш ранг: <code>{escape(actor_label)}</code>\n"
            f"Нужный ранг: <code>{escape(required_label)}</code>"
        ),
        parse_mode="HTML",
    )
    return False


async def _handle_command_rank_phrase(message: Message, activity_repo, text: str) -> bool:
    phrase = parse_command_rank_phrase(text)
    if phrase is None:
        return False

    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Эта настройка доступна только в группе.")
        return True
    if message.from_user is None:
        return True

    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="manage_command_access",
        bootstrap_if_missing_owner=False,
    )
    if not allowed:
        await message.answer("Недостаточно прав. Нужен доступ к настройке рангов команд.")
        return True

    command_key = resolve_command_key_input(phrase.command_input)
    if command_key is None:
        normalized_input = normalize_text_command(phrase.command_input)
        if normalized_input:
            aliases = await activity_repo.list_chat_aliases(chat_id=message.chat.id)
            for alias in aliases:
                if alias.alias_text_norm == normalized_input or alias.source_trigger_norm == normalized_input:
                    command_key = alias.command_key
                    break
    if command_key is None:
        await message.answer("Не удалось распознать команду. Укажите /команду или стандартный алиас.")
        return True

    if phrase.reset:
        removed = await activity_repo.remove_command_access_rule(chat_id=message.chat.id, command_key=command_key)
        if removed:
            await message.answer(f'Ограничение ранга снято для команды <code>{escape(command_key)}</code>.', parse_mode="HTML")
        else:
            await message.answer(
                f'Для команды <code>{escape(command_key)}</code> отдельный ранг не был установлен.',
                parse_mode="HTML",
            )
        return True

    role_input = phrase.role_input or ""
    role = await activity_repo.resolve_chat_role_definition(chat_id=message.chat.id, token=role_input)
    if role is None:
        await message.answer("Неизвестный ранг/роль.")
        return True

    chat = ChatSnapshot(
        telegram_chat_id=message.chat.id,
        chat_type=message.chat.type,
        title=message.chat.title,
    )
    try:
        await activity_repo.upsert_command_access_rule(
            chat=chat,
            command_key=command_key,
            min_role_token=role.role_code,
            updated_by_user_id=message.from_user.id,
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return True

    await message.answer(
        (
            f'Для команды <code>{escape(command_key)}</code> установлен минимальный ранг: '
            f'<code>{escape(role.title_ru)}</code>.'
        ),
        parse_mode="HTML",
    )
    return True


async def _send_announcement(message: Message, activity_repo, body: str, bot: Bot) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Объявления доступны только в группе.")
        return

    if message.from_user is None:
        return

    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="announce",
        bootstrap_if_missing_owner=False,
    )
    if not allowed:
        await message.answer("Недостаточно прав для отправки объявления.")
        return

    recipients = await activity_repo.get_announcement_recipients(chat_id=message.chat.id)
    mentions: list[str] = []
    for user in recipients:
        label = await _announcement_human_name(message, bot, user)
        mentions.append(format_user_link(user_id=user.telegram_user_id, label=label))

    announcement_messages = _build_announcement_messages(body=body, mentions=mentions)
    if not announcement_messages:
        await message.answer("Пока нет участников для тега. Нужна активность пользователей в чате.")
        return

    escaped_messages = _build_announcement_messages(body=escape(body), mentions=mentions)
    for raw_text, escaped_text in zip(announcement_messages, escaped_messages):
        try:
            await message.answer(raw_text, parse_mode="HTML", disable_web_page_preview=True)
        except TelegramBadRequest as exc:
            if "can't parse entities" not in str(exc).lower():
                raise
            await message.answer(escaped_text, parse_mode="HTML", disable_web_page_preview=True)


async def _set_announcement_subscription(message: Message, activity_repo, *, enabled: bool) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команды рег/анрег работают только в группе.")
        return

    if message.from_user is None:
        return

    chat = ChatSnapshot(
        telegram_chat_id=message.chat.id,
        chat_type=message.chat.type,
        title=message.chat.title,
    )
    user = UserSnapshot(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
    )
    await activity_repo.set_announcement_subscription(chat=chat, user=user, enabled=enabled)
    if enabled:
        await message.answer("Вы снова будете попадать в теги объявлений.")
    else:
        await message.answer("Ок, больше не тегаю вас в объявлениях этого чата.")


async def _handle_naming_command(message: Message, activity_repo, *, value: str | None) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await _answer_quiet(message, "Команда нейминга работает только в группе.")
        return
    if message.from_user is None:
        return

    current = await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=message.from_user.id)
    if value is None:
        current_value = escape(current) if current else "не задан"
        await _answer_quiet(
            message,
            (
                "<b>Нейминг в этом чате</b>\n"
                f"<b>Текущий:</b> {current_value}\n"
                "Установить: <code>нейминг ВашеИмя</code> или <code>/naming ВашеИмя</code>\n"
                "Сбросить: <code>нейминг сброс</code>"
            ),
            parse_mode="HTML",
        )
        return

    new_value: str | None = value
    if _is_naming_reset(value):
        new_value = None

    chat = ChatSnapshot(
        telegram_chat_id=message.chat.id,
        chat_type=message.chat.type,
        title=message.chat.title,
    )
    user = UserSnapshot(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
    )
    await activity_repo.set_chat_display_name(chat=chat, user=user, display_name=new_value)

    default_label = display_name_from_parts(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )
    await GAME_STORE.set_player_label(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        user_label=new_value or default_label,
    )

    if new_value is None:
        await _answer_quiet(message, "Нейминг сброшен. Теперь используется обычное имя/username.")
        return

    mention = f'<a href="tg://user?id={message.from_user.id}">{escape(new_value)}</a>'
    await _answer_quiet(message, f"Нейминг обновлён: {mention}", parse_mode="HTML")


async def _answer_inline_private_empty(inline_query: InlineQuery) -> None:
    await _safe_inline_query_answer(inline_query, _INLINE_PM_EMPTY_RESULT, cache_time=0, is_personal=True)


@router.inline_query()
async def inline_private_query_handler(inline_query: InlineQuery, activity_repo, bot: Bot) -> None:
    if inline_query.from_user is None:
        await _answer_inline_private_empty(inline_query)
        return

    bot_username = await _get_bot_username(bot)
    raw_query = (inline_query.query or "").strip()
    raw_tokens = [token for token in raw_query.split() if token]

    if raw_tokens and raw_tokens[0].lower() != "pm":
        rp_payload = _parse_inline_rp_payload(raw_query)
        if rp_payload is not None:
            now = _now_utc()
            _cleanup_inline_private_pending(now=now)
            actor = UserSnapshot(
                telegram_user_id=int(inline_query.from_user.id),
                username=inline_query.from_user.username,
                first_name=inline_query.from_user.first_name,
                last_name=inline_query.from_user.last_name,
                is_bot=bool(inline_query.from_user.is_bot),
            )
            rp_targets = await _resolve_inline_rp_targets(
                activity_repo,
                sender_user_id=actor.telegram_user_id,
                search_text=rp_payload.search_text,
            )
            if not rp_targets:
                await _answer_inline_private_empty(inline_query)
                return

            results: list[InlineQueryResultArticle] = []
            for target in rp_targets:
                result_id = f"rp:{rp_payload.action_key}:{target.telegram_user_id}"
                _INLINE_RP_PENDING[result_id] = (actor.telegram_user_id, target, now)
                results.append(
                    InlineQueryResultArticle(
                        id=result_id,
                        title=_inline_rp_result_title(action_key=rp_payload.action_key, target=target),
                        description=_inline_rp_result_description(action_key=rp_payload.action_key, target=target),
                        input_message_content=InputTextMessageContent(
                            message_text=_inline_rp_render_message(
                                action_key=rp_payload.action_key,
                                actor=actor,
                                target=target,
                            ),
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                        ),
                    )
                )
            await _safe_inline_query_answer(inline_query, results, cache_time=0, is_personal=True)
            return

    pm_query = " ".join(raw_tokens[1:]) if raw_tokens and raw_tokens[0].lower() == "pm" else raw_query
    payload = _parse_inline_private_payload(pm_query, bot_username=bot_username)
    if payload is None:
        await _answer_inline_private_empty(inline_query)
        return

    now = _now_utc()
    _cleanup_inline_private_pending(now=now)
    if _inline_private_cooldown_left_seconds(sender_user_id=inline_query.from_user.id, now=now) > 0:
        await _answer_inline_private_empty(inline_query)
        return

    resolved_receivers = await _resolve_inline_private_receivers(
        activity_repo,
        sender_user_id=inline_query.from_user.id,
        receiver_usernames=payload.receiver_usernames,
    )
    resolved_by_username = {
        (item.username or "").strip().lower(): item
        for item in resolved_receivers
        if (item.username or "").strip()
    }

    results: list[InlineQueryResultArticle] = []

    for target_usernames in _build_inline_private_target_usernames(payload.receiver_usernames):
        mentions = [_inline_private_username_mention(username) for username in target_usernames]
        preview = ", ".join(
            _inline_private_preview_label(
                username=username,
                resolved_user=resolved_by_username.get(username.lstrip("@").strip().lower()),
            )
            for username in target_usernames
        )
        if len(preview) > 120:
            preview = f"{preview[:117]}..."

        receiver_ids: list[int] = []
        seen_receiver_ids: set[int] = set()
        for username in target_usernames:
            resolved = resolved_by_username.get(username.lstrip("@").strip().lower())
            if resolved is None:
                continue
            if resolved.telegram_user_id in seen_receiver_ids:
                continue
            seen_receiver_ids.add(resolved.telegram_user_id)
            receiver_ids.append(resolved.telegram_user_id)

        message_id = str(uuid4())
        _INLINE_PM_PENDING[message_id] = _InlinePrivatePendingMessage(
            sender_id=int(inline_query.from_user.id),
            receiver_ids=tuple(receiver_ids),
            receiver_usernames=tuple(username.lstrip("@").strip().lower() for username in target_usernames),
            text=payload.text,
            created_at=now,
        )

        results.append(
            InlineQueryResultArticle(
                id=message_id,
                title=_inline_private_result_title(target_usernames),
                description=f"Получатели: {preview}",
                input_message_content=InputTextMessageContent(
                    message_text=_inline_private_group_message(mentions=mentions),
                    parse_mode="HTML",
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=_INLINE_PM_BUTTON_TEXT,
                                callback_data=_inline_private_callback_data(message_id),
                            )
                        ]
                    ]
                ),
            )
        )
        if len(results) >= _INLINE_PM_RESULTS_LIMIT:
            break

    explicit_set = {name.lower() for name in payload.receiver_usernames}
    recent_usernames = await activity_repo.list_recent_inline_private_receiver_usernames(
        sender_user_id=inline_query.from_user.id,
        limit=_INLINE_PM_HISTORY_LIMIT,
    )
    for username in recent_usernames:
        normalized_username = username.lstrip("@").strip().lower()
        if not normalized_username or normalized_username in explicit_set:
            continue

        message_id = str(uuid4())
        _INLINE_PM_PENDING[message_id] = _InlinePrivatePendingMessage(
            sender_id=int(inline_query.from_user.id),
            receiver_ids=tuple(),
            receiver_usernames=(normalized_username,),
            text=payload.text,
            created_at=now,
        )
        mention = _inline_private_username_mention(normalized_username)
        results.append(
            InlineQueryResultArticle(
                id=message_id,
                title=_inline_private_result_title((normalized_username,)),
                description=f"История: {mention}",
                input_message_content=InputTextMessageContent(
                    message_text=_inline_private_group_message(mentions=[mention]),
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=_INLINE_PM_BUTTON_TEXT,
                                callback_data=_inline_private_callback_data(message_id),
                            )
                        ]
                    ]
                ),
            )
        )
        if len(results) >= _INLINE_PM_RESULTS_LIMIT:
            break

    recent_receivers = await activity_repo.list_recent_inline_private_receivers(
        sender_user_id=inline_query.from_user.id,
        limit=_INLINE_PM_HISTORY_LIMIT,
    )
    history_username_set = {name.lower() for name in recent_usernames}
    for user in recent_receivers:
        username = (user.username or "").strip().lower()
        if username and (username in explicit_set or username in history_username_set):
            continue

        mention = _inline_private_history_mention(user)
        preview = _inline_private_history_label(user)
        receiver_ids = (int(user.telegram_user_id),)
        receiver_usernames = (username,) if username else ()

        message_id = str(uuid4())
        _INLINE_PM_PENDING[message_id] = _InlinePrivatePendingMessage(
            sender_id=int(inline_query.from_user.id),
            receiver_ids=receiver_ids,
            receiver_usernames=receiver_usernames,
            text=payload.text,
            created_at=now,
        )

        results.append(
            InlineQueryResultArticle(
                id=message_id,
                title=_inline_private_history_result_title(user),
                description=f"История: {preview}",
                input_message_content=InputTextMessageContent(
                    message_text=_inline_private_group_message(mentions=[mention]),
                    parse_mode="HTML",
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=_INLINE_PM_BUTTON_TEXT,
                                callback_data=_inline_private_callback_data(message_id),
                            )
                        ]
                    ]
                ),
            )
        )
        if len(results) >= _INLINE_PM_RESULTS_LIMIT:
            break

    if not results:
        await _answer_inline_private_empty(inline_query)
        return

    await _safe_inline_query_answer(inline_query, results, cache_time=0, is_personal=True)


@router.chosen_inline_result()
async def inline_private_chosen_handler(chosen_result: ChosenInlineResult, activity_repo, bot: Bot) -> None:
    if chosen_result.from_user is None:
        return

    message_id = (chosen_result.result_id or "").strip()
    if message_id.startswith("rp:"):
        now = _now_utc()
        _cleanup_inline_private_pending(now=now)
        pending_rp = _INLINE_RP_PENDING.pop(message_id, None)
        if pending_rp is None:
            return
        sender_id, target, _ = pending_rp
        if sender_id != int(chosen_result.from_user.id):
            return
        recent_targets = [
            user
            for user in _INLINE_RP_RECENT_TARGETS.get(sender_id, [])
            if user.telegram_user_id != target.telegram_user_id
        ]
        _INLINE_RP_RECENT_TARGETS[sender_id] = [target, *recent_targets][: _INLINE_RP_HISTORY_LIMIT]
        return

    if not _is_uuid_string(message_id):
        return

    now = _now_utc()
    _cleanup_inline_private_pending(now=now)
    if _inline_private_cooldown_left_seconds(sender_user_id=chosen_result.from_user.id, now=now) > 0:
        return

    pending = _INLINE_PM_PENDING.get(message_id)
    if pending is not None:
        if pending.sender_id != int(chosen_result.from_user.id):
            return
        if (now - pending.created_at).total_seconds() > _INLINE_PM_PENDING_TTL_SECONDS:
            _INLINE_PM_PENDING.pop(message_id, None)
            return
        text = pending.text
        receiver_ids = list(pending.receiver_ids)
        receiver_usernames = list(pending.receiver_usernames)
    else:
        bot_username = await _get_bot_username(bot)
        payload = _parse_inline_private_payload(chosen_result.query or "", bot_username=bot_username)
        if payload is None:
            return

        resolved_receivers = await _resolve_inline_private_receivers(
            activity_repo,
            sender_user_id=chosen_result.from_user.id,
            receiver_usernames=payload.receiver_usernames,
        )
        text = payload.text
        receiver_ids = [item.telegram_user_id for item in resolved_receivers]
        receiver_usernames = [username.lstrip("@").strip().lower() for username in payload.receiver_usernames]

    try:
        await activity_repo.create_inline_private_message(
            id=message_id,
            chat_id=None,
            chat_instance=None,
            sender_id=chosen_result.from_user.id,
            receiver_ids=receiver_ids,
            receiver_usernames=receiver_usernames,
            text=text,
            created_at=now,
        )
    except SQLAlchemyError:
        await safe_rollback(activity_repo)
        existing = await activity_repo.get_inline_private_message(id=message_id)
        if existing is None:
            return

    _INLINE_PM_PENDING.pop(message_id, None)
    _mark_inline_private_sent(sender_user_id=chosen_result.from_user.id, now=now)


@router.callback_query(F.data.startswith(_INLINE_PM_CALLBACK_PREFIX))
async def inline_private_read_callback(query: CallbackQuery, activity_repo) -> None:
    if query.from_user is None:
        await _safe_callback_answer(query)
        return

    now = _now_utc()
    _cleanup_inline_private_pending(now=now)

    message_id = _parse_inline_private_callback_data(query.data)
    if message_id is None:
        await _safe_callback_answer(query, "Сообщение недоступно", show_alert=True)
        return

    inline_message = await activity_repo.get_inline_private_message(id=message_id)
    if inline_message is None:
        pending = _INLINE_PM_PENDING.get(message_id)
        if pending is not None and (now - pending.created_at).total_seconds() <= _INLINE_PM_PENDING_TTL_SECONDS:
            try:
                await activity_repo.create_inline_private_message(
                    id=message_id,
                    chat_id=None,
                    chat_instance=None,
                    sender_id=pending.sender_id,
                    receiver_ids=list(pending.receiver_ids),
                    receiver_usernames=list(pending.receiver_usernames),
                    text=pending.text,
                    created_at=pending.created_at,
                )
                inline_message = await activity_repo.get_inline_private_message(id=message_id)
                _mark_inline_private_sent(sender_user_id=pending.sender_id, now=now)
            except SQLAlchemyError:
                await safe_rollback(activity_repo)
                inline_message = await activity_repo.get_inline_private_message(id=message_id)
        else:
            _INLINE_PM_PENDING.pop(message_id, None)

    if inline_message is None:
        await _safe_callback_answer(
            query,
            "Сообщение не найдено. Включите inline feedback в BotFather: /setinlinefeedback -> 100.",
            show_alert=True,
        )
        return
    _INLINE_PM_PENDING.pop(message_id, None)

    callback_chat_id = None
    if query.message is not None and query.message.chat is not None:
        callback_chat_id = int(query.message.chat.id)
    await activity_repo.set_inline_private_message_context(
        id=message_id,
        chat_id=callback_chat_id,
        chat_instance=query.chat_instance,
    )

    actor_user_id = int(query.from_user.id)
    allowed = actor_user_id == inline_message.sender_id or actor_user_id in inline_message.receiver_ids
    if not allowed:
        actor_username = (query.from_user.username or "").strip().lower()
        allowed = bool(actor_username and actor_username in inline_message.receiver_usernames)
    if not allowed:
        actor_username = (query.from_user.username or "").strip().lower()
        source_text = ""
        if query.message is not None:
            source_text = (getattr(query.message, "text", None) or getattr(query.message, "caption", None) or "").strip()
        mentioned_usernames = _extract_inline_private_usernames(source_text)
        allowed = bool(actor_username and actor_username in mentioned_usernames)

    if not allowed:
        await _safe_callback_answer(query, "Это сообщение не для вас", show_alert=True)
        return

    chunks = _split_inline_private_text(inline_message.text)
    if not chunks:
        await _safe_callback_answer(query, "Сообщение пустое", show_alert=True)
        return

    page_key = (message_id, actor_user_id)
    page_data = _INLINE_PM_ALERT_PAGE.get(page_key)
    page_index = page_data[0] if page_data is not None else 0
    if page_index >= len(chunks):
        page_index = 0

    next_index = page_index + 1 if page_index + 1 < len(chunks) else 0
    _INLINE_PM_ALERT_PAGE[page_key] = (next_index, now)
    await _safe_callback_answer(query, chunks[page_index], show_alert=True)


@router.callback_query(F.data.startswith(_GACHA_CALLBACK_PREFIX))
async def gacha_callback(query: CallbackQuery, bot: Bot, settings: Settings, economy_repo, chat_settings: ChatSettings) -> None:
    action, banner, pull_id, owner_user_id, currency_amount = _parse_gacha_callback_data(query.data)
    if action is None or banner is None or owner_user_id is None:
        await _safe_callback_answer(query)
        return
    if query.from_user is None or query.message is None:
        await _safe_callback_answer(query)
        return
    if int(query.from_user.id) != int(owner_user_id):
        await _safe_callback_answer(query, "Эта кнопка не для вас.", show_alert=True)
        return

    if not chat_settings.gacha_enabled:
        await _safe_callback_answer(query)
        return

    if not await _require_channel_subscription_callback(bot, query, query.from_user.id):
        return

    economy_mode = _gacha_economy_mode(chat_type=query.message.chat.type, chat_settings=chat_settings)
    economy_chat_id = _gacha_economy_chat_id(chat_type=query.message.chat.type, chat_id=query.message.chat.id)

    if action == "buy":
        try:
            response = await purchase_gacha_pull(
                settings,
                user_id=query.from_user.id,
                username=query.from_user.username,
                banner=banner,
            )
        except GachaUseCaseError as exc:
            await _safe_callback_answer(query, exc.message, show_alert=True)
            return

        await _deliver_gacha_pull_response(
            query.message,
            settings,
            banner=banner,
            response=response,
            owner_user_id=query.from_user.id,
        )
        text, reply_markup = await _build_gacha_info_view(
            settings,
            economy_repo,
            user_id=query.from_user.id,
            economy_mode=economy_mode,
            chat_id=economy_chat_id,
        )
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)
        except TelegramBadRequest:
            pass
        await _safe_callback_answer(query)
        return

    if action == "currency":
        if currency_amount is None:
            await _safe_callback_answer(query)
            return
        try:
            result = await buy_gacha_currency_with_coins(
                settings,
                economy_repo,
                economy_mode=economy_mode,
                chat_id=economy_chat_id,
                user_id=query.from_user.id,
                username=query.from_user.username,
                banner=banner,
                currency_amount=currency_amount,
            )
        except GachaUseCaseError as exc:
            await _safe_callback_answer(query, exc.message, show_alert=True)
            return

        text, reply_markup = await _build_gacha_info_view(
            settings,
            economy_repo,
            user_id=query.from_user.id,
            economy_mode=economy_mode,
            chat_id=economy_chat_id,
        )
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)
        except TelegramBadRequest:
            pass
        await _safe_callback_answer(query, result.message)
        return

    if pull_id is None:
        await _safe_callback_answer(query)
        return

    try:
        response = await sell_gacha_pull(
            settings,
            user_id=query.from_user.id,
            pull_id=pull_id,
            banner=banner,
        )
    except GachaUseCaseError as exc:
        await _safe_callback_answer(query, exc.message, show_alert=True)
        return

    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await _safe_callback_answer(query, response.message)


@router.message(Command("menu"))
async def menu_command(message: Message, settings: Settings, chat_settings: ChatSettings) -> None:
    if message.from_user is None:
        return
    mode = chat_settings.economy_mode if message.chat.type in {"group", "supergroup"} else "global"
    web_base_url = settings.resolved_web_base_url if settings.web_enabled else None
    owner_user_id = message.from_user.id if message.chat.type in {"group", "supergroup"} else None
    await message.answer(
        _menu_text(screen="main", mode=mode),
        parse_mode="HTML",
        reply_markup=_menu_keyboard(
            screen="main",
            owner_user_id=owner_user_id,
            mode=mode,
            web_base_url=web_base_url,
        ),
        disable_notification=message.chat.type in {"group", "supergroup"},
    )


@router.callback_query(F.data.startswith("menu:"))
async def menu_callback(query: CallbackQuery, settings: Settings, chat_settings: ChatSettings) -> None:
    screen, owner_user_id = _parse_menu_callback(query.data)
    if screen is None:
        await _safe_callback_answer(query)
        return
    if query.from_user is None or query.message is None:
        await _safe_callback_answer(query)
        return
    if query.message.chat.type in {"group", "supergroup"} and owner_user_id not in {None, query.from_user.id}:
        await _safe_callback_answer(query, "Это меню открыто другим пользователем.", show_alert=True)
        return

    mode = chat_settings.economy_mode if query.message.chat.type in {"group", "supergroup"} else "global"
    web_base_url = settings.resolved_web_base_url if settings.web_enabled else None
    try:
        await query.message.edit_text(
            _menu_text(screen=screen, mode=mode),
            parse_mode="HTML",
            reply_markup=_menu_keyboard(
                screen=screen,
                owner_user_id=owner_user_id,
                mode=mode,
                web_base_url=web_base_url,
            ),
        )
    except TelegramBadRequest:
        pass
    await _safe_callback_answer(query)


@router.message(F.photo)
async def zhmyh_photo_handler(message: Message, bot: Bot, settings: Settings, chat_settings: ChatSettings, activity_repo) -> None:
    caption = message.caption or ""
    if not caption:
        return

    if not chat_settings.text_commands_enabled:
        return

    if chat_settings.text_commands_locale.lower() != "ru":
        return

    if message.chat.type not in settings.supported_chat_types:
        return

    zhmyh_matched, zhmyh_level, zhmyh_error = _extract_zhmyh_level(caption)
    if not zhmyh_matched:
        return
    if not await _enforce_command_access(message, activity_repo, command_key="zhmyh"):
        return
    if zhmyh_error is not None:
        await _answer_quiet(message, zhmyh_error, parse_mode="HTML")
        return

    photo_sizes = message.photo or []
    if not photo_sizes:
        return

    level = int(zhmyh_level or _ZHMYH_DEFAULT_LEVEL)
    source = BytesIO()
    try:
        await bot.download(photo_sizes[-1], destination=source)
        rendered = _make_zhmyh_image(source.getvalue(), level=level)
    except ModuleNotFoundError as exc:
        module_name = getattr(exc, "name", "") or ""
        if module_name.startswith("PIL"):
            await _answer_quiet(
                message,
                (
                    "Жмых недоступен: в окружении не установлен <code>Pillow</code>.\n"
                    "Установи зависимости проекта заново: <code>pip install -e .[dev]</code> "
                    "или минимум <code>pip install Pillow</code>, затем перезапусти бота."
                ),
                parse_mode="HTML",
            )
            return
        await _answer_quiet(
            message,
            "Не смог пережмыхать фото. Пришли картинку как фото с подписью <code>жмых 1..6</code>.",
            parse_mode="HTML",
        )
        return
    except Exception as exc:
        logger.exception(
            "Zhmyh photo handler failed",
            extra={
                "chat_id": getattr(message.chat, "id", None),
                "user_id": getattr(message.from_user, "id", None) if message.from_user else None,
                "level": level,
            },
        )
        details = f"{type(exc).__name__}: {str(exc)}".strip(": ")
        details = escape(details[:220] or "unknown error")
        await _answer_quiet(
            message,
            (
                "Не смог пережмыхать фото. Пришли картинку как фото с подписью <code>жмых 1..6</code>.\n"
                f"<code>{details}</code>"
            ),
            parse_mode="HTML",
        )
        return

    await message.answer_photo(
        photo=BufferedInputFile(rendered, filename=_ZHMYH_FILENAME),
        caption=f"Жмых {level}/6 готов.",
        disable_notification=message.chat.type in {"group", "supergroup"},
    )


@router.message(F.text)
async def text_commands_handler(
    message: Message,
    activity_repo,
    economy_repo,
    bot: Bot,
    settings: Settings,
    chat_settings: ChatSettings,
    session_factory,
    achievement_orchestrator=None,
) -> None:
    text = message.text or ""
    if _is_reply_profile_lookup(message, text):
        if not await _enforce_command_access(message, activity_repo, command_key="me"):
            return
        await send_user_stats(
            message,
            activity_repo,
            bot,
            settings,
            chat_settings,
            user_id=message.reply_to_message.from_user.id,
        )
        return

    if message.chat.type in {"group", "supergroup"} and chat_settings.text_commands_locale.lower() == "ru":
        try:
            alias_mode = await activity_repo.get_chat_alias_mode(chat_id=message.chat.id)
            aliases = await activity_repo.list_chat_aliases(chat_id=message.chat.id)
        except SQLAlchemyError:
            await safe_rollback(activity_repo)
            alias_mode = "both"
            aliases = []

        rewritten = _apply_alias_mode_to_text(text=text, mode=alias_mode, aliases=aliases)
        if rewritten is None:
            return
        text = rewritten

    if _is_reply_profile_lookup(message, text):
        if not await _enforce_command_access(message, activity_repo, command_key="me"):
            return
        await send_user_stats(
            message,
            activity_repo,
            bot,
            settings,
            chat_settings,
            user_id=message.reply_to_message.from_user.id,
        )
        return

    naming_matched, naming_value, naming_error = _extract_naming_value(text)
    if naming_matched:
        if naming_error is not None:
            await _answer_quiet(message, naming_error)
            return
        if not await _enforce_command_access(message, activity_repo, command_key="naming"):
            return
        await _handle_naming_command(message, activity_repo, value=naming_value)
        return

    about_matched, about_text, about_error = _extract_profile_about_text(text)
    if about_matched:
        if about_error is not None:
            await _answer_quiet(message, about_error, parse_mode="HTML")
            return
        await set_about_text_command(message, activity_repo, about_text=about_text or "")
        return

    award_matched, award_target_token, award_title, award_error = _extract_award_request(text)
    if award_matched:
        if award_error is not None:
            await _answer_quiet(message, award_error, parse_mode="HTML")
            return
        await award_text_command(
            message,
            activity_repo,
            bot,
            title=award_title or "",
            target_token=award_target_token,
        )
        return

    award_remove_matched, award_remove_index, award_remove_error = _extract_award_remove_index(text)
    if award_remove_matched:
        if award_remove_error is not None:
            await _answer_quiet(message, award_remove_error, parse_mode="HTML")
            return
        await remove_award_reply_text_command(
            message,
            activity_repo,
            bot,
            award_index=award_remove_index or 0,
            timezone_name=settings.bot_timezone,
        )
        return

    if await _handle_command_rank_phrase(message, activity_repo, text):
        return

    if not chat_settings.text_commands_enabled:
        if message.chat.type in {"group", "supergroup"}:
            if chat_settings.custom_rp_enabled:
                custom_social_action = await match_custom_social_action(activity_repo, chat_id=message.chat.id, text=text)
                if custom_social_action is not None:
                    await send_custom_social_action(message, activity_repo, custom_social_action)
                    return
            if chat_settings.smart_triggers_enabled and not text.strip().startswith("/"):
                trigger = await match_chat_trigger(activity_repo, chat_id=message.chat.id, text=text)
                if trigger is not None:
                    await send_chat_trigger(message, activity_repo, trigger)
        return

    if chat_settings.text_commands_locale.lower() != "ru":
        if message.chat.type in {"group", "supergroup"}:
            if chat_settings.custom_rp_enabled:
                custom_social_action = await match_custom_social_action(activity_repo, chat_id=message.chat.id, text=text)
                if custom_social_action is not None:
                    await send_custom_social_action(message, activity_repo, custom_social_action)
                    return
            if chat_settings.smart_triggers_enabled and not text.strip().startswith("/"):
                trigger = await match_chat_trigger(activity_repo, chat_id=message.chat.id, text=text)
                if trigger is not None:
                    await send_chat_trigger(message, activity_repo, trigger)
        return

    if message.chat.type not in settings.supported_chat_types:
        if message.chat.type in {"group", "supergroup"}:
            if chat_settings.custom_rp_enabled:
                custom_social_action = await match_custom_social_action(activity_repo, chat_id=message.chat.id, text=text)
                if custom_social_action is not None:
                    await send_custom_social_action(message, activity_repo, custom_social_action)
                    return
            if chat_settings.smart_triggers_enabled and not text.strip().startswith("/"):
                trigger = await match_chat_trigger(activity_repo, chat_id=message.chat.id, text=text)
                if trigger is not None:
                    await send_chat_trigger(message, activity_repo, trigger)
        return

    announce_body, announce_error = _extract_announcement_body(text)
    if announce_error is not None:
        await message.answer(announce_error, parse_mode="HTML")
        return
    if announce_body is not None:
        if not await _enforce_command_access(message, activity_repo, command_key="announce"):
            return
        await _send_announcement(message, activity_repo, announce_body, bot)
        return

    smart_trigger_match = _SMART_TRIGGER_LEARN_PATTERN.fullmatch(text)
    if smart_trigger_match is not None:
        await smart_trigger_set_command(
            message,
            command=_command_object_from_args((smart_trigger_match.group("body") or "").strip()),
            activity_repo=activity_repo,
        )
        return

    custom_rp_match = _CUSTOM_RP_ADD_PATTERN.fullmatch(text)
    if custom_rp_match is not None:
        await custom_rp_add_command(
            message,
            command=_command_object_from_args((custom_rp_match.group("body") or "").strip()),
            activity_repo=activity_repo,
        )
        return

    zhmyh_matched, zhmyh_level, zhmyh_error = _extract_zhmyh_level(text)
    if zhmyh_matched:
        if not await _enforce_command_access(message, activity_repo, command_key="zhmyh"):
            return
        if zhmyh_error is not None:
            await _answer_quiet(message, zhmyh_error, parse_mode="HTML")
            return
        level = int(zhmyh_level or _ZHMYH_DEFAULT_LEVEL)
        await _answer_quiet(
            message,
            (
                f"Пришли фото с подписью <code>жмых {level}</code>.\n"
                "Уровень: <code>1</code>.. <code>6</code> (чем выше, тем сильнее жмых)."
            ),
            parse_mode="HTML",
        )
        return

    if chat_settings.custom_rp_enabled and message.chat.type in {"group", "supergroup"}:
        custom_social_action = await match_custom_social_action(activity_repo, chat_id=message.chat.id, text=text)
        if custom_social_action is not None:
            await send_custom_social_action(message, activity_repo, custom_social_action)
            return

    social_action = _extract_social_action(text)
    if social_action is not None:
        if not await _enforce_command_access(message, activity_repo, command_key=f"social_{social_action}"):
            return
        await _send_social_action(message, activity_repo, chat_settings, action_key=social_action)
        return

    today_randomizer_matched, today_predicate, today_randomizer_error = _extract_today_randomizer_predicate(text)
    if today_randomizer_matched:
        if today_randomizer_error is not None:
            await _answer_quiet(message, today_randomizer_error, parse_mode="HTML")
            return
        await _send_today_randomizer(message, activity_repo, bot, predicate=today_predicate or "")
        return

    if _is_daily_article_command(text):
        await _send_daily_article(message, activity_repo)
        return

    try:
        intent = resolve_text_command(
            text,
            top_default=chat_settings.top_limit_default,
            top_max=chat_settings.top_limit_max,
        )
    except TextCommandResolutionError as exc:
        await message.answer(str(exc))
        return

    if intent is None:
        if (
            chat_settings.smart_triggers_enabled
            and message.chat.type in {"group", "supergroup"}
            and not text.strip().startswith("/")
        ):
            trigger = await match_chat_trigger(activity_repo, chat_id=message.chat.id, text=text)
            if trigger is not None:
                await send_chat_trigger(message, activity_repo, trigger)
        return

    if intent.name in {"gacha_on", "gacha_off"}:
        if message.from_user and not await _require_channel_subscription(bot, message, message.from_user.id):
            return
        await _manage_gacha_toggle(
            message,
            activity_repo,
            settings,
            chat_settings,
            command_key=intent.name,
            duration_seconds=intent.args.get("duration_seconds") if intent.args else None,
        )
        return

    if not await _enforce_command_access(message, activity_repo, command_key=intent.name):
        return

    if intent.name == "help":
        await send_help(message, settings)
        return

    if intent.name == "alive":
        await message.answer("<b>Я на связи и работаю.</b>", parse_mode="HTML")
        return

    if intent.name in {"gacha_pull", "gacha_profile", "gacha_info"}:
        chat_settings = await _check_and_maybe_restore_gacha(message, activity_repo, chat_settings)
        if not chat_settings.gacha_enabled:
            return
        if message.from_user and not await _require_channel_subscription(bot, message, message.from_user.id):
            return

    if intent.name == "gacha_pull":
        await _send_gacha_pull(message, settings, banner=str(intent.args.get("banner", "")))
        return

    if intent.name == "gacha_profile":
        await _send_gacha_profile(message, settings, banner=str(intent.args.get("banner", "")))
        return

    if intent.name == "gacha_info":
        await _send_gacha_info(message, settings, economy_repo, chat_settings)
        return

    if intent.name == "gacha_skip":
        if message.from_user and not await _require_channel_subscription(bot, message, message.from_user.id):
            return
        await _send_gacha_skip(
            message,
            activity_repo,
            settings,
            banner=str(intent.args.get("banner", "")),
            target_username=intent.args.get("target_username"),
        )
        return

    if intent.name == "start":
        await game_start_command(message, economy_repo=economy_repo, activity_repo=activity_repo, settings=settings)
        return

    if intent.name == "game":
        await game_slash_command(
            message,
            bot=bot,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            chat_settings=chat_settings,
            activity_repo=activity_repo,
        )
        return

    if intent.name == "zhmyh":
        await _answer_quiet(
            message,
            (
                "Пришли фото с подписью <code>жмых</code> или <code>жмых 1..6</code>.\n"
                "Чем выше уровень, тем сильнее искажения."
            ),
            parse_mode="HTML",
        )
        return

    if intent.name == "shipperim":
        await _send_random_shipper(message, activity_repo, bot)
        return

    if intent.name == "announce_reg":
        await _set_announcement_subscription(message, activity_repo, enabled=True)
        return

    if intent.name == "announce_unreg":
        await _set_announcement_subscription(message, activity_repo, enabled=False)
        return

    if intent.name == "me":
        raw_args = intent.args.get("raw_args")
        if isinstance(raw_args, str) and raw_args.strip():
            target, error = await _resolve_stats_target_user(
                message,
                command=_command_object_from_args(raw_args),
                activity_repo=activity_repo,
            )
            if target is None:
                await _answer_quiet(message, error or "Не удалось определить пользователя.")
                return
            if target.is_bot:
                await _answer_quiet(message, "Профиль бота недоступен.")
                return
            await send_user_stats(
                message,
                activity_repo,
                bot,
                settings,
                chat_settings,
                user_id=target.telegram_user_id,
            )
            return
        await send_me_stats(message, activity_repo, bot, settings, chat_settings)
        return

    if intent.name == "rep":
        await send_rep_stats(message, activity_repo, chat_settings)
        return

    if intent.name == "achievements":
        await achievements_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            activity_repo=activity_repo,
            settings=settings,
            achievement_orchestrator=achievement_orchestrator,
        )
        return

    if intent.name == "active":
        limit = int(intent.args.get("limit", chat_settings.top_limit_default))
        await send_top_stats(message, activity_repo, settings, chat_settings, limit, mode="activity")
        return

    if intent.name == "inactive":
        await send_inactive_members(message, activity_repo)
        return

    if intent.name == "top":
        mode = str(intent.args.get("mode", "activity"))
        if mode not in {"mix", "activity", "karma"}:
            mode = "activity"
        period = str(intent.args.get("period", "all"))
        if period not in {"all", "7d", "hour", "day", "week", "month"}:
            period = "all"
        limit = int(intent.args.get("limit", chat_settings.top_limit_default))
        await send_top_stats(
            message,
            activity_repo,
            settings,
            chat_settings,
            limit,
            mode=mode,  # type: ignore[arg-type]
            period=period,  # type: ignore[arg-type]
            include_chart=True,
            include_keyboard=should_include_hybrid_top_keyboard(
                chat_settings=chat_settings,
                mode=mode,  # type: ignore[arg-type]
                period=period,  # type: ignore[arg-type]
            ),
        )
        return

    if intent.name == "lastseen":
        await send_last_seen(message, activity_repo, settings, target_user_id=intent.target_user_id)
        return

    if intent.name == "role":
        await game_role_command(message, command=_command_object_from_args(intent.args.get("raw_args")))  # type: ignore[arg-type]
        return

    if intent.name == "eco":
        await economy_eco_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            economy_repo=economy_repo,
            chat_settings=chat_settings,
        )
        return

    if intent.name == "tap":
        await economy_tap_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            economy_repo=economy_repo,
            chat_settings=chat_settings,
        )
        return

    if intent.name == "daily":
        await economy_daily_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            economy_repo=economy_repo,
            chat_settings=chat_settings,
        )
        return

    if intent.name == "farm":
        await economy_farm_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            bot=bot,
            economy_repo=economy_repo,
            chat_settings=chat_settings,
        )
        return

    if intent.name == "shop":
        await economy_shop_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            bot=bot,
            economy_repo=economy_repo,
            chat_settings=chat_settings,
        )
        return

    if intent.name == "inventory":
        await economy_inventory_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            bot=bot,
            economy_repo=economy_repo,
            chat_settings=chat_settings,
        )
        return

    if intent.name == "craft":
        await economy_craft_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            bot=bot,
            economy_repo=economy_repo,
            chat_settings=chat_settings,
        )
        return

    if intent.name == "lottery":
        await economy_lottery_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            bot=bot,
            economy_repo=economy_repo,
            chat_settings=chat_settings,
        )
        return

    if intent.name == "market":
        await economy_market_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            bot=bot,
            economy_repo=economy_repo,
            chat_settings=chat_settings,
        )
        return

    if intent.name == "pay":
        await economy_pay_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            bot=bot,
            economy_repo=economy_repo,
            activity_repo=activity_repo,
            chat_settings=chat_settings,
        )
        return

    if intent.name == "auction":
        await economy_auction_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            bot=bot,
            economy_repo=economy_repo,
            activity_repo=activity_repo,
            chat_settings=chat_settings,
            session_factory=session_factory,
        )
        return

    if intent.name == "bid":
        await economy_bid_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            bot=bot,
            economy_repo=economy_repo,
            activity_repo=activity_repo,
            chat_settings=chat_settings,
        )
        return

    if intent.name == "growth":
        await economy_growth_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            bot=bot,
            economy_repo=economy_repo,
            activity_repo=activity_repo,
            settings=settings,
            chat_settings=chat_settings,
        )
        return

    if intent.name == "growth_action":
        await economy_growth_command(
            message,
            command=_command_object_from_args("do"),
            bot=bot,
            economy_repo=economy_repo,
            activity_repo=activity_repo,
            settings=settings,
            chat_settings=chat_settings,
        )
        return

    if intent.name == "relation":
        await relationship_relation_command(message, activity_repo=activity_repo)
        return

    if intent.name == "marriage":
        await relationship_marriage_status_command(message, activity_repo=activity_repo)
        return

    if intent.name == "marriages":
        await relationship_marriages_command(message, activity_repo=activity_repo)
        return

    if intent.name == "pair":
        await relationship_pair_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            activity_repo=activity_repo,
        )
        return

    if intent.name == "adopt":
        await family_adopt_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            activity_repo=activity_repo,
            chat_settings=chat_settings,
        )
        return

    if intent.name == "pet":
        await family_pet_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            activity_repo=activity_repo,
            chat_settings=chat_settings,
        )
        return

    if intent.name == "family":
        await family_tree_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            activity_repo=activity_repo,
            chat_settings=chat_settings,
        )
        return

    if intent.name in {"antiraid_on", "antiraid_off", "chat_lock", "chat_unlock"}:
        await manage_chat_gate_command(
            message,
            activity_repo=activity_repo,
            bot=bot,
            chat_settings=chat_settings,
            command_key=intent.name,
            raw_args=intent.args.get("raw_args") if intent.args else None,
        )
        return

    if intent.name == "marry":
        await relationship_marry_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            activity_repo=activity_repo,
        )
        return

    if intent.name == "breakup":
        await relationship_breakup_command(message, activity_repo=activity_repo)
        return

    if intent.name == "love":
        await relationship_love_command(message, activity_repo=activity_repo)
        return

    if intent.name == "care":
        await relationship_care_command(message, activity_repo=activity_repo)
        return

    if intent.name == "date":
        await relationship_date_command(message, activity_repo=activity_repo)
        return

    if intent.name == "gift":
        await relationship_gift_command(message, activity_repo=activity_repo)
        return

    if intent.name == "support":
        await relationship_support_command(message, activity_repo=activity_repo)
        return

    if intent.name == "flirt":
        await relationship_flirt_command(message, activity_repo=activity_repo)
        return

    if intent.name == "surprise":
        await relationship_surprise_command(message, activity_repo=activity_repo)
        return

    if intent.name == "vow":
        await relationship_vow_command(message, activity_repo=activity_repo)
        return

    if intent.name == "divorce":
        await relationship_divorce_command(message, activity_repo=activity_repo)
        return

    if intent.name == "title":
        await title_prefix_command(
            message,
            command=_command_object_from_args(intent.args.get("raw_args")),  # type: ignore[arg-type]
            activity_repo=activity_repo,
            economy_repo=economy_repo,
            chat_settings=chat_settings,
        )
        return
