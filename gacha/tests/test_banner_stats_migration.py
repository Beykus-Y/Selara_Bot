from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

GACHA_ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_REVISION = "20260818_0005"
HEAD_REVISION = "20261012_0006"
MIGRATION_PATH = GACHA_ROOT / "alembic" / "versions" / "20261012_0006_banner_player_stats.py"

# (user_id, banner, points, adventure_xp, source) — every row is one audit-trail pull or grant.
_PULLS = [
    (1, "genshin", 10, 100, "free"),
    (1, "genshin", 5, 40, "paid"),
    (1, "hsr", 7, 30, "free"),
    (1, "hsr", 3, 0, "admin"),
    (2, "zzz", 4, 20, "free"),
]
# Shared legacy totals as the previous release wrote them: sums across all banners.
_LEGACY_PLAYERS = [
    (1, "alpha", 25, 170),
    (2, "beta", 4, 20),
    (3, "gamma", 0, 0),  # never pulled: no history rows
]
_EXPECTED_STATS = [
    (1, "genshin", 15, 140),
    (1, "hsr", 10, 30),
    (2, "zzz", 4, 20),
]


def _with_database(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path=f"/{name}"))


def _run_alembic(url: str, *args: str) -> None:
    env = {**os.environ, "GACHA_DATABASE_URL": url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=GACHA_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("banner_player_stats_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def scratch_url():
    base_url = os.getenv("TEST_GACHA_DATABASE_URL")
    if not base_url:
        pytest.skip("TEST_GACHA_DATABASE_URL is not set")

    name = f"gacha_mig_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(base_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield _with_database(base_url, name)
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


async def _seed_previous_schema(url: str) -> None:
    started = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            for user_id, username, total_points, adventure_xp in _LEGACY_PLAYERS:
                await connection.execute(
                    text(
                        "INSERT INTO gacha_players (user_id, username, adventure_rank, adventure_xp, "
                        "total_points, total_primogems) VALUES (:uid, :name, 1, :xp, :points, 0)"
                    ),
                    {"uid": user_id, "name": username, "xp": adventure_xp, "points": total_points},
                )
            for index, (user_id, banner, points, adventure_xp, source) in enumerate(_PULLS):
                await connection.execute(
                    text(
                        "INSERT INTO gacha_pull_history (user_id, banner, character_code, character_name, rarity, "
                        "points, primogems, adventure_xp, image_url, source, base_currency_price, purchase_price, "
                        "sale_price, sold_at, pulled_at) VALUES (:uid, :banner, 'card', 'Card', 'common', :points, "
                        "0, :xp, 'img.png', :source, 0, 0, NULL, NULL, :pulled_at)"
                    ),
                    {
                        "uid": user_id,
                        "banner": banner,
                        "points": points,
                        "xp": adventure_xp,
                        "source": source,
                        "pulled_at": started + timedelta(minutes=index),
                    },
                )
    finally:
        await engine.dispose()


async def _stats_rows(url: str) -> list[tuple[int, str, int, int]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT user_id, banner, total_points, adventure_xp FROM gacha_player_banner_stats "
                    "ORDER BY user_id, banner"
                )
            )
            return [(int(uid), banner, int(points), int(xp)) for uid, banner, points, xp in result.all()]
    finally:
        await engine.dispose()


async def _legacy_rows(url: str) -> list[tuple[int, int, int]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT user_id, total_points, adventure_xp FROM gacha_players ORDER BY user_id")
            )
            return [(int(uid), int(points), int(xp)) for uid, points, xp in result.all()]
    finally:
        await engine.dispose()


async def test_upgrade_rebuilds_exact_per_banner_totals_from_history(scratch_url) -> None:
    _run_alembic(scratch_url, "upgrade", PREVIOUS_REVISION)
    await _seed_previous_schema(scratch_url)

    _run_alembic(scratch_url, "upgrade", HEAD_REVISION)

    assert await _stats_rows(scratch_url) == _EXPECTED_STATS
    # Users without history get no stats row; they read as zero per banner.
    assert all(row[0] != 3 for row in await _stats_rows(scratch_url))
    # Legacy shared totals are untouched by the migration.
    assert await _legacy_rows(scratch_url) == [(1, 25, 170), (2, 4, 20), (3, 0, 0)]


async def test_rerunning_upgrade_and_backfill_is_a_no_op(scratch_url) -> None:
    _run_alembic(scratch_url, "upgrade", PREVIOUS_REVISION)
    await _seed_previous_schema(scratch_url)
    _run_alembic(scratch_url, "upgrade", HEAD_REVISION)

    _run_alembic(scratch_url, "upgrade", HEAD_REVISION)
    assert await _stats_rows(scratch_url) == _EXPECTED_STATS

    # Re-running only the backfill must not duplicate rows or overwrite values that
    # live writes already maintained.
    backfill_sql = _load_migration_module()._BACKFILL_SQL
    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE gacha_player_banner_stats SET total_points = 999 WHERE user_id = 1 AND banner = 'hsr'")
            )
            await connection.execute(text(backfill_sql))
            await connection.execute(text(backfill_sql))
    finally:
        await engine.dispose()

    expected_after_live_write = [
        (1, "genshin", 15, 140),
        (1, "hsr", 999, 30),
        (2, "zzz", 4, 20),
    ]
    assert await _stats_rows(scratch_url) == expected_after_live_write


async def test_downgrade_then_upgrade_reproduces_the_same_totals(scratch_url) -> None:
    _run_alembic(scratch_url, "upgrade", PREVIOUS_REVISION)
    await _seed_previous_schema(scratch_url)
    _run_alembic(scratch_url, "upgrade", HEAD_REVISION)

    _run_alembic(scratch_url, "downgrade", PREVIOUS_REVISION)
    _run_alembic(scratch_url, "upgrade", HEAD_REVISION)

    assert await _stats_rows(scratch_url) == _EXPECTED_STATS
