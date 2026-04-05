from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from gacha_service.application.catalog import get_banner_config
from gacha_service.application.service import GachaService, PAID_PULL_PRICE
from gacha_service.domain.models import CardRarity, GachaCard, PlayerState
from gacha_service.web.api import _render_profile_message, _resolve_public_image_url, _to_history_payload, PlayerPayload


class FakeGachaRepository:
    def __init__(self) -> None:
        self.players: dict[int, PlayerState] = {}
        self.history: list[dict[str, object]] = []
        self.collection: dict[tuple[int, str, str], int] = {}
        self.banner_cooldowns: dict[tuple[int, str], datetime] = {}
        self.banner_wallets: dict[tuple[int, str], int] = {}
        self.pull_records: dict[int, dict[str, object]] = {}
        self.next_pull_id = 1

    async def get_banner_cooldown(self, *, user_id: int, banner: str) -> datetime | None:
        return self.banner_cooldowns.get((user_id, banner))

    async def get_banner_currency_balance(self, *, user_id: int, banner: str) -> int:
        return self.banner_wallets.get((user_id, banner), 0)

    async def get_or_create_player(self, *, user_id: int, username: str | None, banner: str | None = None) -> PlayerState:
        player = self.players.get(user_id)
        if player is None:
            player = PlayerState(
                user_id=user_id,
                username=username,
                adventure_rank=1,
                adventure_xp=0,
                total_points=0,
                total_primogems=0,
                next_pull_at=None,
            )
            self.players[user_id] = player
            if banner is None:
                return player
            return replace(player, total_primogems=self.banner_wallets.get((user_id, banner), 0))
        if username is not None and username != player.username:
            player = replace(player, username=username)
            self.players[user_id] = player
        if banner is None:
            return player
        return replace(player, total_primogems=self.banner_wallets.get((user_id, banner), 0))

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

        current = await self.get_or_create_player(user_id=user_id, username=username)
        wallet_key = (user_id, banner)
        wallet_balance = self.banner_wallets.get(wallet_key, 0) + amount
        if wallet_balance < 0:
            raise ValueError("Недостаточно валюты для списания.")

        total_primogems = current.total_primogems + amount
        if total_primogems < 0:
            raise ValueError("Недостаточно валюты для списания.")

        self.banner_wallets[wallet_key] = wallet_balance
        updated = replace(
            current,
            username=username if username is not None else current.username,
            total_primogems=total_primogems,
        )
        self.players[user_id] = updated
        return replace(updated, total_primogems=wallet_balance)

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
        current = await self.get_or_create_player(user_id=user_id, username=username)
        key = (user_id, card.banner, card.code)
        copies_owned = self.collection.get(key, 0) + 1
        self.collection[key] = copies_owned
        wallet_key = (user_id, card.banner)
        wallet_balance = self.banner_wallets.get(wallet_key, 0)
        if purchase_price > wallet_balance:
            raise ValueError("Недостаточно валюты для платной крутки.")
        wallet_balance += card.primogems - purchase_price
        self.banner_wallets[wallet_key] = wallet_balance
        updated = replace(
            current,
            username=username,
            adventure_xp=current.adventure_xp + adventure_xp_gained,
            total_points=current.total_points + card.points,
            total_primogems=current.total_primogems + card.primogems - purchase_price,
            next_pull_at=next_pull_at if update_cooldown else current.next_pull_at,
        )
        if update_cooldown and next_pull_at is not None:
            self.banner_cooldowns[(user_id, card.banner)] = next_pull_at
        self.players[user_id] = updated
        pull_id = self.next_pull_id
        self.next_pull_id += 1
        sale_price = 0 if sellable else None
        self.pull_records[pull_id] = {
            "user_id": user_id,
            "banner": card.banner,
            "base_currency_price": base_currency_price,
            "sale_price": sale_price,
            "sold_at": None,
            "source": pull_source,
        }
        self.history.append({"user_id": user_id, "card_code": card.code, "pulled_at": pulled_at, "pull_id": pull_id})
        return replace(updated, total_primogems=wallet_balance), copies_owned, pull_id

    async def sell_pull(
        self,
        *,
        user_id: int,
        pull_id: int,
        sold_at: datetime,
    ) -> tuple[PlayerState, int, str, datetime]:
        record = self.pull_records.get(pull_id)
        if record is None or int(record["user_id"]) != user_id:
            raise ValueError("Крутка не найдена.")
        if record["sale_price"] is None:
            raise ValueError("Эту копию нельзя продать.")
        if record["sold_at"] is not None:
            raise ValueError("Эта копия уже продана.")

        banner = str(record["banner"])
        sale_price = int(record["base_currency_price"]) * 3
        wallet_key = (user_id, banner)
        wallet_balance = self.banner_wallets.get(wallet_key, 0) + sale_price
        self.banner_wallets[wallet_key] = wallet_balance
        current = self.players[user_id]
        updated = replace(current, total_primogems=current.total_primogems + sale_price)
        self.players[user_id] = updated
        record["sale_price"] = sale_price
        record["sold_at"] = sold_at
        return replace(updated, total_primogems=wallet_balance), sale_price, banner, sold_at

    async def get_card_copies(self, *, user_id: int, banner: str, card_code: str) -> int:
        return self.collection.get((user_id, banner, card_code), 0)

    async def get_card_ownership_stats(self, *, banner: str, card_code: str) -> tuple[int, int]:
        owners = {
            user_id
            for (user_id, current_banner, current_card_code), copies_owned in self.collection.items()
            if current_banner == banner and current_card_code == card_code and copies_owned > 0
        }
        banner_players = {
            user_id
            for (user_id, current_banner, _), copies_owned in self.collection.items()
            if current_banner == banner and copies_owned > 0
        }
        return len(owners), len(banner_players)

    async def get_user_collection(self, *, user_id: int, banner: str):
        return [
            SimpleNamespace(
                user_id=current_user_id,
                banner=current_banner,
                character_code=current_card_code,
                copies_owned=copies_owned,
            )
            for (current_user_id, current_banner, current_card_code), copies_owned in self.collection.items()
            if current_user_id == user_id and current_banner == banner and copies_owned > 0
        ]


@pytest.mark.asyncio
async def test_pull_grants_card_and_updates_totals() -> None:
    repo = FakeGachaRepository()
    service = GachaService(repo)
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
    cooldown_seconds = get_banner_config("genshin").cooldown_seconds

    result = await service.pull(user_id=100, username="tester", banner="genshin", now=now)

    assert result.status == "ok"
    assert result.card is not None
    assert result.player.total_points > 0
    assert result.player.total_primogems > 0
    assert result.cooldown_until == now + timedelta(seconds=cooldown_seconds)
    assert result.is_new
    assert result.copies_owned == 1
    assert "Вы получили новую карту" in result.message
    assert "Редкость: " in result.message
    assert "⬜ Редкость" not in result.message
    assert "Такая карта есть у 100% игроков" in result.message


@pytest.mark.asyncio
async def test_pull_respects_cooldown() -> None:
    repo = FakeGachaRepository()
    service = GachaService(repo)
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)

    first = await service.pull(user_id=101, username="tester", banner="genshin", now=now)
    second = await service.pull(user_id=101, username="tester", banner="genshin", now=now + timedelta(minutes=5))

    assert first.status == "ok"
    assert second.status == "cooldown"
    assert second.card is None
    assert "До следующей крутки" in second.message


@pytest.mark.asyncio
async def test_pull_can_drop_epic_card() -> None:
    repo = FakeGachaRepository()
    service = GachaService(repo)
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)

    pulls = []
    for offset in range(12):
        result = await service.pull(
            user_id=300 + offset,
            username=f"user{offset}",
            banner="genshin",
            now=now + timedelta(hours=offset * 4),
        )
        pulls.append(result)

    assert any(
        pull.card is not None and pull.card.rarity in {CardRarity.epic, CardRarity.legendary}
        for pull in pulls
    )


@pytest.mark.asyncio
async def test_pull_handles_naive_cooldown_datetime_from_storage() -> None:
    repo = FakeGachaRepository()
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
    repo.players[777] = PlayerState(
        user_id=777,
        username="tester",
        adventure_rank=1,
        adventure_xp=0,
        total_points=0,
        total_primogems=0,
        next_pull_at=None,
    )
    repo.banner_cooldowns[(777, "genshin")] = datetime(2026, 3, 14, 14, 0)
    service = GachaService(repo)

    result = await service.pull(user_id=777, username="tester", banner="genshin", now=now)

    assert result.status == "cooldown"
    assert result.seconds_remaining == 2 * 60 * 60


@pytest.mark.asyncio
async def test_hsr_uses_banner_specific_cooldown() -> None:
    repo = FakeGachaRepository()
    service = GachaService(repo)
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)

    result = await service.pull(user_id=202, username="trailblazer", banner="hsr", now=now)

    assert result.status == "ok"
    assert result.card is not None
    assert result.card.banner == "hsr"
    assert result.cooldown_until == now + timedelta(hours=3)
    assert result.seconds_remaining == 3 * 60 * 60
    assert "Опыт освоения" in result.message
    assert "Уровень освоения" in result.message
    assert "Звездный нефрит" in result.message
    assert "Регион:" not in result.message
    assert "Стихия:" not in result.message


@pytest.mark.asyncio
async def test_hsr_duplicates_use_eidolons_and_sell_offer_after_e6() -> None:
    repo = FakeGachaRepository()
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
    card = GachaCard(
        code="hsr_fixed",
        banner="hsr",
        name="Кафка",
        rarity=CardRarity.legendary,
        points=11000,
        primogems=22,
        adventure_xp=200,
        image_url="https://example.com/kafka.png",
        weight=1,
    )

    class FixedRandom:
        def choices(self, population, weights=None, k=1):
            return [card]

    service = GachaService(repo, rng=FixedRandom())

    results = []
    for offset in range(8):
        results.append(
            await service.pull(
                user_id=1202,
                username="trail",
                banner="hsr",
                now=now + timedelta(hours=3 * offset),
            )
        )

    assert results[1].card is not None
    assert results[1].card.name == "Кафка (E1)"
    assert "эйдолон" in results[1].message.lower()
    assert results[6].card is not None
    assert results[6].card.name == "Кафка (E6)"
    assert results[7].card is not None
    assert results[7].card.name == "Кафка (E6) дубликат"
    assert results[7].sell_offer is not None
    assert results[7].sell_offer.sale_price == card.primogems * 3


@pytest.mark.asyncio
async def test_admin_grant_card_adds_card_without_setting_cooldown() -> None:
    repo = FakeGachaRepository()
    service = GachaService(repo)
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)

    result = await service.grant_card(
        user_id=555,
        username="tester",
        banner="genshin",
        card_code="tartalia",
        now=now,
    )

    assert result.status == "ok"
    assert result.card is not None
    assert result.card.code == "tartalia"
    assert result.player.total_points > 0
    assert result.player.total_primogems > 0
    assert repo.banner_cooldowns == {}
    assert "Админ выдал" in result.message


@pytest.mark.asyncio
async def test_pull_shows_card_ownership_percentage_across_banner_players() -> None:
    repo = FakeGachaRepository()
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
    card = GachaCard(
        code="shared",
        banner="genshin",
        name="Тест",
        rarity=CardRarity.common,
        points=100,
        primogems=2,
        adventure_xp=20,
        image_url="https://example.com/test.png",
        weight=1,
    )
    other_card = GachaCard(
        code="other",
        banner="genshin",
        name="Другой",
        rarity=CardRarity.common,
        points=100,
        primogems=2,
        adventure_xp=20,
        image_url="https://example.com/other.png",
        weight=1,
    )

    class FixedRandom:
        def __init__(self) -> None:
            self.calls = 0

        def choices(self, population, weights=None, k=1):
            self.calls += 1
            return [card if self.calls != 2 else other_card]

    service = GachaService(repo, rng=FixedRandom())

    await service.pull(user_id=1, username="one", banner="genshin", now=now)
    await service.pull(user_id=2, username="two", banner="genshin", now=now + timedelta(hours=4))
    result = await service.pull(user_id=3, username="three", banner="genshin", now=now + timedelta(hours=8))

    assert "Такая карта есть у 66.7% игроков" in result.message


@pytest.mark.asyncio
async def test_cooldowns_are_independent_between_banners() -> None:
    repo = FakeGachaRepository()
    service = GachaService(repo)
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)

    first = await service.pull(user_id=303, username="tester", banner="genshin", now=now)
    second = await service.pull(user_id=303, username="tester", banner="hsr", now=now + timedelta(minutes=1))

    assert first.status == "ok"
    assert second.status == "ok"
    assert second.card is not None
    assert second.card.banner == "hsr"


@pytest.mark.asyncio
async def test_currency_balances_are_independent_between_banners() -> None:
    repo = FakeGachaRepository()
    service = GachaService(repo)
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)

    first = await service.pull(user_id=4040, username="multi", banner="genshin", now=now)
    second = await service.pull(user_id=4040, username="multi", banner="hsr", now=now + timedelta(hours=3))

    assert first.card is not None
    assert second.card is not None
    assert first.player.total_primogems == repo.banner_wallets[(4040, "genshin")]
    assert second.player.total_primogems == repo.banner_wallets[(4040, "hsr")]
    assert repo.players[4040].total_primogems == repo.banner_wallets[(4040, "genshin")] + repo.banner_wallets[(4040, "hsr")]


@pytest.mark.asyncio
async def test_duplicate_card_grants_less_adventure_xp() -> None:
    repo = FakeGachaRepository()
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
    card = GachaCard(
        code="fixed",
        banner="genshin",
        name="Тест",
        rarity=CardRarity.epic,
        points=1000,
        primogems=5,
        adventure_xp=120,
        image_url="https://example.com/test.png",
        weight=1,
    )

    class FixedRandom:
        def choices(self, population, weights=None, k=1):
            return [card]

    service = GachaService(repo, rng=FixedRandom())

    first = await service.pull(user_id=909, username="dup", banner="genshin", now=now)
    second = await service.pull(user_id=910, username="dup", banner="genshin", now=now)
    second = await service.pull(user_id=909, username="dup", banner="genshin", now=now + timedelta(hours=4))

    assert first.is_new
    assert first.adventure_xp_gained == 120
    assert second.is_new is False
    assert second.copies_owned == 2
    assert second.adventure_xp_gained == 60
    assert second.card is not None
    assert second.card.name == "Тест (С1)"
    assert "созвездие" in second.message.lower()


@pytest.mark.asyncio
async def test_genshin_post_c6_duplicate_doubles_primogems() -> None:
    repo = FakeGachaRepository()
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
    card = GachaCard(
        code="fixed",
        banner="genshin",
        name="Фишль",
        rarity=CardRarity.epic,
        points=1000,
        primogems=5,
        adventure_xp=120,
        image_url="https://example.com/test.png",
        weight=1,
    )

    class FixedRandom:
        def choices(self, population, weights=None, k=1):
            return [card]

    service = GachaService(repo, rng=FixedRandom())

    results = []
    for offset in range(8):
        results.append(
            await service.pull(
                user_id=919,
                username="const",
                banner="genshin",
                now=now + timedelta(hours=4 * offset),
            )
        )

    assert results[0].card is not None
    assert results[0].card.name == "Фишль"
    assert results[1].card is not None
    assert results[1].card.name == "Фишль (С1)"
    assert results[6].card is not None
    assert results[6].card.name == "Фишль (С6)"
    assert results[7].card is not None
    assert results[7].card.name == "Фишль (С6) дубликат"
    assert results[7].card.primogems == 10
    assert "дубликат" in results[7].message.lower()
    assert results[7].sell_offer is not None
    assert results[7].sell_offer.sale_price == 15


@pytest.mark.asyncio
async def test_paid_pull_spends_banner_currency_and_sets_new_cooldown() -> None:
    repo = FakeGachaRepository()
    repo.banner_wallets[(404, "genshin")] = 250
    repo.players[404] = PlayerState(
        user_id=404,
        username="buyer",
        adventure_rank=1,
        adventure_xp=0,
        total_points=0,
        total_primogems=250,
        next_pull_at=None,
    )
    service = GachaService(repo)
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)

    result = await service.pull_purchase(user_id=404, username="buyer", banner="genshin", now=now)

    assert result.status == "ok"
    assert result.card is not None
    assert result.player.total_primogems == 250 - PAID_PULL_PRICE + result.card.primogems
    assert result.cooldown_until == now + timedelta(seconds=get_banner_config("genshin").cooldown_seconds)
    assert "Платная крутка: -160" in result.message


@pytest.mark.asyncio
async def test_paid_pull_requires_enough_banner_currency() -> None:
    repo = FakeGachaRepository()
    repo.banner_wallets[(405, "hsr")] = PAID_PULL_PRICE - 1
    repo.players[405] = PlayerState(
        user_id=405,
        username="buyer",
        adventure_rank=1,
        adventure_xp=0,
        total_points=0,
        total_primogems=PAID_PULL_PRICE - 1,
        next_pull_at=None,
    )
    service = GachaService(repo)
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="Недостаточно"):
        await service.pull_purchase(user_id=405, username="buyer", banner="hsr", now=now)


@pytest.mark.asyncio
async def test_sell_pull_grants_extra_base_price_x3_only_once() -> None:
    repo = FakeGachaRepository()
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
    card = GachaCard(
        code="sellable",
        banner="genshin",
        name="Нин Гуан",
        rarity=CardRarity.epic,
        points=5400,
        primogems=18,
        adventure_xp=160,
        image_url="https://example.com/ning.png",
        weight=1,
    )

    class FixedRandom:
        def choices(self, population, weights=None, k=1):
            return [card]

    service = GachaService(repo, rng=FixedRandom())

    last_result = None
    for offset in range(8):
        last_result = await service.pull(
            user_id=606,
            username="seller",
            banner="genshin",
            now=now + timedelta(hours=4 * offset),
        )

    assert last_result is not None
    assert last_result.pull_id is not None
    assert last_result.sell_offer is not None

    sold = await service.sell_pull(user_id=606, pull_id=last_result.pull_id, now=now + timedelta(days=1))

    assert sold.sale_price == card.primogems * 3
    assert sold.player.total_primogems == repo.banner_wallets[(606, "genshin")]
    assert "Продажа:" in sold.message

    with pytest.raises(ValueError, match="уже продана"):
        await service.sell_pull(user_id=606, pull_id=last_result.pull_id, now=now + timedelta(days=1, minutes=1))


@pytest.mark.asyncio
async def test_grant_currency_adds_banner_balance_without_touching_progress() -> None:
    repo = FakeGachaRepository()
    service = GachaService(repo)

    result = await service.grant_currency(
        user_id=707,
        username="buyer",
        banner="genshin",
        amount=160,
    )

    assert result.status == "ok"
    assert result.banner == "genshin"
    assert result.amount == 160
    assert result.player.total_primogems == 160
    assert result.player.total_points == 0
    assert result.player.adventure_xp == 0
    assert "Баланс пополнен" in result.message


@pytest.mark.asyncio
async def test_grant_currency_allows_negative_adjustment_within_wallet() -> None:
    repo = FakeGachaRepository()
    service = GachaService(repo)

    await service.grant_currency(user_id=808, username="buyer", banner="hsr", amount=160)
    result = await service.grant_currency(user_id=808, username="buyer", banner="hsr", amount=-60)

    assert result.player.total_primogems == 100
    assert result.amount == -60
    assert "скорректирован" in result.message


def test_history_payload_uses_rarity_labels() -> None:
    entry = SimpleNamespace(
        banner="genshin",
        character_code="amber",
        pulled_at=datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc),
        character_name="Эмбер",
        rarity="common",
        points=800,
        primogems=2,
        adventure_xp=40,
        image_url="https://example.com/amber.png",
    )

    payload = _to_history_payload(entry)

    assert payload.rarity == "common"
    assert payload.rarity_label == "⬜ Обычная"
    assert payload.region_code is not None
    assert payload.element_code is not None
    assert payload.region_label == "Мондштадт"
    assert payload.element_label == "Пиро"


def test_profile_message_includes_recent_pulls_block() -> None:
    player = PlayerPayload(
        user_id=1,
        adventure_rank=2,
        adventure_xp=320,
        xp_into_rank=20,
        xp_for_next_rank=450,
        total_points=1500,
        total_primogems=12,
    )
    recent = [
        _to_history_payload(
            SimpleNamespace(
                banner="genshin",
                character_code="amber",
                pulled_at=datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc),
                character_name="Эмбер",
                rarity="common",
                points=800,
                primogems=2,
                adventure_xp=40,
                image_url="https://example.com/amber.png",
            )
        )
    ]

    message = _render_profile_message(
        banner="genshin",
        banner_title="Genshin Impact",
        player_payload=player,
        unique_cards=1,
        total_copies=1,
        rarity_counts=[
            SimpleNamespace(
                rarity="legendary",
                rarity_label="🟨 Легендарная",
                summary_label="Легендарных карт",
                count=2,
            )
        ],
        recent_pulls=recent,
    )

    assert "💠 Геншин" in message
    assert "⭐ Очки: 1 500 | 💠 Примогемы: 12" in message
    assert "📊 В коллекции: 🟨 2" in message
    assert "🕘 Последние крутки:" in message
    assert "⬜ Эмбер (14.03 в 12:00)" in message
    assert "Мондштадт" not in message
    assert "Пиро" not in message


def test_hsr_profile_message_uses_hsr_terms() -> None:
    player = PlayerPayload(
        user_id=2,
        adventure_rank=3,
        adventure_xp=600,
        xp_into_rank=150,
        xp_for_next_rank=600,
        total_points=3200,
        total_primogems=40,
    )

    message = _render_profile_message(
        banner="hsr",
        banner_title="Honkai: Star Rail",
        player_payload=player,
        unique_cards=4,
        total_copies=6,
        rarity_counts=[],
        recent_pulls=[],
    )

    assert "Освоение" in message
    assert "Нефрит" in message


def test_hsr_profile_message_does_not_render_origin_block() -> None:
    player = PlayerPayload(
        user_id=4,
        adventure_rank=3,
        adventure_xp=600,
        xp_into_rank=150,
        xp_for_next_rank=600,
        total_points=3200,
        total_primogems=40,
    )
    recent = [
        _to_history_payload(
            SimpleNamespace(
                banner="hsr",
                character_code="kafka",
                pulled_at=datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc),
                character_name="Кафка",
                rarity="legendary",
                points=11000,
                primogems=22,
                adventure_xp=200,
                image_url="https://example.com/kafka.png",
            )
        )
    ]

    message = _render_profile_message(
        banner="hsr",
        banner_title="Honkai: Star Rail",
        player_payload=player,
        unique_cards=1,
        total_copies=1,
        rarity_counts=[],
        recent_pulls=recent,
    )

    assert "Кафка" in message
    assert "Неизвестно" not in message


def test_profile_message_renders_unknown_card_without_origin_details() -> None:
    player = PlayerPayload(
        user_id=3,
        adventure_rank=1,
        adventure_xp=0,
        xp_into_rank=0,
        xp_for_next_rank=300,
        total_points=0,
        total_primogems=0,
    )
    recent = [
        _to_history_payload(
            SimpleNamespace(
                banner="genshin",
                character_code="missing_card",
                pulled_at=datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc),
                character_name="Неизвестный",
                rarity="common",
                points=1,
                primogems=1,
                adventure_xp=1,
                image_url="https://example.com/missing.png",
            )
        )
    ]

    message = _render_profile_message(
        banner="genshin",
        banner_title="Genshin Impact",
        player_payload=player,
        unique_cards=1,
        total_copies=1,
        rarity_counts=[],
        recent_pulls=recent,
    )

    assert "⬜ Неизвестный (14.03 в 12:00)" in message
    assert "Неизвестно" not in message


@pytest.mark.asyncio
async def test_pull_message_includes_non_zero_rarity_summary() -> None:
    repo = FakeGachaRepository()
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
    card = GachaCard(
        code="albedo",
        banner="genshin",
        name="Альбедо",
        rarity=CardRarity.legendary,
        points=10000,
        primogems=22,
        adventure_xp=220,
        image_url="https://example.com/albedo.png",
        weight=1,
    )

    class FixedRandom:
        def choices(self, population, weights=None, k=1):
            return [card]

    service = GachaService(repo, rng=FixedRandom())
    result = await service.pull(user_id=1, username="tester", banner="genshin", now=now)

    assert "📊 В коллекции: 🟨 1" in result.message
    assert "Эпических карт у вас" not in result.message


def test_resolve_public_image_url_builds_vps_link() -> None:
    request = SimpleNamespace(base_url="http://127.0.0.1:8001/")

    image_url = _resolve_public_image_url(request, "/images/genshin/amber.jpg")

    assert image_url == "http://127.0.0.1:8001/images/genshin/amber.jpg"
