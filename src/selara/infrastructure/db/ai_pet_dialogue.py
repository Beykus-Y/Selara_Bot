"""Storage side of pet dialogue: talk admission, history, weekly aggregates and notes.

A talk is admitted under the pet row lock: the guest share of the owner's daily
pool is counted from ``ai_pet_messages`` and a ``pending`` row is written in the
same transaction, so concurrent guests cannot overrun it. The owner's total is
enforced separately by the feature quota (pool ``pet_daily``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from selara.application.ai_pets.dialogue import MAX_NOTES, NOTE_MAX_LEN
from selara.infrastructure.db.models import (
    AiPetEventModel,
    AiPetMemoryModel,
    AiPetMessageModel,
    AiPetModel,
)

AdmitStatus = Literal["ok", "duplicate", "unavailable", "cooldown", "guests_exhausted", "guest_exhausted"]

TALK_COOLDOWN = timedelta(seconds=15)
# History older than this is pruned beyond the newest KEEP_RECENT rows; a day's talks are always kept for counting.
HISTORY_RETENTION = timedelta(days=2)
KEEP_RECENT = 40
_AGGREGATED_EVENTS = ("pat", "play", "feed", "toy", "tease", "hurt")


@dataclass(frozen=True, slots=True)
class TalkAdmission:
    status: AdmitStatus
    message_id: int | None = None
    owner_user_id: int | None = None
    retry_after: timedelta | None = None


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class AiPetDialogueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def pet_for_reply(self, *, chat_id: int, telegram_message_id: int) -> int | None:
        """The pet whose line in this chat the user replied to."""
        return await self._session.scalar(
            select(AiPetMessageModel.pet_id)
            .where(
                AiPetMessageModel.chat_id == chat_id,
                AiPetMessageModel.telegram_message_id == telegram_message_id,
                AiPetMessageModel.role == "assistant",
            )
            .limit(1)
        )

    async def admit_talk(
        self,
        *,
        pet_id: int,
        chat_id: int,
        author_user_id: int,
        content: str,
        idempotency_key: str,
        telegram_message_id: int | None,
        day_start: datetime,
        guests_limit: int,
        guest_limit: int,
        now: datetime,
    ) -> TalkAdmission:
        pet = await self._session.scalar(select(AiPetModel).where(AiPetModel.id == pet_id).with_for_update())
        if pet is None or pet.status != "active" or pet.current_chat_id != chat_id:
            return TalkAdmission(status="unavailable")
        owner_id = int(pet.owner_user_id)
        existing = await self._session.scalar(
            select(AiPetMessageModel.id).where(AiPetMessageModel.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return TalkAdmission(status="duplicate", owner_user_id=owner_id)

        last_at = await self._session.scalar(
            select(func.max(AiPetMessageModel.created_at)).where(
                AiPetMessageModel.pet_id == pet_id,
                AiPetMessageModel.author_user_id == author_user_id,
                AiPetMessageModel.role == "user",
            )
        )
        if last_at is not None:
            left = _as_utc(last_at) + TALK_COOLDOWN - now
            if left.total_seconds() > 0:
                return TalkAdmission(status="cooldown", owner_user_id=owner_id, retry_after=left)

        is_owner = author_user_id == owner_id
        if not is_owner:
            counted = (
                AiPetMessageModel.pet_id == pet_id,
                AiPetMessageModel.role == "user",
                AiPetMessageModel.author_is_owner.is_(False),
                AiPetMessageModel.status.in_(("pending", "ok")),
                AiPetMessageModel.created_at >= day_start,
            )
            guests_used = int(await self._session.scalar(select(func.count()).where(*counted)) or 0)
            if guests_used >= guests_limit:
                return TalkAdmission(status="guests_exhausted", owner_user_id=owner_id)
            mine = int(
                await self._session.scalar(
                    select(func.count()).where(*counted, AiPetMessageModel.author_user_id == author_user_id)
                )
                or 0
            )
            if mine >= guest_limit:
                return TalkAdmission(status="guest_exhausted", owner_user_id=owner_id)

        row = AiPetMessageModel(
            pet_id=pet_id,
            chat_id=chat_id,
            author_user_id=author_user_id,
            author_is_owner=is_owner,
            role="user",
            content=content,
            status="pending",
            idempotency_key=idempotency_key,
            telegram_message_id=telegram_message_id,
            created_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return TalkAdmission(status="ok", message_id=int(row.id), owner_user_id=owner_id)

    async def set_status(self, *, message_id: int, status: str) -> None:
        await self._session.execute(
            update(AiPetMessageModel).where(AiPetMessageModel.id == message_id).values(status=status)
        )

    async def add_reply(self, *, pet_id: int, chat_id: int, content: str, now: datetime) -> int:
        row = AiPetMessageModel(
            pet_id=pet_id, chat_id=chat_id, author_user_id=None, author_is_owner=False,
            role="assistant", content=content, status="ok", created_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return int(row.id)

    async def set_reply_message_id(self, *, row_id: int, telegram_message_id: int) -> None:
        await self._session.execute(
            update(AiPetMessageModel)
            .where(AiPetMessageModel.id == row_id)
            .values(telegram_message_id=telegram_message_id)
        )

    async def recent(self, *, pet_id: int, chat_id: int, limit: int) -> list[AiPetMessageModel]:
        rows = await self._session.scalars(
            select(AiPetMessageModel)
            .where(
                AiPetMessageModel.pet_id == pet_id,
                AiPetMessageModel.chat_id == chat_id,
                AiPetMessageModel.status == "ok",
            )
            .order_by(AiPetMessageModel.created_at.desc(), AiPetMessageModel.id.desc())
            .limit(limit)
        )
        return list(reversed(list(rows)))

    async def record_talk(self, *, pet_id: int, chat_id: int, author_user_id: int, idempotency_key: str, now: datetime) -> int:
        """Journal a successful talk and return how many this pet has had in this chat.

        The journal is never pruned (unlike the dialogue history), so the count stays
        monotonic and drives the note-extraction cadence.
        """
        self._session.add(
            AiPetEventModel(
                pet_id=pet_id, chat_id=chat_id, actor_user_id=author_user_id, event_type="talk",
                effects={}, idempotency_key=idempotency_key, created_at=now,
            )
        )
        await self._session.flush()
        return int(
            await self._session.scalar(
                select(func.count()).where(
                    AiPetEventModel.pet_id == pet_id,
                    AiPetEventModel.chat_id == chat_id,
                    AiPetEventModel.event_type == "talk",
                )
            )
            or 0
        )

    async def weekly_aggregates(self, *, pet_id: int, chat_id: int, now: datetime, limit: int = 8) -> list[tuple[int, str, int]]:
        rows = await self._session.execute(
            select(AiPetEventModel.actor_user_id, AiPetEventModel.event_type, func.count())
            .where(
                AiPetEventModel.pet_id == pet_id,
                AiPetEventModel.chat_id == chat_id,
                AiPetEventModel.actor_user_id.is_not(None),
                AiPetEventModel.event_type.in_(_AGGREGATED_EVENTS),
                AiPetEventModel.created_at >= now - timedelta(days=7),
            )
            .group_by(AiPetEventModel.actor_user_id, AiPetEventModel.event_type)
            .order_by(func.count().desc())
            .limit(limit)
        )
        return [(int(actor), str(event_type), int(count)) for actor, event_type, count in rows]

    async def notes(self, *, pet_id: int, chat_id: int, limit: int = MAX_NOTES) -> list[str]:
        rows = await self._session.scalars(
            select(AiPetMemoryModel.content)
            .where(AiPetMemoryModel.pet_id == pet_id, AiPetMemoryModel.chat_id == chat_id)
            .order_by(AiPetMemoryModel.created_at.desc(), AiPetMemoryModel.id.desc())
            .limit(limit)
        )
        return list(reversed(list(rows)))

    async def add_notes(self, *, pet_id: int, chat_id: int, notes: list[str], now: datetime) -> None:
        for note in notes:
            self._session.add(
                AiPetMemoryModel(pet_id=pet_id, chat_id=chat_id, content=note[:NOTE_MAX_LEN], source="dialogue", created_at=now)
            )
        await self._session.flush()
        keep = (
            select(AiPetMemoryModel.id)
            .where(AiPetMemoryModel.pet_id == pet_id, AiPetMemoryModel.chat_id == chat_id)
            .order_by(AiPetMemoryModel.created_at.desc(), AiPetMemoryModel.id.desc())
            .limit(MAX_NOTES)
        )
        keep_ids = list(await self._session.scalars(keep))
        await self._session.execute(
            delete(AiPetMemoryModel).execution_options(synchronize_session=False).where(
                AiPetMemoryModel.pet_id == pet_id,
                AiPetMemoryModel.chat_id == chat_id,
                AiPetMemoryModel.id.not_in(keep_ids),
            )
        )

    async def prune_history(self, *, pet_id: int, chat_id: int, now: datetime) -> None:
        keep_ids = list(
            await self._session.scalars(
                select(AiPetMessageModel.id)
                .where(AiPetMessageModel.pet_id == pet_id, AiPetMessageModel.chat_id == chat_id)
                .order_by(AiPetMessageModel.created_at.desc(), AiPetMessageModel.id.desc())
                .limit(KEEP_RECENT)
            )
        )
        await self._session.execute(
            delete(AiPetMessageModel).execution_options(synchronize_session=False).where(
                AiPetMessageModel.pet_id == pet_id,
                AiPetMessageModel.chat_id == chat_id,
                AiPetMessageModel.created_at < now - HISTORY_RETENTION,
                AiPetMessageModel.id.not_in(keep_ids),
            )
        )

    async def forget(self, *, pet_id: int, chat_id: int) -> tuple[int, int]:
        """Owner wipes what the pet remembers from this chat: dialogue history and notes."""
        messages = await self._session.execute(
            delete(AiPetMessageModel).execution_options(synchronize_session=False).where(AiPetMessageModel.pet_id == pet_id, AiPetMessageModel.chat_id == chat_id)
        )
        notes = await self._session.execute(
            delete(AiPetMemoryModel).execution_options(synchronize_session=False).where(AiPetMemoryModel.pet_id == pet_id, AiPetMemoryModel.chat_id == chat_id)
        )
        return int(messages.rowcount or 0), int(notes.rowcount or 0)
