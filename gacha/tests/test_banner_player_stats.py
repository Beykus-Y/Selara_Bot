from __future__ import annotations

import asyncio
import os
import random

import pytest
from gacha_service.application.catalog import get_cards_for_banner
from gacha_service.application.service import GachaService
from gacha_service.domain.models import resolve_rank, xp_for_rank
from gacha_service.infrastructure.models import (
    Base,
    PlayerBannerStatsModel,
    PullHistoryModel,
)
from gacha_service.infrastructure.repository import GachaRepository
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

USER_ID = 7001


@pytest.fixture
async def session_factory():
    database_url = os.getenv("TEST_GACHA_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_GACHA_DATABASE_URL is not set")

    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _grant(session_factory, *, user_id: int, banner: str, code: str, seed: int = 1) -> None:
    async with session_factory() as session:
        await GachaService(GachaRepository(session), rng=random.Random(seed)).grant_card(
            user_id=user_id,
            username="player",
            banner=banner,
            card_code=code,
        )


async def _history_totals(session_factory, *, user_id: int) -> dict[str, tuple[int, int]]:
    """Oracle: per-banner sums straight from the pull audit trail."""
    stmt = (
        select(
            PullHistoryModel.banner,
            func.sum(PullHistoryModel.points),
            func.sum(PullHistoryModel.adventure_xp),
        )
        .where(PullHistoryModel.user_id == user_id)
        .group_by(PullHistoryModel.banner)
    )
    async with session_factory() as session:
        rows = (await session.execute(stmt)).all()
    return {banner: (int(points), int(xp)) for banner, points, xp in rows}


async def _stats_totals(session_factory, *, user_id: int) -> dict[str, tuple[int, int]]:
    stmt = select(
        PlayerBannerStatsModel.banner,
        PlayerBannerStatsModel.total_points,
        PlayerBannerStatsModel.adventure_xp,
    ).where(PlayerBannerStatsModel.user_id == user_id)
    async with session_factory() as session:
        rows = (await session.execute(stmt)).all()
    return {banner: (int(points), int(xp)) for banner, points, xp in rows}


async def test_banner_totals_match_pull_history_per_banner(session_factory) -> None:
    for banner, grants in (("genshin", 3), ("hsr", 1), ("zzz", 2)):
        code = get_cards_for_banner(banner)[0].code
        for _ in range(grants):
            await _grant(session_factory, user_id=USER_ID, banner=banner, code=code)

    history = await _history_totals(session_factory, user_id=USER_ID)
    stats = await _stats_totals(session_factory, user_id=USER_ID)

    assert set(history) == {"genshin", "hsr", "zzz"}
    assert stats == history


async def test_player_state_reads_are_scoped_to_the_requested_banner(session_factory) -> None:
    genshin_code = get_cards_for_banner("genshin")[0].code
    hsr_code = get_cards_for_banner("hsr")[0].code
    for _ in range(2):
        await _grant(session_factory, user_id=USER_ID, banner="genshin", code=genshin_code)
    await _grant(session_factory, user_id=USER_ID, banner="hsr", code=hsr_code)

    history = await _history_totals(session_factory, user_id=USER_ID)
    async with session_factory() as session:
        repo = GachaRepository(session)
        genshin = await repo.get_player(user_id=USER_ID, banner="genshin")
        hsr = await repo.get_player(user_id=USER_ID, banner="hsr")
        shared = await repo.get_player(user_id=USER_ID)

    assert genshin is not None and hsr is not None and shared is not None
    assert (genshin.total_points, genshin.adventure_xp) == history["genshin"]
    assert (hsr.total_points, hsr.adventure_xp) == history["hsr"]
    assert genshin.adventure_rank == resolve_rank(genshin.adventure_xp)[0]
    assert hsr.adventure_rank == resolve_rank(hsr.adventure_xp)[0]
    # The shared row keeps the account-wide sums for compatibility.
    assert shared.total_points == history["genshin"][0] + history["hsr"][0]
    assert shared.adventure_xp == history["genshin"][1] + history["hsr"][1]


async def test_rank_is_derived_from_each_banners_own_xp(session_factory) -> None:
    user_id = USER_ID + 1
    banner_xp = xp_for_rank(1)  # enough XP to reach rank 2 in one banner only
    async with session_factory() as session:
        repo = GachaRepository(session)
        await repo.get_or_create_player(user_id=user_id, username="ranker")
        session.add_all(
            [
                PlayerBannerStatsModel(user_id=user_id, banner="genshin", total_points=0, adventure_xp=banner_xp),
                PlayerBannerStatsModel(user_id=user_id, banner="hsr", total_points=0, adventure_xp=0),
            ]
        )
        await session.commit()

    async with session_factory() as session:
        repo = GachaRepository(session)
        genshin = await repo.get_player(user_id=user_id, banner="genshin")
        hsr = await repo.get_player(user_id=user_id, banner="hsr")

    assert genshin is not None and hsr is not None
    assert genshin.adventure_rank == resolve_rank(banner_xp)[0] == 2
    assert hsr.adventure_rank == 1
    assert hsr.adventure_xp == 0


async def test_user_without_history_has_zero_banner_totals(session_factory) -> None:
    user_id = USER_ID + 2
    async with session_factory() as session:
        state = await GachaRepository(session).get_or_create_player(user_id=user_id, username="novice", banner="zzz")

    assert state.total_points == 0
    assert state.adventure_xp == 0
    assert state.adventure_rank == 1
    assert await _stats_totals(session_factory, user_id=user_id) == {}


async def test_concurrent_grants_in_one_banner_keep_banner_totals_exact(session_factory) -> None:
    code = get_cards_for_banner("genshin")[0].code
    await asyncio.gather(
        *(
            _grant(session_factory, user_id=USER_ID + 3, banner="genshin", code=code, seed=seed)
            for seed in range(4)
        )
    )

    history = await _history_totals(session_factory, user_id=USER_ID + 3)
    stats = await _stats_totals(session_factory, user_id=USER_ID + 3)
    assert stats == history
    assert set(history) == {"genshin"}


async def test_concurrent_grants_across_banners_stay_isolated(session_factory) -> None:
    user_id = USER_ID + 4
    genshin_code = get_cards_for_banner("genshin")[0].code
    hsr_code = get_cards_for_banner("hsr")[0].code
    # Create the shared player row first: the first-row INSERT is not covered by the
    # per-banner lock and is out of scope for this change.
    async with session_factory() as session:
        await GachaRepository(session).get_or_create_player(user_id=user_id, username="both")

    await asyncio.gather(
        *(_grant(session_factory, user_id=user_id, banner="genshin", code=genshin_code, seed=s) for s in range(3)),
        *(_grant(session_factory, user_id=user_id, banner="hsr", code=hsr_code, seed=s) for s in range(3)),
    )

    history = await _history_totals(session_factory, user_id=user_id)
    stats = await _stats_totals(session_factory, user_id=user_id)
    assert stats == history
    assert set(history) == {"genshin", "hsr"}
