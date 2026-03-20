from __future__ import annotations

import random
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Literal

from gacha_service.application.catalog import get_banner_config, get_card_for_banner, get_cards_for_banner
from gacha_service.domain.models import (
    CurrencyGrantResult,
    GachaCard,
    PlayerState,
    PullResult,
    RARITY_LABELS,
    SellOffer,
    SellResult,
    format_element_icon,
    format_element_label,
    format_region_label,
    resolve_rank,
)
from gacha_service.infrastructure.repository import GachaRepository

PAID_PULL_PRICE = 160


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_duration(seconds: int) -> str:
    normalized = max(0, seconds)
    hours, remainder = divmod(normalized, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def _format_percentage(value: float) -> str:
    rounded = round(value, 1)
    if rounded.is_integer():
        return str(int(rounded))
    return f"{rounded:.1f}"


@dataclass(frozen=True, slots=True)
class _BannerTerms:
    xp_label: str
    rank_label: str
    currency_label: str


def _get_banner_terms(banner: str) -> _BannerTerms:
    if banner == "hsr":
        return _BannerTerms(
            xp_label="Опыт освоения",
            rank_label="Уровень освоения",
            currency_label="Звездный нефрит",
        )
    return _BannerTerms(
        xp_label="Опыт приключений",
        rank_label="Ранг приключений",
        currency_label="Примогемы",
    )


def _get_duplicate_stage_prefix(banner: str) -> str | None:
    if banner == "genshin":
        return "С"
    if banner == "hsr":
        return "E"
    return None


def _get_progression_reward_label(banner: str) -> str:
    if banner == "hsr":
        return "эйдолон"
    return "созвездие"


def _pick_card(cards: tuple[GachaCard, ...], rng: random.Random) -> GachaCard:
    weights = [card.weight for card in cards]
    return rng.choices(cards, weights=weights, k=1)[0]


def _resolve_reward_variant(
    card: GachaCard,
    *,
    existing_copies: int,
) -> tuple[GachaCard, Literal["new", "constellation", "duplicate"], bool]:
    if existing_copies <= 0:
        return card, "new", False

    stage_prefix = _get_duplicate_stage_prefix(card.banner)
    if stage_prefix is None:
        return card, "duplicate", False

    stage_level = min(existing_copies, 6)
    if existing_copies <= 6:
        return replace(card, name=f"{card.name} ({stage_prefix}{stage_level})"), "constellation", False
    return replace(card, name=f"{card.name} ({stage_prefix}6) дубликат", primogems=card.primogems * 2), "duplicate", True


def _resolve_adventure_xp_gain(base_xp: int, existing_copies: int) -> int:
    if existing_copies <= 0:
        multiplier = 1.0
    elif existing_copies == 1:
        multiplier = 0.5
    elif existing_copies == 2:
        multiplier = 0.25
    else:
        multiplier = 0.1
    return max(1, int(round(base_xp * multiplier)))


def _render_card_origin_block(card: GachaCard) -> str:
    if card.banner != "genshin":
        return ""
    region_label = format_region_label(card.region_code)
    element_label = format_element_label(card.element_code)
    element_icon = format_element_icon(card.element_code)
    return f"🌍 Регион: {region_label}\n{element_icon} Стихия: {element_label}\n"


def _render_success_message(
    card: GachaCard,
    player: PlayerState,
    *,
    seconds_remaining: int,
    outcome: Literal["new", "constellation", "duplicate"],
    copies_owned: int,
    adventure_xp_gained: int,
    ownership_percent: float,
    purchase_price: int = 0,
) -> str:
    rank, xp_into_rank, xp_for_next_rank = resolve_rank(player.adventure_xp)
    rarity_label = RARITY_LABELS[card.rarity]
    terms = _get_banner_terms(card.banner)
    origin_block = _render_card_origin_block(card)
    if outcome == "new":
        card_line = "🍀 Вы получили новую карту"
    elif outcome == "constellation":
        card_line = f"✨ Вы получили {_get_progression_reward_label(card.banner)}"
    else:
        card_line = "♻️ Вам выпал дубликат"
    purchase_block = ""
    if purchase_price > 0:
        purchase_block = f"🛒 Платная крутка: -{purchase_price}\n"
    return (
        f"{card_line}: {card.name}\n\n"
        f"⬜ Редкость: {rarity_label}\n"
        f"{origin_block}"
        "\n"
        f"🗂 Копий у вас: {copies_owned}\n"
        f"👥 Такая карта есть у {_format_percentage(ownership_percent)}% игроков\n"
        f"🧭 {terms.xp_label}: +{adventure_xp_gained}\n"
        f"🌟 Очки: +{card.points} [{player.total_points}]\n"
        f"💠 {terms.currency_label}: +{card.primogems} [{player.total_primogems}]\n"
        f"{purchase_block}"
        f"🧭 {terms.rank_label}: {rank} ({xp_into_rank}/{xp_for_next_rank})\n\n"
        f"⌛️ Вы сможете получить карту через: {_format_duration(seconds_remaining)}"
    )


def _render_cooldown_message(player: PlayerState, *, banner: str, seconds_remaining: int) -> str:
    rank, xp_into_rank, xp_for_next_rank = resolve_rank(player.adventure_xp)
    terms = _get_banner_terms(banner)
    return (
        "⏳ Новая карта пока недоступна.\n\n"
        f"🧭 {terms.rank_label}: {rank} ({xp_into_rank}/{xp_for_next_rank})\n"
        f"🌟 Очки: [{player.total_points}]\n"
        f"💠 {terms.currency_label}: [{player.total_primogems}]\n\n"
        f"⌛️ До следующей крутки: {_format_duration(seconds_remaining)}"
    )


def _render_admin_grant_message(
    card: GachaCard,
    player: PlayerState,
    *,
    outcome: Literal["new", "constellation", "duplicate"],
    copies_owned: int,
    adventure_xp_gained: int,
    ownership_percent: float,
) -> str:
    rank, xp_into_rank, xp_for_next_rank = resolve_rank(player.adventure_xp)
    rarity_label = RARITY_LABELS[card.rarity]
    terms = _get_banner_terms(card.banner)
    origin_block = _render_card_origin_block(card)
    if outcome == "new":
        card_line = "🎁 Админ выдал новую карту"
    elif outcome == "constellation":
        card_line = f"🎁 Админ выдал {_get_progression_reward_label(card.banner)}"
    else:
        card_line = "🎁 Админ выдал дубликат"
    return (
        f"{card_line}: {card.name}\n\n"
        f"⬜ Редкость: {rarity_label}\n"
        f"{origin_block}"
        "\n"
        f"🗂 Копий у пользователя: {copies_owned}\n"
        f"👥 Такая карта есть у {_format_percentage(ownership_percent)}% игроков\n"
        f"🧭 {terms.xp_label}: +{adventure_xp_gained}\n"
        f"🌟 Очки: +{card.points} [{player.total_points}]\n"
        f"💠 {terms.currency_label}: +{card.primogems} [{player.total_primogems}]\n"
        f"🧭 {terms.rank_label}: {rank} ({xp_into_rank}/{xp_for_next_rank})"
    )


def _render_sell_message(*, banner: str, sale_price: int, player: PlayerState) -> str:
    terms = _get_banner_terms(banner)
    return f"Продажа: +{sale_price} {terms.currency_label.lower()}. Баланс: {player.total_primogems}."


def _render_currency_grant_message(*, banner: str, amount: int, player: PlayerState) -> str:
    terms = _get_banner_terms(banner)
    if amount > 0:
        return f"Баланс пополнен: +{amount} {terms.currency_label.lower()}. На счету: {player.total_primogems}."
    normalized_amount = abs(amount)
    return f"Баланс скорректирован: -{normalized_amount} {terms.currency_label.lower()}. На счету: {player.total_primogems}."


class GachaService:
    def __init__(self, repo: GachaRepository, *, default_cooldown_seconds: int | None = None, rng: random.Random | None = None) -> None:
        self._repo = repo
        self._default_cooldown_seconds = default_cooldown_seconds
        self._rng = rng or random.Random()

    async def pull(self, *, user_id: int, username: str | None, banner: str, now: datetime | None = None) -> PullResult:
        current_time = now or _utc_now()
        player = await self._repo.get_or_create_player(user_id=user_id, username=username, banner=banner)
        next_pull_at = _coerce_utc_datetime(await self._repo.get_banner_cooldown(user_id=user_id, banner=banner))
        banner_config = get_banner_config(banner)
        cooldown_seconds = self._default_cooldown_seconds or banner_config.cooldown_seconds

        if next_pull_at is not None and current_time < next_pull_at:
            seconds_remaining = int((next_pull_at - current_time).total_seconds())
            return PullResult(
                status="cooldown",
                message=_render_cooldown_message(player, banner=banner, seconds_remaining=seconds_remaining),
                card=None,
                player=player,
                cooldown_until=next_pull_at,
                seconds_remaining=seconds_remaining,
            )

        return await self._execute_pull(
            user_id=user_id,
            username=username,
            banner=banner,
            pulled_at=current_time,
            cooldown_seconds=cooldown_seconds,
            purchase_price=0,
            pull_source="free",
        )

    async def pull_purchase(self, *, user_id: int, username: str | None, banner: str, now: datetime | None = None) -> PullResult:
        current_time = now or _utc_now()
        player = await self._repo.get_or_create_player(user_id=user_id, username=username, banner=banner)
        if player.total_primogems < PAID_PULL_PRICE:
            terms = _get_banner_terms(banner)
            raise ValueError(
                f"Недостаточно {terms.currency_label.lower()}. Нужно {PAID_PULL_PRICE}, у вас {player.total_primogems}."
            )

        banner_config = get_banner_config(banner)
        cooldown_seconds = self._default_cooldown_seconds or banner_config.cooldown_seconds
        return await self._execute_pull(
            user_id=user_id,
            username=username,
            banner=banner,
            pulled_at=current_time,
            cooldown_seconds=cooldown_seconds,
            purchase_price=PAID_PULL_PRICE,
            pull_source="paid",
        )

    async def _execute_pull(
        self,
        *,
        user_id: int,
        username: str | None,
        banner: str,
        pulled_at: datetime,
        cooldown_seconds: int,
        purchase_price: int,
        pull_source: str,
    ) -> PullResult:
        base_card = _pick_card(get_cards_for_banner(banner), self._rng)
        existing_copies = await self._repo.get_card_copies(user_id=user_id, banner=banner, card_code=base_card.code)
        card, outcome, sellable = _resolve_reward_variant(base_card, existing_copies=existing_copies)
        adventure_xp_gained = _resolve_adventure_xp_gain(card.adventure_xp, existing_copies)
        next_pull_at = pulled_at + timedelta(seconds=cooldown_seconds)
        updated_player, copies_owned, pull_id = await self._repo.apply_pull(
            user_id=user_id,
            username=username,
            card=card,
            adventure_xp_gained=adventure_xp_gained,
            pulled_at=pulled_at,
            next_pull_at=next_pull_at,
            pull_source=pull_source,
            purchase_price=purchase_price,
            base_currency_price=base_card.primogems,
            sellable=sellable,
        )
        owners_with_card, total_banner_players = await self._repo.get_card_ownership_stats(
            banner=card.banner,
            card_code=base_card.code,
        )
        ownership_percent = 0.0
        if total_banner_players > 0:
            ownership_percent = owners_with_card / total_banner_players * 100
        is_new = existing_copies == 0
        sell_offer = SellOffer(sale_price=base_card.primogems * 3) if sellable else None
        return PullResult(
            status="ok",
            message=_render_success_message(
                card,
                updated_player,
                seconds_remaining=cooldown_seconds,
                outcome=outcome,
                copies_owned=copies_owned,
                adventure_xp_gained=adventure_xp_gained,
                ownership_percent=ownership_percent,
                purchase_price=purchase_price,
            ),
            card=card,
            player=updated_player,
            cooldown_until=next_pull_at,
            seconds_remaining=cooldown_seconds,
            is_new=is_new,
            copies_owned=copies_owned,
            adventure_xp_gained=adventure_xp_gained,
            pull_id=pull_id,
            sell_offer=sell_offer,
        )

    async def grant_card(
        self,
        *,
        user_id: int,
        username: str | None,
        banner: str,
        card_code: str,
        now: datetime | None = None,
    ) -> PullResult:
        current_time = now or _utc_now()
        await self._repo.get_or_create_player(user_id=user_id, username=username, banner=banner)
        base_card = get_card_for_banner(banner, card_code)
        existing_copies = await self._repo.get_card_copies(user_id=user_id, banner=banner, card_code=base_card.code)
        card, outcome, sellable = _resolve_reward_variant(base_card, existing_copies=existing_copies)
        adventure_xp_gained = _resolve_adventure_xp_gain(card.adventure_xp, existing_copies)
        current_cooldown = _coerce_utc_datetime(await self._repo.get_banner_cooldown(user_id=user_id, banner=banner))

        updated_player, copies_owned, pull_id = await self._repo.apply_pull(
            user_id=user_id,
            username=username,
            card=card,
            adventure_xp_gained=adventure_xp_gained,
            pulled_at=current_time,
            next_pull_at=current_cooldown,
            update_cooldown=False,
            pull_source="admin",
            purchase_price=0,
            base_currency_price=base_card.primogems,
            sellable=sellable,
        )
        owners_with_card, total_banner_players = await self._repo.get_card_ownership_stats(
            banner=card.banner,
            card_code=base_card.code,
        )
        ownership_percent = 0.0
        if total_banner_players > 0:
            ownership_percent = owners_with_card / total_banner_players * 100
        is_new = existing_copies == 0
        sell_offer = SellOffer(sale_price=base_card.primogems * 3) if sellable else None
        return PullResult(
            status="ok",
            message=_render_admin_grant_message(
                card,
                updated_player,
                outcome=outcome,
                copies_owned=copies_owned,
                adventure_xp_gained=adventure_xp_gained,
                ownership_percent=ownership_percent,
            ),
            card=card,
            player=updated_player,
            cooldown_until=current_cooldown or current_time,
            seconds_remaining=max(
                0,
                int(((current_cooldown or current_time) - current_time).total_seconds()),
            ),
            is_new=is_new,
            copies_owned=copies_owned,
            adventure_xp_gained=adventure_xp_gained,
            pull_id=pull_id,
            sell_offer=sell_offer,
        )

    async def sell_pull(self, *, user_id: int, pull_id: int, now: datetime | None = None) -> SellResult:
        sold_at = now or _utc_now()
        player, sale_price, banner, sold_timestamp = await self._repo.sell_pull(
            user_id=user_id,
            pull_id=pull_id,
            sold_at=sold_at,
        )
        return SellResult(
            status="ok",
            message=_render_sell_message(banner=banner, sale_price=sale_price, player=player),
            player=player,
            pull_id=pull_id,
            banner=banner,
            sale_price=sale_price,
            sold_at=sold_timestamp,
        )

    async def grant_currency(
        self,
        *,
        user_id: int,
        username: str | None,
        banner: str,
        amount: int,
    ) -> CurrencyGrantResult:
        get_banner_config(banner)
        player = await self._repo.adjust_banner_currency(
            user_id=user_id,
            username=username,
            banner=banner,
            amount=amount,
        )
        return CurrencyGrantResult(
            status="ok",
            message=_render_currency_grant_message(banner=banner, amount=amount, player=player),
            player=player,
            banner=banner,
            amount=amount,
        )
