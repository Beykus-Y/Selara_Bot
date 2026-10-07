"""Restore drill checks against a real PostgreSQL server (issue #69).

They need TEST_DATABASE_URL. Each test builds its own scratch database from the
current models and a recorded alembic head, so they do not depend on what other
integration tests leave in the shared test database. A full pg_dump/pg_restore
round trip needs PostgreSQL client tools on the CI runner; the nightly drill in
the bot runtime covers that path, and these tests cover the SQL the drill runs.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from selara.infrastructure import backup_drill
from selara.infrastructure.backup_drill import BackupDrillError, DrillTarget
from selara.infrastructure.db import models  # noqa: F401 - registers every table on Base.metadata
from selara.infrastructure.db.base import Base

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _admin_url() -> URL:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return make_url(database_url)


@asynccontextmanager
async def _scratch_schema(head: str) -> AsyncIterator[URL]:
    """Yield a scratch database holding the current models and `head` in alembic_version; drop it afterwards."""
    admin_url = _admin_url()
    name = f"{backup_drill.SCRATCH_DATABASE_PREFIX}test_{uuid.uuid4().hex}"
    admin_engine = backup_drill._create_engine(admin_url, autocommit=True)
    try:
        await backup_drill._create_scratch_database(admin_engine, name)
        scratch_url = admin_url.set(database=name)
        scratch_engine = create_async_engine(scratch_url, poolclass=NullPool)
        try:
            async with scratch_engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                await connection.execute(
                    text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
                )
                await connection.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
                    {"head": head},
                )
        finally:
            await scratch_engine.dispose()
        yield scratch_url
    finally:
        await backup_drill._drop_scratch_database(admin_engine, name)
        await admin_engine.dispose()


async def _database_exists(engine: AsyncEngine, name: str) -> bool:
    async with engine.connect() as connection:
        count = (
            await connection.execute(
                text("SELECT count(*) FROM pg_database WHERE datname = :name"),
                {"name": name},
            )
        ).scalar_one()
    return count == 1


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_scratch_database_is_created_and_dropped_on_the_server() -> None:
    admin_url = _admin_url()
    name = f"{backup_drill.SCRATCH_DATABASE_PREFIX}test_{uuid.uuid4().hex}"
    engine = backup_drill._create_engine(admin_url, autocommit=True)
    try:
        await backup_drill._create_scratch_database(engine, name)
        assert await _database_exists(engine, name)
        await backup_drill._drop_scratch_database(engine, name)
        assert not await _database_exists(engine, name)
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_live_schema_head_is_read_from_the_restored_alembic_version() -> None:
    head = backup_drill.repository_alembic_head(REPOSITORY_ROOT / "alembic")

    async with _scratch_schema(head) as scratch_url:
        assert await backup_drill.read_live_schema_head(scratch_url, label="test database") == head


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_restored_database_check_passes_at_the_expected_head() -> None:
    head = backup_drill.repository_alembic_head(REPOSITORY_ROOT / "alembic")
    target = DrillTarget(
        label="test database",
        required_tables=("users", "chats", "chat_settings"),
        expected_head=head,
    )

    async with _scratch_schema(head) as scratch_url:
        result = await backup_drill.check_restored_database(scratch_url, target)

    assert result.schema_head == head
    assert result.row_counts == {"users": 0, "chats": 0, "chat_settings": 0}


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_restored_database_check_rejects_a_different_schema_head() -> None:
    head = backup_drill.repository_alembic_head(REPOSITORY_ROOT / "alembic")
    target = DrillTarget(label="test database", required_tables=("users",), expected_head=head)

    async with _scratch_schema("0000_not_a_revision") as scratch_url:
        with pytest.raises(BackupDrillError, match=f"does not match expected {head}"):
            await backup_drill.check_restored_database(scratch_url, target)


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_restored_database_check_rejects_an_empty_table_that_must_hold_rows() -> None:
    head = backup_drill.repository_alembic_head(REPOSITORY_ROOT / "alembic")
    target = DrillTarget(
        label="test database",
        required_tables=("users",),
        non_empty_tables=("users",),
        expected_head=head,
    )

    async with _scratch_schema(head) as scratch_url:
        with pytest.raises(BackupDrillError, match="table users is empty"):
            await backup_drill.check_restored_database(scratch_url, target)
