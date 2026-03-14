from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

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
        next_pull_at: datetime,
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
            next_pull_at=next_pull_at,
        )
        self.banner_cooldowns[(user_id, card.banner)] = next_pull_at
        self.players[user_id] = updated
        self.history.append({"user_id": user_id, "card_code": card.code, "pulled_at": pulled_at})
        return updated, copies_owned

    async def get_card_copies(self, *, user_id: int, banner: str, card_code: str) -> int:
        return self.collection.get((user_id, banner, card_code), 0)


@pytest.mark.asyncio
async def test_pull_grants_card_and_updates_totals() -> None:
    repo = FakeGachaRepository()
    service = GachaService(repo)
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)

    result = await service.pull(user_id=100, username="tester", banner="genshin", now=now)

    assert result.status == "ok"
    assert result.card is not None
    assert result.player.total_points > 0
    assert result.player.total_primogems > 0
    assert result.cooldown_until == now + timedelta(hours=3)
    assert result.is_new
    assert result.copies_owned == 1
    assert "Вы получили новую карту" in result.message


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
    assert "дубликат" in second.message.lower()


def test_history_payload_uses_rarity_labels() -> None:
    entry = SimpleNamespace(
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
        banner_title="Genshin Impact",
        player_payload=player,
        unique_cards=1,
        total_copies=1,
        recent_pulls=recent,
    )

    assert "Статистика гачи: Genshin Impact" in message
    assert "Последние крутки" in message
    assert "Эмбер" in message


def test_resolve_public_image_url_builds_vps_link() -> None:
    request = SimpleNamespace(base_url="http://127.0.0.1:8001/")

    image_url = _resolve_public_image_url(request, "/images/genshin/amber.jpg")

    assert image_url == "http://127.0.0.1:8001/images/genshin/amber.jpg"
