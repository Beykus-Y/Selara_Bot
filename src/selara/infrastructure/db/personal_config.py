from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.personal_config import (
    CachedPersonalConfigProvider,
    PersonalConfig,
    PersonalConfigOverride,
    config_from_settings,
    merge_config,
)
from selara.core.config import Settings
from selara.infrastructure.db.models import SelaraPersonalConfigModel

_ROW_ID = 1


class SqlAlchemyPersonalConfigStore:
    """Reads and saves the singleton override row and refreshes the provider cache on save."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        base: PersonalConfig,
        provider: CachedPersonalConfigProvider | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._base = base
        self._provider = provider

    async def load_override(self) -> PersonalConfigOverride | None:
        async with self._session_factory() as session:
            row = await session.get(SelaraPersonalConfigModel, _ROW_ID)
        if row is None:
            return None
        return PersonalConfigOverride(
            price_stars=row.price_stars,
            duration_days=row.duration_days,
            free_daily_limit=row.free_daily_limit,
            paid_daily_limit=row.paid_daily_limit,
            memory_free_limit=row.memory_free_limit,
            memory_paid_limit=row.memory_paid_limit,
            memory_auto_extract=row.memory_auto_extract,
            memory_extract_every=row.memory_extract_every,
        )

    async def save_override(self, override: PersonalConfigOverride, *, updated_by: int | None = None) -> PersonalConfig:
        """Replace the stored override (``None`` fields clear it back to .env); returns the effective config."""
        effective = merge_config(self._base, override)  # ValueError before anything is written
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(SelaraPersonalConfigModel, _ROW_ID, with_for_update=True)
                if row is None:
                    row = SelaraPersonalConfigModel(id=_ROW_ID)
                    session.add(row)
                row.price_stars = override.price_stars
                row.duration_days = override.duration_days
                row.free_daily_limit = override.free_daily_limit
                row.paid_daily_limit = override.paid_daily_limit
                row.memory_free_limit = override.memory_free_limit
                row.memory_paid_limit = override.memory_paid_limit
                row.memory_auto_extract = override.memory_auto_extract
                row.memory_extract_every = override.memory_extract_every
                row.updated_by = updated_by
        if self._provider is not None:
            self._provider.invalidate()
        return effective


def build_personal_config(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    ttl_seconds: float = 15.0,
) -> tuple[CachedPersonalConfigProvider, SqlAlchemyPersonalConfigStore]:
    base = config_from_settings(settings)
    store = SqlAlchemyPersonalConfigStore(session_factory, base)
    provider = CachedPersonalConfigProvider(base, store.load_override, ttl_seconds=ttl_seconds)
    store._provider = provider
    return provider, store
