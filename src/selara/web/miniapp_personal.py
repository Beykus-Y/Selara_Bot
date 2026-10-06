"""Mini App «Моя Selara»: a user's own Personal AI profile, memory and subscription status.

Every route works on the session user only (``/api/miniapp/personal``): the user id always comes from the
signed Mini App session and is never read from the request, so one user cannot see or change another one's
data. Texts go through the same validators as the bot dialogue (``/ai``, ``/memory``), so a value that the bot
refuses is refused here too.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.ai_character import CHARACTER_PRESETS
from selara.application.ai_character.presets import CUSTOM_PRESET_KEY, preset_title
from selara.application.ai_character.profile import (
    ADDRESS_FORMS,
    MAX_ADDRESS_LENGTH,
    MAX_CUSTOM_CHARACTER_LENGTH,
    MAX_DISPLAY_NAME_LENGTH,
    MODES,
    REPLY_LENGTHS,
    ProfileValidationError,
    validate_address,
    validate_custom_character,
    validate_display_name,
)
from selara.application.feature_access import AccessReason, AccessTier, FeatureAccessService, QuotaScope
from selara.application.personal_config import PersonalConfig, PersonalConfigProvider
from selara.application.personal_memory import MAX_MEMORY_LENGTH, MemoryValidationError, normalize_memory_text
from selara.core.config import Settings
from selara.domain.entities import UserSnapshot
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.db.personal_ai_repository import AddMemoryStatus, PersonalAiRepository, StoredProfile
from selara.infrastructure.db.telegram_stars import SqlAlchemyUserEntitlementResolver
from selara.infrastructure.llm.features import AiFeature
from selara.infrastructure.llm.runtime import llm_runtime_config
from selara.presentation.auth import resolve_owner_private_exemption
from selara.presentation.handlers.premium import personal_offer_available

log = logging.getLogger(__name__)

UserLoader = Callable[[AsyncSession, Request], Awaitable[UserSnapshot | None]]

_NO_STORE = {"Cache-Control": "no-store"}
_ACCESS_ERROR_TEXT = "Не удалось определить тариф. Попробуйте позже."
_PROFILE_BOOLEANS = ("emoji_enabled", "memory_enabled", "auto_memory_enabled")
_PROFILE_CHOICES = {"formality": ADDRESS_FORMS, "reply_length": REPLY_LENGTHS, "mode": MODES}
_PROFILE_FIELDS = frozenset(
    {"display_name", "character_preset", "character_custom", "address_form", *_PROFILE_BOOLEANS, *_PROFILE_CHOICES}
)


@dataclass(frozen=True, slots=True)
class PersonalAccess:
    """What the Mini App shows about the user's Selara Personal subscription."""

    # "free" | "paid" | "owner"; only meaningful when ``available``.
    tier: str = "free"
    available: bool = True
    valid_until: datetime | None = None
    quota_limit: int | None = None
    quota_used: int | None = None
    quota_remaining: int | None = None
    quota_reset_at: datetime | None = None
    unlimited: bool = False

    @property
    def paid(self) -> bool:
        return self.available and self.tier in ("paid", "owner")


AccessResolver = Callable[[int], Awaitable[PersonalAccess]]


def build_access_resolver(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    personal_config: PersonalConfigProvider,
    service: FeatureAccessService | None = None,
) -> AccessResolver:
    """Resolve tier, subscription end and today's quota without reserving or consuming anything."""

    service = service or FeatureAccessService(
        SqlAlchemyFeatureQuotaRepository(session_factory),
        user_entitlement_resolver=SqlAlchemyUserEntitlementResolver(session_factory, personal_config),
        personal_config=personal_config,
    )

    async def resolve(user_id: int) -> PersonalAccess:
        owner = resolve_owner_private_exemption(user_id=user_id, admin_user_id=settings.admin_user_id)
        scope = QuotaScope.user(user_id)
        try:
            decision = await service.resolve_feature_access(
                feature=AiFeature.PERSONAL_CHAT,
                chat_id=user_id,
                trigger="telegram_message",
                scope=scope,
                owner_exempt=owner,
            )
            if decision.reason == AccessReason.ACCESS_UNAVAILABLE:
                return PersonalAccess(available=False)
            summary = await service.get_usage_summary(
                feature=AiFeature.PERSONAL_CHAT,
                chat_id=user_id,
                trigger="telegram_message",
                timezone_name=settings.bot_timezone,
                scope=scope,
                owner_exempt=owner,
            )
        except Exception:
            log.exception("miniapp personal: access resolution failed user_id=%s", user_id)
            return PersonalAccess(available=False)
        if decision.access_tier == AccessTier.OWNER_INTERNAL:
            tier = "owner"
        elif decision.access_tier == AccessTier.PAID:
            tier = "paid"
        else:
            tier = "free"
        return PersonalAccess(
            tier=tier,
            valid_until=decision.entitlement_valid_until if tier == "paid" else None,
            quota_limit=summary.quota_limit,
            quota_used=summary.quota_used,
            quota_remaining=summary.quota_remaining,
            quota_reset_at=summary.reset_at,
            unlimited=summary.unlimited,
        )

    return resolve


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _profile_json(stored: StoredProfile) -> dict[str, Any]:
    profile = stored.profile
    return {
        "display_name": profile.display_name,
        "character_preset": profile.character_preset,
        "character_title": preset_title(profile.character_preset),
        "character_custom": profile.character_custom,
        "address_form": profile.address_form,
        "formality": profile.formality,
        "reply_length": profile.reply_length,
        "emoji_enabled": profile.emoji_enabled,
        "mode": profile.mode,
        "memory_enabled": stored.memory_enabled,
        "auto_memory_enabled": stored.auto_memory_enabled,
        "revision": stored.revision,
    }


def _memory_json(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "content": row.content,
        "source": row.source,
        "pinned": bool(row.pinned),
        "created_at": _iso(row.created_at),
        "last_used_at": _iso(row.last_used_at),
    }


def _memory_limit_for(config: PersonalConfig, access: PersonalAccess) -> int | None:
    """Fact limit of the user's tier; ``None`` (unresolved tier) blocks adding, it never means "unlimited"."""
    if not access.available:
        return None
    return config.memory_paid_limit if access.paid else config.memory_free_limit


def _fail(status_code: int, message: str, *, code: str | None = None, **extra: Any) -> JSONResponse:
    content: dict[str, Any] = {"ok": False, "status_code": status_code, "message": message}
    if code is not None:
        content["code"] = code
    content.update(extra)
    return JSONResponse(status_code=status_code, content=content, headers=_NO_STORE)


def build_miniapp_personal_router(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    load_user: UserLoader,
    personal_config: PersonalConfigProvider,
    resolve_access: AccessResolver,
    bot_url: str,
) -> APIRouter:
    router = APIRouter(prefix="/api/miniapp/personal", tags=["miniapp-personal"])

    async def current_user(request: Request):
        """Yields ``(session, user_id)``; the id comes from the verified Mini App session only."""
        async with session_factory() as session:
            user = await load_user(session, request)
            if user is None:
                raise HTTPException(status_code=401, detail="Mini App сессия истекла.")
            try:
                yield session, user.telegram_user_id
            except BaseException:
                await session.rollback()
                raise
            await session.commit()

    Viewer = Depends(current_user)

    async def _json_body(request: Request) -> dict[str, Any]:
        # A JSON content type cannot be sent cross-site without a CORS preflight, which this API never grants.
        if not request.headers.get("content-type", "").lower().startswith("application/json"):
            raise HTTPException(status_code=415, detail="Ожидался JSON.")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="Ожидался JSON.") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="Некорректные данные.")
        return payload

    async def _overview(repo: PersonalAiRepository, user_id: int) -> dict[str, Any]:
        stored = await repo.get_or_create_profile(user_id)
        count = await repo.count_memories(user_id=user_id)
        config = await personal_config.get()
        access = await resolve_access(user_id)
        limit = _memory_limit_for(config, access)
        ai_available = llm_runtime_config(settings) is not None
        return {
            "ok": True,
            "profile": _profile_json(stored),
            "options": {
                "presets": [{"key": key, "title": title} for key, (title, _desc) in CHARACTER_PRESETS.items()]
                + [{"key": CUSTOM_PRESET_KEY, "title": preset_title(CUSTOM_PRESET_KEY)}],
                "formalities": list(ADDRESS_FORMS),
                "reply_lengths": list(REPLY_LENGTHS),
                "modes": list(MODES),
                "max_display_name": MAX_DISPLAY_NAME_LENGTH,
                "max_custom_character": MAX_CUSTOM_CHARACTER_LENGTH,
                "max_address": MAX_ADDRESS_LENGTH,
            },
            "subscription": {
                "available": access.available,
                "tier": access.tier if access.available else None,
                "valid_until": _iso(access.valid_until),
                "quota": {
                    "limit": access.quota_limit,
                    "used": access.quota_used,
                    "remaining": access.quota_remaining,
                    "reset_at": _iso(access.quota_reset_at),
                    "unlimited": access.unlimited,
                },
                "price_stars": config.price_stars,
                "duration_days": config.duration_days,
                "offer_available": personal_offer_available(settings, config),
                "ai_available": ai_available,
                "purchase_command": "/premium",
                "bot_url": bot_url,
            },
            "memory": {
                "count": count,
                "limit": limit,
                "max_length": MAX_MEMORY_LENGTH,
                # Facts found by the model are a paid, opt-in extra; the switch is shown only when it can work.
                "auto_extract_enabled_by_admin": config.memory_auto_extract,
                "auto_extract_available": bool(config.memory_auto_extract and access.paid),
            },
        }

    @router.get("")
    async def personal_overview(viewer=Viewer):
        session, user_id = viewer
        payload = await _overview(PersonalAiRepository(session), user_id)
        await session.commit()
        return JSONResponse(content=payload, headers=_NO_STORE)

    @router.patch("/profile")
    async def personal_profile_update(request: Request, viewer=Viewer):
        session, user_id = viewer
        payload = await _json_body(request)
        revision = payload.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool):
            return _fail(422, "Нужен номер ревизии настроек (revision).", code="revision_required")
        unknown = sorted(set(payload) - _PROFILE_FIELDS - {"revision"})
        if unknown:
            return _fail(422, "Неизвестные поля: " + ", ".join(unknown), code="unknown_field")

        changes: dict[str, Any] = {}
        try:
            if "display_name" in payload:
                changes["display_name"] = validate_display_name(_as_text(payload["display_name"]))
            if "address_form" in payload:
                raw = payload["address_form"]
                # Empty or null puts the default address back.
                changes["address_form"] = None if raw in (None, "") else validate_address(_as_text(raw))
            if "character_custom" in payload:
                raw = payload["character_custom"]
                changes["character_custom"] = None if raw in (None, "") else validate_custom_character(_as_text(raw))
            if "character_preset" in payload:
                preset = payload["character_preset"]
                if not isinstance(preset, str) or (preset != CUSTOM_PRESET_KEY and preset not in CHARACTER_PRESETS):
                    return _fail(422, "Такого характера нет.", code="invalid_preset")
                changes["character_preset"] = preset
        except ProfileValidationError as exc:
            return _fail(422, str(exc), code="invalid_value")

        for field, allowed in _PROFILE_CHOICES.items():
            if field in payload:
                if payload[field] not in allowed:
                    return _fail(422, "Недопустимое значение поля.", code="invalid_value", field=field)
                changes[field] = payload[field]
        for field in _PROFILE_BOOLEANS:
            if field in payload:
                if not isinstance(payload[field], bool):
                    return _fail(422, "Ожидалось true или false.", code="invalid_value", field=field)
                changes[field] = payload[field]

        repo = PersonalAiRepository(session)
        stored = await repo.get_or_create_profile(user_id)
        # A custom character without text would silently fall back to the default one.
        effective_preset = changes.get("character_preset", stored.profile.character_preset)
        effective_custom = changes["character_custom"] if "character_custom" in changes else stored.profile.character_custom
        if effective_preset == CUSTOM_PRESET_KEY and not effective_custom:
            return _fail(422, "Для своего характера нужно описание.", code="custom_required")
        if effective_preset != CUSTOM_PRESET_KEY and "character_custom" in changes and changes["character_custom"]:
            # Text of a custom character is only used with the "custom" preset; keep both in step.
            changes["character_preset"] = CUSTOM_PRESET_KEY

        if changes.get("auto_memory_enabled") is True:
            config = await personal_config.get()
            access = await resolve_access(user_id)
            if not (config.memory_auto_extract and access.paid):
                return _fail(
                    409,
                    "Авто-запоминание доступно только с Selara Personal и когда оно включено администратором.",
                    code="auto_memory_unavailable",
                )

        if not changes:
            await session.commit()
            return JSONResponse(
                content={"ok": True, "profile": _profile_json(stored)}, headers=_NO_STORE
            )
        updated = await repo.update_profile(user_id, expected_revision=revision, **changes)
        await session.commit()
        if updated is None:
            current = await repo.get_or_create_profile(user_id)
            return _fail(
                409,
                "Настройки уже изменились, показываю актуальные.",
                code="revision_conflict",
                profile=_profile_json(current),
            )
        return JSONResponse(content={"ok": True, "profile": _profile_json(updated)}, headers=_NO_STORE)

    @router.get("/memories")
    async def personal_memories(viewer=Viewer):
        session, user_id = viewer
        repo = PersonalAiRepository(session)
        rows = await repo.list_memories(user_id=user_id)
        config = await personal_config.get()
        limit = _memory_limit_for(config, await resolve_access(user_id))
        stored = await repo.get_or_create_profile(user_id)
        await session.commit()
        return JSONResponse(
            content={
                "ok": True,
                "items": [_memory_json(row) for row in rows],
                "count": len(rows),
                "limit": limit,
                "max_length": MAX_MEMORY_LENGTH,
                "memory_enabled": stored.memory_enabled,
            },
            headers=_NO_STORE,
        )

    @router.post("/memories")
    async def personal_memory_add(request: Request, viewer=Viewer):
        session, user_id = viewer
        payload = await _json_body(request)
        content = payload.get("content")
        if not isinstance(content, str):
            return _fail(422, "Нужен текст факта.", code="invalid_value")
        try:
            fact = normalize_memory_text(content)
        except MemoryValidationError as exc:
            return _fail(422, str(exc), code="invalid_value")
        repo = PersonalAiRepository(session)
        stored = await repo.get_or_create_profile(user_id)
        if not stored.memory_enabled:
            return _fail(409, "Память выключена, поэтому факты не сохраняются. Включите её в настройках.", code="memory_disabled")
        config = await personal_config.get()
        limit = _memory_limit_for(config, await resolve_access(user_id))
        if limit is None:
            return _fail(503, _ACCESS_ERROR_TEXT, code="access_unavailable")
        result = await repo.add_memory(user_id=user_id, content=fact, source="explicit", limit=limit)
        await session.commit()
        if result.status == AddMemoryStatus.DUPLICATE:
            return _fail(409, "Это я уже помню.", code="duplicate")
        if result.status == AddMemoryStatus.LIMIT_REACHED:
            return _fail(
                409,
                f"Достигнут лимит памяти: {limit} фактов. Я ничего не стираю сама: удалите лишнее, и тогда сохраню новое.",
                code="limit_reached",
                limit=limit,
            )
        return JSONResponse(
            status_code=201,
            content={"ok": True, "item": _memory_json(result.memory), "count": await repo.count_memories(user_id=user_id)},
            headers=_NO_STORE,
        )

    @router.patch("/memories/{memory_id}")
    async def personal_memory_pin(memory_id: int, request: Request, viewer=Viewer):
        session, user_id = viewer
        payload = await _json_body(request)
        pinned = payload.get("pinned")
        if not isinstance(pinned, bool):
            return _fail(422, "Ожидалось pinned: true или false.", code="invalid_value")
        repo = PersonalAiRepository(session)
        done = await repo.set_memory_pinned(user_id=user_id, memory_id=memory_id, pinned=pinned)
        await session.commit()
        if not done:
            return _fail(404, "Этого факта уже нет.", code="not_found")
        return JSONResponse(content={"ok": True, "id": memory_id, "pinned": pinned}, headers=_NO_STORE)

    @router.delete("/memories/{memory_id}")
    async def personal_memory_delete(memory_id: int, viewer=Viewer):
        session, user_id = viewer
        repo = PersonalAiRepository(session)
        done = await repo.delete_memory(user_id=user_id, memory_id=memory_id)
        await session.commit()
        if not done:
            return _fail(404, "Этого факта уже нет.", code="not_found")
        return JSONResponse(
            content={"ok": True, "id": memory_id, "count": await repo.count_memories(user_id=user_id)},
            headers=_NO_STORE,
        )

    return router


def _as_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ProfileValidationError("Ожидался текст.")
    return value
