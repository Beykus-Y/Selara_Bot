from __future__ import annotations

import asyncio
import logging
import random
import re
from html import escape
from typing import Any

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from selara.application.use_cases.economy.grant_game_rewards import execute as grant_game_rewards
from selara.core.chat_settings import ChatSettings
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.domain.value_objects import display_name_from_parts
from selara.presentation.auth import has_permission
from selara.presentation.game_state import (
    GAME_DEFINITIONS,
    GAME_LAUNCHABLE_KINDS,
    GAME_STORE,
    BUNKER_CARD_FIELDS,
    BredRoundResolution,
    BunkerCard,
    BunkerRevealResult,
    BunkerVoteResolution,
    DiceRollResult,
    DayVoteResolution,
    ExecutionConfirmResolution,
    GameKind,
    GroupGame,
    MAFIA_ROLE_CHILD,
    MAFIA_ROLE_JESTER,
    MAFIA_ROLE_JOURNALIST,
    MAFIA_ROLE_MANIAC,
    MAFIA_ROLE_SERIAL,
    MAFIA_ROLE_VETERAN,
    MAFIA_TEAM_CIVILIAN,
    MAFIA_TEAM_MAFIA,
    MAFIA_TEAM_VAMPIRE,
    NightResolution,
    QuizRoundResolution,
    SpyVoteResolution,
    WhoamiAnswerResolution,
    WhoamiGuessResolution,
    WhoamiQuestionResult,
    ZlobRoundResolution,
)
from selara.presentation.handlers.game.modes import (
    build_bunker_start_text,
    build_bredovukha_start_text,
    build_dice_start_text,
    build_mafia_start_text,
    build_number_start_text,
    build_quiz_start_text,
    build_spy_start_text,
    build_whoami_start_text,
    build_zlobcards_start_text,
    number_distance_hint,
)
from selara.presentation.handlers.private_panel import send_private_start_panel

router = Router(name="game")
logger = logging.getLogger(__name__)

_GAME_PHASE_TASKS: dict[str, asyncio.Task[None]] = {}
_BOT_USERNAME_CACHE: str | None = None
_LOBBY_VIEW_LIMIT = 12
_ALIVE_VIEW_LIMIT = 12
_DEAD_VIEW_LIMIT = 10
_GAME_PARTICIPANT_REWARD_MIN = 10
_GAME_PARTICIPANT_REWARD_MAX = 20
_GAME_WINNER_REWARD_TOTAL_BY_KIND: dict[GameKind, tuple[int, int]] = {
    "zlobcards": (130, 170),
    "dice": (110, 130),
    "number": (120, 150),
    "quiz": (130, 165),
    "bredovukha": (130, 165),
    "bunker": (130, 170),
    "spy": (125, 155),
    "mafia": (130, 170),
    "whoami": (125, 155),
}
_ZLOBCARDS_PRIVATE_SECONDS = 75
_ZLOBCARDS_VOTE_SECONDS = 75


def _is_stale_callback_query_error(exc: TelegramBadRequest) -> bool:
    error_text = str(exc).lower()
    return "query is too old" in error_text or "query id is invalid" in error_text


async def _safe_callback_answer(query: CallbackQuery, text: str | None = None, *, show_alert: bool = False) -> bool:
    try:
        await query.answer(text=text, show_alert=show_alert)
        return True
    except TelegramBadRequest as exc:
        if _is_stale_callback_query_error(exc):
            return False
        raise


def _chat_settings_to_values(chat_settings: ChatSettings) -> dict[str, object]:
    return {
        "top_limit_default": chat_settings.top_limit_default,
        "top_limit_max": chat_settings.top_limit_max,
        "vote_daily_limit": chat_settings.vote_daily_limit,
        "leaderboard_hybrid_buttons_enabled": chat_settings.leaderboard_hybrid_buttons_enabled,
        "leaderboard_hybrid_karma_weight": chat_settings.leaderboard_hybrid_karma_weight,
        "leaderboard_hybrid_activity_weight": chat_settings.leaderboard_hybrid_activity_weight,
        "leaderboard_7d_days": chat_settings.leaderboard_7d_days,
        "leaderboard_week_start_weekday": chat_settings.leaderboard_week_start_weekday,
        "leaderboard_week_start_hour": chat_settings.leaderboard_week_start_hour,
        "mafia_night_seconds": chat_settings.mafia_night_seconds,
        "mafia_day_seconds": chat_settings.mafia_day_seconds,
        "mafia_vote_seconds": chat_settings.mafia_vote_seconds,
        "mafia_reveal_eliminated_role": chat_settings.mafia_reveal_eliminated_role,
        "text_commands_enabled": chat_settings.text_commands_enabled,
        "text_commands_locale": chat_settings.text_commands_locale,
        "actions_18_enabled": chat_settings.actions_18_enabled,
        "economy_enabled": chat_settings.economy_enabled,
        "economy_mode": chat_settings.economy_mode,
        "economy_tap_cooldown_seconds": chat_settings.economy_tap_cooldown_seconds,
        "economy_daily_base_reward": chat_settings.economy_daily_base_reward,
        "economy_daily_streak_cap": chat_settings.economy_daily_streak_cap,
        "economy_lottery_ticket_price": chat_settings.economy_lottery_ticket_price,
        "economy_lottery_paid_daily_limit": chat_settings.economy_lottery_paid_daily_limit,
        "economy_transfer_daily_limit": chat_settings.economy_transfer_daily_limit,
        "economy_transfer_tax_percent": chat_settings.economy_transfer_tax_percent,
        "economy_market_fee_percent": chat_settings.economy_market_fee_percent,
        "economy_negative_event_chance_percent": chat_settings.economy_negative_event_chance_percent,
        "economy_negative_event_loss_percent": chat_settings.economy_negative_event_loss_percent,
    }


async def _actor_can_manage_games(
    activity_repo,
    *,
    chat_id: int,
    chat_type: str,
    chat_title: str | None,
    user: UserSnapshot,
    bootstrap_if_missing_owner: bool = False,
) -> bool:
    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=chat_id,
        chat_type=chat_type,
        chat_title=chat_title,
        user_id=user.telegram_user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        is_bot=user.is_bot,
        permission="manage_games",
        bootstrap_if_missing_owner=bootstrap_if_missing_owner,
    )
    return allowed


async def _actor_can_start_game(
    activity_repo,
    *,
    game: GroupGame,
    chat_type: str,
    chat_title: str | None,
    user: UserSnapshot,
    bootstrap_if_missing_owner: bool = False,
) -> bool:
    if game.owner_user_id == user.telegram_user_id:
        return True
    return await _actor_can_manage_games(
        activity_repo,
        chat_id=game.chat_id,
        chat_type=chat_type,
        chat_title=chat_title,
        user=user,
        bootstrap_if_missing_owner=bootstrap_if_missing_owner,
    )


async def _persist_mafia_reveal_default(
    activity_repo,
    *,
    chat_id: int,
    chat_type: str,
    chat_title: str | None,
    chat_settings: ChatSettings,
    reveal_eliminated_role: bool,
) -> None:
    values = _chat_settings_to_values(chat_settings)
    values["mafia_reveal_eliminated_role"] = reveal_eliminated_role
    chat = ChatSnapshot(
        telegram_chat_id=chat_id,
        chat_type=chat_type,
        title=chat_title,
    )
    try:
        await activity_repo.upsert_chat_settings(chat=chat, values=values)
    except Exception:
        logger.exception(
            "Failed to persist lobby mafia setting",
            extra={"chat_id": chat_id},
        )


def _build_private_delivery_warning_text(failed_dm: int) -> str:
    return (
        f"Не удалось отправить ЛС для {failed_dm} игрок(ов). "
        "Им нужно открыть диалог с ботом через кнопку роли или карточки."
    )


async def _notify_private_delivery_warning(bot: Bot, game: GroupGame, failed_dm: int) -> None:
    if failed_dm <= 0:
        return
    try:
        await bot.send_message(chat_id=game.chat_id, text=_build_private_delivery_warning_text(failed_dm))
    except Exception:
        logger.exception(
            "Failed to deliver private role warning",
            extra={"chat_id": game.chat_id, "game_id": game.game_id, "failed_dm": failed_dm},
        )


def _user_label(user_id: int, username: str | None, first_name: str | None, last_name: str | None) -> str:
    if username:
        return f"@{username}"
    return display_name_from_parts(
        user_id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        chat_display_name=None,
    )


async def _resolve_chat_player_label(
    activity_repo,
    *,
    chat_id: int,
    user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
) -> str:
    if activity_repo is not None:
        try:
            display_name = await activity_repo.get_chat_display_name(chat_id=chat_id, user_id=user_id)
            if display_name:
                return display_name
        except Exception:
            pass
    return _user_label(user_id=user_id, username=username, first_name=first_name, last_name=last_name)


async def _refresh_game_player_label(
    activity_repo,
    *,
    game: GroupGame | None,
    chat_id: int,
    user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
) -> str:
    label = await _resolve_chat_player_label(
        activity_repo,
        chat_id=chat_id,
        user_id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
    )
    if game is not None and user_id in game.players:
        await GAME_STORE.set_player_label(chat_id=chat_id, user_id=user_id, user_label=label)
    return label


def _mention(user_id: int, label: str) -> str:
    return f'<a href="tg://user?id={user_id}">{escape(label)}</a>'


def _format_duration(seconds: int) -> str:
    total_seconds = max(0, seconds)
    minutes, rest_seconds = divmod(total_seconds, 60)
    if minutes and rest_seconds:
        return f"{minutes}м {rest_seconds}с"
    if minutes:
        return f"{minutes}м"
    return f"{rest_seconds}с"


def _sorted_player_ids(game: GroupGame, user_ids: set[int] | tuple[int, ...] | list[int]) -> list[int]:
    return sorted(user_ids, key=lambda user_id: game.players.get(user_id, f"user:{user_id}").lower())


def _render_player_inline_list(
    game: GroupGame,
    user_ids: set[int] | tuple[int, ...] | list[int],
    *,
    limit: int = 8,
) -> str:
    ordered = _sorted_player_ids(game, user_ids)
    if not ordered:
        return "—"

    parts = [escape(game.players.get(user_id, f"user:{user_id}")) for user_id in ordered[:limit]]
    if len(ordered) > limit:
        parts.append(f"+{len(ordered) - limit}")
    return " • ".join(parts)


def _spy_vote_counts(game: GroupGame) -> dict[int, int]:
    vote_counts: dict[int, int] = {}
    for target_user_id in game.spy_votes.values():
        if target_user_id in game.players:
            vote_counts[target_user_id] = vote_counts.get(target_user_id, 0) + 1
    return vote_counts


def _mafia_day_vote_counts(game: GroupGame) -> dict[int, int]:
    vote_counts: dict[int, int] = {}
    for voter_user_id, target_user_id in game.day_votes.items():
        if voter_user_id not in game.alive_player_ids or target_user_id not in game.alive_player_ids:
            continue
        vote_counts[target_user_id] = vote_counts.get(target_user_id, 0) + 1
    return vote_counts


def _render_vote_leaders(
    game: GroupGame,
    vote_counts: dict[int, int],
    *,
    limit: int = 3,
    empty_text: str = "пока нет",
) -> str:
    if not vote_counts:
        return empty_text

    leaders = sorted(
        vote_counts.items(),
        key=lambda item: (-item[1], game.players.get(item[0], f"user:{item[0]}").lower(), item[0]),
    )
    parts = [f"{escape(game.players.get(user_id, f'user:{user_id}'))} — {votes}" for user_id, votes in leaders[:limit]]
    if len(leaders) > limit:
        parts.append(f"+{len(leaders) - limit}")
    return " • ".join(parts)


def _bunker_field_label(field_key: str) -> str:
    labels = {
        "profession": "Профессия",
        "age": "Возраст",
        "gender": "Пол",
        "health_condition": "Состояние здоровья",
        "skill": "Навык",
        "hobby": "Хобби",
        "phobia": "Фобия",
        "trait": "Особенность",
        "item": "Предмет в рюкзаке",
    }
    return labels.get(field_key, field_key)


def _bunker_card_value(card: BunkerCard, field_key: str) -> str:
    return getattr(card, field_key, "-")


def _render_bunker_full_card(card: BunkerCard) -> str:
    lines = []
    for field_key in BUNKER_CARD_FIELDS:
        lines.append(f"• <b>{_bunker_field_label(field_key)}:</b> {escape(_bunker_card_value(card, field_key))}")
    return "\n".join(lines)


def _parse_kind(raw: str | None) -> GameKind | None:
    if raw is None:
        return None

    value = raw.strip().lower()
    aliases: dict[str, GameKind] = {
        "spy": "spy",
        "шпион": "spy",
        "spygame": "spy",
        "mafia": "mafia",
        "мафия": "mafia",
        "dice": "dice",
        "кости": "dice",
        "кубик": "dice",
        "кубики": "dice",
        "number": "number",
        "num": "number",
        "число": "number",
        "угадай": "number",
        "угадай число": "number",
        "quiz": "quiz",
        "викторина": "quiz",
        "вик": "quiz",
        "bredovukha": "bredovukha",
        "bred": "bredovukha",
        "бредовуха": "bredovukha",
        "бред": "bredovukha",
        "bunker": "bunker",
        "бункер": "bunker",
        "whoami": "whoami",
        "who am i": "whoami",
        "кто я": "whoami",
        "ктоя": "whoami",
        "zlobcards": "zlobcards",
        "zlob": "zlobcards",
        "cah": "zlobcards",
        "злобные карты": "zlobcards",
        "злобкарты": "zlobcards",
    }
    return aliases.get(value)


def _build_game_selection_keyboard(*, requester_user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for kind in GAME_LAUNCHABLE_KINDS:
        definition = GAME_DEFINITIONS[kind]  # type: ignore[index]
        builder.button(text=definition.title, callback_data=f"game:new:{kind}:u{requester_user_id}")
    builder.adjust(1)
    return builder.as_markup()


def _build_mafia_day_vote_buttons(game: GroupGame) -> InlineKeyboardMarkup | None:
    if game.kind != "mafia" or game.phase != "day_vote" or game.status != "started":
        return None

    alive_items = _sorted_player_ids(game, game.alive_player_ids)
    if not alive_items:
        return None

    vote_counts = _mafia_day_vote_counts(game)
    builder = InlineKeyboardBuilder()
    for user_id in alive_items:
        label = game.players.get(user_id, f"user:{user_id}")
        count_text = f" · {vote_counts.get(user_id, 0)}" if vote_counts.get(user_id, 0) > 0 else ""
        text = label if len(label) <= 15 else f"{label[:12]}..."
        builder.button(text=f"🗳 {text}{count_text}", callback_data=f"gmvote:{game.game_id}:{user_id}")

    builder.adjust(2)
    return builder.as_markup()


def _build_private_day_vote_keyboard(game: GroupGame, *, actor_user_id: int) -> InlineKeyboardMarkup | None:
    if game.kind != "mafia" or game.phase != "day_vote" or game.status != "started":
        return None

    if actor_user_id not in game.alive_player_ids:
        return None

    alive_items = sorted(game.alive_player_ids)
    if not alive_items:
        return None

    current_target_user_id = game.day_votes.get(actor_user_id)
    builder = InlineKeyboardBuilder()
    for user_id in alive_items:
        label = game.players.get(user_id, f"user:{user_id}")
        text = label if len(label) <= 24 else f"{label[:21]}..."
        icon = "✅" if current_target_user_id == user_id else "🗳"
        builder.button(text=f"{icon} {text}", callback_data=f"gmvote:{game.game_id}:{user_id}")

    builder.adjust(1)
    return builder.as_markup()


def _build_mafia_execution_confirm_buttons(game: GroupGame) -> InlineKeyboardMarkup | None:
    if game.kind != "mafia" or game.phase != "day_execution_confirm" or game.status != "started":
        return None

    voted_count, alive_count, yes_count, no_count = _count_alive_execution_confirm_votes(game)

    builder = InlineKeyboardBuilder()
    builder.button(text=f"✅ Да ({yes_count})", callback_data=f"gmconfirm:{game.game_id}:yes")
    builder.button(text=f"❌ Нет ({no_count})", callback_data=f"gmconfirm:{game.game_id}:no")
    builder.button(text=f"🗳 {voted_count}/{alive_count}", callback_data=f"gmconfirm:{game.game_id}:noop")
    builder.adjust(2, 1)
    return builder.as_markup()


def _quiz_choice_label(index: int) -> str:
    symbols = ["A", "B", "C", "D", "E", "F"]
    if 0 <= index < len(symbols):
        return symbols[index]
    return str(index + 1)


def _build_quiz_answer_buttons(game: GroupGame) -> InlineKeyboardMarkup | None:
    if game.kind != "quiz" or game.status != "started" or game.phase != "freeplay":
        return None
    if game.quiz_current_question_index is None:
        return None
    if game.quiz_current_question_index < 0 or game.quiz_current_question_index >= len(game.quiz_questions):
        return None

    question = game.quiz_questions[game.quiz_current_question_index]
    if not question.options:
        return None

    builder = InlineKeyboardBuilder()
    for option_index, option_text in enumerate(question.options):
        short_text = option_text if len(option_text) <= 18 else f"{option_text[:15]}..."
        builder.button(
            text=f"{_quiz_choice_label(option_index)}. {short_text}",
            callback_data=f"gquiz:{game.game_id}:{option_index}",
        )

    answered_count = len({user_id for user_id in game.quiz_answers if user_id in game.players})
    builder.button(
        text=f"🗳 {answered_count}/{len(game.players)}",
        callback_data=f"gquiz:{game.game_id}:noop",
    )
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def _build_spy_vote_buttons(game: GroupGame) -> InlineKeyboardMarkup | None:
    if game.kind != "spy" or game.status != "started" or game.phase != "freeplay":
        return None
    if len(game.players) < 3:
        return None

    vote_counts = _spy_vote_counts(game)
    builder = InlineKeyboardBuilder()
    for user_id, label in sorted(game.players.items(), key=lambda item: item[1].lower()):
        count_text = f" · {vote_counts.get(user_id, 0)}" if vote_counts.get(user_id, 0) > 0 else ""
        text = label if len(label) <= 17 else f"{label[:14]}..."
        builder.button(text=f"🚨 {text}{count_text}", callback_data=f"gspy:{game.game_id}:{user_id}")

    voted_count = len(game.spy_votes)
    builder.button(text=f"🗳 {voted_count}/{len(game.players)}", callback_data=f"gspy:{game.game_id}:noop")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def _build_whoami_answer_buttons(game: GroupGame) -> InlineKeyboardMarkup | None:
    if game.kind != "whoami" or game.status != "started" or game.phase != "whoami_answer":
        return None
    if game.whoami_current_actor_user_id is None or not game.whoami_pending_question_text:
        return None

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data=f"gwho:{game.game_id}:yes")
    builder.button(text="❌ Нет", callback_data=f"gwho:{game.game_id}:no")
    builder.button(text="🤷 Не знаю", callback_data=f"gwho:{game.game_id}:unknown")
    builder.button(text="🎭 Неважно", callback_data=f"gwho:{game.game_id}:irrelevant")
    builder.adjust(2, 2)
    return builder.as_markup()


def _build_bred_vote_buttons(game: GroupGame) -> InlineKeyboardMarkup | None:
    if game.kind != "bredovukha" or game.status != "started" or game.phase != "public_vote":
        return None
    if not game.bred_options:
        return None

    builder = InlineKeyboardBuilder()
    for option_index, option_text in enumerate(game.bred_options):
        short_text = option_text if len(option_text) <= 24 else f"{option_text[:21]}..."
        builder.button(
            text=f"{_quiz_choice_label(option_index)}. {short_text}",
            callback_data=f"gbred:{game.game_id}:{option_index}",
        )

    voted_count = len({user_id for user_id in game.bred_votes if user_id in game.players})
    builder.button(text=f"🗳 {voted_count}/{len(game.players)}", callback_data=f"gbred:{game.game_id}:noop")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def _build_bred_category_buttons(game: GroupGame) -> InlineKeyboardMarkup | None:
    if game.kind != "bredovukha" or game.status != "started" or game.phase != "category_pick":
        return None
    if not game.bred_category_options:
        return None

    builder = InlineKeyboardBuilder()
    for option_index, category in enumerate(game.bred_category_options):
        text = category if len(category) <= 22 else f"{category[:19]}..."
        builder.button(
            text=f"📚 {text}",
            callback_data=f"gbredcat:{game.game_id}:{option_index}",
        )

    selector_label = "-"
    if game.bred_current_selector_user_id is not None:
        selector_label = game.players.get(game.bred_current_selector_user_id, f"user:{game.bred_current_selector_user_id}")
    builder.button(
        text=f"🎯 Ход: {selector_label[:16]}",
        callback_data=f"gbredcat:{game.game_id}:noop",
    )
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def _build_zlob_vote_buttons(game: GroupGame) -> InlineKeyboardMarkup | None:
    if game.kind != "zlobcards" or game.status != "started" or game.phase != "public_vote":
        return None
    if not game.zlob_options:
        return None

    builder = InlineKeyboardBuilder()
    for option_index, option_text in enumerate(game.zlob_options):
        short_text = option_text if len(option_text) <= 24 else f"{option_text[:21]}..."
        builder.button(
            text=f"{_quiz_choice_label(option_index)}. {short_text}",
            callback_data=f"gzlobv:{game.game_id}:{option_index}",
        )

    voted_count = len({user_id for user_id in game.zlob_votes if user_id in game.players})
    builder.button(text=f"🗳 {voted_count}/{len(game.players)}", callback_data=f"gzlobv:{game.game_id}:noop")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def _build_private_zlob_submit_keyboard(game: GroupGame, *, actor_user_id: int) -> InlineKeyboardMarkup | None:
    if game.kind != "zlobcards" or game.status != "started" or game.phase != "private_answers":
        return None
    if actor_user_id not in game.players:
        return None

    hand = list(game.zlob_hands.get(actor_user_id, ()))
    if not hand:
        return None

    slots = max(1, int(game.zlob_black_slots))
    builder = InlineKeyboardBuilder()
    if slots == 1:
        for card_index, card_text in enumerate(hand):
            label = card_text if len(card_text) <= 24 else f"{card_text[:21]}..."
            builder.button(
                text=f"🃏 {label}",
                callback_data=f"gzlobp:{game.game_id}:{card_index}",
            )
    else:
        for first in range(len(hand)):
            for second in range(first + 1, len(hand)):
                first_text = hand[first]
                second_text = hand[second]
                merged = f"{first_text} + {second_text}"
                label = merged if len(merged) <= 24 else f"{merged[:21]}..."
                builder.button(
                    text=f"🃏 {label}",
                    callback_data=f"gzlobp:{game.game_id}:{first}-{second}",
                )
    builder.button(text="🔄 Обновить", callback_data=f"gzlobp:{game.game_id}:noop")
    builder.adjust(1)
    return builder.as_markup()


def _build_private_bunker_reveal_keyboard(game: GroupGame, *, actor_user_id: int) -> InlineKeyboardMarkup | None:
    if game.kind != "bunker" or game.status != "started" or game.phase != "bunker_reveal":
        return None
    if actor_user_id not in game.alive_player_ids:
        return None
    if actor_user_id != game.bunker_current_actor_user_id:
        return None

    card = game.bunker_cards.get(actor_user_id)
    if card is None:
        return None
    revealed = game.bunker_revealed_fields.get(actor_user_id, set())
    hidden_fields = [field_key for field_key in BUNKER_CARD_FIELDS if field_key not in revealed]
    if not hidden_fields:
        return None

    builder = InlineKeyboardBuilder()
    for field_key in hidden_fields:
        text = _bunker_field_label(field_key)
        builder.button(text=f"🃏 {text}", callback_data=f"gbkr:{game.game_id}:{field_key}")
    builder.button(text="🔄 Обновить", callback_data=f"gbkr:{game.game_id}:noop")
    builder.adjust(1)
    return builder.as_markup()


def _build_private_bunker_vote_keyboard(game: GroupGame, *, actor_user_id: int) -> InlineKeyboardMarkup | None:
    if game.kind != "bunker" or game.status != "started" or game.phase != "bunker_vote":
        return None
    if actor_user_id not in game.alive_player_ids:
        return None

    targets = sorted(user_id for user_id in game.alive_player_ids if user_id != actor_user_id)
    if not targets:
        return None

    current_target = game.bunker_votes.get(actor_user_id)
    builder = InlineKeyboardBuilder()
    for user_id in targets:
        label = game.players.get(user_id, f"user:{user_id}")
        short_label = label if len(label) <= 24 else f"{label[:21]}..."
        icon = "✅" if current_target == user_id else "🗳"
        builder.button(text=f"{icon} {short_label}", callback_data=f"gbkv:{game.game_id}:{user_id}")
    builder.button(text="🔄 Обновить", callback_data=f"gbkv:{game.game_id}:noop")
    builder.adjust(1)
    return builder.as_markup()


def _build_private_night_action_keyboard(game: GroupGame, *, actor_user_id: int) -> InlineKeyboardMarkup | None:
    if game.kind != "mafia" or game.status != "started" or game.phase != "night":
        return None

    targets = GAME_STORE._mafia_night_action_targets(game, actor_user_id=actor_user_id)
    if not targets:
        return None

    builder = InlineKeyboardBuilder()

    for user_id in targets:
        label = game.players.get(user_id, f"user:{user_id}")
        text = label if len(label) <= 24 else f"{label[:21]}..."
        builder.button(text=f"🎯 {text}", callback_data=f"gmact:{game.game_id}:{user_id}")
    columns = 1
    if len(targets) >= 4:
        columns = 2
    builder.adjust(columns)
    return builder.as_markup()


def _phase_title(game: GroupGame) -> str:
    if game.phase == "lobby":
        return "Набор игроков"
    if game.phase == "whoami_ask":
        return "Кто я: вопрос"
    if game.phase == "whoami_answer":
        return "Кто я: ответ"
    if game.phase == "category_pick":
        return "Выбор категории"
    if game.phase == "private_answers":
        return "Сбор ответов в ЛС"
    if game.phase == "public_vote":
        return "Голосование"
    if game.phase == "bunker_reveal":
        return "Бункер: раскрытие"
    if game.phase == "bunker_vote":
        return "Бункер: голосование"
    if game.phase == "freeplay":
        if game.kind == "number":
            return "Поиск числа"
        if game.kind == "quiz":
            return "Вопрос викторины"
        return "Свободная игра"
    if game.phase == "night":
        return "Ночь"
    if game.phase == "day_discussion":
        return "День: обсуждение"
    if game.phase == "day_vote":
        return "День: голосование"
    if game.phase == "day_execution_confirm":
        return "День: подтверждение казни"
    return "Завершено"


def _render_lobby_players(game: GroupGame) -> str:
    players = sorted(game.players.items(), key=lambda item: item[1].lower())
    lines: list[str] = [f"<b>Лобби ({len(players)}):</b>"]

    for idx, (user_id, label) in enumerate(players[:_LOBBY_VIEW_LIMIT], start=1):
        lines.append(f"{idx}. {_mention(user_id, label)}")
    if len(players) > _LOBBY_VIEW_LIMIT:
        lines.append(f"... и ещё <code>{len(players) - _LOBBY_VIEW_LIMIT}</code> игроков")

    return "\n".join(lines)


def _render_alive_players(game: GroupGame) -> str:
    if game.kind != "mafia":
        return ""

    alive = _sorted_player_ids(game, game.alive_player_ids)
    dead = _sorted_player_ids(game, set(game.players.keys()) - set(alive))
    return "\n".join(
        [
            f"<b>В игре ({len(alive)}):</b> {_render_player_inline_list(game, alive, limit=_ALIVE_VIEW_LIMIT)}",
            f"<b>Выбыли ({len(dead)}):</b> {_render_player_inline_list(game, dead, limit=_DEAD_VIEW_LIMIT)}",
        ]
    )


def _render_spy_vote_status(game: GroupGame) -> str:
    if game.kind != "spy":
        return ""

    total_players = len(game.players)
    voted_count = len(game.spy_votes)
    majority = total_players // 2 + 1
    vote_counts = _spy_vote_counts(game)

    lines = [
        f"<b>Прогресс голосования:</b> {voted_count}/{total_players}",
        f"<b>Порог обвинения:</b> {majority} голоса за одного игрока.",
    ]

    if vote_counts:
        leaders = sorted(vote_counts.items(), key=lambda item: (-item[1], game.players.get(item[0], f"user:{item[0]}").lower()))
        top_user_id, top_votes = leaders[0]
        leader_label = game.players.get(top_user_id, f"user:{top_user_id}")
        lines.append(f"<b>Главный подозреваемый:</b> {escape(leader_label)} ({top_votes})")
        lines.append(f"<b>Доска подозрений:</b> {_render_vote_leaders(game, vote_counts, limit=4)}")
    else:
        lines.append("<b>Главный подозреваемый:</b> пока нет.")

    waiting_user_ids = [user_id for user_id in _sorted_player_ids(game, game.players.keys()) if user_id not in game.spy_votes]
    if waiting_user_ids:
        lines.append(f"<b>Ещё без голоса:</b> {_render_player_inline_list(game, waiting_user_ids, limit=5)}")

    return "\n".join(lines)


def _whoami_category_label(game: GroupGame) -> str:
    return game.whoami_category or "случайная тема"


def _spy_category_label(game: GroupGame) -> str:
    return game.spy_category or "случайная тема"


def _zlob_category_label(game: GroupGame) -> str:
    return game.zlob_category or "случайная тема"


def _extract_whoami_guess(text: str) -> str | None:
    guess_patterns = (
        r"^\s*я думаю,?\s+что я\s+(.+?)\s*$",
        r"^\s*моя догадка[:\-]?\s+(.+?)\s*$",
        r"^\s*кажется,?\s+я\s+(.+?)\s*$",
    )
    for pattern in guess_patterns:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if match:
            guess = match.group(1).strip(" .!?,")
            return guess or None
    return None


def _should_handle_whoami_group_text(active_game: GroupGame | None, *, user_id: int | None, text: str) -> bool:
    if user_id is None or active_game is None:
        return False
    if active_game.kind != "whoami" or active_game.status != "started":
        return False
    if user_id not in active_game.players:
        return False
    if user_id != active_game.whoami_current_actor_user_id:
        return False

    normalized = text.strip()
    if not normalized:
        return False
    if _extract_whoami_guess(normalized) is not None:
        return True
    return active_game.phase == "whoami_ask" and "?" in normalized


def _should_handle_number_guess(active_game: GroupGame | None) -> bool:
    return active_game is not None and active_game.kind == "number" and active_game.status == "started"


def _should_handle_bred_private_answer(game: GroupGame | None, *, text: str) -> bool:
    normalized = text.strip()
    if not normalized or normalized.startswith("/"):
        return False
    return game is not None


def _render_whoami_history(game: GroupGame, *, limit: int = 6) -> str:
    if game.kind != "whoami":
        return ""
    if not game.whoami_history:
        return "<b>Последние ходы:</b> пока пусто."

    lines = ["<b>Последние ходы:</b>"]
    for entry in game.whoami_history[-limit:]:
        actor_label = game.players.get(entry.actor_user_id, f"user:{entry.actor_user_id}")
        if entry.question_text and entry.answer_label:
            responder_label = "-"
            if entry.responder_user_id is not None:
                responder_label = game.players.get(entry.responder_user_id, f"user:{entry.responder_user_id}")
            lines.append(
                f"- {escape(actor_label)}: {escape(entry.question_text)} → "
                f"<b>{escape(entry.answer_label)}</b> ({escape(responder_label)})"
            )
            continue
        if entry.guessed_correctly is not None and entry.guess_text:
            verdict = "угадал" if entry.guessed_correctly else "мимо"
            lines.append(f"- {escape(actor_label)} делает догадку → {verdict}")
    return "\n".join(lines)


def _render_whoami_status(game: GroupGame) -> str:
    if game.kind != "whoami":
        return ""

    actor_line = "—"
    if game.whoami_current_actor_user_id is not None:
        actor_label = game.players.get(game.whoami_current_actor_user_id, f"user:{game.whoami_current_actor_user_id}")
        actor_line = _mention(game.whoami_current_actor_user_id, actor_label)

    lines = [
        f"<b>Категория:</b> {escape(_whoami_category_label(game))}",
        f"<b>Ходит:</b> {actor_line}",
        f"<b>Разгадали себя:</b> {len(game.whoami_solved_user_ids)}/{len(game.players)}",
    ]
    if game.phase == "whoami_ask":
        lines.append("<b>Сейчас:</b> текущий игрок задаёт вопрос сообщением в чат или делает догадку.")
        lines.append(
            "<i>Если игрок разгадал себя, он выходит из круга вопросов, "
            "но остаётся частью стола. После «нет / не знаю / неважно» ход переходит дальше.</i>"
        )
    elif game.phase == "whoami_answer":
        lines.append(f"<b>Вопрос:</b> {escape(game.whoami_pending_question_text or '-')}")
        lines.append("<b>Сейчас:</b> стол выбирает ответ кнопками «да / нет / не знаю / неважно».")

    lines.append("")
    lines.append(_render_whoami_history(game))
    return "\n".join(lines)


def _render_dice_progress(game: GroupGame) -> str:
    if game.kind != "dice":
        return ""

    total_players = len(game.players)
    rolled_count = len(game.dice_scores)
    lines = [f"<b>Бросили:</b> {rolled_count}/{total_players}"]
    if not game.dice_scores:
        lines.append("<b>Результаты:</b> пока нет бросков.")
        return "\n".join(lines)

    ranking = sorted(
        game.dice_scores.items(),
        key=lambda item: (-item[1], game.players.get(item[0], f"user:{item[0]}").lower(), item[0]),
    )
    lines.append("<b>Текущие броски:</b>")
    for idx, (user_id, score) in enumerate(ranking, start=1):
        lines.append(f"{idx}. {_mention(user_id, game.players.get(user_id, f'user:{user_id}'))} — <code>{score}</code>")
    return "\n".join(lines)


def _render_bunker_public_profiles(game: GroupGame) -> str:
    if game.kind != "bunker":
        return ""

    alive = sorted(game.alive_player_ids)
    eliminated = sorted(set(game.players.keys()) - set(alive))
    lines: list[str] = [f"<b>Живые ({len(alive)}):</b>"]

    if not alive:
        lines.append("- нет")
    else:
        for idx, user_id in enumerate(alive[:_ALIVE_VIEW_LIMIT], start=1):
            label = game.players.get(user_id, f"user:{user_id}")
            revealed = game.bunker_revealed_fields.get(user_id, set())
            revealed_items: list[str] = []
            card = game.bunker_cards.get(user_id)
            for field_key in BUNKER_CARD_FIELDS:
                if field_key not in revealed or card is None:
                    continue
                revealed_items.append(f"{_bunker_field_label(field_key)}: {escape(_bunker_card_value(card, field_key))}")
            if revealed_items:
                lines.append(
                    f"{idx}. {_mention(user_id, label)} — {len(revealed)}/{len(BUNKER_CARD_FIELDS)}: "
                    + "; ".join(revealed_items)
                )
            else:
                lines.append(f"{idx}. {_mention(user_id, label)} — 0/{len(BUNKER_CARD_FIELDS)} (пока всё скрыто)")
        if len(alive) > _ALIVE_VIEW_LIMIT:
            lines.append(f"... и ещё <code>{len(alive) - _ALIVE_VIEW_LIMIT}</code>")

    lines.append("")
    lines.append(f"<b>Выбывшие ({len(eliminated)}):</b>")
    if not eliminated:
        lines.append("- нет")
    else:
        for idx, user_id in enumerate(eliminated[:_DEAD_VIEW_LIMIT], start=1):
            lines.append(f"{idx}. {escape(game.players.get(user_id, f'user:{user_id}'))}")
        if len(eliminated) > _DEAD_VIEW_LIMIT:
            lines.append(f"... и ещё <code>{len(eliminated) - _DEAD_VIEW_LIMIT}</code>")

    return "\n".join(lines)


def _render_bunker_vote_status(game: GroupGame) -> str:
    if game.kind != "bunker":
        return ""

    alive_count = len(game.alive_player_ids)
    voted_count = len({voter for voter in game.bunker_votes if voter in game.alive_player_ids})
    lines = [f"<b>Голосование:</b> {voted_count}/{alive_count}"]

    vote_counts: dict[int, int] = {}
    for voter_user_id, target_user_id in game.bunker_votes.items():
        if voter_user_id not in game.alive_player_ids:
            continue
        if target_user_id not in game.alive_player_ids:
            continue
        vote_counts[target_user_id] = vote_counts.get(target_user_id, 0) + 1

    if vote_counts:
        leader_votes = max(vote_counts.values())
        leaders = [user_id for user_id, votes in vote_counts.items() if votes == leader_votes]
        if len(leaders) == 1:
            leader_label = game.players.get(leaders[0], f"user:{leaders[0]}")
            lines.append(f"<b>Лидер:</b> {escape(leader_label)} ({leader_votes})")
        else:
            lines.append(f"<b>Лидер:</b> ничья по {leader_votes} голос(ам)")
    else:
        lines.append("<b>Лидер:</b> пока нет.")
    waiting_ids = [uid for uid in _sorted_player_ids(game, game.alive_player_ids) if uid not in game.bunker_votes]
    if waiting_ids:
        lines.append(f"<b>Ждём:</b> {_render_player_inline_list(game, waiting_ids, limit=5)}")
    return "\n".join(lines)


def _render_roles_reveal(game: GroupGame) -> str:
    lines = ["<b>Раскрытие ролей:</b>"]

    if game.kind == "spy":
        lines.append(f"<b>Тема:</b> {escape(_spy_category_label(game))}")
        if game.spy_location:
            lines.append(f"<b>Локация:</b> <code>{escape(game.spy_location)}</code>")
    if game.kind == "whoami":
        lines.append(f"<b>Категория:</b> {escape(_whoami_category_label(game))}")

    items = sorted(game.players.items(), key=lambda x: x[1].lower())
    for user_id, label in items:
        role = game.roles.get(user_id, "-")
        lines.append(f"- {_mention(user_id, label)} — <code>{escape(role)}</code>")

    return "\n".join(lines)


def _format_day_vote_protocol(game: GroupGame, resolution: DayVoteResolution) -> str:
    lines = ["<b>Протокол дневного голосования:</b>"]
    for voter_id, target_id in resolution.vote_protocol:
        voter_label = game.players.get(voter_id, f"user:{voter_id}")
        if target_id is None:
            target_text = "(не голосовал)"
        else:
            target_text = game.players.get(target_id, f"user:{target_id}")
        lines.append(f"- {escape(voter_label)} -> {escape(target_text)}")
    return "\n".join(lines)


def _format_execution_confirm_protocol(game: GroupGame, resolution: ExecutionConfirmResolution) -> str:
    lines = ["<b>Протокол подтверждения:</b>"]
    for voter_id, vote in resolution.vote_protocol:
        voter_label = game.players.get(voter_id, f"user:{voter_id}")
        if vote is True:
            vote_text = "Да"
        elif vote is False:
            vote_text = "Нет"
        else:
            vote_text = "(не голосовал)"
        lines.append(f"- {escape(voter_label)} -> {vote_text}")
    return "\n".join(lines)


def _format_elimination_event(game: GroupGame, *, user_id: int, label: str, role: str | None) -> str:
    if game.reveal_eliminated_role and role:
        return f"выбыл {escape(label)} (<code>{escape(role)}</code>)"
    return f"выбыл {escape(label)}"


def _winner_ids_for_mafia(game: GroupGame) -> set[int]:
    text = (game.winner_text or "").lower()
    if "мирных" in text:
        return {
            user_id
            for user_id in game.roles
            if GAME_STORE._mafia_team_for_user(game, user_id) == MAFIA_TEAM_CIVILIAN
        }
    if "мафии" in text:
        return {
            user_id
            for user_id in game.roles
            if GAME_STORE._mafia_team_for_user(game, user_id) == MAFIA_TEAM_MAFIA
        }
    if "вампир" in text:
        return {
            user_id
            for user_id in game.roles
            if GAME_STORE._mafia_team_for_user(game, user_id) == MAFIA_TEAM_VAMPIRE
        }
    if "маньяк" in text:
        return {user_id for user_id, role in game.roles.items() if role == MAFIA_ROLE_MANIAC}
    if "серийного" in text:
        return {user_id for user_id, role in game.roles.items() if role == MAFIA_ROLE_SERIAL}
    if "шута" in text:
        return {user_id for user_id, role in game.roles.items() if role == MAFIA_ROLE_JESTER}
    if "нейтрала" in text or "нейтрал" in text:
        return {
            user_id
            for user_id in game.roles
            if GAME_STORE._mafia_team_for_user(game, user_id) not in {MAFIA_TEAM_CIVILIAN, MAFIA_TEAM_MAFIA, MAFIA_TEAM_VAMPIRE}
        }
    return set()


def _winner_ids_for_spy(game: GroupGame) -> set[int]:
    text = (game.winner_text or "").lower()
    if "победа мирн" in text or "мирные" in text:
        return {user_id for user_id, role in game.roles.items() if role != "Шпион"}
    if "победа шпион" in text or "шпион побед" in text:
        return {user_id for user_id, role in game.roles.items() if role == "Шпион"}
    return set()


def _winner_ids_for_whoami(game: GroupGame) -> set[int]:
    if game.kind != "whoami":
        return set()
    if game.whoami_finish_order:
        return set(game.whoami_finish_order)
    if game.whoami_winner_user_id is None:
        return set()
    return {game.whoami_winner_user_id}


def _build_game_rewards(
    game: GroupGame,
    *,
    winner_user_ids_override: set[int] | None = None,
) -> dict[int, int]:
    winner_text = (game.winner_text or "").lower()
    if "остановлена" in winner_text or "по решению" in winner_text:
        return {}

    rewards: dict[int, int] = {
        user_id: random.randint(_GAME_PARTICIPANT_REWARD_MIN, _GAME_PARTICIPANT_REWARD_MAX)
        for user_id in game.players
    }

    winner_user_ids: set[int] = set()
    if winner_user_ids_override is not None:
        winner_user_ids = set(winner_user_ids_override)
    elif game.kind == "dice" and game.dice_scores:
        max_score = max(game.dice_scores.values())
        winner_user_ids = {user_id for user_id, score in game.dice_scores.items() if score == max_score}
    elif game.kind == "quiz" and game.quiz_scores:
        max_score = max(game.quiz_scores.values())
        winner_user_ids = {user_id for user_id, score in game.quiz_scores.items() if score == max_score}
    elif game.kind == "bredovukha" and game.bred_scores:
        max_score = max(game.bred_scores.values())
        winner_user_ids = {user_id for user_id, score in game.bred_scores.items() if score == max_score}
    elif game.kind == "bunker":
        winner_user_ids = set(game.alive_player_ids)
    elif game.kind == "mafia":
        winner_user_ids = _winner_ids_for_mafia(game)
    elif game.kind == "spy":
        winner_user_ids = _winner_ids_for_spy(game)
    elif game.kind == "whoami":
        winner_user_ids = _winner_ids_for_whoami(game)

    winner_reward_range = _GAME_WINNER_REWARD_TOTAL_BY_KIND.get(game.kind, (120, 150))
    for user_id in winner_user_ids:
        if user_id in rewards:
            rewards[user_id] = max(rewards[user_id], random.randint(*winner_reward_range))

    return rewards


async def _grant_game_rewards_if_needed(
    game: GroupGame,
    *,
    economy_repo,
    chat_settings: ChatSettings,
    winner_user_ids_override: set[int] | None = None,
) -> str | None:
    if game.status != "finished":
        return None
    if game.economy_rewards_granted:
        return None
    if economy_repo is None or not chat_settings.economy_enabled:
        return None

    rewards = _build_game_rewards(game, winner_user_ids_override=winner_user_ids_override)
    if not rewards:
        game.economy_rewards_granted = True
        return None

    result, error = await grant_game_rewards(
        economy_repo,
        economy_mode=chat_settings.economy_mode,
        chat_id=game.chat_id,
        game_kind=game.kind,
        game_id=game.game_id,
        rewards_by_user=rewards,
    )
    if error:
        logger.warning(
            "Failed to grant game rewards",
            extra={"game_id": game.game_id, "chat_id": game.chat_id, "error": error},
        )
        return None

    game.economy_rewards_granted = True
    if result is None:
        return None
    return f"<b>Эконом-награды:</b> выдано {result.total_distributed} монет {len(result.rewarded_users)} участникам."


def _count_alive_execution_confirm_votes(game: GroupGame) -> tuple[int, int, int, int]:
    alive = set(game.alive_player_ids)
    voted = {voter for voter in game.execution_confirm_votes if voter in alive}
    yes_count = sum(1 for voter in voted if game.execution_confirm_votes.get(voter) is True)
    no_count = sum(1 for voter in voted if game.execution_confirm_votes.get(voter) is False)
    return len(voted), len(alive), yes_count, no_count


def _render_private_day_vote_text(game: GroupGame, *, actor_user_id: int, voted_count: int, alive_count: int) -> str:
    current_target_user_id = game.day_votes.get(actor_user_id)
    current_target = "ещё не выбран"
    if current_target_user_id is not None:
        current_target = game.players.get(current_target_user_id, f"user:{current_target_user_id}")

    lines = [
        f"<b>День {game.round_no}: голосование</b>",
        "Выберите кандидата на выбывание. Можно менять выбор до конца этапа.",
        f"<b>Ваш голос:</b> {escape(current_target)}",
        f"<b>Прогресс:</b> {voted_count}/{alive_count}",
    ]
    return "\n".join(lines)


def _render_execution_confirm_prompt(game: GroupGame) -> str:
    candidate_text = "-"
    if game.mafia_execution_candidate_user_id is not None:
        candidate_text = game.players.get(game.mafia_execution_candidate_user_id, f"user:{game.mafia_execution_candidate_user_id}")

    voted_count, alive_count, yes_count, no_count = _count_alive_execution_confirm_votes(game)
    lines = [
        "<b>Подтверждение казни</b>",
        f"<b>Кандидат:</b> {escape(candidate_text)}",
        "<i>Стол решает: казнить или оставить в игре.</i>",
        f"<b>Голоса:</b> ✅ {yes_count} • ❌ {no_count} • 🗳 {voted_count}/{alive_count}",
    ]
    return "\n".join(lines)


def _render_execution_confirm_result(game: GroupGame, resolution: ExecutionConfirmResolution) -> str:
    if resolution.executed_user_id is not None:
        executed_label = resolution.executed_user_label or f"user:{resolution.executed_user_id}"
        outcome = _format_elimination_event(
            game,
            user_id=resolution.executed_user_id,
            label=executed_label,
            role=resolution.executed_user_role,
        )
    else:
        outcome = "никто не выбыл"

    status = "казнь подтверждена" if resolution.passed else "казнь отклонена"
    return (
        "<b>Подтверждение казни завершено</b>\n"
        f"<b>Итог:</b> {status}, {outcome}.\n"
        f"<b>Счёт:</b> ✅ {resolution.yes_count} • ❌ {resolution.no_count}"
    )


def _render_quiz_question(game: GroupGame) -> str:
    if game.kind != "quiz" or game.quiz_current_question_index is None:
        return ""
    if game.quiz_current_question_index < 0 or game.quiz_current_question_index >= len(game.quiz_questions):
        return ""

    question = game.quiz_questions[game.quiz_current_question_index]
    lines = [
        f"<b>Вопрос {game.quiz_current_question_index + 1}/{len(game.quiz_questions)}</b>",
        escape(question.prompt),
    ]
    for option_index, option in enumerate(question.options):
        lines.append(f"{_quiz_choice_label(option_index)}. {escape(option)}")

    answered_count = len({user_id for user_id in game.quiz_answers if user_id in game.players})
    lines.append(f"<b>Ответили:</b> {answered_count}/{len(game.players)}")
    waiting_ids = [uid for uid in _sorted_player_ids(game, game.players.keys()) if uid not in game.quiz_answers]
    if waiting_ids:
        lines.append(f"<b>Ждём:</b> {_render_player_inline_list(game, waiting_ids, limit=5)}")
    return "\n".join(lines)


def _render_quiz_scoreboard(game: GroupGame, *, limit: int = 8) -> str:
    if game.kind != "quiz":
        return ""

    if not game.quiz_scores:
        return "<b>Счёт:</b> пока без очков."

    ranking = sorted(
        game.quiz_scores.items(),
        key=lambda item: (-item[1], game.players.get(item[0], f"user:{item[0]}").lower(), item[0]),
    )
    lines = ["<b>Счёт:</b>"]
    for idx, (user_id, score) in enumerate(ranking[:limit], start=1):
        label = game.players.get(user_id, f"user:{user_id}")
        lines.append(f"{idx}. {_mention(user_id, label)} — <code>{score}</code>")
    return "\n".join(lines)


def _render_number_attempts(game: GroupGame, *, limit: int = 8) -> str:
    if game.kind != "number":
        return ""

    lines = [f"<b>Всего попыток:</b> {game.number_attempts_total}"]
    if not game.number_attempts:
        lines.append("<b>Личный зачёт:</b> пока пусто.")
        return "\n".join(lines)

    ranking = sorted(
        game.number_attempts.items(),
        key=lambda item: (item[1], game.players.get(item[0], f"user:{item[0]}").lower(), item[0]),
    )
    lines.append("<b>Личный зачёт (меньше лучше):</b>")
    for idx, (user_id, attempts) in enumerate(ranking[:limit], start=1):
        label = game.players.get(user_id, f"user:{user_id}")
        lines.append(f"{idx}. {_mention(user_id, label)} — <code>{attempts}</code>")
    return "\n".join(lines)


def _render_bred_scoreboard(game: GroupGame, *, limit: int = 8) -> str:
    if game.kind != "bredovukha":
        return ""
    if not game.bred_scores:
        return "<b>Счёт:</b> пока без очков."

    ranking = sorted(
        game.bred_scores.items(),
        key=lambda item: (-item[1], game.players.get(item[0], f"user:{item[0]}").lower(), item[0]),
    )
    lines = ["<b>Счёт:</b>"]
    for idx, (user_id, score) in enumerate(ranking[:limit], start=1):
        label = game.players.get(user_id, f"user:{user_id}")
        lines.append(f"{idx}. {_mention(user_id, label)} — <code>{score}</code>")
    return "\n".join(lines)


def _render_zlob_scoreboard(game: GroupGame, *, limit: int = 8) -> str:
    if game.kind != "zlobcards":
        return ""
    if not game.zlob_scores:
        return "<b>Счёт:</b> пока без очков."

    ranking = sorted(
        game.zlob_scores.items(),
        key=lambda item: (-item[1], game.players.get(item[0], f"user:{item[0]}").lower(), item[0]),
    )
    lines = ["<b>Счёт:</b>"]
    for idx, (user_id, score) in enumerate(ranking[:limit], start=1):
        label = game.players.get(user_id, f"user:{user_id}")
        lines.append(f"{idx}. {_mention(user_id, label)} — <code>{score}</code>")
    return "\n".join(lines)


def _render_bred_question(game: GroupGame) -> str:
    if game.kind != "bredovukha":
        return ""
    if game.phase == "category_pick":
        selector_label = "-"
        if game.bred_current_selector_user_id is not None:
            selector_label = game.players.get(game.bred_current_selector_user_id, f"user:{game.bred_current_selector_user_id}")

        lines = [
            f"<b>Раунд {max(game.round_no, 1)}/{game.bred_rounds}</b>",
            f"<b>Категорию выбирает:</b> {escape(selector_label)}",
        ]
        if game.bred_category_options:
            lines.append("<i>Кнопки ниже сразу откроют тему раунда.</i>")
        return "\n".join(lines)

    if not game.bred_question_prompt:
        return ""

    lines = [
        f"<b>Раунд {max(game.round_no, 1)}/{game.bred_rounds}</b>",
        f"<b>Категория:</b> {escape(game.bred_current_category or '-')}",
        "<b>Факт с пропуском:</b>",
        escape(game.bred_question_prompt),
    ]

    if game.phase == "private_answers":
        submitted_user_ids = [
            user_id for user_id in _sorted_player_ids(game, game.players.keys()) if user_id in game.bred_lies
        ]
        waiting_user_ids = [
            user_id for user_id in _sorted_player_ids(game, game.players.keys()) if user_id not in game.bred_lies
        ]
        lines.append("<i>Придумайте правдоподобную ложь и сдайте её боту в ЛС или на сайте.</i>")
        lines.append(f"<b>Сдано:</b> {len(submitted_user_ids)}/{len(game.players)}")
        lines.append(f"<b>Уже ответили:</b> {_render_player_inline_list(game, submitted_user_ids, limit=6)}")
        if waiting_user_ids:
            lines.append(f"<b>Ждём:</b> {_render_player_inline_list(game, waiting_user_ids, limit=6)}")
        return "\n".join(lines)

    if game.phase == "public_vote":
        vote_tally = [0 for _ in game.bred_options]
        for voted_option_index in game.bred_votes.values():
            if 0 <= voted_option_index < len(vote_tally):
                vote_tally[voted_option_index] += 1
        leader_text = "пока нет"
        if vote_tally:
            top_votes = max(vote_tally)
            if top_votes > 0:
                leader_indices = [idx for idx, count in enumerate(vote_tally) if count == top_votes]
                if len(leader_indices) == 1:
                    leader_text = f"{_quiz_choice_label(leader_indices[0])} ({top_votes})"
                else:
                    leader_text = f"ничья по {top_votes}"
        lines.append("<i>Выберите вариант кнопками ниже. Голос можно менять до конца этапа.</i>")
        voted_count = len({user_id for user_id in game.bred_votes if user_id in game.players})
        lines.append(f"<b>Прогресс:</b> {voted_count}/{len(game.players)} голосов")
        lines.append(f"<b>Лидер:</b> {leader_text}")
        waiting_ids = [uid for uid in _sorted_player_ids(game, game.players.keys()) if uid not in game.bred_votes]
        if waiting_ids:
            lines.append(f"<b>Ждём:</b> {_render_player_inline_list(game, waiting_ids, limit=5)}")
        return "\n".join(lines)

    return "\n".join(lines)


def _render_zlob_round_status(game: GroupGame) -> str:
    if game.kind != "zlobcards" or not game.zlob_black_text:
        return ""

    lines = [
        f"<b>Раунд {max(game.round_no, 1)}/{game.zlob_rounds}</b>",
        f"<b>Тема:</b> {escape(_zlob_category_label(game))}",
        f"<b>Чёрная карточка ({game.zlob_black_slots}):</b>",
        escape(game.zlob_black_text),
    ]

    if game.phase == "private_answers":
        submitted_user_ids = [
            user_id for user_id in _sorted_player_ids(game, game.players.keys()) if user_id in game.zlob_submissions
        ]
        waiting_user_ids = [
            user_id for user_id in _sorted_player_ids(game, game.players.keys()) if user_id not in game.zlob_submissions
        ]
        lines.append("<i>Выберите карту(ы) из руки в ЛС или на сайте. Ответы публикуются анонимно.</i>")
        lines.append(f"<b>Сдано:</b> {len(submitted_user_ids)}/{len(game.players)}")
        lines.append(f"<b>Уже сдали:</b> {_render_player_inline_list(game, submitted_user_ids, limit=6)}")
        if waiting_user_ids:
            lines.append(f"<b>Ждём:</b> {_render_player_inline_list(game, waiting_user_ids, limit=6)}")
        return "\n".join(lines)

    if game.phase == "public_vote":
        vote_tally = [0 for _ in game.zlob_options]
        for voted_option_index in game.zlob_votes.values():
            if 0 <= voted_option_index < len(vote_tally):
                vote_tally[voted_option_index] += 1
        leader_text = "пока нет"
        if vote_tally:
            top_votes = max(vote_tally)
            if top_votes > 0:
                leader_indices = [idx for idx, count in enumerate(vote_tally) if count == top_votes]
                if len(leader_indices) == 1:
                    leader_text = f"{_quiz_choice_label(leader_indices[0])} ({top_votes})"
                else:
                    leader_text = f"ничья по {top_votes}"
        lines.append("<i>Голосуйте за самый смешной ответ. Голос можно менять до закрытия раунда.</i>")
        voted_count = len({user_id for user_id in game.zlob_votes if user_id in game.players})
        lines.append(f"<b>Прогресс:</b> {voted_count}/{len(game.players)} голосов")
        lines.append(f"<b>Лидер:</b> {leader_text}")
        waiting_ids = [uid for uid in _sorted_player_ids(game, game.players.keys()) if uid not in game.zlob_votes]
        if waiting_ids:
            lines.append(f"<b>Ждём:</b> {_render_player_inline_list(game, waiting_ids, limit=5)}")
        return "\n".join(lines)

    return "\n".join(lines)


def _format_quiz_round_resolution(game: GroupGame, resolution: QuizRoundResolution) -> str:
    lines = [
        f"<b>Викторина:</b> вопрос {resolution.question_index + 1} закрыт.",
        f"<b>Верный ответ:</b> {_quiz_choice_label(resolution.correct_option_index)}. {escape(resolution.correct_option_text)}",
    ]
    if resolution.correct_players:
        labels = ", ".join(escape(game.players.get(user_id, f"user:{user_id}")) for user_id in resolution.correct_players)
        lines.append(f"<b>Верно ответили:</b> {labels}")
    else:
        lines.append("<b>Верно ответили:</b> никто.")
    lines.append(_render_quiz_scoreboard(game))
    return "\n".join(lines)


def _format_bred_round_resolution(game: GroupGame, resolution: BredRoundResolution) -> str:
    lines = [
        f"<b>Бредовуха • раунд {resolution.round_no}/{game.bred_rounds}</b>",
        f"<b>Категория:</b> {escape(resolution.category)}",
        f"<b>Правильный вариант:</b> {_quiz_choice_label(resolution.correct_option_index)}. {escape(resolution.correct_option_text)}",
    ]
    if resolution.fact_text:
        lines.append(f"<b>Разбор факта:</b> {escape(resolution.fact_text)}")

    option_lines = ["<b>Кто за что отвечал:</b>"]
    for option_index, option_text in enumerate(resolution.options):
        votes = resolution.vote_tally[option_index] if option_index < len(resolution.vote_tally) else 0
        owner_user_id = resolution.option_owner_user_ids[option_index] if option_index < len(resolution.option_owner_user_ids) else None
        if owner_user_id is None:
            owner_text = "правда"
        else:
            owner_text = game.players.get(owner_user_id, f"user:{owner_user_id}")
        option_lines.append(
            f"{_quiz_choice_label(option_index)}. {escape(option_text)} — {escape(owner_text)} • {votes} голос(а/ов)"
        )
    lines.extend(option_lines)

    gain_parts: list[str] = []
    for user_id, gain in sorted(resolution.gains, key=lambda item: (-item[1], game.players.get(item[0], f"user:{item[0]}").lower(), item[0])):
        label = game.players.get(user_id, f"user:{user_id}")
        gain_parts.append(f"{escape(label)} +{gain}")
    if gain_parts:
        lines.append(f"<b>Очки за раунд:</b> {' • '.join(gain_parts)}")

    lines.append(_render_bred_scoreboard(game))
    if resolution.finished:
        lines.append(f"<b>Победа:</b> {escape(resolution.winner_text or '-')}")
    elif resolution.next_round_no is not None and resolution.next_selector_label is not None:
        lines.append(
            f"<b>Далее:</b> раунд {resolution.next_round_no}. "
            f"Категорию выбирает {escape(resolution.next_selector_label)}."
        )
    return "\n".join(lines)


def _format_zlob_round_resolution(game: GroupGame, resolution: ZlobRoundResolution) -> str:
    lines = [
        f"<b>500 Злобных Карт • раунд {resolution.round_no}/{game.zlob_rounds}</b>",
        f"<b>Чёрная карточка ({resolution.black_slots}):</b> {escape(resolution.black_text)}",
    ]

    option_lines = ["<b>Варианты и голоса:</b>"]
    for option_index, option_text in enumerate(resolution.options):
        votes = resolution.vote_tally[option_index] if option_index < len(resolution.vote_tally) else 0
        owner_user_id = resolution.option_owner_user_ids[option_index] if option_index < len(resolution.option_owner_user_ids) else None
        owner_text = game.players.get(owner_user_id, f"user:{owner_user_id}") if owner_user_id is not None else "-"
        win_mark = " 🏆" if option_index in resolution.winner_option_indexes else ""
        option_lines.append(
            f"{_quiz_choice_label(option_index)}. {escape(option_text)} — {escape(owner_text)} • {votes} голос(а/ов){win_mark}"
        )
    lines.extend(option_lines)

    gain_parts: list[str] = []
    for user_id, gain in sorted(
        resolution.gains,
        key=lambda item: (-item[1], game.players.get(item[0], f"user:{item[0]}").lower(), item[0]),
    ):
        if gain <= 0:
            continue
        label = game.players.get(user_id, f"user:{user_id}")
        gain_parts.append(f"{escape(label)} +{gain}")
    if gain_parts:
        lines.append(f"<b>Очки за раунд:</b> {' • '.join(gain_parts)}")
    else:
        lines.append("<b>Очки за раунд:</b> без начислений.")

    lines.append(_render_zlob_scoreboard(game))
    if resolution.finished:
        lines.append(f"<b>Победа:</b> {escape(resolution.winner_text or '-')}")
    elif resolution.next_round_no is not None:
        lines.append(f"<b>Далее:</b> раунд {resolution.next_round_no}.")
    return "\n".join(lines)


def _format_spy_vote_resolution(game: GroupGame, resolution: SpyVoteResolution) -> str:
    lines = [
        "<b>Шпион • финал раунда</b>",
        f"<b>Голосов подано:</b> {resolution.voted_count}/{resolution.total_players}",
    ]
    if resolution.tie:
        lines.append("<b>Итог:</b> ничья по голосам.")
    elif resolution.candidate_user_id is not None:
        candidate_label = resolution.candidate_user_label or f"user:{resolution.candidate_user_id}"
        lines.append(f"<b>Под обвинением:</b> {escape(candidate_label)} ({resolution.candidate_votes} голосов)")
        if resolution.candidate_is_spy is True:
            lines.append("<b>Проверка:</b> это был шпион.")
        elif resolution.candidate_is_spy is False:
            lines.append("<b>Проверка:</b> это был мирный.")
    spy_user_id = next((user_id for user_id, role in game.roles.items() if role == "Шпион"), None)
    if spy_user_id is not None:
        spy_label = game.players.get(spy_user_id, f"user:{spy_user_id}")
        lines.append(f"<b>Кто был шпионом:</b> {escape(spy_label)}")
    lines.append(f"<b>Тема:</b> {escape(_spy_category_label(game))}")
    if game.spy_location:
        lines.append(f"<b>Локация:</b> <code>{escape(game.spy_location)}</code>")
    if resolution.winner_text:
        lines.append(f"<b>Победа:</b> {escape(resolution.winner_text)}")
    return "\n".join(lines)


def _format_whoami_answer_resolution(game: GroupGame, resolution: WhoamiAnswerResolution) -> str:
    actor_label = resolution.actor_user_label or f"user:{resolution.actor_user_id}"
    responder_label = resolution.responder_user_label or f"user:{resolution.responder_user_id}"
    lines = [
        "<b>Кто я • ответ стола</b>",
        f"<b>Игрок:</b> {escape(actor_label)}",
        f"<b>Вопрос:</b> {escape(resolution.question_text)}",
        f"<b>Ответ:</b> {escape(resolution.answer_label)} ({escape(responder_label)})",
    ]
    if resolution.keeps_turn:
        lines.append(f"<b>Ход остаётся у:</b> {escape(actor_label)}")
    elif resolution.next_actor_label:
        lines.append(f"<b>Следующий ход:</b> {escape(resolution.next_actor_label)}")
    return "\n".join(lines)


def _format_whoami_guess_resolution(game: GroupGame, resolution: WhoamiGuessResolution) -> str:
    actor_label = resolution.actor_user_label or f"user:{resolution.actor_user_id}"
    lines = [
        "<b>Кто я • проверка догадки</b>",
        f"<b>Игрок:</b> {escape(actor_label)}",
    ]
    if resolution.guessed_correctly:
        lines.append("<b>Результат:</b> карточка разгадана.")
        lines.append(f"<b>Прогресс:</b> {len(game.whoami_solved_user_ids)}/{len(game.players)}")
        if resolution.finished and resolution.winner_text:
            lines.append(f"<b>Финал:</b> {escape(resolution.winner_text)}")
        else:
            lines.append("<b>Статус:</b> игрок больше не задаёт вопросы, но может отвечать столу.")
            if resolution.next_actor_label:
                lines.append(f"<b>Следующий ход:</b> {escape(resolution.next_actor_label)}")
    else:
        lines.append("<b>Результат:</b> мимо.")
        if resolution.next_actor_label:
            lines.append(f"<b>Следующий ход:</b> {escape(resolution.next_actor_label)}")
    return "\n".join(lines)


def _format_bunker_vote_resolution(game: GroupGame, resolution: BunkerVoteResolution) -> str:
    lines = [
        "<b>Голосование в «Бункере» завершено</b>",
        f"<b>Голосов подано:</b> {resolution.voted_count}/{resolution.total_alive}",
    ]

    if resolution.tie:
        lines.append("<b>Итог:</b> ничья, никто не выбыл.")
    elif resolution.eliminated_user_id is not None:
        eliminated_label = resolution.eliminated_user_label or f"user:{resolution.eliminated_user_id}"
        lines.append(f"<b>Выбыл:</b> {escape(eliminated_label)}")
        if resolution.eliminated_card is not None:
            lines.append("<b>Раскрытие карточки выбывшего:</b>")
            lines.append(_render_bunker_full_card(resolution.eliminated_card))

    if resolution.finished and resolution.winner_text:
        lines.append(f"<b>Итог:</b> {escape(resolution.winner_text)}")
    return "\n".join(lines)


def _render_game_text(
    game: GroupGame,
    chat_settings: ChatSettings,
    *,
    note: str | None = None,
    include_reveal: bool = False,
) -> str:
    definition = GAME_DEFINITIONS[game.kind]
    players_count = len(game.players)

    lines = [f"<b>{escape(definition.title)}</b>"]
    if game.phase == "lobby":
        lines.append(f"<i>{escape(definition.short_description)}</i>")
    lines.append(f"<b>Этап:</b> {escape(_phase_title(game))}")
    lines.append(f"<b>Игроков:</b> {players_count}")

    if game.phase == "lobby":
        owner_label = game.players.get(game.owner_user_id, f"user:{game.owner_user_id}")
        lines.append(f"<b>Создатель лобби:</b> {_mention(game.owner_user_id, owner_label)}")
        lines.append("")
        lines.append(_render_lobby_players(game))
        if game.kind == "mafia":
            mode = "показывать" if game.reveal_eliminated_role else "скрывать"
            lines.append(f"<b>Роль выбывшего:</b> {mode}")
        if game.kind == "bredovukha":
            min_rounds = len(game.players)
            lines.append(f"<b>Раундов:</b> {game.bred_rounds}")
            lines.append(f"<b>Минимум для старта:</b> {min_rounds} (по числу игроков)")
        if game.kind == "bunker":
            lines.append(f"<b>Мест в бункере:</b> {game.bunker_seats}")
            lines.append("<b>Минимум для старта:</b> 6 игроков")
        if game.kind == "spy":
            lines.append(f"<b>Тема:</b> {escape(_spy_category_label(game))}")
            lines.append("<b>Формат:</b> шпион не знает локацию, мирные знают и вычисляют его по вопросам.")
        if game.kind == "whoami":
            lines.append(f"<b>Категория:</b> {escape(_whoami_category_label(game))}")
            lines.append("<b>Формат:</b> вопрос в чат -> ответ кнопкой -> новая догадка или следующий игрок.")
        if game.kind == "zlobcards":
            lines.append(f"<b>Категория:</b> {escape(_zlob_category_label(game))}")
            lines.append(f"<b>Раундов:</b> {game.zlob_rounds}")
            lines.append(f"<b>Цель по очкам:</b> {game.zlob_target_score}")
            lines.append("<b>Формат:</b> чёрная карточка -> приватный выбор -> анонимное голосование.")

    if game.kind == "mafia" and game.status == "started":
        lines.append(f"<b>Раунд:</b> {max(game.round_no, 1)}")

        if game.phase == "night":
            lines.append(
                f"<b>Сейчас:</b> ночь. Ночные роли ходят в ЛС, остальные ждут рассвета "
                f"({_format_duration(chat_settings.mafia_night_seconds)})."
            )
        elif game.phase == "day_discussion":
            lines.append(
                f"<b>Сейчас:</b> обсуждение перед голосованием. Сверяйте версии и ищите нестыковки "
                f"({_format_duration(chat_settings.mafia_day_seconds)})."
            )
        elif game.phase == "day_vote":
            voted_count = len({voter for voter in game.day_votes if voter in game.alive_player_ids})
            lines.append(
                f"<b>Сейчас:</b> дневное голосование. Выберите кандидата кнопками ниже или в ЛС "
                f"({_format_duration(chat_settings.mafia_vote_seconds)})."
            )
            lines.append(f"<b>Прогресс:</b> {voted_count}/{len(game.alive_player_ids)}")
            lines.append(f"<b>Под ударом:</b> {_render_vote_leaders(game, _mafia_day_vote_counts(game))}")
            waiting_ids = [uid for uid in _sorted_player_ids(game, game.alive_player_ids) if uid not in game.day_votes]
            if waiting_ids:
                lines.append(f"<b>Ждём:</b> {_render_player_inline_list(game, waiting_ids, limit=5)}")
        elif game.phase == "day_execution_confirm":
            candidate_label = "-"
            if game.mafia_execution_candidate_user_id is not None:
                candidate_label = game.players.get(game.mafia_execution_candidate_user_id, f"user:{game.mafia_execution_candidate_user_id}")
            voted_count, alive_count, yes_count, no_count = _count_alive_execution_confirm_votes(game)
            lines.append(
                f"<b>Сейчас:</b> стол подтверждает казнь кандидата "
                f"({_format_duration(chat_settings.mafia_vote_seconds)})."
            )
            lines.append(f"<b>Кандидат:</b> {escape(candidate_label)}")
            lines.append(f"<b>Голоса:</b> ✅ {yes_count} • ❌ {no_count} • 🗳 {voted_count}/{alive_count}")

        lines.append("")
        lines.append(_render_alive_players(game))

    if game.kind == "spy" and game.phase == "freeplay":
        lines.append(f"<b>Раунд:</b> {max(game.round_no, 1)}")
        lines.append(f"<b>Тема:</b> {escape(_spy_category_label(game))}")
        lines.append("<b>Сейчас:</b> задавайте вопросы по кругу и ловите того, кто не знает локацию.")
        lines.append("")
        lines.append(_render_spy_vote_status(game))

    if game.kind == "whoami" and game.status == "started":
        lines.append(f"<b>Раунд:</b> {max(game.round_no, 1)}")
        lines.append("<i>Свою карточку вы не видите. Угадавший игрок продолжает отвечать на вопросы.</i>")
        lines.append("")
        lines.append(_render_whoami_status(game))

    if game.kind == "whoami" and game.status == "finished":
        lines.append(f"<b>Категория:</b> {escape(_whoami_category_label(game))}")
        lines.append("")
        lines.append(_render_whoami_history(game))
        lines.append("")
        lines.append(_render_roles_reveal(game))

    if game.kind == "dice" and game.status == "started":
        lines.append("<i>Каждый участник делает ровно один бросок кнопкой «Бросить».</i>")
        lines.append("")
        lines.append(_render_dice_progress(game))

    if game.kind == "number" and game.status == "started":
        lines.append("<i>Пишите число от 1 до 100 отдельным сообщением в чат.</i>")
        lines.append("<i>Бот подскажет: больше/меньше и насколько вы близко.</i>")
        lines.append("")
        lines.append(_render_number_attempts(game))

    if game.kind == "quiz" and game.status == "started":
        lines.append(f"<b>Раунд:</b> {max(game.round_no, 1)}")
        lines.append("<i>Выбирайте ответ кнопками под этим сообщением.</i>")
        question_block = _render_quiz_question(game)
        if question_block:
            lines.append("")
            lines.append(question_block)
        score_block = _render_quiz_scoreboard(game)
        if score_block:
            lines.append("")
            lines.append(score_block)

    if game.kind == "bredovukha" and game.status == "started":
        lines.append(f"<b>Раунд:</b> {max(game.round_no, 1)}/{game.bred_rounds}")
        lines.append("<i>Очки: +2 за правду, +1 за каждого, кто попался на вашу ложь.</i>")
        if game.phase == "category_pick":
            lines.append("<b>Сейчас:</b> выбранный игрок задаёт тему раунда кнопками ниже.")
        elif game.phase == "private_answers":
            lines.append("<b>Сейчас:</b> придумайте фальшивый ответ и сдайте его боту в ЛС или на сайте.")
        elif game.phase == "public_vote":
            lines.append("<b>Сейчас:</b> голосуйте за вариант, который кажется настоящим.")
        question_block = _render_bred_question(game)
        if question_block:
            lines.append("")
            lines.append(question_block)
        score_block = _render_bred_scoreboard(game)
        if score_block:
            lines.append("")
            lines.append(score_block)

    if game.kind == "zlobcards" and game.status == "started":
        lines.append(f"<b>Раунд:</b> {max(game.round_no, 1)}/{game.zlob_rounds}")
        lines.append(f"<b>Цель по очкам:</b> {game.zlob_target_score}")
        if game.phase == "private_answers":
            lines.append("<b>Сейчас:</b> выберите карту(ы) из руки в ЛС или на сайте.")
        elif game.phase == "public_vote":
            lines.append("<b>Сейчас:</b> голосуйте за лучший анонимный вариант.")
        zlob_block = _render_zlob_round_status(game)
        if zlob_block:
            lines.append("")
            lines.append(zlob_block)
        score_block = _render_zlob_scoreboard(game)
        if score_block:
            lines.append("")
            lines.append(score_block)

    if game.kind == "bunker" and game.status == "started":
        lines.append(f"<b>Раунд:</b> {max(game.round_no, 1)}")
        lines.append(f"<b>Катастрофа:</b> {escape(game.bunker_catastrophe or '-')}")
        lines.append(f"<b>Условия бункера:</b> {escape(game.bunker_condition or '-')}")
        lines.append(f"<b>Мест в бункере:</b> {game.bunker_seats}")
        lines.append(f"<b>Живых:</b> {len(game.alive_player_ids)}")
        if game.phase == "bunker_reveal":
            actor_label = "-"
            if game.bunker_current_actor_user_id is not None:
                actor_label = game.players.get(game.bunker_current_actor_user_id, f"user:{game.bunker_current_actor_user_id}")
            lines.append(f"<i>Сейчас раскрывает характеристику: {escape(actor_label)} (в ЛС).</i>")
        elif game.phase == "bunker_vote":
            lines.append("<i>Идёт голосование на выбывание (голоса в ЛС, анонс в группе).</i>")
            lines.append(_render_bunker_vote_status(game))
        if game.bunker_pool_overflow_fields:
            labels = ", ".join(_bunker_field_label(field_key) for field_key in sorted(game.bunker_pool_overflow_fields))
            lines.append(f"<i>Внимание: в части категорий допущены повторы ({escape(labels)}).</i>")
        lines.append("")
        lines.append(_render_bunker_public_profiles(game))

    if game.winner_text:
        lines.append("")
        lines.append(f"<b>Итог:</b> {escape(game.winner_text)}")

    if note:
        lines.append("")
        lines.append(note)

    if include_reveal:
        lines.append("")
        lines.append(_render_roles_reveal(game))

    return "\n".join(lines)


def _build_game_controls(*, game: GroupGame, bot_username: str) -> InlineKeyboardMarkup | None:
    builder = InlineKeyboardBuilder()

    if game.status == "lobby":
        builder.button(text="➕ Присоединиться", callback_data=f"game:join:{game.game_id}")

        if game.kind == "mafia":
            reveal_text = "вкл" if game.reveal_eliminated_role else "выкл"
            builder.button(text=f"🎭 Роль выбывшего: {reveal_text}", callback_data=f"gcfg:{game.game_id}:reveal_elim")
        if game.kind == "bredovukha":
            builder.button(text="➖ Раунды", callback_data=f"gcfg:{game.game_id}:bred_rounds_dec")
            builder.button(text=f"🔢 Раундов: {game.bred_rounds}", callback_data=f"gcfg:{game.game_id}:bred_rounds_noop")
            builder.button(text="➕ Раунды", callback_data=f"gcfg:{game.game_id}:bred_rounds_inc")
        if game.kind == "spy":
            category_text = _spy_category_label(game)
            if len(category_text) > 18:
                category_text = f"{category_text[:15]}..."
            builder.button(text=f"🗺 Тема: {category_text}", callback_data=f"gcfg:{game.game_id}:spy_cat_next")
        if game.kind == "whoami":
            category_text = _whoami_category_label(game)
            if len(category_text) > 18:
                category_text = f"{category_text[:15]}..."
            builder.button(text=f"🧠 Тема: {category_text}", callback_data=f"gcfg:{game.game_id}:whoami_cat_next")
        if game.kind == "zlobcards":
            category_text = _zlob_category_label(game)
            if len(category_text) > 18:
                category_text = f"{category_text[:15]}..."
            builder.button(text=f"😈 Тема: {category_text}", callback_data=f"gcfg:{game.game_id}:zlob_cat_next")
            builder.button(text="➖ Раунды", callback_data=f"gcfg:{game.game_id}:zlob_rounds_dec")
            builder.button(text=f"🔢 Раунды: {game.zlob_rounds}", callback_data=f"gcfg:{game.game_id}:zlob_rounds_noop")
            builder.button(text="➕ Раунды", callback_data=f"gcfg:{game.game_id}:zlob_rounds_inc")
            builder.button(text="➖ Цель", callback_data=f"gcfg:{game.game_id}:zlob_target_dec")
            builder.button(text=f"🏁 Цель: {game.zlob_target_score}", callback_data=f"gcfg:{game.game_id}:zlob_target_noop")
            builder.button(text="➕ Цель", callback_data=f"gcfg:{game.game_id}:zlob_target_inc")
        if game.kind == "bunker":
            builder.button(text="➖ Места", callback_data=f"gcfg:{game.game_id}:bunker_seats_dec")
            builder.button(text=f"🏚 Мест: {game.bunker_seats}", callback_data=f"gcfg:{game.game_id}:bunker_seats_noop")
            builder.button(text="➕ Места", callback_data=f"gcfg:{game.game_id}:bunker_seats_inc")

        builder.button(text="🎬 Старт", callback_data=f"game:start:{game.game_id}")
        builder.button(text="🛑 Отменить", callback_data=f"game:cancel:{game.game_id}")

    elif game.status == "started":
        if game.kind == "mafia" and game.phase in {"night", "day_discussion", "day_vote", "day_execution_confirm"}:
            advance_text = "⏭ Следующая фаза"
            if game.phase == "night":
                advance_text = "🌅 Завершить ночь"
            elif game.phase == "day_discussion":
                advance_text = "🗳 Открыть голосование"
            elif game.phase == "day_vote":
                advance_text = "⚖️ Подвести голоса"
            elif game.phase == "day_execution_confirm":
                advance_text = "☠️ Закрыть казнь"
            builder.button(text=advance_text, callback_data=f"game:advance:{game.game_id}")
        if game.kind == "quiz" and game.phase == "freeplay":
            builder.button(text="⏭ Закрыть вопрос", callback_data=f"game:advance:{game.game_id}")
        if game.kind == "bredovukha" and game.phase == "category_pick":
            builder.button(text="🎲 Случайная тема", callback_data=f"game:advance:{game.game_id}")
        if game.kind == "bredovukha" and game.phase == "private_answers":
            builder.button(text="🗳 Открыть голосование", callback_data=f"game:advance:{game.game_id}")
        if game.kind == "bredovukha" and game.phase == "public_vote":
            builder.button(text="📣 Закрыть раунд", callback_data=f"game:advance:{game.game_id}")
        if game.kind == "zlobcards" and game.phase == "private_answers":
            builder.button(text="🗳 Открыть голосование", callback_data=f"game:advance:{game.game_id}")
        if game.kind == "zlobcards" and game.phase == "public_vote":
            builder.button(text="📣 Закрыть раунд", callback_data=f"game:advance:{game.game_id}")
        if game.kind == "bunker" and game.phase == "bunker_reveal":
            builder.button(text="⏭ Пропустить ход", callback_data=f"game:advance:{game.game_id}")
        if game.kind == "bunker" and game.phase == "bunker_vote":
            builder.button(text="⏭ Завершить голосование", callback_data=f"game:advance:{game.game_id}")
        if game.kind == "dice" and game.phase == "freeplay":
            builder.button(text="🎲 Бросить", callback_data=f"gdice:{game.game_id}:roll")

        if game.kind == "spy" and game.phase == "freeplay":
            builder.button(text="📍 Сводка голосов", callback_data=f"gspy:{game.game_id}:noop")
            builder.button(text="🔎 Раскрыть роли", callback_data=f"game:reveal:{game.game_id}")

        builder.button(text="🛑 Завершить", callback_data=f"game:cancel:{game.game_id}")

    if bot_username and game.status == "started":
        if game.kind in {"spy", "mafia"}:
            builder.button(text="🕵️ Моя роль", url=f"https://t.me/{bot_username}?start=game_{game.game_id}")
            if game.kind == "mafia" and game.phase == "night":
                builder.button(text="🌙 Ночной ход", url=f"https://t.me/{bot_username}?start=game_{game.game_id}")
        if game.kind == "whoami":
            builder.button(text="🪪 Карточки", url=f"https://t.me/{bot_username}?start=game_{game.game_id}")
        if game.kind == "bredovukha" and game.phase == "private_answers":
            builder.button(text="✍️ Сдать ложь в ЛС", url=f"https://t.me/{bot_username}")
        if game.kind == "zlobcards" and game.phase == "private_answers":
            builder.button(text="🃏 Рука в ЛС", url=f"https://t.me/{bot_username}?start=game_{game.game_id}")
        if game.kind == "bunker":
            builder.button(text="🔐 Действие в ЛС", url=f"https://t.me/{bot_username}?start=game_{game.game_id}")

    day_vote_keyboard = _build_mafia_day_vote_buttons(game)
    if day_vote_keyboard is not None:
        for row in day_vote_keyboard.inline_keyboard:
            for button in row:
                builder.add(button)

    quiz_keyboard = _build_quiz_answer_buttons(game)
    if quiz_keyboard is not None:
        for row in quiz_keyboard.inline_keyboard:
            for button in row:
                builder.add(button)

    spy_keyboard = _build_spy_vote_buttons(game)
    if spy_keyboard is not None:
        for row in spy_keyboard.inline_keyboard:
            for button in row:
                builder.add(button)

    whoami_keyboard = _build_whoami_answer_buttons(game)
    if whoami_keyboard is not None:
        for row in whoami_keyboard.inline_keyboard:
            for button in row:
                builder.add(button)

    bred_keyboard = _build_bred_vote_buttons(game)
    if bred_keyboard is not None:
        for row in bred_keyboard.inline_keyboard:
            for button in row:
                builder.add(button)

    zlob_vote_keyboard = _build_zlob_vote_buttons(game)
    if zlob_vote_keyboard is not None:
        for row in zlob_vote_keyboard.inline_keyboard:
            for button in row:
                builder.add(button)

    bred_categories_keyboard = _build_bred_category_buttons(game)
    if bred_categories_keyboard is not None:
        for row in bred_categories_keyboard.inline_keyboard:
            for button in row:
                builder.add(button)

    if not builder.buttons:
        return None

    builder.adjust(2)
    return builder.as_markup()


async def _safe_edit_or_send_game_board(
    bot: Bot,
    game: GroupGame,
    chat_settings: ChatSettings,
    *,
    note: str | None = None,
    include_reveal: bool = False,
) -> None:
    bot_username = await _get_bot_username(bot)
    text = _render_game_text(game, chat_settings, note=note, include_reveal=include_reveal)
    keyboard = _build_game_controls(game=game, bot_username=bot_username)

    if game.message_id is not None:
        try:
            await bot.edit_message_text(
                chat_id=game.chat_id,
                message_id=game.message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return

    sent = await bot.send_message(
        chat_id=game.chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_notification=True,
    )
    await GAME_STORE.set_message_id(game_id=game.game_id, message_id=sent.message_id)


async def _send_game_feed_event(
    bot: Bot,
    game: GroupGame,
    *,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    kwargs: dict[str, object] = {
        "chat_id": game.chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": reply_markup,
        "disable_notification": True,
    }

    if game.message_id is not None:
        kwargs["reply_to_message_id"] = game.message_id

    try:
        await bot.send_message(**kwargs)
    except TelegramBadRequest:
        kwargs.pop("reply_to_message_id", None)
        await bot.send_message(**kwargs)


async def _sync_quiz_feed_message(bot: Bot, game: GroupGame, *, question_no: int | None) -> None:
    if game.kind != "quiz":
        return

    if game.quiz_feed_message_id is not None:
        try:
            await bot.delete_message(chat_id=game.chat_id, message_id=game.quiz_feed_message_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
        finally:
            await GAME_STORE.set_quiz_feed_message_id(game_id=game.game_id, message_id=None)

    if question_no is None:
        return

    kwargs: dict[str, object] = {
        "chat_id": game.chat_id,
        "text": build_quiz_start_text(question_no=question_no),
        "parse_mode": "HTML",
        "disable_notification": True,
    }
    if game.message_id is not None:
        kwargs["reply_to_message_id"] = game.message_id

    try:
        sent = await bot.send_message(**kwargs)
    except TelegramBadRequest:
        kwargs.pop("reply_to_message_id", None)
        sent = await bot.send_message(**kwargs)

    await GAME_STORE.set_quiz_feed_message_id(game_id=game.game_id, message_id=sent.message_id)


def _build_private_phase_keyboard(game: GroupGame, *, actor_user_id: int) -> InlineKeyboardMarkup | None:
    if game.status != "started":
        return None

    if game.kind == "mafia":
        if game.phase == "night":
            return _build_private_night_action_keyboard(game, actor_user_id=actor_user_id)
        if game.phase == "day_vote":
            return _build_private_day_vote_keyboard(game, actor_user_id=actor_user_id)

    if game.kind == "bunker":
        if game.phase == "bunker_reveal":
            return _build_private_bunker_reveal_keyboard(game, actor_user_id=actor_user_id)
        if game.phase == "bunker_vote":
            return _build_private_bunker_vote_keyboard(game, actor_user_id=actor_user_id)

    if game.kind == "zlobcards":
        if game.phase == "private_answers":
            return _build_private_zlob_submit_keyboard(game, actor_user_id=actor_user_id)

    return None


async def _sync_execution_confirm_message(bot: Bot, game: GroupGame, *, force_new: bool) -> None:
    if game.kind != "mafia" or game.status != "started" or game.phase != "day_execution_confirm":
        return

    text = _render_execution_confirm_prompt(game)
    keyboard = _build_mafia_execution_confirm_buttons(game)
    if keyboard is None:
        return

    message_id = None if force_new else game.execution_confirm_message_id

    if message_id is not None:
        try:
            await bot.edit_message_text(
                chat_id=game.chat_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return
        except TelegramBadRequest as exc:
            error_text = str(exc).lower()
            if "message is not modified" in error_text:
                return
            if "message to edit not found" not in error_text and "can't be edited" not in error_text:
                raise

    kwargs: dict[str, object] = {
        "chat_id": game.chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": keyboard,
    }
    if game.message_id is not None:
        kwargs["reply_to_message_id"] = game.message_id

    try:
        sent = await bot.send_message(**kwargs)
    except TelegramBadRequest:
        kwargs.pop("reply_to_message_id", None)
        sent = await bot.send_message(**kwargs)

    await GAME_STORE.set_execution_confirm_message_id(game_id=game.game_id, message_id=sent.message_id)


async def _notify_mafia_day_vote_private(bot: Bot, game: GroupGame) -> None:
    if game.kind != "mafia" or game.status != "started" or game.phase != "day_vote":
        return

    voted_count = len({voter for voter in game.day_votes if voter in game.alive_player_ids})
    alive_count = len(game.alive_player_ids)

    for user_id in sorted(game.alive_player_ids):
        keyboard = _build_private_day_vote_keyboard(game, actor_user_id=user_id)
        if keyboard is None:
            continue

        try:
            await bot.send_message(
                user_id,
                _render_private_day_vote_text(
                    game,
                    actor_user_id=user_id,
                    voted_count=voted_count,
                    alive_count=alive_count,
                ),
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except TelegramForbiddenError:
            continue


async def _send_bred_question_to_user(bot: Bot, game: GroupGame, user_id: int) -> bool:
    if game.kind != "bredovukha" or game.status != "started" or game.phase != "private_answers":
        return True

    if not game.bred_question_prompt:
        return True

    lines = [
        f"<b>{escape(GAME_DEFINITIONS[game.kind].title)}</b>",
        f"<b>Чат:</b> {escape(game.chat_title or str(game.chat_id))}",
        f"<b>Раунд:</b> {game.round_no}/{game.bred_rounds}",
        f"<b>Категория:</b> {escape(game.bred_current_category or '-')}",
        "<b>Факт с пропуском:</b>",
        escape(game.bred_question_prompt),
        "<i>Ответьте ложью — одно сообщение, без копирования чужих вариантов.</i>",
    ]
    try:
        await bot.send_message(user_id, "\n".join(lines), parse_mode="HTML")
        return True
    except TelegramForbiddenError:
        return False


async def _notify_bred_private_answers(bot: Bot, game: GroupGame) -> int:
    if game.kind != "bredovukha" or game.status != "started" or game.phase != "private_answers":
        return 0

    failed = 0
    for user_id in sorted(game.players.keys()):
        ok = await _send_bred_question_to_user(bot, game, user_id)
        if not ok:
            failed += 1
    return failed


def _render_private_zlob_status_text(game: GroupGame, *, actor_user_id: int) -> str:
    if game.kind != "zlobcards":
        return "Это не «500 Злобных Карт»."
    if actor_user_id not in game.players:
        return "Вы не участник этого лобби."

    hand = list(game.zlob_hands.get(actor_user_id, ()))
    lines = [
        f"<b>{escape(GAME_DEFINITIONS[game.kind].title)}</b>",
        f"<b>Чат:</b> {escape(game.chat_title or str(game.chat_id))}",
        f"<b>Раунд:</b> {max(game.round_no, 1)}/{game.zlob_rounds}",
        f"<b>Тема:</b> {escape(_zlob_category_label(game))}",
        f"<b>Цель по очкам:</b> {game.zlob_target_score}",
    ]

    if game.zlob_black_text:
        lines.extend(
            [
                "",
                f"<b>Чёрная карточка ({max(1, int(game.zlob_black_slots))}):</b>",
                escape(game.zlob_black_text),
            ]
        )

    lines.append("")
    lines.append("<b>Ваша рука:</b>")
    if not hand:
        lines.append("<i>Карты закончились. Дождитесь следующего раунда.</i>")
    else:
        for index, card in enumerate(hand, start=1):
            lines.append(f"{index}. {escape(card)}")

    if game.phase == "private_answers":
        submission = game.zlob_submissions.get(actor_user_id)
        lines.append("")
        if submission:
            lines.append(f"<i>Вы уже выбрали: {escape(' + '.join(submission))}</i>")
            lines.append("<i>Можно выбрать другой вариант до конца этапа.</i>")
        else:
            lines.append("<i>Выберите карточку(и) кнопками ниже.</i>")
    elif game.phase == "public_vote":
        lines.append("")
        lines.append("<i>Идёт голосование в группе. На свою карточку голосовать нельзя.</i>")
    return "\n".join(lines)


async def _send_zlob_hand_to_user(bot: Bot, game: GroupGame, user_id: int) -> bool:
    if game.kind != "zlobcards":
        return True
    if user_id not in game.players:
        return True

    text = _render_private_zlob_status_text(game, actor_user_id=user_id)
    keyboard = _build_private_phase_keyboard(game, actor_user_id=user_id)
    try:
        await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=keyboard)
        return True
    except TelegramForbiddenError:
        return False


async def _notify_zlob_private_hands(bot: Bot, game: GroupGame) -> int:
    if game.kind != "zlobcards" or game.status != "started" or game.phase != "private_answers":
        return 0
    failed = 0
    for user_id in sorted(game.players.keys()):
        ok = await _send_zlob_hand_to_user(bot, game, user_id)
        if not ok:
            failed += 1
    return failed


def _render_private_bunker_status_text(game: GroupGame, *, actor_user_id: int) -> str:
    if game.kind != "bunker":
        return "Это не «Бункер»."
    if actor_user_id not in game.players:
        return "Вы не участник этого лобби."

    card = game.bunker_cards.get(actor_user_id)
    if card is None:
        return "Карточка недоступна."

    lines = [
        f"<b>{escape(GAME_DEFINITIONS[game.kind].title)}</b>",
        f"<b>Чат:</b> {escape(game.chat_title or str(game.chat_id))}",
        f"<b>Раунд:</b> {max(game.round_no, 1)}",
        f"<b>Катастрофа:</b> {escape(game.bunker_catastrophe or '-')}",
        f"<b>Условия бункера:</b> {escape(game.bunker_condition or '-')}",
        f"<b>Мест в бункере:</b> {game.bunker_seats}",
        "",
        "<b>Ваша карточка:</b>",
        _render_bunker_full_card(card),
    ]

    if game.phase == "bunker_reveal":
        if game.bunker_current_actor_user_id == actor_user_id:
            lines.append("")
            lines.append("<i>Ваш ход: выберите характеристику для раскрытия в группе.</i>")
        else:
            actor_label = "-"
            if game.bunker_current_actor_user_id is not None:
                actor_label = game.players.get(game.bunker_current_actor_user_id, f"user:{game.bunker_current_actor_user_id}")
            lines.append("")
            lines.append(f"<i>Сейчас раскрывается: {escape(actor_label)}.</i>")
    elif game.phase == "bunker_vote":
        voted_count = len({voter for voter in game.bunker_votes if voter in game.alive_player_ids})
        lines.append("")
        lines.append(f"<i>Идёт голосование. Прогресс: {voted_count}/{len(game.alive_player_ids)}.</i>")
        current_target = game.bunker_votes.get(actor_user_id)
        if current_target is not None:
            target_label = game.players.get(current_target, f"user:{current_target}")
            lines.append(f"<b>Ваш голос:</b> {escape(target_label)}")
    return "\n".join(lines)


async def _send_bunker_card_to_user(bot: Bot, game: GroupGame, user_id: int) -> bool:
    if game.kind != "bunker":
        return True
    if user_id not in game.players:
        return True

    text = _render_private_bunker_status_text(game, actor_user_id=user_id)
    keyboard = _build_private_phase_keyboard(game, actor_user_id=user_id)
    try:
        await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=keyboard)
        return True
    except TelegramForbiddenError:
        return False


async def _notify_bunker_reveal_turn(bot: Bot, game: GroupGame) -> bool:
    if game.kind != "bunker" or game.status != "started" or game.phase != "bunker_reveal":
        return True
    actor_user_id = game.bunker_current_actor_user_id
    if actor_user_id is None:
        return True
    return await _send_bunker_card_to_user(bot, game, actor_user_id)


async def _notify_bunker_vote_private(bot: Bot, game: GroupGame) -> int:
    if game.kind != "bunker" or game.status != "started" or game.phase != "bunker_vote":
        return 0

    failed = 0
    for user_id in sorted(game.alive_player_ids):
        text = _render_private_bunker_status_text(game, actor_user_id=user_id)
        keyboard = _build_private_phase_keyboard(game, actor_user_id=user_id)
        try:
            await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=keyboard)
        except TelegramForbiddenError:
            failed += 1
    return failed


def _render_whoami_private_view(game: GroupGame, *, actor_user_id: int) -> str:
    lines = [
        f"<b>{escape(GAME_DEFINITIONS[game.kind].title)}</b>",
        f"<b>Чат:</b> {escape(game.chat_title or str(game.chat_id))}",
        f"<b>Категория:</b> {escape(_whoami_category_label(game))}",
        "<i>Свою карточку не смотрите: бот показывает только чужие карточки.</i>",
        "",
        "<b>Карточки за столом:</b>",
    ]

    for user_id, label in sorted(game.players.items(), key=lambda item: item[1].lower()):
        if user_id == actor_user_id:
            lines.append(f"- {escape(label)} — <code>???</code>")
            continue
        identity = game.roles.get(user_id, "-")
        lines.append(f"- {escape(label)} — <code>{escape(identity)}</code>")

    actor_label = game.players.get(game.whoami_current_actor_user_id or 0, "-")
    lines.append("")
    lines.append(f"<b>Ходит:</b> {escape(actor_label)}")
    if game.phase == "whoami_answer":
        lines.append(f"<b>Активный вопрос:</b> {escape(game.whoami_pending_question_text or '-')}")
        lines.append("<i>Стол должен ответить «да / нет / не знаю / неважно» в группе или на сайте.</i>")
    else:
        lines.append("<i>Если сейчас ваш ход, задайте вопрос сообщением в группу с вопросительным знаком или попробуйте угадать себя.</i>")
    return "\n".join(lines)


async def _get_bot_username(bot: Bot) -> str:
    global _BOT_USERNAME_CACHE
    if _BOT_USERNAME_CACHE:
        return _BOT_USERNAME_CACHE

    me = await bot.get_me()
    _BOT_USERNAME_CACHE = me.username or ""
    return _BOT_USERNAME_CACHE


def _cancel_phase_timer(game_id: str) -> None:
    task = _GAME_PHASE_TASKS.pop(game_id, None)
    if task is not None and not task.done():
        task.cancel()


def _schedule_phase_timer(bot: Bot, game: GroupGame, chat_settings: ChatSettings) -> None:
    _cancel_phase_timer(game.game_id)

    if game.status != "started":
        return

    if game.kind not in {"mafia", "zlobcards"}:
        return

    expected_kind = game.kind
    expected_phase = game.phase
    expected_round_no = game.round_no
    expected_phase_started_at = game.phase_started_at

    async def _is_stale_timer() -> bool:
        current_game = await GAME_STORE.get_game(game.game_id)
        if current_game is None:
            return True
        return not (
            current_game.kind == expected_kind
            and current_game.status == "started"
            and current_game.phase == expected_phase
            and current_game.round_no == expected_round_no
            and current_game.phase_started_at == expected_phase_started_at
        )

    if game.kind == "zlobcards":
        if game.phase == "private_answers":
            delay = max(5, _ZLOBCARDS_PRIVATE_SECONDS)

            async def _zlob_private_job() -> None:
                try:
                    await asyncio.sleep(delay)
                    if await _is_stale_timer():
                        return
                    _, error = await _open_zlob_vote_phase(
                        bot,
                        game.game_id,
                        chat_settings,
                        force=True,
                        triggered_by_auto=True,
                    )
                    if error is not None:
                        logger.debug("Zlobcards private timer open skipped", extra={"game_id": game.game_id, "error": error})
                except Exception:
                    logger.exception("Failed to advance zlobcards private timer", extra={"game_id": game.game_id})

            _GAME_PHASE_TASKS[game.game_id] = asyncio.create_task(_zlob_private_job())
            return

        if game.phase == "public_vote":
            delay = max(5, _ZLOBCARDS_VOTE_SECONDS)

            async def _zlob_vote_job() -> None:
                try:
                    await asyncio.sleep(delay)
                    if await _is_stale_timer():
                        return
                    _, error = await _resolve_zlob_round(
                        bot,
                        game.game_id,
                        chat_settings,
                        force=True,
                        triggered_by_auto=True,
                    )
                    if error is not None:
                        logger.debug("Zlobcards vote timer resolve skipped", extra={"game_id": game.game_id, "error": error})
                except Exception:
                    logger.exception("Failed to advance zlobcards vote timer", extra={"game_id": game.game_id})

            _GAME_PHASE_TASKS[game.game_id] = asyncio.create_task(_zlob_vote_job())
            return

        return

    if game.phase == "night":
        delay = max(5, chat_settings.mafia_night_seconds)

        async def _night_job() -> None:
            try:
                await asyncio.sleep(delay)
                if await _is_stale_timer():
                    return
                await _advance_mafia_night(bot, game.game_id, chat_settings, triggered_by_timer=True)
            except Exception:
                logger.exception("Failed to advance mafia night timer", extra={"game_id": game.game_id})

        _GAME_PHASE_TASKS[game.game_id] = asyncio.create_task(_night_job())
        return

    if game.phase == "day_discussion":
        delay = max(5, chat_settings.mafia_day_seconds)

        async def _discussion_job() -> None:
            try:
                await asyncio.sleep(delay)
                if await _is_stale_timer():
                    return
                await _open_mafia_day_vote(bot, game.game_id, chat_settings, triggered_by_timer=True)
            except Exception:
                logger.exception("Failed to advance mafia day discussion timer", extra={"game_id": game.game_id})

        _GAME_PHASE_TASKS[game.game_id] = asyncio.create_task(_discussion_job())
        return

    if game.phase == "day_vote":
        delay = max(5, chat_settings.mafia_vote_seconds)

        async def _vote_job() -> None:
            try:
                await asyncio.sleep(delay)
                if await _is_stale_timer():
                    return
                await _resolve_mafia_day_vote(bot, game.game_id, chat_settings, triggered_by_timer=True)
            except Exception:
                logger.exception("Failed to advance mafia vote timer", extra={"game_id": game.game_id})

        _GAME_PHASE_TASKS[game.game_id] = asyncio.create_task(_vote_job())
        return

    if game.phase == "day_execution_confirm":
        delay = max(5, chat_settings.mafia_vote_seconds)

        async def _confirm_job() -> None:
            try:
                await asyncio.sleep(delay)
                if await _is_stale_timer():
                    return
                await _resolve_mafia_execution_confirm(bot, game.game_id, chat_settings, triggered_by_timer=True)
            except Exception:
                logger.exception("Failed to advance mafia execution confirm timer", extra={"game_id": game.game_id})

        _GAME_PHASE_TASKS[game.game_id] = asyncio.create_task(_confirm_job())


async def restore_phase_timers(bot: Bot, session_factory: Any) -> None:
    """Восстанавливает таймеры фаз для всех активных игр после перезапуска бота.

    Для каждой запущенной игры вычисляет сколько времени уже прошло с начала фазы
    и запускает таймер с оставшейся задержкой (минимум 5 секунд).
    """
    from datetime import datetime, timezone

    from sqlalchemy.ext.asyncio import AsyncSession

    from selara.core.chat_settings import default_chat_settings
    from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository

    active_games = await GAME_STORE.list_active_games()
    timer_games = [g for g in active_games if g.status == "started" and g.kind in {"mafia", "zlobcards"}]

    if not timer_games:
        return

    logger.info("Restoring phase timers for %d active game(s) after restart", len(timer_games))

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        settings_obj = None
        try:
            from selara.core.config import get_settings
            settings_obj = get_settings()
        except Exception:
            pass

        for game in timer_games:
            try:
                chat_settings = await repo.get_chat_settings(chat_id=game.chat_id)
                if chat_settings is None:
                    if settings_obj is not None:
                        chat_settings = default_chat_settings(settings_obj)
                    else:
                        continue

                now = datetime.now(timezone.utc)
                elapsed = 0.0
                if game.phase_started_at is not None:
                    elapsed = max(0.0, (now - game.phase_started_at).total_seconds())

                if game.kind == "zlobcards":
                    if game.phase == "private_answers":
                        full_delay = max(5, _ZLOBCARDS_PRIVATE_SECONDS)
                    elif game.phase == "public_vote":
                        full_delay = max(5, _ZLOBCARDS_VOTE_SECONDS)
                    else:
                        continue
                elif game.kind == "mafia":
                    if game.phase == "night":
                        full_delay = max(5, chat_settings.mafia_night_seconds)
                    elif game.phase == "day_discussion":
                        full_delay = max(5, chat_settings.mafia_day_seconds)
                    elif game.phase == "day_vote":
                        full_delay = max(5, chat_settings.mafia_vote_seconds)
                    elif game.phase == "day_execution_confirm":
                        full_delay = max(5, chat_settings.mafia_vote_seconds)
                    else:
                        continue
                else:
                    continue

                remaining = max(5.0, full_delay - elapsed)
                logger.info(
                    "Restoring timer for game %s (kind=%s phase=%s elapsed=%.0fs remaining=%.0fs)",
                    game.game_id,
                    game.kind,
                    game.phase,
                    elapsed,
                    remaining,
                )

                # Создаём временную версию игры с уменьшенной задержкой через патч настроек
                # Проще всего — вызвать _schedule_phase_timer с модифицированными settings
                _cancel_phase_timer(game.game_id)
                _schedule_phase_timer_with_remaining(bot, game, chat_settings, remaining)
            except Exception:
                logger.exception("Failed to restore timer for game %s", game.game_id)


def _schedule_phase_timer_with_remaining(
    bot: Bot, game: GroupGame, chat_settings: ChatSettings, remaining_seconds: float
) -> None:
    """Запускает таймер фазы с конкретной оставшейся задержкой (используется при восстановлении)."""
    _cancel_phase_timer(game.game_id)

    if game.status != "started":
        return

    expected_kind = game.kind
    expected_phase = game.phase
    expected_round_no = game.round_no
    expected_phase_started_at = game.phase_started_at

    async def _is_stale_timer() -> bool:
        current_game = await GAME_STORE.get_game(game.game_id)
        if current_game is None:
            return True
        return not (
            current_game.kind == expected_kind
            and current_game.status == "started"
            and current_game.phase == expected_phase
            and current_game.round_no == expected_round_no
            and current_game.phase_started_at == expected_phase_started_at
        )

    delay = remaining_seconds

    if game.kind == "zlobcards" and game.phase == "private_answers":
        async def _job() -> None:
            try:
                await asyncio.sleep(delay)
                if await _is_stale_timer():
                    return
                _, error = await _open_zlob_vote_phase(bot, game.game_id, chat_settings, force=True, triggered_by_auto=True)
                if error is not None:
                    logger.debug("Restored zlobcards private timer skipped", extra={"game_id": game.game_id, "error": error})
            except Exception:
                logger.exception("Restored zlobcards private timer failed", extra={"game_id": game.game_id})

    elif game.kind == "zlobcards" and game.phase == "public_vote":
        async def _job() -> None:
            try:
                await asyncio.sleep(delay)
                if await _is_stale_timer():
                    return
                _, error = await _resolve_zlob_round(bot, game.game_id, chat_settings, force=True, triggered_by_auto=True)
                if error is not None:
                    logger.debug("Restored zlobcards vote timer skipped", extra={"game_id": game.game_id, "error": error})
            except Exception:
                logger.exception("Restored zlobcards vote timer failed", extra={"game_id": game.game_id})

    elif game.kind == "mafia" and game.phase == "night":
        async def _job() -> None:
            try:
                await asyncio.sleep(delay)
                if await _is_stale_timer():
                    return
                await _advance_mafia_night(bot, game.game_id, chat_settings, triggered_by_timer=True)
            except Exception:
                logger.exception("Restored mafia night timer failed", extra={"game_id": game.game_id})

    elif game.kind == "mafia" and game.phase == "day_discussion":
        async def _job() -> None:
            try:
                await asyncio.sleep(delay)
                if await _is_stale_timer():
                    return
                await _open_mafia_day_vote(bot, game.game_id, chat_settings, triggered_by_timer=True)
            except Exception:
                logger.exception("Restored mafia day discussion timer failed", extra={"game_id": game.game_id})

    elif game.kind == "mafia" and game.phase == "day_vote":
        async def _job() -> None:
            try:
                await asyncio.sleep(delay)
                if await _is_stale_timer():
                    return
                await _resolve_mafia_day_vote(bot, game.game_id, chat_settings, triggered_by_timer=True)
            except Exception:
                logger.exception("Restored mafia day vote timer failed", extra={"game_id": game.game_id})

    elif game.kind == "mafia" and game.phase == "day_execution_confirm":
        async def _job() -> None:
            try:
                await asyncio.sleep(delay)
                if await _is_stale_timer():
                    return
                await _resolve_mafia_execution_confirm(bot, game.game_id, chat_settings, triggered_by_timer=True)
            except Exception:
                logger.exception("Restored mafia execution confirm timer failed", extra={"game_id": game.game_id})

    else:
        return

    _GAME_PHASE_TASKS[game.game_id] = asyncio.create_task(_job())


async def _send_role_to_user(bot: Bot, game: GroupGame, user_id: int) -> bool:
    label = game.players.get(user_id, f"user:{user_id}")
    if game.kind == "bunker":
        card = game.bunker_cards.get(user_id)
        if card is None:
            return True
        lines = [
            f"<b>{escape(GAME_DEFINITIONS[game.kind].title)}</b>",
            f"<b>Чат:</b> {escape(game.chat_title or str(game.chat_id))}",
            f"<b>Вы:</b> {escape(label)}",
            f"<b>Катастрофа:</b> {escape(game.bunker_catastrophe or '-')}",
            f"<b>Условия бункера:</b> {escape(game.bunker_condition or '-')}",
            f"<b>Мест в бункере:</b> {game.bunker_seats}",
            "",
            "<b>Ваша карточка:</b>",
            _render_bunker_full_card(card),
        ]
        if game.phase == "bunker_reveal":
            if game.bunker_current_actor_user_id == user_id:
                lines.append("")
                lines.append("<i>Ваш ход: раскройте одну характеристику кнопками ниже.</i>")
            else:
                actor_label = "-"
                if game.bunker_current_actor_user_id is not None:
                    actor_label = game.players.get(game.bunker_current_actor_user_id, f"user:{game.bunker_current_actor_user_id}")
                lines.append("")
                lines.append(f"<i>Сейчас раскрывается: {escape(actor_label)}.</i>")
        elif game.phase == "bunker_vote":
            lines.append("")
            lines.append("<i>Идёт голосование: выберите кандидата на выбывание.</i>")

        try:
            await bot.send_message(
                user_id,
                "\n".join(lines),
                parse_mode="HTML",
                reply_markup=_build_private_phase_keyboard(game, actor_user_id=user_id),
            )
            return True
        except TelegramForbiddenError:
            return False

    if game.kind == "whoami":
        if user_id not in game.players:
            return True
        try:
            await bot.send_message(
                user_id,
                _render_whoami_private_view(game, actor_user_id=user_id),
                parse_mode="HTML",
            )
            return True
        except TelegramForbiddenError:
            return False

    if game.kind == "zlobcards":
        return await _send_zlob_hand_to_user(bot, game, user_id)

    role = game.roles.get(user_id)
    if role is None:
        return True

    lines = [
        f"<b>{escape(GAME_DEFINITIONS[game.kind].title)}</b>",
        f"<b>Чат:</b> {escape(game.chat_title or str(game.chat_id))}",
        f"<b>Вы:</b> {escape(label)}",
        f"<b>Роль:</b> <code>{escape(role)}</code>",
    ]

    if game.kind == "spy":
        lines.append(f"<b>Тема:</b> {escape(_spy_category_label(game))}")
        if role == "Шпион":
            lines.append("<b>Локация:</b> <code>неизвестна</code>")
            lines.append("<i>Ваша цель — вычислить локацию, не выдав себя.</i>")
        else:
            lines.append(f"<b>Локация:</b> <code>{escape(game.spy_location or '-')}</code>")
            lines.append("<i>Найдите шпиона через вопросы и ответы.</i>")

    if game.kind == "mafia":
        lines.append("<i>Идёт мафия. Следите за фазами и анонсами ведущего в групповом чате.</i>")
        if game.phase == "night":
            lines.append("<i>Сейчас ночь: доступно действие вашей роли.</i>")
        elif game.phase == "day_vote":
            lines.append("<i>Сейчас дневное голосование: бот пришлёт отдельную карточку для голоса в ЛС.</i>")

    try:
        await bot.send_message(
            user_id,
            "\n".join(lines),
            parse_mode="HTML",
        )
        return True
    except TelegramForbiddenError:
        return False


async def _send_roles_to_private(bot: Bot, game: GroupGame) -> int:
    failed = 0
    for user_id in game.players:
        ok = await _send_role_to_user(bot, game, user_id)
        if not ok:
            failed += 1
    return failed


async def _notify_mafia_night_actions(bot: Bot, game: GroupGame) -> None:
    if game.kind != "mafia" or game.status != "started" or game.phase != "night":
        return

    for user_id in sorted(game.alive_player_ids):
        keyboard = _build_private_night_action_keyboard(game, actor_user_id=user_id)
        if keyboard is None:
            continue

        role = game.roles.get(user_id, "-")
        first_pick_note = ""
        if role == MAFIA_ROLE_JOURNALIST and user_id in game.journalist_first_pick:
            first_pick_note = "\n<b>Шаг 2/2:</b> выберите второго игрока для сравнения."
        if role == MAFIA_ROLE_VETERAN:
            first_pick_note = "\n<i>Для боевой готовности выберите себя. Это одноразово.</i>"
        if role == MAFIA_ROLE_CHILD and user_id not in game.child_revealed:
            first_pick_note = "\n<i>Можно раскрыться: выберите себя.</i>"

        try:
            await bot.send_message(
                user_id,
                (
                    f"<b>Ночь {game.round_no}</b>\n"
                    f"<b>Роль:</b> <code>{escape(role)}</code>\n"
                    "Выберите цель для ночного действия. "
                    "Можно менять выбор до завершения ночи."
                    f"{first_pick_note}"
                ),
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except TelegramForbiddenError:
            continue


async def _send_night_private_reports(bot: Bot, game: GroupGame, resolution: NightResolution) -> None:
    sent_to: set[int] = set()
    for user_id, report_text in resolution.private_reports:
        if user_id in sent_to and not report_text:
            continue
        try:
            await bot.send_message(
                user_id,
                report_text,
                parse_mode="HTML",
            )
            sent_to.add(user_id)
        except TelegramForbiddenError:
            continue

    # Backward-compatible fallback for games started before расширенного резолва.
    if resolution.sheriff_checked_user_id is None:
        return

    fallback_user_id = next(
        (user_id for user_id, role in game.roles.items() if role in {"Шериф", "Комиссар"}),
        None,
    )
    if fallback_user_id is None or fallback_user_id in sent_to:
        return

    checked_label = resolution.sheriff_checked_user_label or f"user:{resolution.sheriff_checked_user_id}"
    verdict = "мафия" if resolution.sheriff_checked_is_mafia else "не мафия"
    try:
        await bot.send_message(
            fallback_user_id,
            (
                f"<b>Отчёт комиссара (ночь {max(game.round_no, 1)})</b>\n"
                f"Проверка: <b>{escape(checked_label)}</b>\n"
                f"Результат: <code>{verdict}</code>"
            ),
            parse_mode="HTML",
        )
    except TelegramForbiddenError:
        return


async def _advance_mafia_night(
    bot: Bot,
    game_id: str,
    chat_settings: ChatSettings,
    economy_repo=None,
    *,
    triggered_by_timer: bool,
) -> None:
    game, resolution, error = await GAME_STORE.mafia_resolve_night(game_id=game_id)
    if game is None or resolution is None or error:
        return

    note_parts: list[str] = []
    event_parts: list[str] = [f"<b>Ведущий:</b> Ночь {game.round_no} завершена."]

    if resolution.killed_user_ids:
        elimination_texts: list[str] = []
        for user_id in resolution.killed_user_ids:
            label = game.players.get(user_id, f"user:{user_id}")
            role = game.roles.get(user_id)
            elimination_texts.append(_format_elimination_event(game, user_id=user_id, label=label, role=role))
        night_text = "; ".join(elimination_texts)
    elif resolution.tie_on_mafia_vote:
        night_text = "мафия не договорилась, никто не выбыл"
    else:
        night_text = "никто не выбыл"

    note_parts.append(f"<b>Итог ночи:</b> {night_text}.")
    event_parts.append(f"<b>Итог ночи:</b> {night_text}.")

    for note in resolution.public_notes:
        safe_note = escape(note)
        note_parts.append(f"<b>Событие:</b> {safe_note}")
        event_parts.append(f"<b>Событие:</b> {safe_note}")

    if resolution.winner_text:
        note_parts.append(f"<b>Победа:</b> {escape(resolution.winner_text)}")
        event_parts.append(f"<b>Победа:</b> {escape(resolution.winner_text)}")

    await _send_night_private_reports(bot, game, resolution)

    if game.status == "finished":
        reward_line = await _grant_game_rewards_if_needed(game, economy_repo=economy_repo, chat_settings=chat_settings)
        if reward_line:
            note_parts.append(reward_line)
            event_parts.append(reward_line)
        event_parts.append(_render_roles_reveal(game))
        _cancel_phase_timer(game.game_id)
        await _safe_edit_or_send_game_board(
            bot,
            game,
            chat_settings,
            note="\n".join(note_parts),
            include_reveal=True,
        )
        await _send_game_feed_event(bot, game, text="\n".join(event_parts))
        return

    note_parts.append("<b>День начался:</b> обсуждение перед голосованием.")
    event_parts.append(
        f"<b>День {game.round_no} начался:</b> обсуждение запущено на {_format_duration(chat_settings.mafia_day_seconds)}."
    )

    await _safe_edit_or_send_game_board(bot, game, chat_settings, note="\n".join(note_parts))
    await _send_game_feed_event(bot, game, text="\n".join(event_parts))
    _schedule_phase_timer(bot, game, chat_settings)

    if triggered_by_timer:
        logger.debug("Mafia night finished by timer", extra={"game_id": game.game_id})


async def _open_mafia_day_vote(
    bot: Bot,
    game_id: str,
    chat_settings: ChatSettings,
    *,
    triggered_by_timer: bool,
) -> None:
    game, error = await GAME_STORE.mafia_open_day_vote(game_id=game_id)
    if game is None or error:
        return

    note = "<b>Голосование открыто.</b> Проголосовать можно на доске или в ЛС-карточке от бота."
    await _safe_edit_or_send_game_board(bot, game, chat_settings, note=note)
    await _send_game_feed_event(
        bot,
        game,
        text=(
            f"<b>Ведущий:</b> Открыто дневное голосование (раунд {game.round_no}).\n"
            f"У вас {_format_duration(chat_settings.mafia_vote_seconds)}. Голосуйте на доске или в ЛС."
        ),
    )
    await _notify_mafia_day_vote_private(bot, game)
    _schedule_phase_timer(bot, game, chat_settings)

    if triggered_by_timer:
        logger.debug("Mafia day vote opened by timer", extra={"game_id": game.game_id})


async def _resolve_mafia_day_vote(
    bot: Bot,
    game_id: str,
    chat_settings: ChatSettings,
    economy_repo=None,
    *,
    triggered_by_timer: bool,
) -> None:
    game, resolution, error = await GAME_STORE.mafia_resolve_day_vote(game_id=game_id)
    if game is None or resolution is None or error:
        return

    protocol_text = _format_day_vote_protocol(game, resolution)

    note_parts: list[str] = []
    event_parts: list[str] = ["<b>Ведущий:</b> Дневное голосование завершено.", protocol_text]

    for note in resolution.public_notes:
        safe_note = escape(note)
        note_parts.append(f"<b>Событие:</b> {safe_note}")
        event_parts.append(f"<b>Событие:</b> {safe_note}")

    if resolution.opened_execution_confirm and resolution.candidate_user_id is not None:
        candidate_label = resolution.candidate_user_label or f"user:{resolution.candidate_user_id}"
        note_parts.append(f"<b>Кандидат на казнь:</b> {escape(candidate_label)}")
        note_parts.append("<b>Следующий этап:</b> подтверждение казни (отдельная карточка ниже).")

        event_parts.append(
            f"<b>Кандидат:</b> {escape(candidate_label)}. "
            f"Открыто подтверждение казни на {chat_settings.mafia_vote_seconds} сек."
        )

        await _safe_edit_or_send_game_board(bot, game, chat_settings, note="\n".join(note_parts))
        await _send_game_feed_event(bot, game, text="\n".join(event_parts))
        await _sync_execution_confirm_message(bot, game, force_new=True)
        _schedule_phase_timer(bot, game, chat_settings)
        return

    if resolution.candidate_user_id is None:
        if resolution.tie:
            note_parts.append("<b>Итог голосования:</b> ничья, кандидат не определён.")
            event_parts.append("<b>Итог:</b> ничья, кандидат не определён.")
        else:
            note_parts.append("<b>Итог голосования:</b> голосов недостаточно, кандидат не определён.")
            event_parts.append("<b>Итог:</b> голосов недостаточно, кандидат не определён.")

    if resolution.winner_text:
        note_parts.append(f"<b>Победа:</b> {escape(resolution.winner_text)}")
        event_parts.append(f"<b>Победа:</b> {escape(resolution.winner_text)}")

    if game.status == "finished":
        reward_line = await _grant_game_rewards_if_needed(game, economy_repo=economy_repo, chat_settings=chat_settings)
        if reward_line:
            note_parts.append(reward_line)
            event_parts.append(reward_line)
        event_parts.append(_render_roles_reveal(game))
        _cancel_phase_timer(game.game_id)
        await _safe_edit_or_send_game_board(
            bot,
            game,
            chat_settings,
            note="\n".join(note_parts),
            include_reveal=True,
        )
        await _send_game_feed_event(bot, game, text="\n".join(event_parts))
        return

    note_parts.append("<b>Ночь началась:</b> роли с ночным действием получили ЛС-уведомления.")
    event_parts.append(f"<b>Ночь {game.round_no} началась.</b> Роли с действиями получили ЛС-уведомления.")

    await _safe_edit_or_send_game_board(bot, game, chat_settings, note="\n".join(note_parts))
    await _send_game_feed_event(bot, game, text="\n".join(event_parts))
    _schedule_phase_timer(bot, game, chat_settings)
    await _notify_mafia_night_actions(bot, game)

    if triggered_by_timer:
        logger.debug("Mafia day vote resolved by timer", extra={"game_id": game.game_id})


async def _resolve_mafia_execution_confirm(
    bot: Bot,
    game_id: str,
    chat_settings: ChatSettings,
    economy_repo=None,
    *,
    triggered_by_timer: bool,
) -> None:
    game_before_resolve = await GAME_STORE.get_game(game_id)
    confirm_message_id = game_before_resolve.execution_confirm_message_id if game_before_resolve else None

    game, resolution, error = await GAME_STORE.mafia_resolve_execution_confirm(game_id=game_id)
    if game is None or resolution is None or error:
        return

    if confirm_message_id is not None:
        try:
            await bot.edit_message_text(
                chat_id=game.chat_id,
                message_id=confirm_message_id,
                text=_render_execution_confirm_result(game, resolution),
                parse_mode="HTML",
            )
        except TelegramBadRequest as exc:
            error_text = str(exc).lower()
            if (
                "message is not modified" not in error_text
                and "message to edit not found" not in error_text
                and "can't be edited" not in error_text
            ):
                raise

    protocol_text = _format_execution_confirm_protocol(game, resolution)
    note_parts: list[str] = [f"<b>Подтверждение:</b> да={resolution.yes_count}, нет={resolution.no_count}."]
    event_parts: list[str] = [
        f"<b>Ведущий:</b> Подтверждение казни завершено.",
        f"<b>Счёт:</b> да={resolution.yes_count}, нет={resolution.no_count}.",
        protocol_text,
    ]

    for note in resolution.public_notes:
        safe_note = escape(note)
        note_parts.append(f"<b>Событие:</b> {safe_note}")
        event_parts.append(f"<b>Событие:</b> {safe_note}")

    if resolution.passed and resolution.executed_user_id is not None:
        executed_label = resolution.executed_user_label or f"user:{resolution.executed_user_id}"
        note_parts.append(f"<b>Казнь подтверждена:</b> {_format_elimination_event(game, user_id=resolution.executed_user_id, label=executed_label, role=resolution.executed_user_role)}.")
        event_parts.append(f"<b>Итог:</b> казнь подтверждена, {_format_elimination_event(game, user_id=resolution.executed_user_id, label=executed_label, role=resolution.executed_user_role)}.")
    else:
        note_parts.append("<b>Казнь не подтверждена:</b> никто не выбыл.")
        event_parts.append("<b>Итог:</b> казнь не подтверждена, никто не выбыл.")

    if resolution.winner_text:
        note_parts.append(f"<b>Победа:</b> {escape(resolution.winner_text)}")
        event_parts.append(f"<b>Победа:</b> {escape(resolution.winner_text)}")

    if game.status == "finished":
        reward_line = await _grant_game_rewards_if_needed(game, economy_repo=economy_repo, chat_settings=chat_settings)
        if reward_line:
            note_parts.append(reward_line)
            event_parts.append(reward_line)
        event_parts.append(_render_roles_reveal(game))
        _cancel_phase_timer(game.game_id)
        await _safe_edit_or_send_game_board(
            bot,
            game,
            chat_settings,
            note="\n".join(note_parts),
            include_reveal=True,
        )
        await _send_game_feed_event(bot, game, text="\n".join(event_parts))
        return

    note_parts.append("<b>Ночь началась:</b> роли с ночным действием получили ЛС-уведомления.")
    event_parts.append(f"<b>Ночь {game.round_no} началась.</b> Роли с действиями получили ЛС-уведомления.")

    await _safe_edit_or_send_game_board(bot, game, chat_settings, note="\n".join(note_parts))
    await _send_game_feed_event(bot, game, text="\n".join(event_parts))
    _schedule_phase_timer(bot, game, chat_settings)
    await _notify_mafia_night_actions(bot, game)

    if triggered_by_timer:
        logger.debug("Mafia execution confirm resolved by timer", extra={"game_id": game.game_id})


async def _resolve_quiz_round(
    bot: Bot,
    game_id: str,
    chat_settings: ChatSettings,
    economy_repo=None,
    *,
    force: bool,
    triggered_by_auto: bool,
) -> tuple[GroupGame | None, str | None]:
    game, resolution, error = await GAME_STORE.quiz_resolve_round(game_id=game_id, force=force)
    if game is None:
        return None, "Игра не найдена"
    if error:
        return game, error
    if resolution is None:
        return game, "Не удалось обработать раунд"

    round_text = _format_quiz_round_resolution(game, resolution)

    if resolution.finished:
        reward_line = await _grant_game_rewards_if_needed(game, economy_repo=economy_repo, chat_settings=chat_settings)
        note = round_text
        if reward_line:
            note = f"{note}\n{reward_line}"
        await _safe_edit_or_send_game_board(bot, game, chat_settings, note=note)
        await _sync_quiz_feed_message(bot, game, question_no=None)
    else:
        next_question_human = (resolution.next_question_index or 0) + 1
        note = f"{round_text}\n<b>Открыт вопрос {next_question_human}.</b>"
        await _safe_edit_or_send_game_board(bot, game, chat_settings, note=note)
        await _sync_quiz_feed_message(bot, game, question_no=next_question_human)

    if triggered_by_auto:
        logger.debug("Quiz round resolved automatically", extra={"game_id": game.game_id, "question_index": resolution.question_index})

    return game, None


async def _resolve_bred_round(
    bot: Bot,
    game_id: str,
    chat_settings: ChatSettings,
    economy_repo=None,
    *,
    force: bool,
    triggered_by_auto: bool,
) -> tuple[GroupGame | None, str | None]:
    game, resolution, error = await GAME_STORE.bred_resolve_round(game_id=game_id, force=force)
    if game is None:
        return None, "Игра не найдена"
    if error:
        return game, error
    if resolution is None:
        return game, "Не удалось обработать голосование"

    round_text = _format_bred_round_resolution(game, resolution)
    if resolution.finished:
        reward_line = await _grant_game_rewards_if_needed(
            game,
            economy_repo=economy_repo,
            chat_settings=chat_settings,
        )
        note = round_text
        if reward_line:
            note = f"{note}\n{reward_line}"

        await _safe_edit_or_send_game_board(bot, game, chat_settings, note=note)
        await _send_game_feed_event(
            bot,
            game,
            text=f"<b>Ведущий:</b> Игра «Бредовуха» завершена.\n{round_text}" + (f"\n{reward_line}" if reward_line else ""),
        )
    else:
        await _safe_edit_or_send_game_board(bot, game, chat_settings, note=round_text)

    if triggered_by_auto:
        logger.debug("Bredovukha resolved automatically", extra={"game_id": game.game_id})

    return game, None


async def _open_zlob_vote_phase(
    bot: Bot,
    game_id: str,
    chat_settings: ChatSettings,
    *,
    force: bool,
    triggered_by_auto: bool,
) -> tuple[GroupGame | None, str | None]:
    game, error = await GAME_STORE.zlob_open_vote(game_id=game_id, force=force)
    if game is None:
        return None, "Игра не найдена"
    if error:
        return game, error

    await _safe_edit_or_send_game_board(
        bot,
        game,
        chat_settings,
        note="<b>Этап:</b> приватный выбор завершён, открыто голосование.",
    )
    _schedule_phase_timer(bot, game, chat_settings)
    if triggered_by_auto:
        logger.debug("Zlobcards vote opened automatically", extra={"game_id": game.game_id})
    return game, None


async def _resolve_zlob_round(
    bot: Bot,
    game_id: str,
    chat_settings: ChatSettings,
    economy_repo=None,
    *,
    force: bool,
    triggered_by_auto: bool,
) -> tuple[GroupGame | None, str | None]:
    game, resolution, error = await GAME_STORE.zlob_resolve_round(game_id=game_id, force=force)
    if game is None:
        return None, "Игра не найдена"
    if error:
        return game, error
    if resolution is None:
        return game, "Не удалось обработать голосование"

    round_text = _format_zlob_round_resolution(game, resolution)
    if resolution.finished:
        reward_line = await _grant_game_rewards_if_needed(
            game,
            economy_repo=economy_repo,
            chat_settings=chat_settings,
        )
        note = round_text
        if reward_line:
            note = f"{note}\n{reward_line}"
        await _safe_edit_or_send_game_board(bot, game, chat_settings, note=note)
        await _send_game_feed_event(
            bot,
            game,
            text=f"<b>Ведущий:</b> Игра «500 Злобных Карт» завершена.\n{round_text}" + (f"\n{reward_line}" if reward_line else ""),
        )
        return game, None

    failed_dm = await _notify_zlob_private_hands(bot, game)
    note_parts = [round_text]
    if failed_dm > 0:
        note_parts.append(f"<b>ЛС недоступно:</b> {failed_dm} игрок(ов) не получили обновлённую руку.")
    await _safe_edit_or_send_game_board(
        bot,
        game,
        chat_settings,
        note="\n".join(note_parts),
    )
    _schedule_phase_timer(bot, game, chat_settings)
    if triggered_by_auto:
        logger.debug("Zlobcards round resolved automatically", extra={"game_id": game.game_id})
    return game, None


async def _resolve_bunker_vote(
    bot: Bot,
    game_id: str,
    chat_settings: ChatSettings,
    economy_repo=None,
    *,
    force: bool,
    triggered_by_auto: bool,
) -> tuple[GroupGame | None, str | None]:
    game, resolution, error = await GAME_STORE.bunker_resolve_vote(game_id=game_id, force=force)
    if game is None:
        return None, "Игра не найдена"
    if error:
        return game, error
    if resolution is None:
        return game, "Не удалось обработать голосование"

    round_text = _format_bunker_vote_resolution(game, resolution)
    note_parts = [round_text]
    event_parts = [f"<b>Ведущий:</b> {round_text}"]

    if resolution.finished:
        reward_line = await _grant_game_rewards_if_needed(
            game,
            economy_repo=economy_repo,
            chat_settings=chat_settings,
            winner_user_ids_override=set(resolution.winner_user_ids),
        )
        if reward_line:
            note_parts.append(reward_line)
            event_parts.append(reward_line)
        await _safe_edit_or_send_game_board(
            bot,
            game,
            chat_settings,
            note="\n".join(note_parts),
        )
        await _send_game_feed_event(bot, game, text="\n".join(event_parts))
        return game, None

    if resolution.next_phase == "bunker_reveal":
        next_actor = resolution.next_actor_label or "-"
        note_parts.append(f"<b>Следующий ход раскрытия:</b> {escape(next_actor)} в ЛС.")
        event_parts.append(f"<b>Следующий ход:</b> {escape(next_actor)} раскрывает характеристику.")
        await _safe_edit_or_send_game_board(
            bot,
            game,
            chat_settings,
            note="\n".join(note_parts),
        )
        await _send_game_feed_event(bot, game, text="\n".join(event_parts))
        ok = await _notify_bunker_reveal_turn(bot, game)
        if not ok:
            await _send_game_feed_event(
                bot,
                game,
                text="<b>ЛС недоступно:</b> текущий игрок не получил карточку хода.",
            )
    elif resolution.next_phase == "bunker_vote":
        note_parts.append("<b>Этап:</b> все характеристики раскрыты, стартовало новое голосование.")
        failed_dm = await _notify_bunker_vote_private(bot, game)
        if failed_dm > 0:
            note_parts.append(f"<b>ЛС недоступно:</b> {failed_dm} игрок(ов) без карточки голосования.")
        await _safe_edit_or_send_game_board(
            bot,
            game,
            chat_settings,
            note="\n".join(note_parts),
        )
        await _send_game_feed_event(bot, game, text="\n".join(event_parts))
    else:
        await _safe_edit_or_send_game_board(
            bot,
            game,
            chat_settings,
            note="\n".join(note_parts),
        )
        await _send_game_feed_event(bot, game, text="\n".join(event_parts))

    if triggered_by_auto:
        logger.debug("Bunker vote resolved automatically", extra={"game_id": game.game_id})

    return game, None


async def _show_role_for_user(message: Message, *, game_id: str) -> None:
    if message.from_user is None:
        return

    game, role = await GAME_STORE.get_role(game_id=game_id, user_id=message.from_user.id)
    if game is None:
        await message.answer("Игра не найдена.")
        return

    if game.status != "started":
        await message.answer("Игра не запущена или уже завершена.")
        return

    if game.kind == "bunker":
        if message.from_user.id not in game.players:
            await message.answer("Вы не участвуете в этой игре.")
            return
        card = game.bunker_cards.get(message.from_user.id)
        if card is None:
            await message.answer("Не удалось найти вашу карточку для этой игры.")
            return

        lines = [
            f"<b>Игра:</b> {escape(GAME_DEFINITIONS[game.kind].title)}",
            f"<b>Чат:</b> {escape(game.chat_title or str(game.chat_id))}",
            f"<b>Раунд:</b> {max(game.round_no, 1)}",
            f"<b>Катастрофа:</b> {escape(game.bunker_catastrophe or '-')}",
            f"<b>Условия бункера:</b> {escape(game.bunker_condition or '-')}",
            f"<b>Мест в бункере:</b> {game.bunker_seats}",
            "",
            "<b>Ваша карточка:</b>",
            _render_bunker_full_card(card),
        ]

        if game.phase == "bunker_reveal":
            if game.bunker_current_actor_user_id == message.from_user.id:
                lines.append("")
                lines.append("<i>Ваш ход: выберите характеристику для раскрытия кнопками ниже.</i>")
            else:
                actor_label = "-"
                if game.bunker_current_actor_user_id is not None:
                    actor_label = game.players.get(game.bunker_current_actor_user_id, f"user:{game.bunker_current_actor_user_id}")
                lines.append("")
                lines.append(f"<i>Сейчас ход у {escape(actor_label)}.</i>")
        elif game.phase == "bunker_vote":
            voted_count = len({voter for voter in game.bunker_votes if voter in game.alive_player_ids})
            lines.append("")
            lines.append(f"<i>Идёт голосование: {voted_count}/{len(game.alive_player_ids)}.</i>")

        await message.answer(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=_build_private_phase_keyboard(game, actor_user_id=message.from_user.id),
        )
        return

    if game.kind == "whoami":
        if message.from_user.id not in game.players:
            await message.answer("Вы не участвуете в этой игре.")
            return
        await message.answer(
            _render_whoami_private_view(game, actor_user_id=message.from_user.id),
            parse_mode="HTML",
        )
        return

    if game.kind == "zlobcards":
        if message.from_user.id not in game.players:
            await message.answer("Вы не участвуете в этой игре.")
            return
        await message.answer(
            _render_private_zlob_status_text(game, actor_user_id=message.from_user.id),
            parse_mode="HTML",
            reply_markup=_build_private_phase_keyboard(game, actor_user_id=message.from_user.id),
        )
        return

    if game.kind not in {"spy", "mafia"}:
        await message.answer("В этой игре нет секретных ролей.")
        return

    if role is None:
        await message.answer("Вы не участвуете в этой игре.")
        return

    lines = [
        f"<b>Игра:</b> {escape(GAME_DEFINITIONS[game.kind].title)}",
        f"<b>Роль:</b> <code>{escape(role)}</code>",
        f"<b>Чат:</b> {escape(game.chat_title or str(game.chat_id))}",
    ]

    if game.kind == "spy":
        lines.append(f"<b>Тема:</b> {escape(_spy_category_label(game))}")
        if role == "Шпион":
            lines.append("<b>Локация:</b> <code>неизвестна</code>")
        else:
            lines.append(f"<b>Локация:</b> <code>{escape(game.spy_location or '-')}</code>")
    elif game.kind == "mafia":
        if game.phase == "night":
            lines.append("<i>Сейчас ночь: ваше действие доступно кнопками ниже.</i>")
        elif game.phase == "day_vote" and message.from_user.id in game.alive_player_ids:
            voted_count = len({voter for voter in game.day_votes if voter in game.alive_player_ids})
            alive_count = len(game.alive_player_ids)
            lines.append(
                _render_private_day_vote_text(
                    game,
                    actor_user_id=message.from_user.id,
                    voted_count=voted_count,
                    alive_count=alive_count,
                )
            )
        elif game.phase == "day_execution_confirm":
            lines.append("<i>Подтверждение казни идёт в групповом чате отдельной карточкой.</i>")

    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=_build_private_phase_keyboard(game, actor_user_id=message.from_user.id),
    )


@router.message(Command("game"))
async def game_command(message: Message, bot: Bot, command: CommandObject, chat_settings: ChatSettings, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Игры запускаются только в группе.")
        return

    if message.from_user is None:
        return

    actor = UserSnapshot(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
    )
    can_manage_games = await _actor_can_manage_games(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user=actor,
        bootstrap_if_missing_owner=False,
    )
    if not can_manage_games:
        await message.answer("Недостаточно прав для запуска игр в этом чате.")
        return

    explicit_kind = _parse_kind(command.args)
    if explicit_kind is None:
        await message.answer(
            "<b>Выберите игру:</b>",
            parse_mode="HTML",
            reply_markup=_build_game_selection_keyboard(requester_user_id=message.from_user.id),
        )
        return
    if explicit_kind not in GAME_LAUNCHABLE_KINDS:
        await message.answer("Игра «Угадай число» больше не доступна для новых запусков.")
        return

    owner_label = await _resolve_chat_player_label(
        activity_repo,
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )
    game, error = await GAME_STORE.create_lobby(
        kind=explicit_kind,
        chat_id=message.chat.id,
        chat_title=message.chat.title,
        owner_user_id=message.from_user.id,
        owner_label=owner_label,
        reveal_eliminated_role=chat_settings.mafia_reveal_eliminated_role,
        actions_18_enabled=chat_settings.actions_18_enabled,
    )
    if error:
        await message.answer(error)
        return
    if game is None:
        await message.answer("Не удалось создать игру")
        return

    bot_username = await _get_bot_username(bot)
    sent = await message.answer(
        _render_game_text(game, chat_settings),
        parse_mode="HTML",
        reply_markup=_build_game_controls(game=game, bot_username=bot_username),
    )
    await GAME_STORE.set_message_id(game_id=game.game_id, message_id=sent.message_id)


@router.callback_query(F.data.startswith("game:new:"))
async def game_new_callback(query: CallbackQuery, bot: Bot, chat_settings: ChatSettings, activity_repo) -> None:
    if query.message is None or not query.data or query.from_user is None:
        await query.answer()
        return

    if query.message.chat.type not in {"group", "supergroup"}:
        await query.answer("Игры доступны только в группе", show_alert=False)
        return

    parts = query.data.split(":")
    if len(parts) not in {3, 4}:
        await query.answer("Некорректный выбор", show_alert=False)
        return

    _, _, raw_kind, *tail = parts
    if tail:
        requester_raw = tail[0]
        if requester_raw.startswith("u") and requester_raw[1:].isdigit():
            requester_id = int(requester_raw[1:])
            if requester_id != query.from_user.id:
                await query.answer("Выбор игры доступен только тому, кто вызвал /game.", show_alert=True)
                return

    kind = _parse_kind(raw_kind)
    if kind is None:
        await query.answer("Неизвестная игра", show_alert=False)
        return
    if kind not in GAME_LAUNCHABLE_KINDS:
        await query.answer("Эта игра больше недоступна для новых запусков.", show_alert=True)
        return

    actor = UserSnapshot(
        telegram_user_id=query.from_user.id,
        username=query.from_user.username,
        first_name=query.from_user.first_name,
        last_name=query.from_user.last_name,
        is_bot=bool(query.from_user.is_bot),
    )
    can_manage_games = await _actor_can_manage_games(
        activity_repo,
        chat_id=query.message.chat.id,
        chat_type=query.message.chat.type,
        chat_title=query.message.chat.title,
        user=actor,
        bootstrap_if_missing_owner=False,
    )
    if not can_manage_games:
        await query.answer("Недостаточно прав для запуска игр в этом чате.", show_alert=True)
        return

    owner_label = await _resolve_chat_player_label(
        activity_repo,
        chat_id=query.message.chat.id,
        user_id=query.from_user.id,
        username=query.from_user.username,
        first_name=query.from_user.first_name,
        last_name=query.from_user.last_name,
    )
    game, error = await GAME_STORE.create_lobby(
        kind=kind,
        chat_id=query.message.chat.id,
        chat_title=query.message.chat.title,
        owner_user_id=query.from_user.id,
        owner_label=owner_label,
        reveal_eliminated_role=chat_settings.mafia_reveal_eliminated_role,
        actions_18_enabled=chat_settings.actions_18_enabled,
    )
    if error:
        await query.answer(error, show_alert=True)
        return
    if game is None:
        await query.answer("Не удалось создать игру", show_alert=False)
        return

    await GAME_STORE.set_message_id(game_id=game.game_id, message_id=query.message.message_id)
    await _safe_edit_or_send_game_board(bot, game, chat_settings)
    await query.answer("Игра создана", show_alert=False)


@router.callback_query(F.data.startswith("gcfg:"))
async def game_config_callback(query: CallbackQuery, bot: Bot, chat_settings: ChatSettings, activity_repo) -> None:
    if query.message is None or not query.data or query.from_user is None:
        await query.answer()
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Некорректные параметры", show_alert=False)
        return

    _, game_id, option = parts

    game = await GAME_STORE.get_game(game_id)
    if game is None:
        await query.answer("Игра не найдена", show_alert=False)
        return

    if game.chat_id != query.message.chat.id:
        await query.answer("Эта кнопка из другого чата", show_alert=False)
        return

    actor_id = query.from_user.id
    actor = UserSnapshot(
        telegram_user_id=query.from_user.id,
        username=query.from_user.username,
        first_name=query.from_user.first_name,
        last_name=query.from_user.last_name,
        is_bot=bool(query.from_user.is_bot),
    )
    allowed = await _actor_can_manage_games(
        activity_repo,
        chat_id=game.chat_id,
        chat_type=query.message.chat.type,
        chat_title=query.message.chat.title,
        user=actor,
        bootstrap_if_missing_owner=False,
    )
    if not allowed:
        await query.answer("Недостаточно прав для управления игрой.", show_alert=False)
        return

    await _refresh_game_player_label(
        activity_repo,
        game=game,
        chat_id=game.chat_id,
        user_id=actor_id,
        username=query.from_user.username,
        first_name=query.from_user.first_name,
        last_name=query.from_user.last_name,
    )

    if option == "reveal_elim":
        if game.kind != "mafia" or game.status != "lobby":
            await query.answer("Настройку можно менять только в лобби мафии", show_alert=False)
            return

        updated_game, error = await GAME_STORE.set_mafia_reveal_eliminated_role(
            game_id=game.game_id,
            reveal_eliminated_role=not game.reveal_eliminated_role,
        )
        if updated_game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return
        if error:
            await query.answer(error, show_alert=False)
            return

        await _persist_mafia_reveal_default(
            activity_repo,
            chat_id=query.message.chat.id,
            chat_type=query.message.chat.type,
            chat_title=query.message.chat.title,
            chat_settings=chat_settings,
            reveal_eliminated_role=updated_game.reveal_eliminated_role,
        )

        await _safe_edit_or_send_game_board(bot, updated_game, chat_settings)
        mode = "показывать" if updated_game.reveal_eliminated_role else "скрывать"
        await query.answer(f"Режим: {mode}", show_alert=False)
        return

    if option.startswith("bred_rounds_"):
        if game.kind != "bredovukha" or game.status != "lobby":
            await query.answer("Настройку можно менять только в лобби «Бредовухи»", show_alert=False)
            return

        if option == "bred_rounds_noop":
            await query.answer(
                f"Раундов: {game.bred_rounds}. Нужно минимум {len(game.players)} по числу игроков.",
                show_alert=False,
            )
            return

        delta = 0
        if option == "bred_rounds_inc":
            delta = 1
        elif option == "bred_rounds_dec":
            delta = -1
        else:
            await query.answer("Неизвестная настройка", show_alert=False)
            return

        updated_game, error = await GAME_STORE.set_bred_rounds(game_id=game.game_id, rounds=game.bred_rounds + delta)
        if updated_game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return
        if error:
            await query.answer(error, show_alert=False)
            return

        await _safe_edit_or_send_game_board(bot, updated_game, chat_settings)
        await query.answer(f"Раундов: {updated_game.bred_rounds}", show_alert=False)
        return

    if option.startswith("zlob_rounds_"):
        if game.kind != "zlobcards" or game.status != "lobby":
            await query.answer("Настройку можно менять только в лобби «500 Злобных Карт»", show_alert=False)
            return

        if option == "zlob_rounds_noop":
            await query.answer(f"Раундов: {game.zlob_rounds}", show_alert=False)
            return

        delta = 0
        if option == "zlob_rounds_inc":
            delta = 1
        elif option == "zlob_rounds_dec":
            delta = -1
        else:
            await query.answer("Неизвестная настройка", show_alert=False)
            return

        updated_game, error = await GAME_STORE.set_zlob_rounds(game_id=game.game_id, rounds=game.zlob_rounds + delta)
        if updated_game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return
        if error:
            await query.answer(error, show_alert=False)
            return

        await _safe_edit_or_send_game_board(bot, updated_game, chat_settings)
        await query.answer(f"Раундов: {updated_game.zlob_rounds}", show_alert=False)
        return

    if option.startswith("zlob_target_"):
        if game.kind != "zlobcards" or game.status != "lobby":
            await query.answer("Настройку можно менять только в лобби «500 Злобных Карт»", show_alert=False)
            return

        if option == "zlob_target_noop":
            await query.answer(f"Цель по очкам: {game.zlob_target_score}", show_alert=False)
            return

        delta = 0
        if option == "zlob_target_inc":
            delta = 1
        elif option == "zlob_target_dec":
            delta = -1
        else:
            await query.answer("Неизвестная настройка", show_alert=False)
            return

        updated_game, error = await GAME_STORE.set_zlob_target_score(
            game_id=game.game_id,
            target_score=game.zlob_target_score + delta,
        )
        if updated_game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return
        if error:
            await query.answer(error, show_alert=False)
            return

        await _safe_edit_or_send_game_board(bot, updated_game, chat_settings)
        await query.answer(f"Цель: {updated_game.zlob_target_score}", show_alert=False)
        return

    if option.startswith("bunker_seats_"):
        if game.kind != "bunker" or game.status != "lobby":
            await query.answer("Настройку можно менять только в лобби «Бункера»", show_alert=False)
            return

        if option == "bunker_seats_noop":
            await query.answer(
                f"Мест в бункере: {game.bunker_seats}. Должно быть меньше игроков ({len(game.players)}).",
                show_alert=False,
            )
            return

        delta = 0
        if option == "bunker_seats_inc":
            delta = 1
        elif option == "bunker_seats_dec":
            delta = -1
        else:
            await query.answer("Неизвестная настройка", show_alert=False)
            return

        updated_game, error = await GAME_STORE.set_bunker_seats(game_id=game.game_id, seats=game.bunker_seats + delta)
        if updated_game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return
        if error:
            await query.answer(error, show_alert=False)
            return

        await _safe_edit_or_send_game_board(bot, updated_game, chat_settings)
        await query.answer(f"Мест в бункере: {updated_game.bunker_seats}", show_alert=False)
        return

    if option == "whoami_cat_next":
        if game.kind != "whoami" or game.status != "lobby":
            await query.answer("Настройку можно менять только в лобби «Кто я»", show_alert=False)
            return

        updated_game, error = await GAME_STORE.cycle_whoami_category(
            game_id=game.game_id,
            actions_18_enabled=chat_settings.actions_18_enabled,
        )
        if updated_game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return
        if error:
            await query.answer(error, show_alert=False)
            return

        await _safe_edit_or_send_game_board(bot, updated_game, chat_settings)
        await query.answer(f"Тема: {_whoami_category_label(updated_game)}", show_alert=False)
        return

    if option == "zlob_cat_next":
        if game.kind != "zlobcards" or game.status != "lobby":
            await query.answer("Настройку можно менять только в лобби «500 Злобных Карт»", show_alert=False)
            return

        updated_game, error = await GAME_STORE.cycle_zlob_category(
            game_id=game.game_id,
            actions_18_enabled=chat_settings.actions_18_enabled,
        )
        if updated_game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return
        if error:
            await query.answer(error, show_alert=False)
            return

        await _safe_edit_or_send_game_board(bot, updated_game, chat_settings)
        await query.answer(f"Тема: {_zlob_category_label(updated_game)}", show_alert=False)
        return

    if option == "spy_cat_next":
        if game.kind != "spy" or game.status != "lobby":
            await query.answer("Настройку можно менять только в лобби «Шпиона»", show_alert=False)
            return

        updated_game, error = await GAME_STORE.cycle_spy_category(game_id=game.game_id)
        if updated_game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return
        if error:
            await query.answer(error, show_alert=False)
            return

        await _safe_edit_or_send_game_board(bot, updated_game, chat_settings)
        await query.answer(f"Тема: {_spy_category_label(updated_game)}", show_alert=False)
        return

    await query.answer("Неизвестная настройка", show_alert=False)


@router.callback_query(F.data.startswith("game:"))
async def game_callback(query: CallbackQuery, bot: Bot, chat_settings: ChatSettings, activity_repo, economy_repo) -> None:
    if query.message is None or not query.data or query.from_user is None:
        await query.answer()
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Некорректные параметры", show_alert=False)
        return

    _, action, game_id = parts
    if action == "new":
        await query.answer()
        return

    game = await GAME_STORE.get_game(game_id)
    if game is None:
        await query.answer("Игра не найдена", show_alert=False)
        return

    if query.message.chat.id != game.chat_id:
        await query.answer("Эта кнопка из другого чата", show_alert=False)
        return

    actor_id = query.from_user.id
    actor = UserSnapshot(
        telegram_user_id=query.from_user.id,
        username=query.from_user.username,
        first_name=query.from_user.first_name,
        last_name=query.from_user.last_name,
        is_bot=bool(query.from_user.is_bot),
    )
    actor_label = await _refresh_game_player_label(
        activity_repo,
        game=game,
        chat_id=game.chat_id,
        user_id=actor_id,
        username=query.from_user.username,
        first_name=query.from_user.first_name,
        last_name=query.from_user.last_name,
    )

    if action == "join":
        updated_game, status = await GAME_STORE.join(game_id=game_id, user_id=actor_id, user_label=actor_label)
        if updated_game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return

        if status == "already_joined":
            await query.answer("Вы уже в игре", show_alert=False)
            return
        if status == "not_lobby":
            await query.answer("Нельзя присоединиться: игра уже запущена", show_alert=False)
            return

        await _safe_edit_or_send_game_board(bot, updated_game, chat_settings)
        await query.answer("Вы присоединились", show_alert=False)
        return

    if action in {"cancel", "advance", "reveal"}:
        allowed = await _actor_can_manage_games(
            activity_repo,
            chat_id=game.chat_id,
            chat_type=query.message.chat.type,
            chat_title=query.message.chat.title,
            user=actor,
            bootstrap_if_missing_owner=False,
        )
        if not allowed:
            await query.answer("Недостаточно прав для управления игрой.", show_alert=False)
            return

    if action == "start":
        can_start = await _actor_can_start_game(
            activity_repo,
            game=game,
            chat_type=query.message.chat.type,
            chat_title=query.message.chat.title,
            user=actor,
            bootstrap_if_missing_owner=False,
        )
        if not can_start:
            await query.answer("Старт может нажать создатель лобби или участник с правом управления играми.", show_alert=True)
            return

        started_game, error = await GAME_STORE.start(
            game_id=game_id,
            actions_18_enabled=chat_settings.actions_18_enabled,
        )
        if started_game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return
        if error:
            await query.answer(error, show_alert=True)
            return

        await _safe_edit_or_send_game_board(bot, started_game, chat_settings)
        await query.answer("Игра началась", show_alert=False)

        if started_game.kind in {"spy", "mafia", "bunker", "whoami", "zlobcards"}:
            failed_dm = await _send_roles_to_private(bot, started_game)
            if failed_dm > 0:
                await _notify_private_delivery_warning(bot, started_game, failed_dm)

        if started_game.kind == "mafia":
            _schedule_phase_timer(bot, started_game, chat_settings)
            await _notify_mafia_night_actions(bot, started_game)
            await _send_game_feed_event(
                bot,
                started_game,
                text=build_mafia_start_text(round_no=started_game.round_no, night_seconds=chat_settings.mafia_night_seconds),
            )
            return

        if started_game.kind == "spy":
            await _send_game_feed_event(bot, started_game, text=build_spy_start_text(category=_spy_category_label(started_game)))
            return

        if started_game.kind == "whoami":
            await _send_game_feed_event(bot, started_game, text=build_whoami_start_text(category=_whoami_category_label(started_game)))
            return

        if started_game.kind == "zlobcards":
            _schedule_phase_timer(bot, started_game, chat_settings)
            await _send_game_feed_event(
                bot,
                started_game,
                text=build_zlobcards_start_text(category=_zlob_category_label(started_game)),
            )
            return

        if started_game.kind == "number":
            await _send_game_feed_event(bot, started_game, text=build_number_start_text())
            return

        if started_game.kind == "dice":
            await _send_game_feed_event(bot, started_game, text=build_dice_start_text())
            return

        if started_game.kind == "quiz":
            await _sync_quiz_feed_message(bot, started_game, question_no=1)
            return

        if started_game.kind == "bredovukha":
            await _send_game_feed_event(bot, started_game, text=build_bredovukha_start_text())
            return

        if started_game.kind == "bunker":
            await _send_game_feed_event(bot, started_game, text=build_bunker_start_text())
            actor_label = "-"
            if started_game.bunker_current_actor_user_id is not None:
                actor_label = started_game.players.get(
                    started_game.bunker_current_actor_user_id,
                    f"user:{started_game.bunker_current_actor_user_id}",
                )
            await _safe_edit_or_send_game_board(
                bot,
                started_game,
                chat_settings,
                note=f"<b>Первый ход раскрытия:</b> {escape(actor_label)} раскрывает характеристику в ЛС.",
            )
            await _notify_bunker_reveal_turn(bot, started_game)
            return

        return

    if action == "advance":
        if game.status != "started":
            await query.answer("Игра уже завершена", show_alert=False)
            return

        if game.kind == "quiz":
            resolved_game, error = await _resolve_quiz_round(
                bot,
                game.game_id,
                chat_settings,
                economy_repo=economy_repo,
                force=True,
                triggered_by_auto=False,
            )
            if error:
                await query.answer(error, show_alert=False)
                return
            if resolved_game is not None and resolved_game.status == "finished":
                await query.answer("Викторина завершена", show_alert=False)
                return
            await query.answer("Вопрос закрыт, следующий открыт", show_alert=False)
            return

        if game.kind == "bredovukha":
            if game.phase == "category_pick":
                opened_game, category, error = await GAME_STORE.bred_force_pick_category(game_id=game.game_id)
                if opened_game is None:
                    await query.answer("Игра не найдена", show_alert=False)
                    return
                if error:
                    await query.answer(error, show_alert=False)
                    return

                await _safe_callback_answer(query, "Выбираю случайную категорию", show_alert=False)
                failed_dm = await _notify_bred_private_answers(bot, opened_game)
                note_parts = [
                    f"<b>Категория раунда:</b> {escape(category or '-')}",
                    "<b>Этап:</b> сбор ответов в ЛС начался.",
                ]
                if failed_dm > 0:
                    note_parts.append(
                        f"<b>ЛС недоступно:</b> {failed_dm} игрок(ов) не получили вопрос."
                    )
                await _safe_edit_or_send_game_board(
                    bot,
                    opened_game,
                    chat_settings,
                    note="\n".join(note_parts),
                )
                return

            if game.phase == "private_answers":
                opened_game, error = await GAME_STORE.bred_open_vote(game_id=game.game_id, force=True)
                if opened_game is None:
                    await query.answer("Игра не найдена", show_alert=False)
                    return
                if error:
                    await query.answer(error, show_alert=False)
                    return

                await _safe_callback_answer(query, "Открываю голосование", show_alert=False)
                await _safe_edit_or_send_game_board(
                    bot,
                    opened_game,
                    chat_settings,
                    note="<b>Этап:</b> сбор ответов завершён, открыто голосование.",
                )
                return

            if game.phase == "public_vote":
                await _safe_callback_answer(query, "Завершаю раунд", show_alert=False)
                _, error = await _resolve_bred_round(
                    bot,
                    game.game_id,
                    chat_settings,
                    economy_repo=economy_repo,
                    force=True,
                    triggered_by_auto=False,
                )
                if error:
                    await _safe_callback_answer(query, error, show_alert=False)
                    return
                return

            await query.answer("Сейчас нечего переключать", show_alert=False)
            return

        if game.kind == "zlobcards":
            if game.phase == "private_answers":
                opened_game, error = await _open_zlob_vote_phase(
                    bot,
                    game.game_id,
                    chat_settings,
                    force=True,
                    triggered_by_auto=False,
                )
                if opened_game is None:
                    await query.answer("Игра не найдена", show_alert=False)
                    return
                if error:
                    await query.answer(error, show_alert=False)
                    return
                _schedule_phase_timer(bot, opened_game, chat_settings)
                await query.answer("Голосование открыто", show_alert=False)
                return

            if game.phase == "public_vote":
                resolved_game, error = await _resolve_zlob_round(
                    bot,
                    game.game_id,
                    chat_settings,
                    economy_repo=economy_repo,
                    force=True,
                    triggered_by_auto=False,
                )
                if error:
                    await query.answer(error, show_alert=False)
                    return
                if resolved_game is not None and resolved_game.status == "started":
                    _schedule_phase_timer(bot, resolved_game, chat_settings)
                await query.answer("Раунд закрыт", show_alert=False)
                return

            await query.answer("Сейчас нечего переключать", show_alert=False)
            return

        if game.kind == "bunker":
            if game.phase == "bunker_reveal":
                updated_game, result, error = await GAME_STORE.bunker_force_advance_reveal(game_id=game.game_id)
                if updated_game is None:
                    await query.answer("Игра не найдена", show_alert=False)
                    return
                if error:
                    await query.answer(error, show_alert=False)
                    return
                if result is None:
                    await query.answer("Не удалось переключить ход", show_alert=False)
                    return

                note_lines = [
                    f"<b>Ход пропущен:</b> {escape(result.actor_user_label)}.",
                ]
                if result.vote_opened:
                    note_lines.append("<b>Этап:</b> открыто голосование на выбывание.")
                    failed_dm = await _notify_bunker_vote_private(bot, updated_game)
                    if failed_dm > 0:
                        note_lines.append(f"<b>ЛС недоступно:</b> {failed_dm} игрок(ов) не получили карточку голосования.")
                elif result.next_actor_label is not None:
                    note_lines.append(f"<b>Следующий ход:</b> {escape(result.next_actor_label)} раскрывает характеристику в ЛС.")
                    await _notify_bunker_reveal_turn(bot, updated_game)

                await _safe_edit_or_send_game_board(
                    bot,
                    updated_game,
                    chat_settings,
                    note="\n".join(note_lines),
                )
                await query.answer("Ход переключён", show_alert=False)
                return

            if game.phase == "bunker_vote":
                resolved_game, error = await _resolve_bunker_vote(
                    bot,
                    game.game_id,
                    chat_settings,
                    economy_repo=economy_repo,
                    force=True,
                    triggered_by_auto=False,
                )
                if error:
                    await query.answer(error, show_alert=False)
                    return
                if resolved_game is not None and resolved_game.status == "finished":
                    await query.answer("Бункер завершён", show_alert=False)
                    return
                await query.answer("Голосование завершено", show_alert=False)
                return

            await query.answer("Сейчас нечего переключать", show_alert=False)
            return

        if game.kind != "mafia":
            await query.answer("Смена фаз доступна только для мафии, викторины, Бредовухи, Злобных Карт и Бункера", show_alert=False)
            return

        if game.phase == "night":
            _cancel_phase_timer(game.game_id)
            await _advance_mafia_night(bot, game.game_id, chat_settings, economy_repo=economy_repo, triggered_by_timer=False)
            await query.answer("Ночь завершена вручную", show_alert=False)
            return

        if game.phase == "day_discussion":
            _cancel_phase_timer(game.game_id)
            await _open_mafia_day_vote(bot, game.game_id, chat_settings, triggered_by_timer=False)
            await query.answer("Обсуждение завершено", show_alert=False)
            return

        if game.phase == "day_vote":
            _cancel_phase_timer(game.game_id)
            await _resolve_mafia_day_vote(
                bot,
                game.game_id,
                chat_settings,
                economy_repo=economy_repo,
                triggered_by_timer=False,
            )
            await query.answer("Голосование завершено", show_alert=False)
            return

        if game.phase == "day_execution_confirm":
            _cancel_phase_timer(game.game_id)
            await _resolve_mafia_execution_confirm(
                bot,
                game.game_id,
                chat_settings,
                economy_repo=economy_repo,
                triggered_by_timer=False,
            )
            await query.answer("Подтверждение завершено", show_alert=False)
            return

        await query.answer("Сейчас нечего переключать", show_alert=False)
        return

    if action == "reveal":
        if game.kind != "spy" or game.status != "started":
            await query.answer("Раскрытие доступно для активной игры «Шпион»", show_alert=False)
            return

        finished_game = await GAME_STORE.finish(game_id=game.game_id, winner_text="Игра завершена по решению ведущего.")
        if finished_game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return

        await _safe_edit_or_send_game_board(bot, finished_game, chat_settings, include_reveal=True)
        await _send_game_feed_event(
            bot,
            finished_game,
            text="<b>Ведущий:</b> Игра «Шпион» завершена, роли раскрыты.\n" + _render_roles_reveal(finished_game),
        )
        await query.answer("Роли раскрыты", show_alert=False)
        return

    if action == "cancel":
        _cancel_phase_timer(game.game_id)
        if game.kind == "quiz":
            await _sync_quiz_feed_message(bot, game, question_no=None)
        finished_game = await GAME_STORE.finish(game_id=game.game_id, winner_text="Игра остановлена ведущим.")
        if finished_game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return

        await _safe_edit_or_send_game_board(
            bot,
            finished_game,
            chat_settings,
            include_reveal=(finished_game.kind in {"spy", "mafia"}),
        )
        event_text = "<b>Ведущий:</b> Игра остановлена ведущим."
        if finished_game.kind in {"spy", "mafia"}:
            event_text = f"{event_text}\n{_render_roles_reveal(finished_game)}"
        await _send_game_feed_event(bot, finished_game, text=event_text)
        await query.answer("Игра завершена", show_alert=False)
        return

    await query.answer("Неизвестное действие", show_alert=False)


@router.callback_query(F.data.startswith("gquiz:"))
async def quiz_answer_callback(query: CallbackQuery, bot: Bot, chat_settings: ChatSettings, economy_repo) -> None:
    if query.data is None or query.from_user is None:
        await query.answer()
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Некорректный ответ", show_alert=False)
        return

    _, game_id, payload = parts
    if payload == "noop":
        snapshot_game, answered_count, total_players = await GAME_STORE.quiz_get_answer_snapshot(game_id=game_id)
        if snapshot_game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return
        await _safe_edit_or_send_game_board(
            bot,
            snapshot_game,
            chat_settings,
            note=f"<b>Прогресс вопроса:</b> {answered_count}/{total_players}",
        )
        await query.answer(f"Ответили: {answered_count}/{total_players}", show_alert=False)
        return

    if not payload.isdigit():
        await query.answer("Некорректный вариант", show_alert=False)
        return

    option_index = int(payload)
    game, result, error = await GAME_STORE.quiz_submit_answer(
        game_id=game_id,
        user_id=query.from_user.id,
        option_index=option_index,
    )
    if error:
        await query.answer(error, show_alert=True)
        return

    if game is None or result is None:
        await query.answer("Игра не найдена", show_alert=False)
        return

    snapshot_game, answered_count, total_players = await GAME_STORE.quiz_get_answer_snapshot(game_id=game_id)
    if snapshot_game is not None:
        await _safe_edit_or_send_game_board(
            bot,
            snapshot_game,
            chat_settings,
            note=f"<b>Прогресс вопроса:</b> {answered_count}/{total_players}",
        )

    if result.all_answered:
        await query.answer("Ответ принят. Все ответили, считаем результат.", show_alert=False)
        await _resolve_quiz_round(
            bot,
            game.game_id,
            chat_settings,
            economy_repo=economy_repo,
            force=False,
            triggered_by_auto=True,
        )
        return

    if result.previous_answer_index is None:
        await query.answer("Ответ принят", show_alert=False)
        return

    if result.previous_answer_index == option_index:
        await query.answer("Ответ уже учтён", show_alert=False)
        return

    await query.answer("Ответ обновлён", show_alert=False)


@router.callback_query(F.data.startswith("gdice:"))
async def dice_roll_callback(query: CallbackQuery, bot: Bot, chat_settings: ChatSettings, economy_repo) -> None:
    if query.data is None or query.from_user is None:
        await query.answer()
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Некорректный бросок", show_alert=False)
        return

    _, game_id, action = parts
    if action != "roll":
        await query.answer("Неизвестное действие", show_alert=False)
        return

    game, result, error = await GAME_STORE.dice_register_roll(game_id=game_id, user_id=query.from_user.id)
    if error:
        await query.answer(error, show_alert=True)
        return
    if game is None or result is None:
        await query.answer("Игра не найдена", show_alert=False)
        return
    if query.message is None or query.message.chat.id != game.chat_id:
        await query.answer("Эта кнопка из другого чата", show_alert=False)
        return

    player_label = game.players.get(query.from_user.id, f"user:{query.from_user.id}")
    note = (
        f"<b>Последний бросок:</b> {_mention(query.from_user.id, player_label)} -> "
        f"<code>{result.roll_value}</code> ({result.rolled_count}/{result.total_players})"
    )

    if result.finished:
        reward_line = await _grant_game_rewards_if_needed(
            game,
            economy_repo=economy_repo,
            chat_settings=chat_settings,
        )
        if reward_line:
            note = f"{note}\n{reward_line}"
        await _safe_edit_or_send_game_board(bot, game, chat_settings, note=note)
        await _send_game_feed_event(
            bot,
            game,
            text=(
                f"<b>Ведущий:</b> Раунд кубиков завершён.\n"
                f"{note}\n"
                f"<b>Итог:</b> {escape(result.winner_text or game.winner_text or 'игра завершена')}"
            ),
        )
        await query.answer(f"Ваш бросок: {result.roll_value}. Раунд завершён.", show_alert=False)
        return

    await _safe_edit_or_send_game_board(bot, game, chat_settings, note=note)
    await query.answer(f"Ваш бросок: {result.roll_value}", show_alert=False)


@router.callback_query(F.data.startswith("gbredcat:"))
async def bred_category_callback(query: CallbackQuery, bot: Bot, chat_settings: ChatSettings, activity_repo) -> None:
    if query.data is None or query.from_user is None:
        await query.answer()
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Некорректный выбор категории", show_alert=False)
        return

    _, game_id, payload = parts
    if payload == "noop":
        game, selector_user_id, options = await GAME_STORE.bred_get_category_snapshot(game_id=game_id)
        if game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return
        if query.message is None or query.message.chat.id != game.chat_id:
            await query.answer("Эта кнопка из другого чата", show_alert=False)
            return
        selector_label = "-"
        if selector_user_id is not None:
            selector_label = game.players.get(selector_user_id, f"user:{selector_user_id}")
        await _safe_edit_or_send_game_board(
            bot,
            game,
            chat_settings,
            note=(
                f"<b>Выбор категории:</b> {len(options)} вариант(ов). "
                f"Сейчас выбирает {escape(selector_label)}."
            ),
        )
        await _safe_callback_answer(query, f"Выбирает: {selector_label}", show_alert=False)
        return

    if not payload.isdigit():
        await query.answer("Некорректная категория", show_alert=False)
        return

    option_index = int(payload)
    existing_game = await GAME_STORE.get_game(game_id)
    if existing_game is not None:
        await _refresh_game_player_label(
            activity_repo,
            game=existing_game,
            chat_id=existing_game.chat_id,
            user_id=query.from_user.id,
            username=query.from_user.username,
            first_name=query.from_user.first_name,
            last_name=query.from_user.last_name,
        )
    game, category, error = await GAME_STORE.bred_choose_category(
        game_id=game_id,
        actor_user_id=query.from_user.id,
        option_index=option_index,
    )
    if error:
        await query.answer(error, show_alert=True)
        return
    if game is None:
        await query.answer("Игра не найдена", show_alert=False)
        return
    if query.message is None or query.message.chat.id != game.chat_id:
        await query.answer("Эта кнопка из другого чата", show_alert=False)
        return

    await _safe_callback_answer(query, "Категория принята", show_alert=False)
    failed_dm = await _notify_bred_private_answers(bot, game)
    selector_label = game.players.get(query.from_user.id, f"user:{query.from_user.id}")
    note_lines = [
        f"<b>Категория выбрана:</b> {escape(category or '-')}",
        f"<b>Выбрал:</b> {_mention(query.from_user.id, selector_label)}",
        "<b>Этап:</b> сбор ответов в ЛС.",
    ]
    if failed_dm > 0:
        note_lines.append(f"<b>ЛС недоступно:</b> {failed_dm} игрок(ов) не получили вопрос.")

    await _safe_edit_or_send_game_board(
        bot,
        game,
        chat_settings,
        note="\n".join(note_lines),
    )


@router.callback_query(F.data.startswith("gbred:"))
async def bred_vote_callback(query: CallbackQuery, bot: Bot, chat_settings: ChatSettings, economy_repo, activity_repo) -> None:
    if query.data is None or query.from_user is None:
        await query.answer()
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Некорректное голосование", show_alert=False)
        return

    _, game_id, payload = parts
    if payload == "noop":
        game, voted_count, total_players, vote_tally = await GAME_STORE.bred_get_vote_snapshot(game_id=game_id)
        if game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return
        if query.message is None or query.message.chat.id != game.chat_id:
            await query.answer("Эта кнопка из другого чата", show_alert=False)
            return

        leader_text = "пока нет"
        if vote_tally:
            top_votes = max(vote_tally)
            if top_votes > 0:
                leader_indices = [idx for idx, count in enumerate(vote_tally) if count == top_votes]
                if len(leader_indices) == 1:
                    leader_index = leader_indices[0]
                    leader_text = f"{_quiz_choice_label(leader_index)}. {game.bred_options[leader_index]} ({top_votes})"
                else:
                    leader_text = f"ничья по {top_votes} голос(ам)"

        await _safe_edit_or_send_game_board(
            bot,
            game,
            chat_settings,
            note=f"<b>Прогресс голосования:</b> {voted_count}/{total_players}. Лидер: {escape(leader_text)}.",
        )
        await _safe_callback_answer(query, f"Голосов: {voted_count}/{total_players}", show_alert=False)
        return

    if not payload.isdigit():
        await query.answer("Некорректный вариант", show_alert=False)
        return

    option_index = int(payload)
    existing_game = await GAME_STORE.get_game(game_id)
    if existing_game is not None:
        await _refresh_game_player_label(
            activity_repo,
            game=existing_game,
            chat_id=existing_game.chat_id,
            user_id=query.from_user.id,
            username=query.from_user.username,
            first_name=query.from_user.first_name,
            last_name=query.from_user.last_name,
        )
    game, result, error = await GAME_STORE.bred_register_vote(
        game_id=game_id,
        voter_user_id=query.from_user.id,
        option_index=option_index,
    )
    if error:
        await query.answer(error, show_alert=True)
        return
    if game is None or result is None:
        await query.answer("Игра не найдена", show_alert=False)
        return
    if query.message is None or query.message.chat.id != game.chat_id:
        await query.answer("Эта кнопка из другого чата", show_alert=False)
        return

    if result.previous_option_index == option_index:
        await _safe_callback_answer(query, "Этот голос уже учтён", show_alert=False)
        return

    answer_text = "Голос принят. Подводим итоги." if result.all_voted else "Голос обновлён" if result.previous_option_index is not None else "Голос принят"
    await _safe_callback_answer(query, answer_text, show_alert=False)

    snapshot_game, voted_count, total_players, _ = await GAME_STORE.bred_get_vote_snapshot(game_id=game_id)
    if snapshot_game is not None:
        await _safe_edit_or_send_game_board(
            bot,
            snapshot_game,
            chat_settings,
            note=f"<b>Прогресс голосования:</b> {voted_count}/{total_players}",
        )

    if result.all_voted:
        await _resolve_bred_round(
            bot,
            game.game_id,
            chat_settings,
            economy_repo=economy_repo,
            force=False,
            triggered_by_auto=True,
        )
        return


@router.callback_query(F.data.startswith("gzlobp:"))
async def zlob_private_submit_callback(query: CallbackQuery, bot: Bot, chat_settings: ChatSettings, activity_repo) -> None:
    if query.data is None or query.from_user is None:
        await query.answer()
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Некорректный выбор карточек", show_alert=False)
        return

    _, game_id, payload = parts
    existing_game = await GAME_STORE.get_game(game_id)
    if existing_game is not None:
        await _refresh_game_player_label(
            activity_repo,
            game=existing_game,
            chat_id=existing_game.chat_id,
            user_id=query.from_user.id,
            username=query.from_user.username,
            first_name=query.from_user.first_name,
            last_name=query.from_user.last_name,
        )

    if payload == "noop":
        game, submitted_count, total_players = await GAME_STORE.zlob_get_submit_snapshot(game_id=game_id)
        if game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return
        if query.message is not None and query.message.chat.type in {"group", "supergroup"} and query.message.chat.id != game.chat_id:
            await query.answer("Эта кнопка из другого чата", show_alert=False)
            return

        if query.message is not None and query.message.chat.type == "private":
            try:
                await query.message.edit_text(
                    _render_private_zlob_status_text(game, actor_user_id=query.from_user.id),
                    parse_mode="HTML",
                    reply_markup=_build_private_phase_keyboard(game, actor_user_id=query.from_user.id),
                )
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    raise

        await _safe_edit_or_send_game_board(
            bot,
            game,
            chat_settings,
            note=f"<b>Сдача карточек:</b> {submitted_count}/{total_players}",
        )
        await _safe_callback_answer(query, f"Сдано: {submitted_count}/{total_players}", show_alert=False)
        return

    selected_indexes: tuple[int, ...]
    if "-" in payload:
        first_raw, second_raw = payload.split("-", maxsplit=1)
        if not first_raw.isdigit() or not second_raw.isdigit():
            await query.answer("Некорректный выбор карточек", show_alert=False)
            return
        selected_indexes = (int(first_raw), int(second_raw))
    else:
        if not payload.isdigit():
            await query.answer("Некорректный выбор карточек", show_alert=False)
            return
        selected_indexes = (int(payload),)

    game, result, error = await GAME_STORE.zlob_submit_cards(
        game_id=game_id,
        user_id=query.from_user.id,
        card_indexes=selected_indexes,
    )
    if error:
        await query.answer(error, show_alert=True)
        return
    if game is None or result is None:
        await query.answer("Игра не найдена", show_alert=False)
        return
    if query.message is not None and query.message.chat.type in {"group", "supergroup"} and query.message.chat.id != game.chat_id:
        await query.answer("Эта кнопка из другого чата", show_alert=False)
        return

    if query.message is not None and query.message.chat.type == "private":
        try:
            await query.message.edit_text(
                _render_private_zlob_status_text(game, actor_user_id=query.from_user.id),
                parse_mode="HTML",
                reply_markup=_build_private_phase_keyboard(game, actor_user_id=query.from_user.id),
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise

    if result.vote_opened:
        _cancel_phase_timer(game.game_id)
        await _safe_edit_or_send_game_board(
            bot,
            game,
            chat_settings,
            note="<b>Этап:</b> все карточки сданы, открыто голосование.",
        )
        await _safe_callback_answer(query, "Карточки приняты. Открыто голосование.", show_alert=False)
        return

    await _safe_edit_or_send_game_board(
        bot,
        game,
        chat_settings,
        note=f"<b>Сдача карточек:</b> {result.submitted_count}/{result.total_players}",
    )

    selected_cards = tuple(game.zlob_submissions.get(query.from_user.id, ()))
    if result.previous_submission == selected_cards:
        await _safe_callback_answer(query, "Этот выбор уже учтён.", show_alert=False)
        return
    if result.previous_submission is None:
        await _safe_callback_answer(query, "Карточки отправлены.", show_alert=False)
        return
    await _safe_callback_answer(query, "Выбор обновлён.", show_alert=False)


@router.callback_query(F.data.startswith("gzlobv:"))
async def zlob_vote_callback(query: CallbackQuery, bot: Bot, chat_settings: ChatSettings, economy_repo, activity_repo) -> None:
    if query.data is None or query.from_user is None:
        await query.answer()
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Некорректное голосование", show_alert=False)
        return

    _, game_id, payload = parts
    existing_game = await GAME_STORE.get_game(game_id)
    if existing_game is not None:
        await _refresh_game_player_label(
            activity_repo,
            game=existing_game,
            chat_id=existing_game.chat_id,
            user_id=query.from_user.id,
            username=query.from_user.username,
            first_name=query.from_user.first_name,
            last_name=query.from_user.last_name,
        )

    if payload == "noop":
        game, voted_count, total_players, vote_tally = await GAME_STORE.zlob_get_vote_snapshot(game_id=game_id)
        if game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return
        if query.message is not None and query.message.chat.type in {"group", "supergroup"} and query.message.chat.id != game.chat_id:
            await query.answer("Эта кнопка из другого чата", show_alert=False)
            return

        leader_text = "пока нет"
        if vote_tally:
            top_votes = max(vote_tally)
            if top_votes > 0:
                leader_indexes = [idx for idx, count in enumerate(vote_tally) if count == top_votes]
                if len(leader_indexes) == 1:
                    leader_index = leader_indexes[0]
                    leader_text = f"{_quiz_choice_label(leader_index)}. {game.zlob_options[leader_index]} ({top_votes})"
                else:
                    leader_text = f"ничья по {top_votes} голос(ам)"

        await _safe_edit_or_send_game_board(
            bot,
            game,
            chat_settings,
            note=f"<b>Прогресс голосования:</b> {voted_count}/{total_players}. Лидер: {escape(leader_text)}.",
        )
        await _safe_callback_answer(query, f"Голосов: {voted_count}/{total_players}", show_alert=False)
        return

    if not payload.isdigit():
        await query.answer("Некорректный вариант", show_alert=False)
        return

    option_index = int(payload)
    game, result, error = await GAME_STORE.zlob_register_vote(
        game_id=game_id,
        voter_user_id=query.from_user.id,
        option_index=option_index,
    )
    if error:
        await query.answer(error, show_alert=True)
        return
    if game is None or result is None:
        await query.answer("Игра не найдена", show_alert=False)
        return
    if query.message is not None and query.message.chat.type in {"group", "supergroup"} and query.message.chat.id != game.chat_id:
        await query.answer("Эта кнопка из другого чата", show_alert=False)
        return

    if result.previous_option_index == option_index:
        await _safe_callback_answer(query, "Этот голос уже учтён.", show_alert=False)
        return

    snapshot_game, voted_count, total_players, _ = await GAME_STORE.zlob_get_vote_snapshot(game_id=game.game_id)
    if snapshot_game is not None:
        await _safe_edit_or_send_game_board(
            bot,
            snapshot_game,
            chat_settings,
            note=f"<b>Прогресс голосования:</b> {voted_count}/{total_players}",
        )

    if result.all_voted:
        _cancel_phase_timer(game.game_id)
        await _safe_callback_answer(query, "Голос принят. Все проголосовали, считаем итог.", show_alert=False)
        _, resolve_error = await _resolve_zlob_round(
            bot,
            game.game_id,
            chat_settings,
            economy_repo=economy_repo,
            force=False,
            triggered_by_auto=True,
        )
        if resolve_error:
            await _safe_callback_answer(query, resolve_error, show_alert=True)
        return

    if result.previous_option_index is None:
        await _safe_callback_answer(query, "Голос принят.", show_alert=False)
        return
    await _safe_callback_answer(query, "Голос обновлён.", show_alert=False)


@router.callback_query(F.data.startswith("gbkr:"))
async def bunker_reveal_callback(query: CallbackQuery, bot: Bot, chat_settings: ChatSettings) -> None:
    if query.data is None or query.from_user is None:
        await query.answer()
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Некорректное раскрытие", show_alert=False)
        return

    _, game_id, payload = parts
    if payload == "noop":
        game, current_index, total_in_round, current_actor_user_id = await GAME_STORE.bunker_get_reveal_snapshot(game_id=game_id)
        if game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return
        actor_label = "-"
        if current_actor_user_id is not None:
            actor_label = game.players.get(current_actor_user_id, f"user:{current_actor_user_id}")
        await _safe_edit_or_send_game_board(
            bot,
            game,
            chat_settings,
            note=(
                f"<b>Раскрытие:</b> ход {current_index + 1}/{max(total_in_round, 1)}. "
                f"Сейчас раскрывает {escape(actor_label)}."
            ),
        )
        if query.message is not None and query.message.chat.type == "private":
            try:
                await query.message.edit_text(
                    _render_private_bunker_status_text(game, actor_user_id=query.from_user.id),
                    parse_mode="HTML",
                    reply_markup=_build_private_phase_keyboard(game, actor_user_id=query.from_user.id),
                )
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    raise
        await query.answer("Статус обновлён", show_alert=False)
        return

    game, result, error = await GAME_STORE.bunker_register_reveal(
        game_id=game_id,
        actor_user_id=query.from_user.id,
        field_key=payload,
    )
    if error:
        await query.answer(error, show_alert=True)
        return
    if game is None or result is None:
        await query.answer("Игра не найдена", show_alert=False)
        return

    if query.message is not None and query.message.chat.type == "private":
        try:
            await query.message.edit_text(
                _render_private_bunker_status_text(game, actor_user_id=query.from_user.id),
                parse_mode="HTML",
                reply_markup=_build_private_phase_keyboard(game, actor_user_id=query.from_user.id),
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise

    reveal_line = (
        f"<b>Раскрытие:</b> {_mention(result.actor_user_id, result.actor_user_label)} "
        f"открыл(а) <b>{escape(result.field_label or '-')}</b>: <code>{escape(result.revealed_value or '-')}</code>."
    )
    note_lines = [
        reveal_line,
        f"<b>Лично открыто:</b> {result.revealed_count_for_actor}/{result.total_fields_for_actor}",
    ]
    if result.vote_opened:
        note_lines.append("<b>Этап:</b> полный круг завершён, открыто голосование.")
        failed_dm = await _notify_bunker_vote_private(bot, game)
        if failed_dm > 0:
            note_lines.append(f"<b>ЛС недоступно:</b> {failed_dm} игрок(ов) не получили голосование.")
    elif result.next_actor_label is not None:
        note_lines.append(f"<b>Следующий ход:</b> {escape(result.next_actor_label)} раскрывает характеристику в ЛС.")
        ok = await _notify_bunker_reveal_turn(bot, game)
        if not ok:
            note_lines.append("<b>ЛС недоступно:</b> следующий игрок не получил карточку хода.")

    await _safe_edit_or_send_game_board(
        bot,
        game,
        chat_settings,
        note="\n".join(note_lines),
    )
    await _send_game_feed_event(bot, game, text="\n".join(note_lines))

    if result.vote_opened:
        await query.answer("Характеристика раскрыта. Голосование открыто.", show_alert=False)
        return
    await query.answer("Характеристика раскрыта", show_alert=False)


@router.callback_query(F.data.startswith("gbkv:"))
async def bunker_vote_callback(query: CallbackQuery, bot: Bot, chat_settings: ChatSettings, economy_repo) -> None:
    if query.data is None or query.from_user is None:
        await query.answer()
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Некорректное голосование", show_alert=False)
        return

    _, game_id, payload = parts
    if payload == "noop":
        game, voted_count, total_alive, leader_user_id, leader_votes = await GAME_STORE.bunker_get_vote_snapshot(game_id=game_id)
        if game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return

        leader_text = "пока нет"
        if leader_user_id is not None:
            leader_label = game.players.get(leader_user_id, f"user:{leader_user_id}")
            leader_text = f"{leader_label} ({leader_votes})"
        await _safe_edit_or_send_game_board(
            bot,
            game,
            chat_settings,
            note=f"<b>Прогресс голосования:</b> {voted_count}/{total_alive}. Лидер: {escape(leader_text)}.",
        )
        if query.message is not None and query.message.chat.type == "private":
            try:
                await query.message.edit_text(
                    _render_private_bunker_status_text(game, actor_user_id=query.from_user.id),
                    parse_mode="HTML",
                    reply_markup=_build_private_phase_keyboard(game, actor_user_id=query.from_user.id),
                )
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    raise
        await query.answer(f"Голосов: {voted_count}/{total_alive}", show_alert=False)
        return

    if not payload.isdigit():
        await query.answer("Некорректная цель", show_alert=False)
        return

    target_user_id = int(payload)
    game, previous_target_user_id, error = await GAME_STORE.bunker_register_vote(
        game_id=game_id,
        voter_user_id=query.from_user.id,
        target_user_id=target_user_id,
    )
    if error:
        await query.answer(error, show_alert=True)
        return
    if game is None:
        await query.answer("Игра не найдена", show_alert=False)
        return

    if query.message is not None and query.message.chat.type == "private":
        try:
            await query.message.edit_text(
                _render_private_bunker_status_text(game, actor_user_id=query.from_user.id),
                parse_mode="HTML",
                reply_markup=_build_private_phase_keyboard(game, actor_user_id=query.from_user.id),
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise

    snapshot_game, voted_count, total_alive, _, _ = await GAME_STORE.bunker_get_vote_snapshot(game_id=game_id)
    target_label = game.players.get(target_user_id, f"user:{target_user_id}")
    voter_label = game.players.get(query.from_user.id, f"user:{query.from_user.id}")
    vote_line = f"<b>Голос:</b> {_mention(query.from_user.id, voter_label)} против {escape(target_label)}."
    note = f"{vote_line}\n<b>Прогресс голосования:</b> {voted_count}/{total_alive}"
    if snapshot_game is not None:
        await _safe_edit_or_send_game_board(
            bot,
            snapshot_game,
            chat_settings,
            note=note,
        )
    await _send_game_feed_event(bot, game, text=note)

    if total_alive > 0 and voted_count == total_alive:
        await query.answer("Голос принят. Все проголосовали, считаем итог.", show_alert=False)
        await _resolve_bunker_vote(
            bot,
            game.game_id,
            chat_settings,
            economy_repo=economy_repo,
            force=False,
            triggered_by_auto=True,
        )
        return

    if previous_target_user_id is None:
        await query.answer("Голос принят", show_alert=False)
        return
    if previous_target_user_id == target_user_id:
        await query.answer("Этот голос уже учтён", show_alert=False)
        return
    await query.answer("Голос обновлён", show_alert=False)


@router.callback_query(F.data.startswith("gwho:"))
async def whoami_answer_callback(query: CallbackQuery, bot: Bot, chat_settings: ChatSettings, activity_repo) -> None:
    if query.data is None or query.from_user is None:
        await query.answer()
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Некорректный ответ", show_alert=False)
        return

    _, game_id, answer_code = parts
    if answer_code not in {"yes", "no", "unknown", "irrelevant"}:
        await query.answer("Некорректный ответ", show_alert=False)
        return

    current_game = await GAME_STORE.get_game(game_id)
    if current_game is None:
        await query.answer("Игра не найдена", show_alert=False)
        return
    await _refresh_game_player_label(
        activity_repo,
        game=current_game,
        chat_id=current_game.chat_id,
        user_id=query.from_user.id,
        username=query.from_user.username,
        first_name=query.from_user.first_name,
        last_name=query.from_user.last_name,
    )

    game, resolution, error = await GAME_STORE.whoami_answer_question(
        game_id=game_id,
        responder_user_id=query.from_user.id,
        answer_code=answer_code,  # type: ignore[arg-type]
    )
    if error:
        await query.answer(error, show_alert=True)
        return
    if game is None or resolution is None:
        await query.answer("Игра не найдена", show_alert=False)
        return
    if query.message is not None and query.message.chat.id != game.chat_id:
        await query.answer("Эта кнопка из другого чата", show_alert=False)
        return

    await _safe_edit_or_send_game_board(
        bot,
        game,
        chat_settings,
        note=_format_whoami_answer_resolution(game, resolution),
    )
    await query.answer(f"Ответ: {resolution.answer_label}", show_alert=False)


@router.callback_query(F.data.startswith("gspy:"))
async def spy_vote_callback(query: CallbackQuery, bot: Bot, chat_settings: ChatSettings, economy_repo) -> None:
    if query.data is None or query.from_user is None:
        await query.answer()
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Некорректное голосование", show_alert=False)
        return

    _, game_id, payload = parts
    if payload == "noop":
        game, voted_count, total_players, leader_user_id, leader_votes = await GAME_STORE.spy_get_vote_snapshot(game_id=game_id)
        if game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return
        if query.message is None or query.message.chat.id != game.chat_id:
            await query.answer("Эта кнопка из другого чата", show_alert=False)
            return
        leader_text = "пока нет"
        if leader_user_id is not None:
            leader_text = f"{game.players.get(leader_user_id, f'user:{leader_user_id}')} ({leader_votes})"
        await _safe_edit_or_send_game_board(
            bot,
            game,
            chat_settings,
            note=f"<b>Прогресс голосования:</b> {voted_count}/{total_players}. Лидер: {escape(leader_text)}.",
        )
        await query.answer(f"Голосов: {voted_count}/{total_players}", show_alert=False)
        return

    if not payload.isdigit():
        await query.answer("Некорректная цель", show_alert=False)
        return

    target_user_id = int(payload)
    game, resolution, previous_target_user_id, error = await GAME_STORE.spy_register_vote(
        game_id=game_id,
        voter_user_id=query.from_user.id,
        target_user_id=target_user_id,
    )
    if error:
        await query.answer(error, show_alert=True)
        return
    if game is None:
        await query.answer("Игра не найдена", show_alert=False)
        return
    if query.message is None or query.message.chat.id != game.chat_id:
        await query.answer("Эта кнопка из другого чата", show_alert=False)
        return

    target_label = game.players.get(target_user_id, f"user:{target_user_id}")
    voter_label = game.players.get(query.from_user.id, f"user:{query.from_user.id}")

    if resolution is None:
        game_snapshot, voted_count, total_players, leader_user_id, leader_votes = await GAME_STORE.spy_get_vote_snapshot(game_id=game.game_id)
        if game_snapshot is not None:
            leader_text = "пока нет"
            if leader_user_id is not None:
                leader_text = f"{game.players.get(leader_user_id, f'user:{leader_user_id}')} ({leader_votes})"
            await _safe_edit_or_send_game_board(
                bot,
                game_snapshot,
                chat_settings,
                note=(
                    f"<b>Подозрение:</b> {_mention(query.from_user.id, voter_label)} -> {escape(target_label)}.\n"
                    f"<b>Прогресс:</b> {voted_count}/{total_players}, лидер: {escape(leader_text)}."
                ),
            )

        if previous_target_user_id is None:
            await query.answer(f"Голос принят: {target_label}", show_alert=False)
        elif previous_target_user_id == target_user_id:
            await query.answer("Этот голос уже учтён", show_alert=False)
        else:
            await query.answer(f"Голос обновлён: {target_label}", show_alert=False)
        return

    reward_line = await _grant_game_rewards_if_needed(
        game,
        economy_repo=economy_repo,
        chat_settings=chat_settings,
    )
    result_text = _format_spy_vote_resolution(game, resolution)
    note = result_text
    if reward_line:
        note = f"{note}\n{reward_line}"

    await _safe_edit_or_send_game_board(
        bot,
        game,
        chat_settings,
        note=note,
        include_reveal=True,
    )
    await _send_game_feed_event(
        bot,
        game,
        text=(
            f"<b>Ведущий:</b> {result_text}"
            + (f"\n{reward_line}" if reward_line else "")
            + f"\n{_render_roles_reveal(game)}"
        ),
    )
    await query.answer("Голос принят. Игра завершена.", show_alert=False)


@router.callback_query(F.data.startswith("gmact:"))
async def mafia_night_action_callback(query: CallbackQuery, bot: Bot, chat_settings: ChatSettings, economy_repo) -> None:
    if query.data is None or query.from_user is None:
        await query.answer()
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Некорректное действие", show_alert=False)
        return

    _, game_id, target_raw = parts
    if not target_raw.isdigit():
        await query.answer("Некорректная цель", show_alert=False)
        return

    target_user_id = int(target_raw)
    game, error = await GAME_STORE.mafia_register_night_action(
        game_id=game_id,
        actor_user_id=query.from_user.id,
        target_user_id=target_user_id,
    )
    if error:
        await query.answer(error, show_alert=True)
        return

    if game is None:
        await query.answer("Игра не найдена", show_alert=False)
        return

    target_label = game.players.get(target_user_id, f"user:{target_user_id}")

    if query.message is not None:
        role = game.roles.get(query.from_user.id, "-")
        status_line = "Можно изменить выбор до конца ночи."
        if role == MAFIA_ROLE_JOURNALIST and query.from_user.id in game.journalist_first_pick:
            status_line = "Первый выбор сохранён. Теперь выберите второго игрока."
        if role == MAFIA_ROLE_CHILD and query.from_user.id in game.child_revealed:
            status_line = "Вы раскрылись как подтверждённый мирный."
        try:
            await query.message.edit_text(
                (
                    f"<b>Ваш ход сохранён.</b>\n"
                    f"<b>Роль:</b> <code>{escape(role)}</code>\n"
                    f"<b>Цель:</b> {escape(target_label)}\n"
                    f"{status_line}"
                ),
                parse_mode="HTML",
                reply_markup=_build_private_night_action_keyboard(game, actor_user_id=query.from_user.id),
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise

    _, ready, _ = await GAME_STORE.mafia_is_night_ready(game_id=game_id)
    if ready:
        _cancel_phase_timer(game.game_id)
        await query.answer(f"Ход принят: {target_label}. Все действия собраны.", show_alert=False)
        await _advance_mafia_night(bot, game.game_id, chat_settings, economy_repo=economy_repo, triggered_by_timer=False)
        return

    await query.answer(f"Цель принята: {target_label}", show_alert=False)


@router.callback_query(F.data.startswith("gmvote:"))
async def mafia_day_vote_callback(query: CallbackQuery, bot: Bot, chat_settings: ChatSettings, economy_repo) -> None:
    if query.data is None or query.from_user is None:
        await query.answer()
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Некорректное голосование", show_alert=False)
        return

    _, game_id, target_raw = parts
    if not target_raw.isdigit():
        await query.answer("Некорректная цель", show_alert=False)
        return

    target_user_id = int(target_raw)
    game, previous_target_user_id, error = await GAME_STORE.mafia_register_day_vote(
        game_id=game_id,
        voter_user_id=query.from_user.id,
        target_user_id=target_user_id,
    )
    if error:
        await query.answer(error, show_alert=True)
        return

    if game is None:
        await query.answer("Игра не найдена", show_alert=False)
        return

    voter_label = game.players.get(query.from_user.id, f"user:{query.from_user.id}")
    target_label = game.players.get(target_user_id, f"user:{target_user_id}")

    if previous_target_user_id is None:
        vote_text = f"<b>Голос:</b> {_mention(query.from_user.id, voter_label)} проголосовал против {escape(target_label)}."
    elif previous_target_user_id == target_user_id:
        vote_text = f"<b>Голос:</b> {_mention(query.from_user.id, voter_label)} подтвердил голос против {escape(target_label)}."
    else:
        prev_label = game.players.get(previous_target_user_id, f"user:{previous_target_user_id}")
        vote_text = (
            f"<b>Голос:</b> {_mention(query.from_user.id, voter_label)} изменил голос: "
            f"{escape(prev_label)} -> {escape(target_label)}."
        )

    snapshot_game, voted_count, alive_count = await GAME_STORE.mafia_get_vote_snapshot(game_id=game_id)
    if snapshot_game is not None:
        await _safe_edit_or_send_game_board(
            bot,
            snapshot_game,
            chat_settings,
            note=f"{vote_text}\n<b>Текущий прогресс голосования:</b> {voted_count}/{alive_count}",
        )

        if query.message is not None and query.message.chat.type == "private":
            try:
                await query.message.edit_text(
                    _render_private_day_vote_text(
                        snapshot_game,
                        actor_user_id=query.from_user.id,
                        voted_count=voted_count,
                        alive_count=alive_count,
                    ),
                    parse_mode="HTML",
                    reply_markup=_build_private_day_vote_keyboard(
                        snapshot_game,
                        actor_user_id=query.from_user.id,
                    ),
                )
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    raise

    if voted_count == alive_count and alive_count > 0:
        _cancel_phase_timer(game.game_id)
        await query.answer("Голос принят. Все проголосовали, считаем итоги.", show_alert=False)
        await _resolve_mafia_day_vote(
            bot,
            game.game_id,
            chat_settings,
            economy_repo=economy_repo,
            triggered_by_timer=False,
        )
        return

    await query.answer("Голос принят", show_alert=False)


@router.callback_query(F.data.startswith("gmconfirm:"))
async def mafia_execution_confirm_callback(query: CallbackQuery, bot: Bot, chat_settings: ChatSettings, economy_repo) -> None:
    if query.data is None or query.from_user is None:
        await query.answer()
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Некорректное подтверждение", show_alert=False)
        return

    _, game_id, decision_raw = parts
    if decision_raw == "noop":
        snapshot_game, voted_count, alive_count, yes_count, no_count = await GAME_STORE.mafia_get_execution_confirm_snapshot(game_id=game_id)
        if snapshot_game is None:
            await query.answer("Игра не найдена", show_alert=False)
            return
        if query.message is not None and query.message.chat.id == snapshot_game.chat_id and snapshot_game.execution_confirm_message_id != query.message.message_id:
            await GAME_STORE.set_execution_confirm_message_id(game_id=snapshot_game.game_id, message_id=query.message.message_id)
            snapshot_game.execution_confirm_message_id = query.message.message_id
        await _sync_execution_confirm_message(bot, snapshot_game, force_new=False)
        await query.answer(
            f"Голоса: ✅ {yes_count} | ❌ {no_count} | 🗳 {voted_count}/{alive_count}",
            show_alert=False,
        )
        return

    if decision_raw not in {"yes", "no"}:
        await query.answer("Некорректное решение", show_alert=False)
        return

    approve = decision_raw == "yes"
    game, previous_vote, error = await GAME_STORE.mafia_register_execution_confirm_vote(
        game_id=game_id,
        voter_user_id=query.from_user.id,
        approve=approve,
    )
    if error:
        await query.answer(error, show_alert=True)
        return

    if game is None:
        await query.answer("Игра не найдена", show_alert=False)
        return

    if query.message is not None and query.message.chat.id == game.chat_id and game.execution_confirm_message_id != query.message.message_id:
        await GAME_STORE.set_execution_confirm_message_id(game_id=game.game_id, message_id=query.message.message_id)
        game.execution_confirm_message_id = query.message.message_id

    snapshot_game, voted_count, alive_count, yes_count, no_count = await GAME_STORE.mafia_get_execution_confirm_snapshot(game_id=game_id)
    if snapshot_game is not None:
        await _safe_edit_or_send_game_board(
            bot,
            snapshot_game,
            chat_settings,
            note=f"<b>Подтверждение:</b> да={yes_count}, нет={no_count}, проголосовали {voted_count}/{alive_count}",
        )
        await _sync_execution_confirm_message(bot, snapshot_game, force_new=False)

    if voted_count == alive_count and alive_count > 0:
        _cancel_phase_timer(game.game_id)
        await query.answer("Голос принят. Все проголосовали, подводим итог.", show_alert=False)
        await _resolve_mafia_execution_confirm(
            bot,
            game.game_id,
            chat_settings,
            economy_repo=economy_repo,
            triggered_by_timer=False,
        )
        return

    if previous_vote is None:
        await query.answer("Решение принято", show_alert=False)
        return

    if previous_vote == approve:
        await query.answer("Решение уже учтено", show_alert=False)
        return

    await query.answer("Решение обновлено", show_alert=False)


@router.message(F.chat.type.in_(("group", "supergroup")), F.text, ~F.text.startswith("/"))
async def whoami_group_message_handler(message: Message, bot: Bot, chat_settings: ChatSettings, economy_repo, activity_repo) -> None:
    text = (message.text or "").strip()
    active_game = await GAME_STORE.get_active_game_for_chat(chat_id=message.chat.id)
    if not _should_handle_whoami_group_text(
        active_game,
        user_id=message.from_user.id if message.from_user is not None else None,
        text=text,
    ):
        raise SkipHandler()

    actor_label = await _refresh_game_player_label(
        activity_repo,
        game=active_game,
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )

    guess = _extract_whoami_guess(text)
    if guess is not None:
        game, resolution, error = await GAME_STORE.whoami_guess_identity(
            game_id=active_game.game_id,
            actor_user_id=message.from_user.id,
            guess_text=guess,
        )
        if error:
            await message.reply(error)
            return
        if game is None or resolution is None:
            return

        note = _format_whoami_guess_resolution(game, resolution)
        if resolution.guessed_correctly:
            reward_line = await _grant_game_rewards_if_needed(
                game,
                economy_repo=economy_repo,
                chat_settings=chat_settings,
            )
            if reward_line:
                note = f"{note}\n{reward_line}"
            await _safe_edit_or_send_game_board(bot, game, chat_settings, note=note)
            if resolution.finished:
                await _send_game_feed_event(
                    bot,
                    game,
                    text=note + "\n" + _render_roles_reveal(game),
                )
            return

        await _safe_edit_or_send_game_board(bot, game, chat_settings, note=note)
        return

    game, result, error = await GAME_STORE.whoami_submit_question(
        game_id=active_game.game_id,
        actor_user_id=message.from_user.id,
        question_text=text,
    )
    if error:
        await message.reply(error)
        return
    if game is None or result is None:
        return

    await _safe_edit_or_send_game_board(
        bot,
        game,
        chat_settings,
        note=(
            f"<b>Вопрос от:</b> {_mention(result.actor_user_id, result.actor_user_label or actor_label or '-')}\n"
            f"<b>Текст:</b> {escape(result.question_text)}"
        ),
    )


@router.message(F.text.regexp(r"^\s*-?\d+\s*$"))
async def number_guess_handler(message: Message, bot: Bot, chat_settings: ChatSettings, economy_repo) -> None:
    if message.chat.type not in {"group", "supergroup"} or message.from_user is None:
        raise SkipHandler()

    text = (message.text or "").strip()
    if not text or text.startswith("/") or not text.lstrip("-").isdigit():
        raise SkipHandler()

    active_game = await GAME_STORE.get_active_game_for_chat(chat_id=message.chat.id)
    if not _should_handle_number_guess(active_game):
        raise SkipHandler()

    guess = int(text)
    game, result, error = await GAME_STORE.number_register_guess(
        game_id=active_game.game_id,
        user_id=message.from_user.id,
        guess=guess,
    )
    if error:
        await message.reply(error)
        return
    if game is None or result is None:
        return

    player_label = game.players.get(message.from_user.id, _user_label(message.from_user.id, message.from_user.username, message.from_user.first_name, message.from_user.last_name))
    if result.direction == "up":
        await _safe_edit_or_send_game_board(
            bot,
            game,
            chat_settings,
            note=(
                f"<b>Последняя попытка:</b> {_mention(message.from_user.id, player_label)} -> <code>{result.guess}</code>, нужно больше.\n"
                f"<b>Близость:</b> {number_distance_hint(result.distance_to_secret)} | "
                f"<b>Попыток:</b> {result.attempts_for_user} (лично) / {result.attempts_total} (всего)"
            ),
        )
        return

    if result.direction == "down":
        await _safe_edit_or_send_game_board(
            bot,
            game,
            chat_settings,
            note=(
                f"<b>Последняя попытка:</b> {_mention(message.from_user.id, player_label)} -> <code>{result.guess}</code>, нужно меньше.\n"
                f"<b>Близость:</b> {number_distance_hint(result.distance_to_secret)} | "
                f"<b>Попыток:</b> {result.attempts_for_user} (лично) / {result.attempts_total} (всего)"
            ),
        )
        return

    winner_text = result.winner_text or f"Победа: {player_label}"
    reward_line = await _grant_game_rewards_if_needed(
        game,
        economy_repo=economy_repo,
        chat_settings=chat_settings,
        winner_user_ids_override={result.winner_user_id} if result.winner_user_id is not None else None,
    )
    await message.answer(
        f"🎉 {_mention(message.from_user.id, player_label)} угадал(а) число <code>{result.guess}</code>!",
        parse_mode="HTML",
        disable_notification=True,
    )
    await _safe_edit_or_send_game_board(bot, game, chat_settings, note=f"<b>Финиш:</b> {escape(winner_text)}")
    await _send_game_feed_event(
        bot,
        game,
        text=(
            f"<b>Ведущий:</b> Игра «Угадай число» завершена.\n"
            f"<b>Победитель:</b> {_mention(message.from_user.id, player_label)}\n"
            f"<b>Число:</b> <code>{result.guess}</code>\n"
            f"<b>Попыток:</b> {result.attempts_total}"
            + (f"\n{reward_line}" if reward_line else "")
        ),
    )


@router.message(F.chat.type == "private", F.text, ~F.text.startswith("/"))
async def bred_private_answer_handler(message: Message, bot: Bot, chat_settings: ChatSettings, activity_repo) -> None:
    text = (message.text or "").strip()
    if message.from_user is None:
        raise SkipHandler()

    game = await GAME_STORE.get_latest_bred_submission_game_for_user(user_id=message.from_user.id)
    if not _should_handle_bred_private_answer(game, text=text):
        raise SkipHandler()

    await _refresh_game_player_label(
        activity_repo,
        game=game,
        chat_id=game.chat_id,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )

    updated_game, result, error = await GAME_STORE.bred_submit_lie(
        game_id=game.game_id,
        user_id=message.from_user.id,
        lie_text=text,
    )
    if error:
        await message.answer(error)
        return
    if updated_game is None or result is None:
        return

    if result.previous_lie is None:
        status_text = "Ответ сохранён."
    elif result.previous_lie == text:
        status_text = "Этот ответ уже учтён."
    else:
        status_text = "Ответ обновлён."

    await message.answer(f"{status_text}\nПрогресс: {result.submitted_count}/{result.total_players}.")

    if result.vote_opened:
        await _safe_edit_or_send_game_board(
            bot,
            updated_game,
            chat_settings,
            note="<b>Этап:</b> все ответы получены, открыто голосование.",
        )
        return

    await _safe_edit_or_send_game_board(
        bot,
        updated_game,
        chat_settings,
        note=f"<b>Ответов в ЛС:</b> {result.submitted_count}/{result.total_players}",
    )


@router.message(Command("role"))
async def role_command(message: Message, command: CommandObject) -> None:
    if message.chat.type != "private":
        await message.answer("Роль можно смотреть в личке с ботом.")
        return

    if message.from_user is None:
        return

    args = (command.args or "").strip()
    if args:
        game_id = args[5:] if args.startswith("game_") else args
        await _show_role_for_user(message, game_id=game_id)
        return

    game, role = await GAME_STORE.get_latest_role_game_for_user(user_id=message.from_user.id)
    if game is None or role is None:
        bunker_game = await GAME_STORE.get_latest_bunker_game_for_user(user_id=message.from_user.id)
        if bunker_game is None:
            await message.answer(
                "У вас нет активной секретной роли или карточки. Запустите игру в группе через <code>/game</code>.",
                parse_mode="HTML",
            )
            return
        await _show_role_for_user(message, game_id=bunker_game.game_id)
        return

    await _show_role_for_user(message, game_id=game.game_id)


@router.message(Command("start"))
async def start_command(message: Message, economy_repo, activity_repo, settings) -> None:
    if message.chat.type != "private":
        return

    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("game_"):
        await _show_role_for_user(message, game_id=parts[1][5:])
        return
    if len(parts) == 2 and parts[1].startswith("eco_"):
        raw = parts[1][4:]
        try:
            chat_id = int(raw)
        except ValueError:
            chat_id = None
        if chat_id is not None and message.from_user is not None:
            await economy_repo.set_private_chat_context(user_id=message.from_user.id, chat_id=chat_id)
            await message.answer(
                (
                    "<b>Контекст экономики установлен.</b>\n"
                    f"Чат: <code>{chat_id}</code>\n"
                    "Теперь можно использовать команды экономики в local-режиме, например <code>/eco local</code>."
                ),
                parse_mode="HTML",
            )
            return
        await message.answer("Некорректный deep-link экономики.")
        return

    await send_private_start_panel(message, activity_repo, economy_repo, settings)
