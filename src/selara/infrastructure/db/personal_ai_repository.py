from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from selara.application.ai_character import CharacterProfile
from selara.infrastructure.db.models import (
    PersonalAiMessageModel,
    PersonalAiProfileModel,
    PersonalAiSummaryModel,
    UserModel,
)

# Columns the settings wizard may change; anything else is rejected.
_PROFILE_FIELDS = frozenset(
    {
        "display_name",
        "character_preset",
        "character_custom",
        "address_form",
        "formality",
        "reply_length",
        "emoji_enabled",
        "mode",
        "memory_enabled",
    }
)


@dataclass(frozen=True, slots=True)
class StoredProfile:
    profile: CharacterProfile
    revision: int
    memory_enabled: bool


def _to_stored(row: PersonalAiProfileModel) -> StoredProfile:
    return StoredProfile(
        profile=CharacterProfile(
            display_name=row.display_name,
            character_preset=row.character_preset,
            character_custom=row.character_custom,
            address_form=row.address_form,
            formality=row.formality,
            reply_length=row.reply_length,
            emoji_enabled=row.emoji_enabled,
            mode=row.mode,
        ),
        revision=row.revision,
        memory_enabled=row.memory_enabled,
    )


class PersonalAiRepository:
    """Storage for a user's private AI data; every query is keyed by ``user_id`` and nothing else."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _insert(self, model):
        """Dialect-aware INSERT ... ON CONFLICT DO NOTHING (PostgreSQL in production, SQLite in unit tests)."""
        dialect = self._session.get_bind().dialect.name
        return (sqlite_insert if dialect == "sqlite" else pg_insert)(model)

    async def _ensure_user(self, user_id: int) -> None:
        if await self._session.get(UserModel, user_id) is not None:
            return
        # A parallel update may create the row first; that is all we needed.
        await self._session.execute(
            self._insert(UserModel).values(telegram_user_id=user_id, is_bot=False).on_conflict_do_nothing()
        )

    async def get_profile(self, user_id: int) -> StoredProfile | None:
        row = await self._session.get(PersonalAiProfileModel, user_id)
        return _to_stored(row) if row is not None else None

    async def get_or_create_profile(self, user_id: int) -> StoredProfile:
        existing = await self.get_profile(user_id)
        if existing is not None:
            return existing
        await self._ensure_user(user_id)
        await self._session.execute(
            self._insert(PersonalAiProfileModel).values(user_id=user_id).on_conflict_do_nothing()
        )
        row = await self._session.get(PersonalAiProfileModel, user_id)
        await self._session.refresh(row)
        return _to_stored(row)

    async def update_profile(self, user_id: int, *, expected_revision: int, **fields: object) -> StoredProfile | None:
        """Apply ``fields`` only if the profile is still at ``expected_revision``; ``None`` means it moved on."""
        unknown = set(fields) - _PROFILE_FIELDS
        if unknown:
            raise ValueError(f"Unknown personal AI profile fields: {sorted(unknown)}")
        await self.get_or_create_profile(user_id)
        result = await self._session.execute(
            update(PersonalAiProfileModel)
            .where(
                PersonalAiProfileModel.user_id == user_id,
                PersonalAiProfileModel.revision == expected_revision,
            )
            .values(**fields, revision=PersonalAiProfileModel.revision + 1, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            return None
        await self._session.flush()
        row = await self._session.get(PersonalAiProfileModel, user_id)
        await self._session.refresh(row)
        return _to_stored(row)

    # --- dialogue history -------------------------------------------------

    async def add_message(
        self, *, user_id: int, thread: str, role: str, content: str, telegram_message_id: int | None = None
    ) -> PersonalAiMessageModel:
        await self._ensure_user(user_id)
        row = PersonalAiMessageModel(
            user_id=user_id, thread=thread, role=role, content=content, telegram_message_id=telegram_message_id
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def recent_messages(self, *, user_id: int, thread: str, limit: int) -> list[PersonalAiMessageModel]:
        """Newest ``limit`` uncompressed messages, returned oldest first."""
        rows = (
            await self._session.scalars(
                select(PersonalAiMessageModel)
                .where(
                    PersonalAiMessageModel.user_id == user_id,
                    PersonalAiMessageModel.thread == thread,
                    PersonalAiMessageModel.compressed.is_(False),
                )
                .order_by(PersonalAiMessageModel.created_at.desc(), PersonalAiMessageModel.id.desc())
                .limit(limit)
            )
        ).all()
        return list(reversed(rows))

    async def count_uncompressed(self, *, user_id: int, thread: str) -> int:
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(PersonalAiMessageModel)
                .where(
                    PersonalAiMessageModel.user_id == user_id,
                    PersonalAiMessageModel.thread == thread,
                    PersonalAiMessageModel.compressed.is_(False),
                )
            )
            or 0
        )

    async def oldest_uncompressed(self, *, user_id: int, thread: str, limit: int) -> list[PersonalAiMessageModel]:
        rows = (
            await self._session.scalars(
                select(PersonalAiMessageModel)
                .where(
                    PersonalAiMessageModel.user_id == user_id,
                    PersonalAiMessageModel.thread == thread,
                    PersonalAiMessageModel.compressed.is_(False),
                )
                .order_by(PersonalAiMessageModel.created_at.asc(), PersonalAiMessageModel.id.asc())
                .limit(limit)
            )
        ).all()
        return list(rows)

    async def mark_compressed(self, *, user_id: int, message_ids: list[int]) -> None:
        if not message_ids:
            return
        await self._session.execute(
            update(PersonalAiMessageModel)
            .where(PersonalAiMessageModel.user_id == user_id, PersonalAiMessageModel.id.in_(message_ids))
            .values(compressed=True)
            .execution_options(synchronize_session=False)
        )

    async def last_user_message_at(self, *, user_id: int) -> datetime | None:
        return await self._session.scalar(
            select(func.max(PersonalAiMessageModel.created_at)).where(
                PersonalAiMessageModel.user_id == user_id, PersonalAiMessageModel.role == "user"
            )
        )

    # --- summaries --------------------------------------------------------

    async def add_summary(
        self,
        *,
        user_id: int,
        thread: str,
        content: str,
        period_start: datetime,
        period_end: datetime,
        messages_count: int,
    ) -> PersonalAiSummaryModel:
        row = PersonalAiSummaryModel(
            user_id=user_id,
            thread=thread,
            content=content,
            period_start=period_start,
            period_end=period_end,
            messages_count=messages_count,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def latest_summary(self, *, user_id: int, thread: str) -> PersonalAiSummaryModel | None:
        return await self._session.scalar(
            select(PersonalAiSummaryModel)
            .where(PersonalAiSummaryModel.user_id == user_id, PersonalAiSummaryModel.thread == thread)
            .order_by(PersonalAiSummaryModel.period_end.desc(), PersonalAiSummaryModel.id.desc())
            .limit(1)
        )

    # --- user-initiated deletion -------------------------------------------

    async def reset_thread(self, *, user_id: int, thread: str) -> int:
        """Delete one thread's messages and summaries; the profile stays. Returns the messages removed."""
        removed = await self._session.execute(
            delete(PersonalAiMessageModel)
            .where(PersonalAiMessageModel.user_id == user_id, PersonalAiMessageModel.thread == thread)
            .execution_options(synchronize_session=False)
        )
        await self._session.execute(
            delete(PersonalAiSummaryModel)
            .where(PersonalAiSummaryModel.user_id == user_id, PersonalAiSummaryModel.thread == thread)
            .execution_options(synchronize_session=False)
        )
        return int(removed.rowcount or 0)
