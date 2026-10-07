from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.llm_routes import CachedFeatureRoutes, validate_route
from selara.infrastructure.db.models import LlmFeatureRouteModel


class SqlAlchemyFeatureRouteStore:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], provider: CachedFeatureRoutes | None = None
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider

    async def load(self) -> dict[str, str | None]:
        async with self._session_factory() as session:
            rows = await session.scalars(select(LlmFeatureRouteModel))
            return {row.route_key: row.profile_key for row in rows}

    async def save(self, route_key: str, profile_key: str | None, *, updated_by: int | None = None) -> None:
        validate_route(route_key, profile_key)
        async with self._session_factory() as session, session.begin():
            row = await session.get(LlmFeatureRouteModel, route_key, with_for_update=True)
            if row is None:
                row = LlmFeatureRouteModel(route_key=route_key)
                session.add(row)
            row.profile_key = profile_key
            row.updated_by = updated_by
            row.updated_at = datetime.now(timezone.utc)
        if self._provider is not None:
            self._provider.invalidate()


def build_feature_routes(session_factory: async_sessionmaker[AsyncSession]):
    store = SqlAlchemyFeatureRouteStore(session_factory)
    provider = CachedFeatureRoutes(store.load)
    store._provider = provider
    return provider, store
