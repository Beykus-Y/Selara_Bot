from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from gacha_service.application.catalog import get_banner_config, get_cards_for_banner
from gacha_service.domain.models import GachaCard, PlayerState, PullResult, RARITY_LABELS, resolve_rank
from gacha_service.infrastructure.repository import GachaRepository


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


def _pick_card(cards: tuple[GachaCard, ...], rng: random.Random) -> GachaCard:
    weights = [card.weight for card in cards]
    return rng.choices(cards, weights=weights, k=1)[0]


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


def _render_success_message(
    card: GachaCard,
    player: PlayerState,
    *,
    seconds_remaining: int,
    is_new: bool,
    copies_owned: int,
    adventure_xp_gained: int,
) -> str:
    rank, xp_into_rank, xp_for_next_rank = resolve_rank(player.adventure_xp)
    rarity_label = RARITY_LABELS[card.rarity]
    card_line = "🍀 Вы получили новую карту" if is_new else "♻️ Вам выпал дубликат"
    return (
        f"{card_line}: {card.name}\n\n"
        f"⬜ Редкость: {rarity_label}\n\n"
        f"🗂 Копий у вас: {copies_owned}\n"
        f"🧭 Опыт приключений: +{adventure_xp_gained}\n"
        f"🌟 Очки: +{card.points} [{player.total_points}]\n"
        f"💠 Примогемы: +{card.primogems} [{player.total_primogems}]\n"
        f"🧭 Ранг приключений: {rank} ({xp_into_rank}/{xp_for_next_rank})\n\n"
        f"⌛️ Вы сможете получить карту через: {_format_duration(seconds_remaining)}"
    )


def _render_cooldown_message(player: PlayerState, *, seconds_remaining: int) -> str:
    rank, xp_into_rank, xp_for_next_rank = resolve_rank(player.adventure_xp)
    return (
        "⏳ Новая карта пока недоступна.\n\n"
        f"🧭 Ранг приключений: {rank} ({xp_into_rank}/{xp_for_next_rank})\n"
        f"🌟 Очки: [{player.total_points}]\n"
        f"💠 Примогемы: [{player.total_primogems}]\n\n"
        f"⌛️ До следующей крутки: {_format_duration(seconds_remaining)}"
    )


class GachaService:
    def __init__(self, repo: GachaRepository, *, default_cooldown_seconds: int | None = None, rng: random.Random | None = None) -> None:
        self._repo = repo
        self._default_cooldown_seconds = default_cooldown_seconds
        self._rng = rng or random.Random()

    async def pull(self, *, user_id: int, username: str | None, banner: str, now: datetime | None = None) -> PullResult:
        current_time = now or _utc_now()
        player = await self._repo.get_or_create_player(user_id=user_id, username=username)
        next_pull_at = _coerce_utc_datetime(await self._repo.get_banner_cooldown(user_id=user_id, banner=banner))
        banner_config = get_banner_config(banner)
        cooldown_seconds = self._default_cooldown_seconds or banner_config.cooldown_seconds

        if next_pull_at is not None and current_time < next_pull_at:
            seconds_remaining = int((next_pull_at - current_time).total_seconds())
            return PullResult(
                status="cooldown",
                message=_render_cooldown_message(player, seconds_remaining=seconds_remaining),
                card=None,
                player=player,
                cooldown_until=next_pull_at,
                seconds_remaining=seconds_remaining,
            )

        card = _pick_card(get_cards_for_banner(banner), self._rng)
        existing_copies = await self._repo.get_card_copies(user_id=user_id, banner=banner, card_code=card.code)
        adventure_xp_gained = _resolve_adventure_xp_gain(card.adventure_xp, existing_copies)
        next_pull_at = current_time + timedelta(seconds=cooldown_seconds)
        updated_player, copies_owned = await self._repo.apply_pull(
            user_id=user_id,
            username=username,
            card=card,
            adventure_xp_gained=adventure_xp_gained,
            pulled_at=current_time,
            next_pull_at=next_pull_at,
        )
        is_new = existing_copies == 0
        return PullResult(
            status="ok",
            message=_render_success_message(
                card,
                updated_player,
                seconds_remaining=cooldown_seconds,
                is_new=is_new,
                copies_owned=copies_owned,
                adventure_xp_gained=adventure_xp_gained,
            ),
            card=card,
            player=updated_player,
            cooldown_until=next_pull_at,
            seconds_remaining=cooldown_seconds,
            is_new=is_new,
            copies_owned=copies_owned,
            adventure_xp_gained=adventure_xp_gained,
        )
