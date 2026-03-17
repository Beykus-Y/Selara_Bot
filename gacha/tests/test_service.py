from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from gacha_service.application.catalog import get_banner_config
from gacha_service.application.service import GachaService
from gacha_service.domain.models import CardRarity, GachaCard, PlayerState
from gacha_service.web.api import _render_profile_message, _resolve_public_image_url, _to_history_payload, PlayerPayload


class FakeGachaRepository:
    def __init__(self) -> None:
        self.players: dict[int, PlayerState] = {}
        self.history: list[dict[str, object]] = []
        self.collection: dict[tuple[int, str, str], int] = {}
        self.banner_cooldowns: dict[tuple[int, str], datetime] = {}

    async def get_banner_cooldown(self, *, user_id: int, banner: str) -> datetime | None:
        return self.banner_cooldowns.get((user_id, banner))

    async def get_or_create_player(self, *, user_id: int, username: str | None) -> PlayerState:
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
            return player
        if username is not None and username != player.username:
            player = replace(player, username=username)
            self.players[user_id] = player
        return player

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
    ) -> tuple[PlayerState, int]:
        current = await self.get_or_create_player(user_id=user_id, username=username)
        key = (user_id, card.banner, card.code)
        copies_owned = self.collection.get(key, 0) + 1
        self.collection[key] = copies_owned
        updated = replace(
            current,
            username=username,
            adventure_xp=current.adventure_xp + adventure_xp_gained,
            total_points=current.total_points + card.points,
            total_primogems=current.total_primogems + card.primogems,
            next_pull_at=next_pull_at if update_cooldown else current.next_pull_at,
        )
        if update_cooldown and next_pull_at is not None:
            self.banner_cooldowns[(user_id, card.banner)] = next_pull_at
        self.players[user_id] = updated
        self.history.append({"user_id": user_id, "card_code": card.code, "pulled_at": pulled_at})
        return updated, copies_owned

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
    assert result.cooldown_until == now + timedelta(hours=2)
    assert result.seconds_remaining == 2 * 60 * 60
    assert "Опыт освоения" in result.message
    assert "Уровень освоения" in result.message
    assert "Звездный нефрит" in result.message
    assert "Регион:" not in result.message
    assert "Стихия:" not in result.message


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
        recent_pulls=recent,
    )

    assert "Статистика гачи: Genshin Impact" in message
    assert "Последние крутки" in message
    assert "Эмбер" in message
    assert "Мондштадт" in message
    assert "Пиро" in message


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
        recent_pulls=[],
    )

    assert "Уровень освоения" in message
    assert "Звездный нефрит" in message


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
        recent_pulls=recent,
    )

    assert "Кафка" in message
    assert "Неизвестно" not in message


def test_profile_message_uses_neizvestno_for_unknown_origin() -> None:
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
        recent_pulls=recent,
    )

    assert "Неизвестно" in message


def test_resolve_public_image_url_builds_vps_link() -> None:
    request = SimpleNamespace(base_url="http://127.0.0.1:8001/")

    image_url = _resolve_public_image_url(request, "/images/genshin/amber.jpg")

    assert image_url == "http://127.0.0.1:8001/images/genshin/amber.jpg"
