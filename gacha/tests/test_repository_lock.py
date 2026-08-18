from __future__ import annotations

import logging
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from gacha_service.infrastructure.repository import GachaRepository


@dataclass
class _FakeDialect:
    name: str


class _FakeBind:
    def __init__(self, dialect_name: str) -> None:
        self.dialect = _FakeDialect(dialect_name)


class _FakeSession:
    """Stand-in for an AsyncSession bound to a non-Postgres dialect — avoids
    pulling in a real sqlite/aiosqlite engine just to exercise the dialect
    check in lock_user_banner."""

    def __init__(self, dialect_name: str) -> None:
        self.bind = _FakeBind(dialect_name)
        self.execute = AsyncMock()
        self.expire_all = lambda: None


@pytest.mark.asyncio
async def test_lock_user_banner_warns_once_on_non_postgres_dialect(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """lock_user_banner silently no-ops on any non-Postgres dialect (e.g. a
    local SQLite dev setup) — production always runs Postgres, so this is
    not an active bug, but a future local/dev run on SQLite would silently
    lose all pull/sell/currency-grant race protection. Make that loud
    instead of silent (see docs/GACHA_MODERNIZATION_TODO.md, Этап 0)."""
    session = _FakeSession("sqlite")
    repo = GachaRepository(session)  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING, logger="gacha_service.infrastructure.repository"):
        await repo.lock_user_banner(user_id=1, banner="genshin")
        await repo.lock_user_banner(user_id=2, banner="hsr")

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 1
    assert "advisory lock" in warning_records[0].message.lower()
    session.execute.assert_not_awaited()
