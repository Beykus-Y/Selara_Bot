from __future__ import annotations

import asyncio
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from selara.core.config import Settings

APPLICATION_NAME = "selara-bot"

DB_ADMISSION_TIMEOUT = "db_admission_timeout"
DB_POOL_TIMEOUT = "db_pool_timeout"
DB_CONNECT_TIMEOUT = "db_connect_timeout"
DB_UNAVAILABLE = "db_unavailable"

# Frames that open a connection: asyncpg's connect() and SQLAlchemy's pool connect.
_CONNECT_FRAME_NAMES = frozenset({"connect", "__connect"})
_DRIVER_PATH_MARKERS = ("/sqlalchemy/", "/asyncpg/")


class DatabaseAdmissionTimeout(TimeoutError):
    """An update waited too long for a database slot and never reached its handler."""


@dataclass(frozen=True, slots=True)
class DatabaseFailure:
    """Category and stage of a database failure, for logs and operator alerts. Carries no DSN."""

    category: str
    stage: str


def create_engine(settings: Settings) -> AsyncEngine:
    connect_args: dict[str, object] = {}
    if make_url(settings.database_url).drivername == "postgresql+asyncpg":
        connect_args = {
            # asyncpg waits 60 s to connect by default, and the pool slot stays taken for that whole wait.
            "timeout": settings.db_connect_timeout_seconds,
            # Without a bound, a query on a silently dead socket never returns and its pool slot never comes back.
            "command_timeout": settings.db_command_timeout_seconds,
            "server_settings": {"application_name": APPLICATION_NAME},
        }
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


class DatabaseAdmission:
    """Caps how many application updates hold a pooled connection at the same time.

    A handler keeps its session, and so its connection, from its first query until the update ends.
    Without a cap, a burst of slow updates takes every pool slot, and the updates behind them fail
    after the pool timeout. Here the extra updates wait without a connection, for at most the same
    timeout, so the pool keeps slots for background jobs and the web panel.
    """

    def __init__(self, *, max_concurrent: int, wait_timeout_seconds: float) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        if wait_timeout_seconds <= 0:
            raise ValueError("wait_timeout_seconds must be positive")
        self.max_concurrent = max_concurrent
        self._wait_timeout_seconds = wait_timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._in_use = 0
        self._waiting = 0

    @classmethod
    def from_settings(cls, settings: Settings) -> DatabaseAdmission:
        limit = settings.db_max_concurrent_updates
        # QueuePool has no overflow limit when pool_size is 0 or max_overflow is -1,
        # so only the configured limit applies there.
        if settings.db_pool_size > 0 and settings.db_max_overflow >= 0:
            capacity = settings.db_pool_size + settings.db_max_overflow
            # One pooled connection always stays free for background jobs, the web panel and nested sessions.
            limit = min(limit, max(1, capacity - 1))
        return cls(max_concurrent=limit, wait_timeout_seconds=settings.db_pool_timeout_seconds)

    @property
    def in_use(self) -> int:
        return self._in_use

    @property
    def waiting(self) -> int:
        return self._waiting

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        self._waiting += 1
        try:
            async with asyncio.timeout(self._wait_timeout_seconds):
                await self._semaphore.acquire()
        except TimeoutError as exc:
            raise DatabaseAdmissionTimeout(
                f"No database slot within {self._wait_timeout_seconds:g}s "
                f"(in use {self._in_use}/{self.max_concurrent}, waiting {self._waiting})"
            ) from exc
        finally:
            self._waiting -= 1
        self._in_use += 1
        try:
            yield
        finally:
            self._in_use -= 1
            self._semaphore.release()


def classify_database_failure(exc: BaseException) -> DatabaseFailure | None:
    """Maps an error from the database layer to a category and stage. Any other error maps to None."""
    if isinstance(exc, DatabaseAdmissionTimeout):
        return DatabaseFailure(DB_ADMISSION_TIMEOUT, "admission")
    if isinstance(exc, PoolTimeoutError):
        return DatabaseFailure(DB_POOL_TIMEOUT, "pool_checkout")
    if isinstance(exc, (DBAPIError, OSError)):
        # Only driver frames count, so a handler named connect() does not turn a query error into a connect failure.
        driver_frames = [
            frame
            for frame in traceback.extract_tb(exc.__traceback__)
            if any(marker in frame.filename for marker in _DRIVER_PATH_MARKERS)
        ]
        if any(frame.name in _CONNECT_FRAME_NAMES for frame in driver_frames):
            # Any failure to open a connection counts: a timeout, a refusal that SQLAlchemy wraps as a generic
            # DBAPIError (too many connections), or a DNS error, which arrives as a plain OSError.
            category = DB_CONNECT_TIMEOUT if isinstance(exc, TimeoutError) else DB_UNAVAILABLE
            return DatabaseFailure(category, "connect")
        if isinstance(exc, DBAPIError):
            if exc.connection_invalidated or isinstance(exc, (OperationalError, InterfaceError)):
                return DatabaseFailure(DB_UNAVAILABLE, "query")
            return None  # a statement or data error: the database answered
        if driver_frames:
            return DatabaseFailure(DB_UNAVAILABLE, "query")
    return None


def describe_session_pool(session: object | None) -> str | None:
    """Pool counters for the engine behind a session. Reads counters only, never the URL."""
    bind = getattr(session, "bind", None)
    pool = getattr(getattr(bind, "sync_engine", None), "pool", None)
    if pool is None:
        return None
    try:
        return str(pool.status())
    except Exception:
        return None
