from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from uuid import uuid4

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from selara.infrastructure.db.models import LlmUsageLogModel, MessageArchiveModel, SttBudgetReservationModel
from selara.infrastructure.db.repositories import STT_CLAIM_STALE_AFTER_SECONDS, SqlAlchemyActivityRepository


def reservation_milliseconds(duration_seconds: float, max_seconds: int) -> int | None:
    duration = Decimal(str(duration_seconds))
    if not duration.is_finite() or duration <= 0 or duration > max_seconds:
        return None
    return int((duration * 1000).to_integral_value(rounding=ROUND_CEILING))


class SttBudgetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.activity = SqlAlchemyActivityRepository(session)

    async def reserve(
        self, *, chat_id: int, archive_row_id: int, claim_at: datetime,
        duration_seconds: float, max_seconds: int, now: datetime | None = None,
    ) -> str | None:
        now = now or datetime.now(timezone.utc)
        reserved_ms = reservation_milliseconds(duration_seconds, max_seconds)
        if reserved_ms is None:
            return None
        window_from = now - timedelta(hours=24)
        await self.activity.lock_resources(f"daily-summary:stt-budget:{chat_id}")
        await self.session.execute(delete(SttBudgetReservationModel).where(
            SttBudgetReservationModel.chat_id == chat_id,
            SttBudgetReservationModel.created_at < window_from,
            SttBudgetReservationModel.lease_expires_at < now,
        ))
        claim = await self.session.scalar(select(MessageArchiveModel.id).where(
            MessageArchiveModel.id == archive_row_id,
            MessageArchiveModel.chat_id == chat_id,
            MessageArchiveModel.transcript.is_(None),
            MessageArchiveModel.transcribed_at == claim_at,
            MessageArchiveModel.transcribed_at > now - timedelta(seconds=STT_CLAIM_STALE_AFTER_SECONDS),
        ))
        if claim is None:
            return None
        duplicate = await self.session.scalar(select(SttBudgetReservationModel.token).where(
            SttBudgetReservationModel.archive_row_id == archive_row_id,
            SttBudgetReservationModel.status == "reserved",
            SttBudgetReservationModel.lease_expires_at > now,
        ))
        if duplicate is not None:
            return None
        charged_ms = await self.session.scalar(select(func.coalesce(func.sum(SttBudgetReservationModel.reserved_ms), 0)).where(
            SttBudgetReservationModel.chat_id == chat_id,
            SttBudgetReservationModel.created_at >= window_from,
        ))
        # New completed charges retain exact milliseconds. Their usage rows are
        # for analytics, not a second budget charge (audio_seconds has scale 2).
        represented = exists(select(SttBudgetReservationModel.token).where(
            SttBudgetReservationModel.usage_log_id == LlmUsageLogModel.id,
        ))
        legacy_seconds = await self.session.scalar(select(func.coalesce(func.sum(LlmUsageLogModel.audio_seconds), 0)).where(
            LlmUsageLogModel.chat_id == chat_id,
            LlmUsageLogModel.stage == "stt",
            LlmUsageLogModel.created_at >= window_from,
            LlmUsageLogModel.created_at <= now,
            ~represented,
        ))
        legacy_ms = int((Decimal(str(legacy_seconds)) * 1000).to_integral_value(rounding=ROUND_CEILING))
        if int(charged_ms) + legacy_ms + reserved_ms > int(max_seconds) * 1000:
            return None
        token = str(uuid4())
        self.session.add(SttBudgetReservationModel(
            token=token, chat_id=chat_id, archive_row_id=archive_row_id, claim_at=claim_at,
            reserved_ms=reserved_ms, lease_expires_at=claim_at + timedelta(seconds=STT_CLAIM_STALE_AFTER_SECONDS),
            status="reserved", created_at=now,
        ))
        await self.session.flush()
        return token

    async def _locked_reservation(self, token: str) -> SttBudgetReservationModel | None:
        chat_id = await self.session.scalar(select(SttBudgetReservationModel.chat_id).where(
            SttBudgetReservationModel.token == token,
        ))
        if chat_id is None:
            return None
        await self.activity.lock_resources(f"daily-summary:stt-budget:{chat_id}")
        return await self.session.scalar(select(SttBudgetReservationModel).where(
            SttBudgetReservationModel.token == token,
            SttBudgetReservationModel.status == "reserved",
        ).execution_options(populate_existing=True))

    async def release(self, *, token: str) -> bool:
        row = await self._locked_reservation(token)
        if row is None:
            return False
        # A stale attempt can release its own reservation, but never a newer claim.
        await self.session.execute(update(MessageArchiveModel).where(
            MessageArchiveModel.id == row.archive_row_id,
            MessageArchiveModel.transcript.is_(None),
            MessageArchiveModel.transcribed_at == row.claim_at,
        ).values(transcribed_at=None))
        await self.session.delete(row)
        await self.session.flush()
        return True

    async def settle(
        self, *, token: str, transcript: str, model: str, audio_seconds: float,
        estimated_cost_usd: float | None, now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        row = await self._locked_reservation(token)
        if row is None:
            return False
        changed = await self.session.scalar(update(MessageArchiveModel).where(
            MessageArchiveModel.id == row.archive_row_id,
            MessageArchiveModel.transcript.is_(None),
            MessageArchiveModel.transcribed_at == row.claim_at,
            # SQL comparison also works when sqlite returns naive datetimes.
            exists(select(SttBudgetReservationModel.token).where(
                SttBudgetReservationModel.token == token,
                SttBudgetReservationModel.lease_expires_at > now,
            )),
        ).values(transcript=transcript, transcribed_at=now).returning(MessageArchiveModel.id))
        if changed is None:
            return False
        usage = LlmUsageLogModel(message_archive_id=row.archive_row_id, chat_id=row.chat_id,
                                feature="daily_summary", stage="stt", model=model,
                                audio_seconds=audio_seconds, estimated_cost_usd=estimated_cost_usd, created_at=now)
        self.session.add(usage)
        await self.session.flush()
        row.status = "consumed"
        row.usage_log_id = usage.id
        row.created_at = now
        await self.session.flush()
        return True
