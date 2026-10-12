import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.infrastructure.db.session import (
    DB_ADMISSION_TIMEOUT,
    DatabaseAdmission,
    DatabaseAdmissionTimeout,
    DatabaseFailure,
    classify_database_failure,
)
from selara.presentation.middlewares import db_session as db_session_module
from selara.presentation.middlewares.db_session import DBSessionMiddleware


@pytest.fixture(autouse=True)
def _stub_achievement_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    # The middleware only stores the catalog; these tests never award achievements.
    monkeypatch.setattr(db_session_module, "get_achievement_catalog_from_settings", lambda _settings: None)


@pytest_asyncio.fixture
async def tiny_pool(tmp_path):
    """One pooled connection and a short wait, so an exhausted pool fails within a fraction of a second."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}",
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.2,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


def _factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


def _pool(engine):
    return engine.sync_engine.pool


def _run(middleware, handler):
    return middleware(handler, SimpleNamespace(), {"settings": SimpleNamespace()})


async def _query_once(_event, data):
    await data["db_session"].execute(text("SELECT 1"))
    return "done"


def _middleware(engine, *, bounded: bool) -> DBSessionMiddleware:
    admission = DatabaseAdmission(max_concurrent=1, wait_timeout_seconds=5) if bounded else None
    return DBSessionMiddleware(_factory(engine), admission=admission)


@pytest.mark.asyncio
async def test_second_session_times_out_while_the_only_slot_is_held(tiny_pool) -> None:
    """The production symptom in miniature: with the pool's one slot taken, the next checkout fails."""
    factory = _factory(tiny_pool)
    holder = factory()
    await holder.execute(text("SELECT 1"))
    try:
        with pytest.raises(PoolTimeoutError):
            async with factory() as waiting:
                await waiting.execute(text("SELECT 1"))
    finally:
        await holder.close()

    assert _pool(tiny_pool).checkedout() == 0


@pytest.mark.asyncio
async def test_admission_keeps_queued_updates_out_of_the_pool(tiny_pool) -> None:
    admission = DatabaseAdmission(max_concurrent=1, wait_timeout_seconds=5)
    middleware = DBSessionMiddleware(_factory(tiny_pool), admission=admission)
    first_holds_connection = asyncio.Event()
    release_first = asyncio.Event()

    async def first(_event, data):
        await data["db_session"].execute(text("SELECT 1"))
        first_holds_connection.set()
        await release_first.wait()
        return "first"

    first_task = asyncio.create_task(_run(middleware, first))
    await first_holds_connection.wait()
    second_task = asyncio.create_task(_run(middleware, _query_once))
    for _ in range(100):
        if admission.waiting == 1:
            break
        await asyncio.sleep(0)

    assert admission.waiting == 1
    assert _pool(tiny_pool).checkedout() == 1

    release_first.set()
    assert await first_task == "first"
    assert await second_task == "done"
    assert admission.in_use == 0
    assert _pool(tiny_pool).checkedout() == 0


@pytest.mark.asyncio
async def test_update_that_never_gets_a_slot_fails_before_its_handler_runs(tiny_pool) -> None:
    admission = DatabaseAdmission(max_concurrent=1, wait_timeout_seconds=0.05)
    middleware = DBSessionMiddleware(_factory(tiny_pool), admission=admission)
    holder_has_slot = asyncio.Event()
    release_holder = asyncio.Event()
    handler_ran: list[bool] = []

    async def holder(_event, data):
        await data["db_session"].execute(text("SELECT 1"))
        holder_has_slot.set()
        await release_holder.wait()

    async def late(_event, _data):
        handler_ran.append(True)

    holder_task = asyncio.create_task(_run(middleware, holder))
    await holder_has_slot.wait()
    try:
        with pytest.raises(DatabaseAdmissionTimeout) as caught:
            await _run(middleware, late)
    finally:
        release_holder.set()
        await holder_task

    assert handler_ran == []
    assert classify_database_failure(caught.value) == DatabaseFailure(DB_ADMISSION_TIMEOUT, "admission")
    assert admission.waiting == 0
    assert admission.in_use == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("bounded", [False, True])
async def test_failed_update_returns_its_connection_to_the_pool(tiny_pool, bounded: bool) -> None:
    middleware = _middleware(tiny_pool, bounded=bounded)

    async def failing(_event, data):
        await data["db_session"].execute(text("SELECT 1"))
        raise ValueError("handler failed")

    with pytest.raises(ValueError, match="handler failed"):
        await _run(middleware, failing)

    assert _pool(tiny_pool).checkedout() == 0
    # With one pooled connection, a leaked slot would make this checkout time out.
    assert await _run(middleware, _query_once) == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("bounded", [False, True])
async def test_cancelled_update_returns_its_connection_to_the_pool(tiny_pool, bounded: bool) -> None:
    middleware = _middleware(tiny_pool, bounded=bounded)
    started = asyncio.Event()

    async def hanging(_event, data):
        await data["db_session"].execute(text("SELECT 1"))
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(_run(middleware, hanging))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert _pool(tiny_pool).checkedout() == 0
    assert await _run(middleware, _query_once) == "done"


class _BrokenRollbackSession:
    closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc_info) -> None:
        self.closed = True

    async def rollback(self) -> None:
        raise ConnectionResetError("connection reset by peer")


@pytest.mark.asyncio
async def test_rollback_failure_does_not_replace_the_handler_error() -> None:
    session = _BrokenRollbackSession()
    middleware = DBSessionMiddleware(lambda: session)

    async def failing(_event, _data):
        raise ValueError("handler failed")

    with pytest.raises(ValueError, match="handler failed"):
        await _run(middleware, failing)

    assert session.closed


def test_admission_rejects_a_limit_below_one() -> None:
    with pytest.raises(ValueError):
        DatabaseAdmission(max_concurrent=0, wait_timeout_seconds=1)
