from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gacha_service.domain.models import GachaCard, PlayerState, resolve_rank
from gacha_service.infrastructure.models import (
    PlayerBannerCooldownModel,
    PlayerBannerWalletModel,
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


def _to_player_state(model: PlayerModel, *, total_primogems_override: int | None = None) -> PlayerState:
    return PlayerState(
        user_id=model.user_id,
        username=model.username,
        adventure_rank=model.adventure_rank,
        adventure_xp=model.adventure_xp,
        total_points=model.total_points,
        total_primogems=model.total_primogems if total_primogems_override is None else total_primogems_override,
        next_pull_at=_coerce_utc_datetime(model.next_pull_at),
    )


class GachaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def adjust_banner_currency(
        self,
        *,
        user_id: int,
        username: str | None,
        banner: str,
        amount: int,
    ) -> PlayerState:
        if amount == 0:
            raise ValueError("Количество валюты должно быть ненулевым.")

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
            await self._session.flush()
        elif username is not None:
            player.username = username

        wallet = await self._session.get(
            PlayerBannerWalletModel,
            {
                "user_id": user_id,
                "banner": banner,
            },
        )
        if wallet is None:
            wallet = PlayerBannerWalletModel(
                user_id=user_id,
                banner=banner,
                currency_balance=0,
            )
            self._session.add(wallet)
            await self._session.flush()

        new_balance = int(wallet.currency_balance) + int(amount)
        if new_balance < 0:
            raise ValueError("Недостаточно валюты для списания.")

        new_total_primogems = int(player.total_primogems) + int(amount)
        if new_total_primogems < 0:
            raise ValueError("Недостаточно валюты для списания.")

        wallet.currency_balance = new_balance
        player.total_primogems = new_total_primogems

        await self._session.commit()
        await self._session.refresh(player)
        return _to_player_state(player, total_primogems_override=int(wallet.currency_balance))

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

    async def get_banner_currency_balance(self, *, user_id: int, banner: str) -> int:
        wallet = await self._session.get(
            PlayerBannerWalletModel,
            {
                "user_id": user_id,
                "banner": banner,
            },
        )
        if wallet is None:
            return 0
        return int(wallet.currency_balance)

    async def get_or_create_player(self, *, user_id: int, username: str | None, banner: str | None = None) -> PlayerState:
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
            if banner is None:
                return _to_player_state(player)
            return _to_player_state(
                player,
                total_primogems_override=await self.get_banner_currency_balance(user_id=user_id, banner=banner),
            )

        if username is not None and username != player.username:
            player.username = username
            await self._session.commit()
            await self._session.refresh(player)
        if banner is None:
            return _to_player_state(player)
        return _to_player_state(
            player,
            total_primogems_override=await self.get_banner_currency_balance(user_id=user_id, banner=banner),
        )

    async def apply_pull(
        self,
        *,
        user_id: int,
        username: str | None,
        card: GachaCard,
        adventure_xp_gained: int,
        pulled_at: datetime,
        next_pull_at: datetime | None,
        update_cooldown: bool = True,
        pull_source: str = "free",
        purchase_price: int = 0,
        base_currency_price: int = 0,
        sellable: bool = False,
    ) -> tuple[PlayerState, int, int]:
        player = await self._session.get(PlayerModel, user_id)
        if player is None:
            player = PlayerModel(user_id=user_id, username=username, adventure_rank=1, adventure_xp=0, total_points=0, total_primogems=0)
            self._session.add(player)
            await self._session.flush()

        if username is not None:
            player.username = username
        player.total_points += card.points
        player.adventure_xp += adventure_xp_gained
        player.adventure_rank = resolve_rank(player.adventure_xp)[0]
        if update_cooldown:
            player.next_pull_at = next_pull_at

        if update_cooldown and next_pull_at is not None:
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

        wallet = await self._session.get(
            PlayerBannerWalletModel,
            {
                "user_id": user_id,
                "banner": card.banner,
            },
        )
        wallet_balance = 0 if wallet is None else int(wallet.currency_balance)
        if purchase_price > wallet_balance:
            raise ValueError("Недостаточно валюты для платной крутки.")
        if wallet is None:
            wallet = PlayerBannerWalletModel(
                user_id=user_id,
                banner=card.banner,
                currency_balance=0,
            )
            self._session.add(wallet)
            await self._session.flush()

        currency_delta = card.primogems - purchase_price
        wallet.currency_balance = wallet_balance + currency_delta
        player.total_primogems += currency_delta

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

        history_entry = PullHistoryModel(
            user_id=user_id,
            banner=card.banner,
            character_code=card.code,
            character_name=card.name,
            rarity=card.rarity.value,
            points=card.points,
            primogems=card.primogems,
            adventure_xp=adventure_xp_gained,
            image_url=card.image_url,
            source=pull_source,
            base_currency_price=base_currency_price,
            purchase_price=purchase_price,
            sale_price=0 if sellable else None,
            sold_at=None,
            pulled_at=pulled_at,
        )
        self._session.add(history_entry)
        await self._session.flush()
        await self._session.commit()
        await self._session.refresh(player)
        return (
            _to_player_state(player, total_primogems_override=int(wallet.currency_balance)),
            int(collection_entry.copies_owned),
            int(history_entry.id),
        )

    async def sell_pull(
        self,
        *,
        user_id: int,
        pull_id: int,
        sold_at: datetime,
    ) -> tuple[PlayerState, int, str, datetime]:
        stmt = (
            select(PullHistoryModel)
            .where(PullHistoryModel.id == pull_id, PullHistoryModel.user_id == user_id)
            .with_for_update()
        )
        result = await self._session.execute(stmt)
        entry = result.scalar_one_or_none()
        if entry is None:
            raise ValueError("Крутка не найдена.")
        if entry.sale_price is None:
            raise ValueError("Эту копию нельзя продать.")
        if entry.sold_at is not None:
            raise ValueError("Эта копия уже продана.")

        player = await self._session.get(PlayerModel, user_id)
        if player is None:
            raise ValueError("Игрок не найден.")

        wallet = await self._session.get(
            PlayerBannerWalletModel,
            {
                "user_id": user_id,
                "banner": entry.banner,
            },
        )
        if wallet is None:
            wallet = PlayerBannerWalletModel(
                user_id=user_id,
                banner=entry.banner,
                currency_balance=0,
            )
            self._session.add(wallet)
            await self._session.flush()

        sale_price = int(entry.base_currency_price) * 3
        wallet.currency_balance = int(wallet.currency_balance) + sale_price
        player.total_primogems += sale_price
        entry.sale_price = sale_price
        entry.sold_at = sold_at

        await self._session.commit()
        await self._session.refresh(player)
        return (
            _to_player_state(player, total_primogems_override=int(wallet.currency_balance)),
            sale_price,
            entry.banner,
            sold_at,
        )

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

    async def get_card_ownership_stats(self, *, banner: str, card_code: str) -> tuple[int, int]:
        owners_stmt = select(
            func.count(func.distinct(PlayerCardCollectionModel.user_id)),
        ).where(
            PlayerCardCollectionModel.banner == banner,
            PlayerCardCollectionModel.character_code == card_code,
            PlayerCardCollectionModel.copies_owned > 0,
        )
        total_players_stmt = select(
            func.count(func.distinct(PlayerCardCollectionModel.user_id)),
        ).where(
            PlayerCardCollectionModel.banner == banner,
        )
        owners_result = await self._session.execute(owners_stmt)
        total_players_result = await self._session.execute(total_players_stmt)
        owners = owners_result.scalar_one()
        total_players = total_players_result.scalar_one()
        return int(owners or 0), int(total_players or 0)

    async def get_user_collection(self, *, user_id: int, banner: str) -> list[PlayerCardCollectionModel]:
        """Получить всю коллекцию карточек пользователя по баннеру"""
        stmt = (
            select(PlayerCardCollectionModel)
            .where(
                PlayerCardCollectionModel.user_id == user_id,
                PlayerCardCollectionModel.banner == banner,
                PlayerCardCollectionModel.copies_owned > 0,
            )
            .order_by(PlayerCardCollectionModel.character_code)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())
