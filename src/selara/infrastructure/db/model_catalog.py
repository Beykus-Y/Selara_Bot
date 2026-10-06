"""Transactional catalog writes and repeatable-read snapshots for the runtime cache."""
from __future__ import annotations

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.model_catalog import (
    CachedModelCatalogProvider, CatalogModel, CatalogSnapshot, ModelCapabilities, ModelProfile,
    ModelConfigurationConflict,
)
from selara.infrastructure.db.models import LlmModelCatalogModel, LlmModelIdentifierModel, LlmModelProfileModel


class SqlAlchemyModelCatalogStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession],
                 provider: CachedModelCatalogProvider | None = None) -> None:
        self._session_factory = session_factory
        self._provider = provider

    async def load(self) -> CatalogSnapshot:
        async with self._session_factory() as session, session.begin():
            # All three SELECTs see the same revision even if an admin writes between them.
            if session.bind.dialect.name == "postgresql":
                await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            models = (await session.scalars(select(LlmModelCatalogModel).order_by(LlmModelCatalogModel.key))).all()
            identifiers = (await session.scalars(select(LlmModelIdentifierModel))).all()
            profiles = (await session.scalars(select(LlmModelProfileModel).order_by(LlmModelProfileModel.profile_key))).all()
            names: dict[str, list[str]] = {}
            for identifier in identifiers:
                names.setdefault(identifier.model_key, []).append(identifier.model_id)
            return CatalogSnapshot(
                models=tuple(CatalogModel(
                    key=m.key, model_id=m.model_id, display_name=m.display_name, enabled=m.enabled,
                    prompt_price_usd_per_million=m.prompt_price_usd_per_million,
                    completion_price_usd_per_million=m.completion_price_usd_per_million,
                    revision=m.revision, updated_by=m.updated_by, updated_at=m.updated_at,
                    capabilities=ModelCapabilities(m.supports_tools, m.supports_structured_output, m.supports_vision),
                    aliases=tuple(sorted(name for name in names.get(m.key, ()) if name != m.model_id)),
                ) for m in models),
                profiles=tuple(ModelProfile(p.profile_key, p.display_name, p.model_key, p.ail_multiplier, p.enabled,
                                             p.revision, p.updated_by, p.updated_at)
                               for p in profiles),
            )

    async def save_model(self, model: CatalogModel, *, expected_revision: int | None = None,
                         updated_by: int | None = None, confirm_disable: bool = False) -> None:
        async with self._session_factory() as session, session.begin():
            # Serialize catalog writers so concurrent alias reassignment is deterministic.
            if session.bind.dialect.name == "postgresql":
                await session.execute(text("SELECT pg_advisory_xact_lock(731902113)"))
            row = await session.get(LlmModelCatalogModel, model.key, with_for_update=True)
            self._check_revision(row, expected_revision)
            if row is not None and row.enabled and not model.enabled and expected_revision is not None:
                references = (await session.scalars(select(LlmModelProfileModel.profile_key).where(
                    LlmModelProfileModel.model_key == model.key))).all()
                if references and not confirm_disable:
                    raise ValueError("Модель используется профилями: " + ", ".join(references)
                                     + ". Подтвердите выключение.")
            if row is None:
                row = LlmModelCatalogModel(key=model.key)
                session.add(row)
            row.revision = (row.revision or 0) + 1
            row.updated_by = updated_by
            for name in ("model_id", "display_name", "enabled", "prompt_price_usd_per_million",
                         "completion_price_usd_per_million"):
                setattr(row, name, getattr(model, name))
            for name in ("supports_tools", "supports_structured_output", "supports_vision"):
                setattr(row, name, getattr(model.capabilities, name))
            await session.flush()
            await session.execute(delete(LlmModelIdentifierModel).where(LlmModelIdentifierModel.model_key == model.key))
            session.add_all(LlmModelIdentifierModel(model_id=name, model_key=model.key)
                            for name in (model.model_id, *model.aliases))
        self._invalidate()

    async def save_profile(self, profile: ModelProfile, *, expected_revision: int | None = None,
                           updated_by: int | None = None) -> None:
        async with self._session_factory() as session, session.begin():
            if session.bind.dialect.name == "postgresql":
                await session.execute(text("SELECT pg_advisory_xact_lock(731902113)"))
            row = await session.get(LlmModelProfileModel, profile.profile_key, with_for_update=True)
            self._check_revision(row, expected_revision)
            if expected_revision is not None and profile.model_key is not None:
                model = await session.get(LlmModelCatalogModel, profile.model_key)
                if model is None:
                    raise ValueError("Модель не найдена.")
                if not model.enabled and (row is None or row.model_key != profile.model_key):
                    raise ValueError("Нельзя назначить выключенную модель.")
            if row is None:
                row = LlmModelProfileModel(profile_key=profile.profile_key)
                session.add(row)
            row.revision = (row.revision or 0) + 1
            row.updated_by = updated_by
            for name in ("display_name", "model_key", "ail_multiplier", "enabled"):
                setattr(row, name, getattr(profile, name))
        self._invalidate()

    @staticmethod
    def _check_revision(row, expected_revision: int | None) -> None:
        if expected_revision is not None and (row.revision if row is not None else 0) != expected_revision:
            raise ModelConfigurationConflict("Настройка уже была изменена. Обновите данные и повторите.")

    def _invalidate(self) -> None:
        if self._provider is not None:
            self._provider.invalidate()


def build_model_catalog(session_factory: async_sessionmaker[AsyncSession], *, ttl_seconds: float = 15
                        ) -> tuple[CachedModelCatalogProvider, SqlAlchemyModelCatalogStore]:
    store = SqlAlchemyModelCatalogStore(session_factory)
    provider = CachedModelCatalogProvider(store.load, ttl_seconds=ttl_seconds)
    store._provider = provider
    return provider, store
