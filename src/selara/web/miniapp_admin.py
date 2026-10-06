from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.datastructures import UploadFile
from redis.asyncio import Redis
from sqlalchemy import case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.personal_config import PersonalConfig, PersonalConfigOverride, config_from_settings
from selara.application.selara_ai_product import SELARA_AI_PRODUCT_KEY
from selara.application.selara_ai_status import checkout_ready
from selara.core.config import Settings
from selara.core.logging import get_admin_log_buffer
from selara.domain.entities import UserSnapshot
from selara.infrastructure.db.ai_accounting import AiAccountingService
from selara.infrastructure.db.ai_analytics import (
    AdminAiAnalyticsRepository,
    PaymentFilters,
    as_utc,
)
from selara.infrastructure.db.models import (
    AdminBroadcastDeliveryModel,
    AdminBroadcastModel,
    ChatMemberCountSnapshotModel,
    ChatMetricsModel,
    ChatModel,
    OperationalAlertModel,
    UserChatMessageEventModel,
    SelaraAiPaymentModel,
    UserFeatureRequestModel,
    UserModel,
)
from selara.infrastructure.db.personal_config import build_personal_config
from selara.infrastructure.db.telegram_stars import SqlAlchemyChatEntitlementResolver
from selara.infrastructure.llm.features import AiFeature
from selara.infrastructure.llm.runtime import llm_runtime_problem
from selara.infrastructure.security.redaction import redact_sensitive_text

UserLoader = Callable[[AsyncSession, Request], Awaitable[UserSnapshot | None]]
BroadcastPreview = Callable[[AsyncSession, dict[str, Any]], Awaitable[dict[str, Any]]]
BroadcastStart = Callable[[int, dict[str, Any]], Awaitable[dict[str, Any]]]
BroadcastStatus = Callable[[AsyncSession, int], Awaitable[dict[str, Any]]]
TelegramBotProbe = Callable[[], Awaitable[dict[str, Any]]]
_PERIODS = {1, 7, 30, 90}
_GROUP_TYPES = ("group", "supergroup")
_PAYMENT_STATES = {"all", "applied", "rejected"}
_PAYMENT_SCOPES = {"all", "chat", "user"}
_REFUND_FILTERS = {"all", "none", "pending", "refunded", "failed"}
_health_last_success: dict[str, str] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_PROCESS_STARTED_AT = _utc_now()


def _decimal_str(value: Decimal) -> str:
    """Serialize money without binary-float rounding or exponent notation."""
    return format(value, "f")


def _iso(value: datetime | None) -> str | None:
    return as_utc(value).isoformat() if value is not None else None


def _validate_period(period_days: int) -> None:
    if period_days not in _PERIODS:
        raise HTTPException(status_code=422, detail="Допустимые периоды: 1, 7, 30 или 90 дней.")


def _percent_change(current: int, previous: int) -> float | None:
    if previous <= 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


def build_miniapp_admin_router(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    load_user: UserLoader,
    broadcast_preview_handler: BroadcastPreview,
    broadcast_start_handler: BroadcastStart,
    broadcast_status_handler: BroadcastStatus,
    telegram_bot_probe: TelegramBotProbe,
) -> APIRouter:
    router = APIRouter(prefix="/api/miniapp/admin", tags=["miniapp-admin"])

    async def require_admin(request: Request):
        async with session_factory() as session:
            user = await load_user(session, request)
            if user is None:
                raise HTTPException(status_code=401, detail="Mini App сессия истекла.")
            if settings.admin_user_id is None or user.telegram_user_id != settings.admin_user_id:
                raise HTTPException(status_code=403, detail="Недостаточно прав.")
            yield session
            await session.commit()

    AdminSession = Depends(require_admin)
    _personal_provider, personal_config_store = build_personal_config(session_factory, settings)

    async def _read_broadcast_payload(request: Request) -> dict[str, Any]:
        if request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            form = await request.form(max_files=1, max_fields=12, max_part_size=10 * 1024 * 1024)
            payload: dict[str, Any] = {}
            for key, value in form.multi_items():
                if isinstance(value, UploadFile):
                    if key == "photo" and value.filename:
                        payload["photo_content"] = await value.read()
                        payload["photo_filename"] = value.filename
                        payload["photo_content_type"] = value.content_type or ""
                else:
                    payload[str(key)] = str(value)
            raw_ids = payload.get("chat_ids")
            if isinstance(raw_ids, str):
                payload["chat_ids"] = [item.strip() for item in raw_ids.split(",") if item.strip()]
            if isinstance(payload.get("confirm"), str):
                payload["confirm"] = payload["confirm"].strip().lower() == "true"
            return payload
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="Ожидался JSON или multipart запрос.") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="Некорректные данные рассылки.")
        return payload

    def _page_limit(limit: int) -> int:
        return max(1, min(limit, 50))

    @router.get("/feedback")
    async def feedback(
        status: str = Query(default="open"),
        search: str = Query(default="", max_length=120),
        start_at: datetime | None = Query(default=None),
        end_at: datetime | None = Query(default=None),
        before_id: int | None = Query(default=None, ge=1),
        limit: int = Query(default=20, ge=1, le=50),
        session: AsyncSession = AdminSession,
    ):
        if status not in {"all", "open", "resolved"}:
            raise HTTPException(status_code=422, detail="Допустимые статусы: all, open, resolved.")
        stmt = (
            select(UserFeatureRequestModel, UserModel)
            .join(UserModel, UserModel.telegram_user_id == UserFeatureRequestModel.user_id)
            .order_by(UserFeatureRequestModel.id.desc())
            .limit(_page_limit(limit) + 1)
        )
        if status != "all":
            stmt = stmt.where(UserFeatureRequestModel.status == ("done" if status == "resolved" else "open"))
        if before_id is not None:
            stmt = stmt.where(UserFeatureRequestModel.id < before_id)
        query = search.strip()
        if query:
            if query.isdigit():
                stmt = stmt.where(
                    (UserFeatureRequestModel.title.ilike(f"%{query}%"))
                    | (UserFeatureRequestModel.details.ilike(f"%{query}%"))
                    | (UserModel.username.ilike(f"%{query}%"))
                    | (UserFeatureRequestModel.user_id == int(query))
                )
            else:
                stmt = stmt.where(
                    (UserFeatureRequestModel.title.ilike(f"%{query}%"))
                    | (UserFeatureRequestModel.details.ilike(f"%{query}%"))
                    | (UserModel.username.ilike(f"%{query}%"))
                )
        if start_at is not None:
            stmt = stmt.where(UserFeatureRequestModel.created_at >= (start_at if start_at.tzinfo else start_at.replace(tzinfo=timezone.utc)))
        if end_at is not None:
            stmt = stmt.where(UserFeatureRequestModel.created_at <= (end_at if end_at.tzinfo else end_at.replace(tzinfo=timezone.utc)))
        rows = (await session.execute(stmt)).all()
        has_more = len(rows) > _page_limit(limit)
        rows = rows[:_page_limit(limit)]
        return {
            "ok": True,
            "items": [
                {
                    "id": row.id,
                    "title": row.title,
                    "preview": row.details[:240],
                    "status": "resolved" if row.status == "done" else "open",
                    "category": "suggestion",
                    "user": {
                        "id": row.user_id,
                        "username": user.username,
                        "first_name": user.first_name,
                    },
                    "created_at": row.created_at.isoformat(),
                    "updated_at": row.updated_at.isoformat(),
                }
                for row, user in rows
            ],
            "next_cursor": rows[-1][0].id if has_more and rows else None,
        }

    @router.get("/feedback/{request_id}")
    async def feedback_detail(request_id: int, session: AsyncSession = AdminSession):
        row = await session.get(UserFeatureRequestModel, request_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Обращение не найдено.")
        user = await session.get(UserModel, row.user_id)
        return {
            "ok": True,
            "id": row.id,
            "title": row.title,
            "details": row.details,
            "status": "resolved" if row.status == "done" else "open",
            "user": {
                "id": row.user_id,
                "username": user.username if user else None,
                "first_name": user.first_name if user else None,
            },
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    async def _set_feedback_status(request_id: int, status: str, session: AsyncSession):
        row = await session.get(UserFeatureRequestModel, request_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Обращение не найдено.")
        row.status = "done" if status == "resolved" else "open"
        row.done_at = _utc_now() if status == "resolved" else None
        row.updated_at = _utc_now()
        await session.commit()
        return {"ok": True, "id": row.id, "status": status}

    @router.post("/feedback/{request_id}/resolve")
    async def resolve_feedback(request_id: int, session: AsyncSession = AdminSession):
        return await _set_feedback_status(request_id, "resolved", session)

    @router.post("/feedback/{request_id}/reopen")
    async def reopen_feedback(request_id: int, session: AsyncSession = AdminSession):
        return await _set_feedback_status(request_id, "open", session)

    @router.get("/alerts")
    async def alerts(
        severity: str = Query(default="all"),
        source: str = Query(default="", max_length=160),
        search: str = Query(default="", max_length=120),
        start_at: datetime | None = Query(default=None),
        end_at: datetime | None = Query(default=None),
        before_id: int | None = Query(default=None, ge=1),
        limit: int = Query(default=20, ge=1, le=50),
        session: AsyncSession = AdminSession,
    ):
        stmt = select(OperationalAlertModel).order_by(OperationalAlertModel.id.desc()).limit(_page_limit(limit) + 1)
        if severity != "all":
            if severity not in {"error", "warning", "info"}:
                raise HTTPException(status_code=422, detail="Неизвестный severity.")
            stmt = stmt.where(OperationalAlertModel.severity == severity)
        if source.strip():
            stmt = stmt.where(OperationalAlertModel.source.ilike(f"%{source.strip()}%"))
        if search.strip():
            needle = f"%{search.strip()}%"
            stmt = stmt.where(
                (OperationalAlertModel.message.ilike(needle))
                | (OperationalAlertModel.fingerprint.ilike(needle))
                | (OperationalAlertModel.source.ilike(needle))
            )
        if before_id is not None:
            stmt = stmt.where(OperationalAlertModel.id < before_id)
        if start_at is not None:
            stmt = stmt.where(OperationalAlertModel.created_at >= (start_at if start_at.tzinfo else start_at.replace(tzinfo=timezone.utc)))
        if end_at is not None:
            stmt = stmt.where(OperationalAlertModel.created_at <= (end_at if end_at.tzinfo else end_at.replace(tzinfo=timezone.utc)))
        records = list((await session.execute(stmt)).scalars())
        has_more = len(records) > _page_limit(limit)
        records = records[:_page_limit(limit)]
        return {
            "ok": True,
            "items": [
                {
                    "id": row.id,
                    "severity": row.severity,
                    "source": row.source,
                    "fingerprint": row.fingerprint,
                    "message": redact_sensitive_text(row.message)[:500],
                    "context": row.context_json,
                    "created_at": row.created_at.isoformat(),
                }
                for row in records
            ],
            "next_cursor": records[-1].id if has_more and records else None,
        }

    @router.get("/alerts/{alert_id}")
    async def alert_detail(alert_id: int, session: AsyncSession = AdminSession):
        row = await session.get(OperationalAlertModel, alert_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Событие не найдено.")
        return {
            "ok": True,
            "id": row.id,
            "severity": row.severity,
            "source": row.source,
            "fingerprint": row.fingerprint,
            "message": redact_sensitive_text(row.message)[:1000],
            "context": row.context_json,
            "traceback": redact_sensitive_text(row.sanitized_traceback or ""),
            "created_at": row.created_at.isoformat(),
        }

    @router.get("/logs")
    async def logs(
        level: str = Query(default="all"),
        source: str = Query(default="", max_length=160),
        search: str = Query(default="", max_length=120),
        start_at: datetime | None = Query(default=None),
        end_at: datetime | None = Query(default=None),
        before_id: int | None = Query(default=None, ge=1),
        limit: int = Query(default=30, ge=1, le=50),
        session: AsyncSession = AdminSession,
    ):
        # Read only a small in-process ring buffer, never the entire service stdout
        # or journald on demand. IDs are monotonic within this process and provide
        # a stable cursor for the bounded retained window.
        if level not in {"all", "debug", "info", "warning", "error", "critical"}:
            raise HTTPException(status_code=422, detail="Неизвестный уровень журнала.")
        records = get_admin_log_buffer().list_records()
        if level != "all":
            records = [row for row in records if row["level"] == level]
        if source.strip():
            needle_source = source.strip().casefold()
            records = [row for row in records if needle_source in row["source"].casefold()]
        if search.strip():
            needle = search.strip().casefold()
            records = [row for row in records if needle in row["message"].casefold()]
        if before_id is not None:
            records = [row for row in records if row["id"] < before_id]
        if start_at is not None:
            start = start_at if start_at.tzinfo else start_at.replace(tzinfo=timezone.utc)
            records = [row for row in records if datetime.fromisoformat(row["created_at"]) >= start]
        if end_at is not None:
            end = end_at if end_at.tzinfo else end_at.replace(tzinfo=timezone.utc)
            records = [row for row in records if datetime.fromisoformat(row["created_at"]) <= end]
        records.sort(key=lambda row: row["id"], reverse=True)
        records = records[: _page_limit(limit) + 1]
        has_more = len(records) > _page_limit(limit)
        records = records[:_page_limit(limit)]
        return {
            "ok": True,
            "items": records,
            "next_cursor": records[-1]["id"] if has_more and records else None,
        }

    @router.post("/broadcast/preview")
    async def broadcast_preview_route(request: Request, session: AsyncSession = AdminSession):
        payload = await _read_broadcast_payload(request)
        return {"ok": True, **(await broadcast_preview_handler(session, payload))}

    @router.post("/broadcast")
    async def broadcast_start_route(request: Request, session: AsyncSession = AdminSession):
        payload = await _read_broadcast_payload(request)
        if payload.get("confirm") is not True:
            raise HTTPException(status_code=422, detail="Требуется явное подтверждение отправки.")
        user = await load_user(session, request)
        if user is None:
            raise HTTPException(status_code=401, detail="Mini App сессия истекла.")
        return {"ok": True, **(await broadcast_start_handler(user.telegram_user_id, payload))}

    @router.get("/broadcast/{broadcast_id}")
    async def broadcast_status_route(broadcast_id: int, session: AsyncSession = AdminSession):
        return {"ok": True, **(await broadcast_status_handler(session, broadcast_id))}

    @router.get("/broadcasts")
    async def broadcast_history(
        before_id: int | None = Query(default=None, ge=1),
        limit: int = Query(default=20, ge=1, le=50),
        session: AsyncSession = AdminSession,
    ):
        stats = (
            select(
                AdminBroadcastDeliveryModel.broadcast_id.label("broadcast_id"),
                func.count(AdminBroadcastDeliveryModel.id).label("target_count"),
                func.sum(case((AdminBroadcastDeliveryModel.status == "sent", 1), else_=0)).label("sent_count"),
                func.sum(case((AdminBroadcastDeliveryModel.status == "failed", 1), else_=0)).label("failed_count"),
                func.sum(case((AdminBroadcastDeliveryModel.status == "pending", 1), else_=0)).label("pending_count"),
            )
            .group_by(AdminBroadcastDeliveryModel.broadcast_id)
            .subquery()
        )
        stmt = (
            select(AdminBroadcastModel, stats.c.target_count, stats.c.sent_count, stats.c.failed_count, stats.c.pending_count)
            .outerjoin(stats, stats.c.broadcast_id == AdminBroadcastModel.id)
            .order_by(AdminBroadcastModel.id.desc())
            .limit(_page_limit(limit) + 1)
        )
        if before_id is not None:
            stmt = stmt.where(AdminBroadcastModel.id < before_id)
        rows = (await session.execute(stmt)).all()
        has_more = len(rows) > _page_limit(limit)
        rows = rows[:_page_limit(limit)]
        return {
            "ok": True,
            "items": [
                {
                    "id": row.id,
                    "body": row.rendered_body or row.body,
                    "created_at": row.created_at.isoformat(),
                    "target_count": int(target_count or 0),
                    "sent_count": int(sent_count or 0),
                    "failed_count": int(failed_count or 0),
                    "pending_count": int(pending_count or 0),
                    "media_type": row.media_type,
                }
                for row, target_count, sent_count, failed_count, pending_count in rows
            ],
            "next_cursor": rows[-1][0].id if has_more and rows else None,
        }

    @router.get("/audience")
    async def audience(period_days: int = Query(default=30), session: AsyncSession = AdminSession):
        if period_days not in _PERIODS:
            raise HTTPException(status_code=422, detail="Допустимые периоды: 1, 7, 30 или 90 дней.")

        now = _utc_now()
        period_start = now - timedelta(days=period_days)
        previous_start = period_start - timedelta(days=period_days)
        user_id = UserChatMessageEventModel.user_id
        is_group = ChatModel.type.in_(_GROUP_TYPES)
        is_human = UserModel.is_bot.is_(False)
        is_private = ChatModel.type == "private"
        inside_current = UserChatMessageEventModel.sent_at >= period_start
        inside_previous = (UserChatMessageEventModel.sent_at >= previous_start) & (
            UserChatMessageEventModel.sent_at < period_start
        )

        activity_stmt = (
            select(
                func.count(distinct(case((inside_current & is_private, user_id)))).label("bot_current"),
                func.count(distinct(case((inside_previous & is_private, user_id)))).label("bot_previous"),
                func.count(distinct(case((inside_current & is_group, user_id)))).label("group_current"),
                func.count(distinct(case((inside_previous & is_group, user_id)))).label("group_previous"),
            )
            .select_from(UserChatMessageEventModel)
            .join(ChatModel, ChatModel.telegram_chat_id == UserChatMessageEventModel.chat_id)
            .join(UserModel, UserModel.telegram_user_id == user_id)
            .where(UserChatMessageEventModel.sent_at >= previous_start, is_human)
        )
        activity = (await session.execute(activity_stmt)).one()

        known_bot_users_stmt = (
            select(func.count(distinct(UserModel.telegram_user_id)))
            .select_from(UserModel)
            .join(ChatModel, ChatModel.telegram_chat_id == UserModel.telegram_user_id)
            .where(ChatModel.type == "private", is_human)
        )
        known_bot_users = int((await session.execute(known_bot_users_stmt)).scalar_one() or 0)

        known_group_members_stmt = (
            select(func.coalesce(func.sum(ChatMetricsModel.active_members_count), 0))
            .select_from(ChatMetricsModel)
            .join(ChatModel, ChatModel.telegram_chat_id == ChatMetricsModel.chat_id)
            .where(ChatModel.type.in_(_GROUP_TYPES), ChatModel.is_bot_member.is_(True))
        )
        known_group_members = int((await session.execute(known_group_members_stmt)).scalar_one() or 0)

        group_rows_stmt = (
            select(
                ChatMemberCountSnapshotModel.member_count,
                ChatMemberCountSnapshotModel.last_success_at,
            )
            .select_from(ChatModel)
            .outerjoin(
                ChatMemberCountSnapshotModel,
                ChatMemberCountSnapshotModel.chat_id == ChatModel.telegram_chat_id,
            )
            .where(ChatModel.type.in_(_GROUP_TYPES), ChatModel.is_bot_member.is_(True))
        )
        group_rows = (await session.execute(group_rows_stmt)).all()
        snapshot_cutoff = now - timedelta(hours=24)
        fresh_counts = [
            int(row.member_count)
            for row in group_rows
            if row.member_count is not None
            and row.last_success_at is not None
            and row.last_success_at.replace(tzinfo=timezone.utc) >= snapshot_cutoff
        ]
        group_count = len(group_rows)
        inaccessible_groups = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ChatModel)
                    .where(ChatModel.type.in_(_GROUP_TYPES), ChatModel.is_bot_member.is_(False))
                )
            ).scalar_one()
            or 0
        )
        checked_groups = len(fresh_counts)
        if group_count == 0 or checked_groups == group_count:
            member_total_status = "available"
            member_total = sum(fresh_counts)
        elif checked_groups:
            member_total_status = "partial"
            member_total = None
        else:
            member_total_status = "unavailable"
            member_total = None

        bot_current = int(activity.bot_current or 0)
        bot_previous = int(activity.bot_previous or 0)
        group_current = int(activity.group_current or 0)
        group_previous = int(activity.group_previous or 0)
        return {
            "ok": True,
            "period_days": period_days,
            "generated_at": now.isoformat(),
            "metrics": {
                "active_bot_users": {
                    "value": bot_current,
                    "change_percent": _percent_change(bot_current, bot_previous),
                },
                "total_bot_users": {"value": known_bot_users},
                "active_group_users": {
                    "value": group_current,
                    "change_percent": _percent_change(group_current, group_previous),
                },
                "total_group_members": {
                    "value": member_total,
                    "status": member_total_status,
                    "checked_groups": checked_groups,
                    "total_groups": group_count,
                    "inaccessible_groups": inaccessible_groups,
                    "known_active_members": known_group_members,
                    "note": (
                        "Показана сумма последних успешных Telegram member_count."
                        if member_total_status == "available"
                        else "Точный итог недоступен: часть текущих групп ещё не проверена или snapshot устарел. Число известных Selara участников не равно полной аудитории групп."
                    ),
                },
            },
        }

    @router.get("/summary")
    async def operational_summary(session: AsyncSession = AdminSession):
        since = _utc_now() - timedelta(hours=24)
        errors_count = int(
            (await session.execute(
                select(func.count()).select_from(OperationalAlertModel).where(OperationalAlertModel.created_at >= since)
            )).scalar_one()
            or 0
        )
        new_feedback_count = int(
            (await session.execute(
                select(func.count()).select_from(UserFeatureRequestModel).where(UserFeatureRequestModel.created_at >= since)
            )).scalar_one()
            or 0
        )
        open_feedback_count = int(
            (await session.execute(
                select(func.count()).select_from(UserFeatureRequestModel).where(UserFeatureRequestModel.status == "open")
            )).scalar_one()
            or 0
        )
        recent_alerts = list(
            (
                await session.execute(
                    select(OperationalAlertModel)
                    .order_by(OperationalAlertModel.id.desc())
                    .limit(3)
                )
            ).scalars()
        )
        recent_feedback = list(
            (
                await session.execute(
                    select(UserFeatureRequestModel)
                    .order_by(UserFeatureRequestModel.id.desc())
                    .limit(3)
                )
            ).scalars()
        )
        recent_events = [
            {
                "id": row.id,
                "severity": row.severity,
                "source": row.source,
                "message": redact_sensitive_text(row.message)[:180],
                "created_at": row.created_at.isoformat(),
            }
            for row in recent_alerts
        ]
        recent_events.extend(
            {
                "id": row.id,
                "severity": "info",
                "source": "Feedback",
                "message": f"Новое обращение #{row.id}: {row.title[:140]}",
                "created_at": row.created_at.isoformat(),
            }
            for row in recent_feedback
        )
        recent_events.sort(key=lambda item: item["created_at"], reverse=True)
        return {
            "ok": True,
            "errors_24h": errors_count,
            "new_feedback_24h": new_feedback_count,
            "open_feedback": open_feedback_count,
            "recent_events": recent_events[:5],
        }

    async def _probe_database(session: AsyncSession) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            await session.execute(select(1))
            return {"status": "healthy", "latency_ms": round((time.perf_counter() - started) * 1000)}
        except Exception:
            return {"status": "down", "latency_ms": None}

    async def _probe_redis() -> dict[str, Any]:
        started = time.perf_counter()
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
        try:
            await asyncio.wait_for(client.ping(), timeout=1.5)
            return {"status": "healthy", "latency_ms": round((time.perf_counter() - started) * 1000)}
        except Exception:
            return {"status": "down", "latency_ms": None}
        finally:
            await client.aclose()

    async def _probe_gacha(url: str | None) -> dict[str, Any]:
        if not url:
            return {"status": "unknown", "latency_ms": None, "detail": "Не настроен"}
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
                response = await client.get(f"{url.rstrip('/')}/v1/gacha/health")
            status = "healthy" if response.is_success else "down"
            return {"status": status, "latency_ms": round((time.perf_counter() - started) * 1000)}
        except Exception:
            return {"status": "down", "latency_ms": None}

    @router.get("/health")
    async def health(session: AsyncSession = AdminSession):
        now = _utc_now()
        web_started = time.perf_counter()
        checks = await asyncio.gather(
            _probe_database(session),
            _probe_redis(),
            _probe_gacha(settings.resolve_gacha_base_url("genshin")),
            _probe_gacha(settings.resolve_gacha_base_url("hsr")),
            telegram_bot_probe(),
        )
        components = {
            "web": {"status": "healthy", "latency_ms": round((time.perf_counter() - web_started) * 1000), "checked_at": now.isoformat()},
            "telegram_bot": {**checks[4], "checked_at": now.isoformat()},
            "postgresql": {**checks[0], "checked_at": now.isoformat()},
            "redis": {**checks[1], "checked_at": now.isoformat()},
            "gacha_genshin": {**checks[2], "checked_at": now.isoformat()},
            "gacha_hsr": {**checks[3], "checked_at": now.isoformat()},
        }
        statuses = {item["status"] for item in components.values()}
        for name, component in components.items():
            if component["status"] == "healthy":
                _health_last_success[name] = now.isoformat()
            component["last_success_at"] = _health_last_success.get(name)
        overall = "down" if "down" in statuses else "degraded" if statuses.intersection({"unknown", "degraded"}) else "healthy"
        return {
            "ok": True,
            "status": overall,
            "environment": settings.app_env,
            "version": os.getenv("GIT_COMMIT") or os.getenv("SOURCE_VERSION"),
            "checked_at": now.isoformat(),
            "process_started_at": _PROCESS_STARTED_AT.isoformat(),
            "process_uptime_seconds": max(0, int((now - _PROCESS_STARTED_AT).total_seconds())),
            "components": components,
        }

    # ----- Selara AI accounting and Stars monetization (owner only) ------------

    def _payment_json(item: dict[str, Any]) -> dict[str, Any]:
        refund = item.get("refund")
        result = {**item, "payment_at": _iso(item["payment_at"])}
        if refund is not None:
            result["refund"] = {
                **refund,
                "requested_at": _iso(refund["requested_at"]),
                "completed_at": _iso(refund["completed_at"]),
            }
        return result

    @router.get("/ai/summary")
    async def ai_summary(period_days: int = Query(default=30), session: AsyncSession = AdminSession):
        _validate_period(period_days)
        window_to = _utc_now()
        window_from = window_to - timedelta(days=period_days)
        aggregate = await AiAccountingService(session_factory).aggregate_window(
            window_from=window_from, window_to=window_to
        )
        series = await AdminAiAnalyticsRepository(session).daily_ai_series(
            window_from=window_from, window_to=window_to, timezone_name=settings.bot_timezone
        )
        average = aggregate.average_known_cost_component_per_started_invocation_usd
        return {
            "ok": True,
            "period_days": period_days,
            "window_from": window_from.isoformat(),
            "window_to": window_to.isoformat(),
            "invocations": aggregate.invocations,
            "provider_calls": aggregate.provider_calls,
            "unsuccessful_invocations": aggregate.failed_invocations,
            "unknown_cost_calls": aggregate.unknown_cost_calls,
            "known_cost_usd": _decimal_str(aggregate.known_cost_usd),
            "average_known_cost_per_invocation_usd": _decimal_str(average) if average is not None else None,
            "daily": [
                {**row, "known_cost_usd": _decimal_str(row["known_cost_usd"])} for row in series
            ],
        }

    @router.get("/ai/breakdown")
    async def ai_breakdown(period_days: int = Query(default=30), session: AsyncSession = AdminSession):
        _validate_period(period_days)
        window_to = _utc_now()
        window_from = window_to - timedelta(days=period_days)
        repository = AdminAiAnalyticsRepository(session)
        features = await repository.feature_breakdown(window_from=window_from, window_to=window_to)
        models, marker_only_calls = await repository.model_breakdown(window_from=window_from, window_to=window_to)
        stages = await repository.stage_breakdown(window_from=window_from, window_to=window_to)
        return {
            "ok": True,
            "period_days": period_days,
            "features": [{**row, "known_cost_usd": _decimal_str(row["known_cost_usd"])} for row in features],
            "models": [{**row, "known_cost_usd": _decimal_str(row["known_cost_usd"])} for row in models],
            "unattributed_provider_calls": marker_only_calls,
            "stages": [{**row, "known_cost_usd": _decimal_str(row["known_cost_usd"])} for row in stages],
        }

    @router.get("/monetization/summary")
    async def monetization_summary(period_days: int = Query(default=30), session: AsyncSession = AdminSession):
        _validate_period(period_days)
        now = _utc_now()
        window_from = now - timedelta(days=period_days)
        repository = AdminAiAnalyticsRepository(session)
        summary = await repository.payment_summary(window_from=window_from, window_to=now + timedelta(seconds=1))
        counts = {
            **await repository.entitlement_counts(now=now),
            **await repository.personal_entitlement_counts(now=now),
        }
        series = await repository.daily_stars_series(
            window_from=window_from, window_to=now + timedelta(seconds=1), timezone_name=settings.bot_timezone
        )
        return {
            "ok": True,
            "period_days": period_days,
            "currency": "XTR",
            **summary,
            **counts,
            "daily": series,
            "checkout": {
                "configured": checkout_ready(settings),
                "price_stars": settings.selara_ai_price_stars,
            },
        }

    @router.get("/monetization/payments")
    async def monetization_payments(
        state: str = Query(default="all"),
        refund: str = Query(default="all"),
        chat_id: int | None = Query(default=None),
        buyer_id: int | None = Query(default=None),
        scope: str = Query(default="all"),
        period_days: int | None = Query(default=None),
        cursor: str | None = Query(default=None, max_length=80),
        limit: int = Query(default=20, ge=1, le=50),
        session: AsyncSession = AdminSession,
    ):
        if state not in _PAYMENT_STATES:
            raise HTTPException(status_code=422, detail="Допустимые статусы: all, applied, rejected.")
        if refund not in _REFUND_FILTERS:
            raise HTTPException(status_code=422, detail="Допустимые состояния возврата: all, none, pending, refunded, failed.")
        if scope not in _PAYMENT_SCOPES:
            raise HTTPException(status_code=422, detail="Допустимые области: all, chat, user.")
        if period_days is not None:
            _validate_period(period_days)
        parsed_cursor: tuple[datetime, int] | None = None
        if cursor:
            try:
                raw_at, raw_id = cursor.rsplit("|", 1)
                parsed_cursor = (as_utc(datetime.fromisoformat(raw_at)), int(raw_id))
            except (ValueError, TypeError):
                raise HTTPException(status_code=422, detail="Некорректный cursor.") from None
        filters = PaymentFilters(
            state=state,
            refund=refund,
            chat_id=chat_id,
            buyer_user_id=buyer_id,
            since=_utc_now() - timedelta(days=period_days) if period_days else None,
            target_scope=None if scope == "all" else scope,
        )
        items, next_cursor = await AdminAiAnalyticsRepository(session).list_payments(
            filters=filters, cursor=parsed_cursor, limit=limit
        )
        return {
            "ok": True,
            "items": [_payment_json(item) for item in items],
            "next_cursor": f"{next_cursor[0].isoformat()}|{next_cursor[1]}" if next_cursor else None,
        }

    @router.get("/monetization/payments/{payment_id}")
    async def monetization_payment_detail(payment_id: int, session: AsyncSession = AdminSession):
        item = await AdminAiAnalyticsRepository(session).payment_detail(payment_id=payment_id, now=_utc_now())
        if item is None:
            raise HTTPException(status_code=404, detail="Платёж не найден.")
        entitlement = item.get("entitlement")
        if entitlement is not None:
            entitlement = {
                **entitlement,
                "valid_from": _iso(entitlement["valid_from"]),
                "valid_until": _iso(entitlement["valid_until"]),
            }
        intent = item.get("intent")
        if intent is not None:
            intent = {
                **intent,
                "created_at": _iso(intent["created_at"]),
                "expires_at": _iso(intent["expires_at"]),
                "terms_accepted_at": _iso(intent["terms_accepted_at"]),
            }
        refund_hint = (
            f"/stars_refund {item['id']}"
            if item["state"] == "rejected" and item["refund"] is None
            else None
        )
        return {
            "ok": True,
            **_payment_json(item),
            "entitlement": entitlement,
            "intent": intent,
            "refund_command": refund_hint,
        }

    @router.get("/monetization/entitlements")
    async def monetization_entitlements(session: AsyncSession = AdminSession):
        now = _utc_now()
        repository = AdminAiAnalyticsRepository(session)
        counts = await repository.entitlement_counts(now=now)
        rows = await repository.active_entitlements(now=now)
        return {
            "ok": True,
            **counts,
            "items": [
                {
                    **row,
                    "valid_until": _iso(row["valid_until"]),
                    "last_purchase_at": _iso(row["last_purchase_at"]),
                }
                for row in rows
            ],
        }

    def _personal_config_json(config: PersonalConfig) -> dict[str, Any]:
        return {
            "price_stars": config.price_stars,
            "duration_days": config.duration_days,
            "free_daily_limit": config.limits.free_daily,
            "paid_daily_limit": config.limits.paid_daily,
            "default_units": _decimal_str(config.default_units),
            "unit_weights": {key: _decimal_str(value) for key, value in sorted(config.unit_weights.items())},
        }

    def _parse_personal_override(payload: dict[str, Any]) -> PersonalConfigOverride:
        def integer(key: str) -> int | None:
            value = payload.get(key)
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key} must be an integer")
            return value

        def decimal(value: Any, key: str) -> Decimal:
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ValueError(f"{key} must be a number")
            try:
                return Decimal(str(value))
            except Exception:
                raise ValueError(f"{key} must be a number") from None

        raw_weights = payload.get("unit_weights")
        if raw_weights is not None and not isinstance(raw_weights, dict):
            raise ValueError("unit_weights must be an object")
        known_features = {feature.value for feature in AiFeature}
        weights = None
        if raw_weights:
            unknown = sorted(set(raw_weights) - known_features)
            if unknown:
                raise ValueError(f"unknown features in unit_weights: {', '.join(unknown)}")
            weights = {key: decimal(value, f"unit_weights.{key}") for key, value in raw_weights.items()}
        default_units = payload.get("default_units")
        return PersonalConfigOverride(
            price_stars=integer("price_stars"),
            duration_days=integer("duration_days"),
            free_daily_limit=integer("free_daily_limit"),
            paid_daily_limit=integer("paid_daily_limit"),
            default_units=None if default_units is None else decimal(default_units, "default_units"),
            unit_weights=weights,
        )

    @router.get("/monetization/personal-config")
    async def personal_config_read(session: AsyncSession = AdminSession):
        base = config_from_settings(settings)
        override = await personal_config_store.load_override()
        effective = await _personal_provider.get()
        return {
            "ok": True,
            "env": _personal_config_json(base),
            "override": None
            if override is None
            else {
                "price_stars": override.price_stars,
                "duration_days": override.duration_days,
                "free_daily_limit": override.free_daily_limit,
                "paid_daily_limit": override.paid_daily_limit,
                "default_units": None if override.default_units is None else _decimal_str(override.default_units),
                "unit_weights": {k: _decimal_str(v) for k, v in (override.unit_weights or {}).items()},
            },
            "effective": _personal_config_json(effective),
            "applies_within_seconds": 15,
        }

    @router.put("/monetization/personal-config")
    async def personal_config_write(request: Request, session: AsyncSession = AdminSession):
        """Replace the override: omitted or null fields fall back to .env. Applies without a restart."""
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="Ожидался JSON.") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="Некорректные данные.")
        user = await load_user(session, request)
        try:
            effective = await personal_config_store.save_override(
                _parse_personal_override(payload), updated_by=user.telegram_user_id if user else None
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"ok": True, "effective": _personal_config_json(effective)}

    @router.get("/ai/readiness")
    async def ai_readiness(session: AsyncSession = AdminSession):
        """Informational diagnostics only; never creates a purchase or calls the provider."""
        now = _utc_now()
        checks: list[dict[str, str]] = []

        def add(key: str, label: str, status: str, detail: str | None = None) -> None:
            checks.append({"key": key, "label": label, "status": status, "detail": detail or ""})

        # Same validation as the bot process and /premium (llm_runtime_problem).
        _config, provider_problem = llm_runtime_problem(settings)
        if provider_problem is None:
            add("llm_provider", "AI-провайдер", "ok", "Настроен.")
        else:
            add("llm_provider", "AI-провайдер", "unavailable", provider_problem)

        if settings.selara_ai_price_stars is not None:
            add("stars_price", "Цена Stars", "ok", f"Цена задана: {settings.selara_ai_price_stars} ⭐.")
        else:
            add("stars_price", "Цена Stars", "missing", "SELARA_AI_PRICE_STARS не настроен.")

        if checkout_ready(settings):
            add("checkout", "Checkout", "ok", "Покупка Selara AI доступна.")
        elif settings.selara_ai_price_stars is None:
            add("checkout", "Checkout", "missing", "Checkout выключен: SELARA_AI_PRICE_STARS не настроен.")
        else:
            add("checkout", "Checkout", "unavailable", "Checkout выключен: AI-провайдер не настроен.")

        try:
            await session.scalar(select(SelaraAiPaymentModel.id).limit(1))
            add("payment_schema", "Схема платежей в БД", "ok")
        except Exception:
            add("payment_schema", "Схема платежей в БД", "unavailable", "Запрос к таблице платежей не удался.")

        try:
            await SqlAlchemyChatEntitlementResolver(session_factory).resolve(
                chat_id=0, feature=AiFeature.LLM_ADMIN, trigger="telegram_message"
            )
            add("entitlement_resolver", "Проверка доступа Selara AI", "ok")
        except Exception:
            add("entitlement_resolver", "Проверка доступа Selara AI", "unavailable", "Resolver не отвечает.")

        try:
            await AiAccountingService(session_factory).aggregate_window(
                window_from=now - timedelta(hours=1), window_to=now
            )
            add("accounting", "Учёт стоимости AI", "ok")
        except Exception:
            add("accounting", "Учёт стоимости AI", "unavailable", "Агрегация учёта не удалась.")

        return {
            "ok": True,
            "checkout": {
                "configured": checkout_ready(settings),
                "price_stars": settings.selara_ai_price_stars,
                "product_key": SELARA_AI_PRODUCT_KEY,
            },
            "checks": checks,
        }

    return router
