"""Mini App API of the "Моя Selara" page: subscription, quota, personal memory and privacy controls.

Every handler works for the user of the Mini App session and nobody else: the Telegram user id comes from
the session, never from the request, and all queries are keyed by it. Mutations need a JSON body (a plain
HTML form from another site cannot send one), read answers are never cached, and nothing here spends quota.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import wraps
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.feature_access import AccessTier, FeatureAccessService
from selara.application.personal_config import PersonalConfig, PersonalConfigProvider
from selara.application.personal_memory import MemoryValidationError, normalize_memory_text
from selara.application.personal_overview import (
    PAID_TIERS,
    build_personal_status,
    memory_limit_for,
    resolve_personal_decision,
)
from selara.core.config import Settings
from selara.domain.entities import UserSnapshot
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.db.personal_ai_repository import AddMemoryStatus, PersonalAiRepository
from selara.infrastructure.db.personal_config import build_personal_config
from selara.infrastructure.db.telegram_stars import SqlAlchemyUserEntitlementResolver
from selara.presentation.auth import resolve_owner_private_exemption

logger = logging.getLogger(__name__)

UserLoader = Callable[[AsyncSession, Request], Awaitable[UserSnapshot | None]]
OfferChecker = Callable[[Settings, PersonalConfig], bool]

_MAX_BODY_BYTES = 4096
_SETTINGS_FIELDS = frozenset({"memory_enabled", "auto_memory_enabled"})


class _ApiError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(content={"ok": False, "message": message}, status_code=status_code)


def _json_errors(handler):
    """Turn ``_ApiError`` into the ``{ok: false, message}`` shape the Mini App client reads."""

    @wraps(handler)
    async def wrapper(*args, **kwargs):
        try:
            return await handler(*args, **kwargs)
        except _ApiError as exc:
            return _error_response(exc.status_code, exc.message)

    return wrapper


async def _read_json_object(request: Request) -> dict[str, Any]:
    if not request.headers.get("content-type", "").lower().startswith("application/json"):
        raise _ApiError(415, "Ожидался JSON.")
    body = await request.body()
    if len(body) > _MAX_BODY_BYTES:
        raise _ApiError(413, "Слишком большой запрос.")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise _ApiError(422, "Некорректный JSON.") from None
    if not isinstance(payload, dict):
        raise _ApiError(422, "Ожидался JSON-объект.")
    return payload


def _memory_item(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "content": row.content,
        "pinned": bool(row.pinned),
        "source": row.source,
        "created_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
    }


def build_miniapp_personal_router(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    load_user: UserLoader,
    personal_config: PersonalConfigProvider | None = None,
    access_service: FeatureAccessService | None = None,
    offer_checker: OfferChecker | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/miniapp/personal", tags=["miniapp-personal"])
    config_provider = personal_config or build_personal_config(session_factory, settings)[0]
    service = access_service or FeatureAccessService(
        SqlAlchemyFeatureQuotaRepository(session_factory),
        user_entitlement_resolver=SqlAlchemyUserEntitlementResolver(session_factory, config_provider),
        personal_config=config_provider,
    )
    if offer_checker is None:
        from selara.presentation.handlers.premium import personal_offer_available as offer_checker  # noqa: PLC0415

    bot_dm_url = f"https://t.me/{settings.bot_username.strip().lstrip('@')}"

    @asynccontextmanager
    async def scope(request: Request):
        async with session_factory() as session:
            user = await load_user(session, request)
            if user is None:
                await session.commit()
                raise _ApiError(401, "Mini App сессия истекла.")
            yield session, user
            await session.commit()

    async def _tier(user: UserSnapshot):
        return await resolve_personal_decision(
            service,
            user_id=user.telegram_user_id,
            owner_exempt=resolve_owner_private_exemption(
                user_id=user.telegram_user_id, admin_user_id=settings.admin_user_id
            ),
        )

    async def _memory_limit(user: UserSnapshot, config: PersonalConfig) -> int | None:
        decision = await _tier(user)
        return memory_limit_for(decision.access_tier if decision else None, config)

    def _profile_payload(stored, config: PersonalConfig, tier: AccessTier | None) -> dict[str, Any]:
        return {
            "memory_enabled": stored.memory_enabled if stored else True,
            "auto_memory_enabled": stored.auto_memory_enabled if stored else False,
            # Extraction only runs for a paid (or owner) user and only while the admin switch is on.
            "auto_memory_available": bool(config.memory_auto_extract and tier in PAID_TIERS),
            "display_name": stored.profile.display_name if stored else None,
            "mode": stored.profile.mode if stored else "assistant",
        }

    @router.get("")
    @_json_errors
    async def overview(request: Request):
        async with scope(request) as (session, user):
            user_id = user.telegram_user_id
            config = await config_provider.get()
            owner_exempt = resolve_owner_private_exemption(user_id=user_id, admin_user_id=settings.admin_user_id)
            decision = await resolve_personal_decision(service, user_id=user_id, owner_exempt=owner_exempt)
            status = await build_personal_status(
                access_service=service,
                decision=decision,
                user_id=user_id,
                owner_exempt=owner_exempt,
                timezone_name=settings.bot_timezone,
                config=config,
                offer_available=offer_checker(settings, config),
                bot_dm_url=bot_dm_url,
            )
            tier = decision.access_tier if decision else None
            repo = PersonalAiRepository(session)
            stored = await repo.get_profile(user_id)  # read-only: opening the page creates nothing
            rows = await repo.list_memories(user_id=user_id)
            payload = {
                "ok": True,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "timezone": settings.bot_timezone,
                **status,
                "profile": _profile_payload(stored, config, tier),
                "memory": {
                    "count": len(rows),
                    "limit": memory_limit_for(tier, config),
                    "items": [_memory_item(row) for row in rows],
                },
            }
        return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})

    @router.post("/memory")
    @_json_errors
    async def add_memory(request: Request):
        async with scope(request) as (session, user):
            payload = await _read_json_object(request)
            content = payload.get("content")
            if not isinstance(content, str):
                raise _ApiError(422, "Напишите, что запомнить.")
            try:
                fact = normalize_memory_text(content)
            except MemoryValidationError as exc:
                raise _ApiError(422, str(exc)) from None
            user_id = user.telegram_user_id
            repo = PersonalAiRepository(session)
            stored = await repo.get_or_create_profile(user_id)
            if not stored.memory_enabled:
                raise _ApiError(409, "Память выключена, поэтому я ничего не сохраняю. Включите её выше.")
            config = await config_provider.get()
            limit = await _memory_limit(user, config)
            if limit is None:
                raise _ApiError(503, "Проверка доступа временно недоступна. Попробуйте позже.")
            result = await repo.add_memory(user_id=user_id, content=fact, source="explicit", limit=limit)
            if result.status == AddMemoryStatus.LIMIT_REACHED:
                raise _ApiError(
                    409, f"Достигнут лимит памяти: {limit} фактов. Удалите лишнее, и тогда я сохраню новое."
                )
            body: dict[str, Any] = {"ok": True, "status": result.status.value}
            if result.memory is not None:
                body["item"] = _memory_item(result.memory)
        return JSONResponse(content=body, headers={"Cache-Control": "no-store"})

    @router.delete("/memory/{memory_id}")
    @_json_errors
    async def delete_memory(memory_id: int, request: Request):
        async with scope(request) as (session, user):
            done = await PersonalAiRepository(session).delete_memory(user_id=user.telegram_user_id, memory_id=memory_id)
            if not done:
                raise _ApiError(404, "Этого факта уже нет.")
        return JSONResponse(content={"ok": True}, headers={"Cache-Control": "no-store"})

    @router.post("/memory/{memory_id}/pin")
    @_json_errors
    async def pin_memory(memory_id: int, request: Request):
        async with scope(request) as (session, user):
            pinned = (await _read_json_object(request)).get("pinned")
            if not isinstance(pinned, bool):
                raise _ApiError(422, "Поле pinned должно быть true или false.")
            done = await PersonalAiRepository(session).set_memory_pinned(
                user_id=user.telegram_user_id, memory_id=memory_id, pinned=pinned
            )
            if not done:
                raise _ApiError(404, "Этого факта уже нет.")
        return JSONResponse(content={"ok": True, "pinned": pinned}, headers={"Cache-Control": "no-store"})

    @router.put("/settings")
    @_json_errors
    async def update_settings(request: Request):
        async with scope(request) as (session, user):
            payload = await _read_json_object(request)
            if not payload or set(payload) - _SETTINGS_FIELDS:
                raise _ApiError(422, "Можно менять только memory_enabled и auto_memory_enabled.")
            if any(not isinstance(value, bool) for value in payload.values()):
                raise _ApiError(422, "Значения должны быть true или false.")
            user_id = user.telegram_user_id
            repo = PersonalAiRepository(session)
            current = await repo.get_or_create_profile(user_id)
            updated = await repo.update_profile(user_id, expected_revision=current.revision, **payload)
            if updated is None:
                raise _ApiError(409, "Настройки уже изменились. Обновите страницу.")
            config = await config_provider.get()
            decision = await _tier(user)
            profile = _profile_payload(updated, config, decision.access_tier if decision else None)
        return JSONResponse(content={"ok": True, "profile": profile}, headers={"Cache-Control": "no-store"})

    @router.post("/forget-all")
    @_json_errors
    async def forget_all(request: Request):
        # The bot's own state (a reply in flight, half-entered inputs and proposals) lives in this process.
        from selara.presentation.handlers import personal_ai, personal_memory  # noqa: PLC0415

        async with scope(request) as (session, user):
            payload = await _read_json_object(request)
            if payload.get("confirm") is not True:
                raise _ApiError(422, "Подтвердите удаление: confirm должен быть true.")
            user_id = user.telegram_user_id
            if user_id in personal_ai._inflight_users:
                raise _ApiError(409, "Selara ещё отвечает на ваше сообщение. Повторите через пару секунд.")
            # Hold the lock a reply holds, so no turn starts from the old data in the middle of the deletion.
            personal_ai._inflight_users.add(user_id)
            try:
                removed = await PersonalAiRepository(session).delete_all_user_data(user_id=user_id)
                personal_ai._pending_inputs.pop(user_id, None)
                personal_memory._pending_memories.pop(user_id, None)
                await session.commit()
            finally:
                personal_ai._inflight_users.discard(user_id)
        return JSONResponse(
            content={
                "ok": True,
                "removed": {
                    "memories": removed.memories,
                    "messages": removed.messages,
                    "summaries": removed.summaries,
                    "profile": removed.profile,
                },
            },
            headers={"Cache-Control": "no-store"},
        )

    return router
