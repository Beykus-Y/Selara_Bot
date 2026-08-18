from __future__ import annotations

import logging

import pytest
from gacha_service.infrastructure.repository import GachaRepository
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_lock_user_banner_warns_once_on_non_postgres_dialect(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """lock_user_banner silently no-ops on any non-Postgres dialect (e.g. a
    local SQLite dev setup) — production always runs Postgres, so this is
    not an active bug, but a future local/dev run on SQLite would silently
    lose all pull/sell/currency-grant race protection. Make that loud
    instead of silent (see docs/GACHA_MODERNIZATION_TODO.md, Этап 0)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        repo = GachaRepository(session)
        with caplog.at_level(logging.WARNING, logger="gacha_service.infrastructure.repository"):
            await repo.lock_user_banner(user_id=1, banner="genshin")
            await repo.lock_user_banner(user_id=2, banner="hsr")

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 1
    assert "advisory lock" in warning_records[0].message.lower()
    await engine.dispose()
