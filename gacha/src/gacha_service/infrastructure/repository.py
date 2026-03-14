from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gacha_service.domain.models import GachaCard, PlayerState, resolve_rank
from gacha_service.infrastructure.models import (
    PlayerBannerCooldownModel,
    PlayerCardCollectionModel,
    PlayerModel,
    PullHistoryModel,
)


def _coerce_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _to_player_state(model: PlayerModel) -> PlayerState:
    return PlayerState(
        user_id=model.user_id,
        username=model.username,
        adventure_rank=model.adventure_rank,
        adventure_xp=model.adventure_xp,
        total_points=model.total_points,
        total_primogems=model.total_primogems,
        next_pull_at=_coerce_utc_datetime(model.next_pull_at),
    )


class GachaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_banner_cooldown(self, *, user_id: int, banner: str) -> datetime | None:
        cooldown = await self._session.get(
            PlayerBannerCooldownModel,
            {
                "user_id": user_id,
                "banner": banner,
            },
        )
        if cooldown is None:
            return None
        return _coerce_utc_datetime(cooldown.next_pull_at)

    async def reset_banner_cooldown(self, *, user_id: int, banner: str) -> bool:
        cooldown = await self._session.get(
            PlayerBannerCooldownModel,
            {
                "user_id": user_id,
                "banner": banner,
            },
        )
        if cooldown is None:
            return False

        await self._session.delete(cooldown)
        await self._session.commit()
        return True

    async def get_or_create_player(self, *, user_id: int, username: str | None) -> PlayerState:
        player = await self._session.get(PlayerModel, user_id)
        if player is None:
            player = PlayerModel(
                user_id=user_id,
                username=username,
                adventure_rank=1,
                adventure_xp=0,
                total_points=0,
                total_primogems=0,
                next_pull_at=None,
            )
            self._session.add(player)
            await self._session.commit()
            await self._session.refresh(player)
            return _to_player_state(player)

        if username is not None and username != player.username:
            player.username = username
            await self._session.commit()
            await self._session.refresh(player)
        return _to_player_state(player)

    async def apply_pull(
        self,
        *,
        user_id: int,
        username: str | None,
        card: GachaCard,
        adventure_xp_gained: int,
        pulled_at: datetime,
        next_pull_at: datetime,
    ) -> tuple[PlayerState, int]:
        player = await self._session.get(PlayerModel, user_id)
        if player is None:
            player = PlayerModel(user_id=user_id, username=username, adventure_rank=1, adventure_xp=0, total_points=0, total_primogems=0)
            self._session.add(player)
            await self._session.flush()

        if username is not None:
            player.username = username
        player.total_points += card.points
        player.total_primogems += card.primogems
        player.adventure_xp += adventure_xp_gained
        player.adventure_rank = resolve_rank(player.adventure_xp)[0]
        player.next_pull_at = next_pull_at

        cooldown_entry = await self._session.get(
            PlayerBannerCooldownModel,
            {
                "user_id": user_id,
                "banner": card.banner,
            },
        )
        if cooldown_entry is None:
            cooldown_entry = PlayerBannerCooldownModel(
                user_id=user_id,
                banner=card.banner,
                next_pull_at=next_pull_at,
            )
            self._session.add(cooldown_entry)
            await self._session.flush()
        else:
            cooldown_entry.next_pull_at = next_pull_at

        collection_entry = await self._session.get(
            PlayerCardCollectionModel,
            {
                "user_id": user_id,
                "banner": card.banner,
                "character_code": card.code,
            },
        )
        if collection_entry is None:
            collection_entry = PlayerCardCollectionModel(
                user_id=user_id,
                banner=card.banner,
                character_code=card.code,
                copies_owned=0,
            )
            self._session.add(collection_entry)
            await self._session.flush()
        collection_entry.copies_owned += 1

        self._session.add(
            PullHistoryModel(
                user_id=user_id,
                banner=card.banner,
                character_code=card.code,
                character_name=card.name,
                rarity=card.rarity.value,
                points=card.points,
                primogems=card.primogems,
                adventure_xp=adventure_xp_gained,
                image_url=card.image_url,
                pulled_at=pulled_at,
            )
        )
        await self._session.commit()
        await self._session.refresh(player)
        return _to_player_state(player), int(collection_entry.copies_owned)

    async def get_card_copies(self, *, user_id: int, banner: str, card_code: str) -> int:
        entry = await self._session.get(
            PlayerCardCollectionModel,
            {
                "user_id": user_id,
                "banner": banner,
                "character_code": card_code,
            },
        )
        if entry is None:
            return 0
        return int(entry.copies_owned)

    async def get_recent_pulls(self, *, user_id: int, limit: int = 10) -> list[PullHistoryModel]:
        stmt = (
            select(PullHistoryModel)
            .where(PullHistoryModel.user_id == user_id)
            .order_by(PullHistoryModel.pulled_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def get_recent_pulls_by_banner(self, *, user_id: int, banner: str, limit: int = 10) -> list[PullHistoryModel]:
        stmt = (
            select(PullHistoryModel)
            .where(PullHistoryModel.user_id == user_id, PullHistoryModel.banner == banner)
            .order_by(PullHistoryModel.pulled_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def get_collection_stats(self, *, user_id: int, banner: str) -> tuple[int, int]:
        stmt = select(
            func.count(PlayerCardCollectionModel.character_code),
            func.coalesce(func.sum(PlayerCardCollectionModel.copies_owned), 0),
        ).where(
            PlayerCardCollectionModel.user_id == user_id,
            PlayerCardCollectionModel.banner == banner,
        )
        result = await self._session.execute(stmt)
        unique_cards, total_copies = result.one()
        return int(unique_cards or 0), int(total_copies or 0)
