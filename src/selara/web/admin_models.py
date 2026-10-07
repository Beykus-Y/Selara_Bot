"""Owner model configuration API using the existing catalog store and router."""
from dataclasses import asdict, replace
from decimal import Decimal
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from selara.application.model_catalog import (
    CatalogModel, CatalogSnapshot, ModelCapabilities, ModelConfigurationConflict, ModelProfile,
)
from selara.application.model_router import DefaultModelRouter
from selara.application.llm_routes import ROUTE_TITLES
from selara.infrastructure.db.llm_routes import build_feature_routes
from selara.infrastructure.db.model_catalog import build_model_catalog

logger = logging.getLogger(__name__)


class CapabilitiesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supports_tools: StrictBool = False
    supports_structured_output: StrictBool = False
    supports_vision: StrictBool = False


class ModelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str
    model_id: str
    enabled: StrictBool = True
    prompt_price_usd_per_million: Decimal | None = None
    completion_price_usd_per_million: Decimal | None = None
    aliases: list[str] = Field(default_factory=list, max_length=100)
    capabilities: CapabilitiesInput = Field(default_factory=CapabilitiesInput)


class ModelCreate(ModelInput):
    key: str


class ModelUpdate(ModelInput):
    revision: Annotated[StrictInt, Field(ge=1)]
    confirm_disable: StrictBool = False


class RouteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_key: str | None = None


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str
    model_key: str | None = None
    ail_multiplier: Decimal
    enabled: StrictBool
    revision: Annotated[StrictInt, Field(ge=1)]


class _SnapshotProvider:
    def __init__(self, snapshot: CatalogSnapshot):
        self.snapshot = snapshot

    async def get(self) -> CatalogSnapshot:
        return self.snapshot


def _model_json(model: CatalogModel, snapshot: CatalogSnapshot) -> dict:
    data = asdict(model)
    for name in ("prompt_price_usd_per_million", "completion_price_usd_per_million"):
        data[name] = None if data[name] is None else format(data[name], "f")
    data["used_by_profiles"] = [p.profile_key for p in snapshot.profiles if p.model_key == model.key]
    return data


def build_admin_models_router(*, settings, session_factory, require_admin) -> APIRouter:
    router = APIRouter(prefix="/ai", dependencies=[Depends(require_admin)])
    _provider, store = build_model_catalog(session_factory)

    async def load() -> CatalogSnapshot:
        try:
            return await store.load()
        except SQLAlchemyError:
            logger.exception("Unable to read admin model configuration")
            raise HTTPException(503, "Не удалось загрузить конфигурацию. Повторите позже.") from None

    async def profiles_json(snapshot: CatalogSnapshot) -> list[dict]:
        resolver = DefaultModelRouter(settings.llm_model, _SnapshotProvider(snapshot))
        result = []
        for profile in snapshot.profiles:
            resolved = await resolver.resolve(profile_key=profile.profile_key)
            assigned = snapshot.models_by_key.get(profile.model_key)
            effective = snapshot.models_by_id.get(resolved.model_id)
            data = asdict(profile)
            data["ail_multiplier"] = format(profile.ail_multiplier, "f")
            # The snapshot reference is internal (pricing source), not part of the admin DTO.
            data["effective"] = asdict(replace(resolved, catalog=None))
            data["effective"].pop("catalog", None)
            data["effective"]["ail_multiplier"] = format(resolved.ail_multiplier, "f")
            data["assigned_model"] = _model_json(assigned, snapshot) if assigned else None
            data["effective_pricing"] = _model_json(effective, snapshot) if effective else None
            result.append(data)
        return result

    async def write(operation):
        try:
            await operation()
        except ModelConfigurationConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except IntegrityError:
            raise HTTPException(409, "Такой ключ, model_id или alias уже используется другой моделью.") from None
        except SQLAlchemyError:
            logger.exception("Unable to save admin model configuration")
            raise HTTPException(503, "Не удалось сохранить конфигурацию. Текущая рабочая конфигурация не изменена.") from None

    def model_value(key: str, payload: ModelInput) -> CatalogModel:
        try:
            values = payload.model_dump(exclude={"key", "revision", "confirm_disable", "capabilities"})
            return CatalogModel(key=key, capabilities=ModelCapabilities(**payload.capabilities.model_dump()), **values)
        except ValueError:
            raise HTTPException(422, "Проверьте ключ, название, model_id и aliases. Цена должна быть конечным числом от 0 до 1000000, максимум 9 знаков после точки.") from None

    @router.get("/models")
    async def models():
        snapshot = await load()
        return {"ok": True, "items": [_model_json(m, snapshot) for m in snapshot.models],
                "applies_within_seconds": 15}

    @router.post("/models", status_code=201)
    async def create_model(payload: ModelCreate):
        model = model_value(payload.key, payload)
        await write(lambda: store.save_model(model, expected_revision=0, updated_by=settings.admin_user_id))
        snapshot = await load()
        return {"ok": True, "item": _model_json(snapshot.models_by_key[model.key], snapshot)}

    @router.put("/models/{key}")
    async def update_model(key: str, payload: ModelUpdate):
        model = model_value(key, payload)
        # Missing rows must not turn PUT into an accidental create.
        if key not in (await load()).models_by_key:
            raise HTTPException(404, "Модель не найдена.")
        await write(lambda: store.save_model(model, expected_revision=payload.revision,
                    updated_by=settings.admin_user_id, confirm_disable=payload.confirm_disable))
        snapshot = await load()
        return {"ok": True, "item": _model_json(snapshot.models_by_key[key], snapshot)}

    @router.get("/model-profiles")
    async def profiles():
        return {"ok": True, "items": await profiles_json(await load()), "applies_within_seconds": 15,
                "fallback_note": "Fallback зависит от операции: LLM_MODEL или LLM_SUMMARY_MODEL."}

    @router.put("/model-profiles/{profile_key}")
    async def update_profile(profile_key: str, payload: ProfileUpdate):
        try:
            profile = ModelProfile(profile_key=profile_key, **payload.model_dump(exclude={"revision"}))
        except ValueError:
            raise HTTPException(422, "Проверьте профиль и название. Коэффициент должен быть конечным числом > 0 и ≤ 1000, максимум 9 знаков после точки.") from None
        if profile_key not in (await load()).profiles_by_key:
            raise HTTPException(404, "Профиль не найден.")
        await write(lambda: store.save_profile(profile, expected_revision=payload.revision,
                                              updated_by=settings.admin_user_id))
        snapshot = await load()
        return {"ok": True, "item": next(p for p in await profiles_json(snapshot) if p["profile_key"] == profile_key)}

    _routes_provider, route_store = build_feature_routes(session_factory)

    async def routes_json(snapshot: CatalogSnapshot) -> list[dict]:
        try:
            stored = await route_store.load()
        except SQLAlchemyError:
            logger.exception("Unable to read feature model routes")
            raise HTTPException(503, "Не удалось загрузить настройки. Повторите позже.") from None
        resolver = DefaultModelRouter(settings.llm_model, _SnapshotProvider(snapshot))
        items = []
        for key, title in ROUTE_TITLES.items():
            profile_key = stored.get(key)
            resolved = await resolver.resolve(profile_key=profile_key)
            items.append({"route_key": key, "title": title, "profile_key": profile_key,
                          "effective_model_id": resolved.model_id, "is_fallback": resolved.is_fallback})
        return items

    @router.get("/feature-routes")
    async def feature_routes():
        snapshot = await load()
        return {"ok": True, "items": await routes_json(snapshot),
                "profiles": [{"profile_key": p.profile_key, "display_name": p.display_name} for p in snapshot.profiles],
                "applies_within_seconds": 15,
                "fallback_note": "Если профиль не выбран, выключен или без модели, используется LLM_MODEL из .env."}

    @router.put("/feature-routes/{route_key}")
    async def update_feature_route(route_key: str, payload: RouteUpdate):
        snapshot = await load()
        if payload.profile_key is not None and payload.profile_key not in snapshot.profiles_by_key:
            raise HTTPException(422, "Профиль не найден.")
        await write(lambda: route_store.save(route_key, payload.profile_key, updated_by=settings.admin_user_id))
        return {"ok": True, "item": next(i for i in await routes_json(await load()) if i["route_key"] == route_key)}

    return router
