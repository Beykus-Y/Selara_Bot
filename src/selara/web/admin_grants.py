"""Owner API for granting and revoking subscriptions (Selara Personal and group Selara AI)."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy.exc import SQLAlchemyError

from selara.application.entitlement_grants import GrantError
from selara.infrastructure.db.entitlement_grants import EntitlementGrantService, GrantOutcome, NoticeSender

logger = logging.getLogger(__name__)

_CONFLICT_CODES = {"idempotency_conflict"}


class GrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str
    target_id: StrictInt
    days: StrictInt
    reason: str
    idempotency_key: str
    notify: StrictBool = True


class RevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str
    target_id: StrictInt
    mode: str
    days: Annotated[StrictInt, Field(ge=1)] | None = None
    reason: str
    idempotency_key: str
    notify: StrictBool = False


def outcome_json(outcome: GrantOutcome, *, notified: bool | None) -> dict:
    return {
        "grant_id": outcome.grant_id,
        "action": outcome.action,
        "scope": outcome.scope,
        "target_id": outcome.target_id,
        "status": outcome.status,
        "valid_until": outcome.valid_until.isoformat() if outcome.valid_until else None,
        "delta_days": round(outcome.delta_seconds / 86_400, 2),
        "duplicate": outcome.duplicate,
        "paid_recently": outcome.paid_recently,
        "notified": notified,
    }


def grant_error_http(exc: GrantError) -> HTTPException:
    return HTTPException(409 if exc.code in _CONFLICT_CODES else 422, {"code": exc.code, "message": exc.message})


def build_admin_grants_router(
    *, settings, session_factory, require_admin, send_notice: NoticeSender | None = None
) -> APIRouter:
    router = APIRouter(prefix="/monetization", dependencies=[Depends(require_admin)])
    service = EntitlementGrantService(session_factory, admin_user_id=settings.admin_user_id)
    actor = int(settings.admin_user_id or 0)

    async def guarded(operation):
        try:
            return await operation()
        except GrantError as exc:
            raise grant_error_http(exc) from None
        except SQLAlchemyError:
            logger.exception("Subscription grant storage failed")
            raise HTTPException(503, "Не удалось выполнить операцию. Повторите позже.") from None

    @router.get("/lookup")
    async def lookup(q: str = Query(default="", max_length=120)):
        return {"ok": True, **await service.lookup(q)}

    @router.get("/target")
    async def target(scope: str = Query(...), target_id: int = Query(...)):
        state = await guarded(lambda: service.target_state(scope=scope, target_id=target_id))
        return {
            "ok": True,
            "scope": state.scope,
            "target_id": state.target_id,
            "title": state.title,
            "status": state.status,
            "active": state.active,
            "valid_until": state.valid_until.isoformat() if state.valid_until else None,
            "paid_recently": state.paid_recently,
            "granted_by_admin": state.granted,
        }

    @router.get("/grants")
    async def grants(limit: int = Query(default=20, ge=1, le=50)):
        return {"ok": True, "items": await service.recent(limit=limit)}

    @router.get("/personal-entitlements")
    async def personal_entitlements():
        return {"ok": True, "items": await service.active_personal()}

    @router.post("/grants")
    async def create_grant(payload: GrantRequest):
        outcome, notified = await guarded(
            lambda: service.grant_and_notify(
                notify=payload.notify,
                send_notice=send_notice,
                timezone_name=settings.bot_timezone,
                scope=payload.scope,
                target_id=payload.target_id,
                days=payload.days,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                actor_user_id=actor,
                source="miniapp",
            )
        )
        return {"ok": True, **outcome_json(outcome, notified=notified)}

    @router.post("/grants/revoke")
    async def revoke_grant(payload: RevokeRequest):
        outcome, notified = await guarded(
            lambda: service.revoke_and_notify(
                notify=payload.notify,
                send_notice=send_notice,
                timezone_name=settings.bot_timezone,
                scope=payload.scope,
                target_id=payload.target_id,
                mode=payload.mode,
                days=payload.days,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                actor_user_id=actor,
                source="miniapp",
            )
        )
        return {"ok": True, **outcome_json(outcome, notified=notified)}

    return router
