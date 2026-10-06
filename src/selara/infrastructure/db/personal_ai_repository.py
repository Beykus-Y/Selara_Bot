from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from selara.application.ai_character import CharacterProfile
from selara.application.personal_memory import MemoryItem
from selara.application.personal_models import is_profile_key
from selara.infrastructure.db.models import (
    PersonalAiMemoryModel,
    PersonalAiMessageModel,
    PersonalAiProfileModel,
    PersonalAiSummaryModel,
    UserModel,
)

# Columns the settings wizard may change; anything else is rejected.
_PROFILE_FIELDS = frozenset(
    {
        "display_name",
        "model_profile_key",
        "character_preset",
        "character_custom",
        "address_form",
        "formality",
        "reply_length",
        "emoji_enabled",
        "mode",
        "memory_enabled",
        "auto_memory_enabled",
    }
)


@dataclass(frozen=True, slots=True)
class StoredProfile:
    profile: CharacterProfile
    revision: int
    memory_enabled: bool
    auto_memory_enabled: bool = False
    memory_extract_cursor: int = 0
    # Logical model profile (basic/analytics/...); routing metadata only, never a privilege.
    model_profile_key: str = "basic"


class AddMemoryStatus(StrEnum):
    ADDED = "added"
    DUPLICATE = "duplicate"
    LIMIT_REACHED = "limit_reached"


@dataclass(frozen=True, slots=True)
class AddMemoryResult:
    status: AddMemoryStatus
    memory: PersonalAiMemoryModel | None = None


@dataclass(frozen=True, slots=True)
class ForgottenData:
    """What /forget_all removed (rows per table)."""

    memories: int
    messages: int
    summaries: int
    profile: bool


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
        auto_memory_enabled=row.auto_memory_enabled,
        memory_extract_cursor=row.memory_extract_cursor,
        model_profile_key=row.model_profile_key or "basic",
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
        # Several columns are changed with Core UPDATEs; never serve a stale identity-map copy.
        row = await self._session.get(PersonalAiProfileModel, user_id, populate_existing=True)
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
        if "model_profile_key" in fields and not is_profile_key(fields["model_profile_key"]):
            # Only stable profile keys are stored; a physical model id or forged value never is.
            raise ValueError("Unknown model profile")
        await self.get_or_create_profile(user_id)
        if fields.get("auto_memory_enabled") is True or fields.get("memory_enabled") is True:
            # Switching memory on starts from "now": text written before (or while it was off) is never analysed.
            fields["memory_extract_cursor"] = (
                select(func.coalesce(func.max(PersonalAiMessageModel.id), 0))
                .where(PersonalAiMessageModel.user_id == user_id)
                .scalar_subquery()
            )
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

    async def commit(self) -> None:
        """End the current transaction so its pooled connection is released (used around slow provider calls)."""
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    async def mark_compressed(self, *, user_id: int, message_ids: list[int]) -> int:
        """Mark rows compressed; returns how many were actually updated."""
        if not message_ids:
            return 0
        result = await self._session.execute(
            update(PersonalAiMessageModel)
            .where(PersonalAiMessageModel.user_id == user_id, PersonalAiMessageModel.id.in_(message_ids))
            .values(compressed=True)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

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

    async def delete_all_user_data(self, *, user_id: int) -> ForgottenData:
        """/forget_all: profile, history, summaries and memories of this one user. The account and billing stay."""
        counts = {}
        for key, model in (
            ("memories", PersonalAiMemoryModel),
            ("messages", PersonalAiMessageModel),
            ("summaries", PersonalAiSummaryModel),
        ):
            result = await self._session.execute(
                delete(model).where(model.user_id == user_id).execution_options(synchronize_session=False)
            )
            counts[key] = int(result.rowcount or 0)
        profile = await self._session.execute(
            delete(PersonalAiProfileModel)
            .where(PersonalAiProfileModel.user_id == user_id)
            .execution_options(synchronize_session=False)
        )
        # ORM identity map must not keep serving the deleted profile to later reads in this session.
        self._session.expire_all()
        return ForgottenData(
            memories=counts["memories"],
            messages=counts["messages"],
            summaries=counts["summaries"],
            profile=bool(profile.rowcount),
        )

    # --- memories ------------------------------------------------------------

    async def add_memory(self, *, user_id: int, content: str, source: str, limit: int) -> AddMemoryResult:
        """Store a fact unless it is a duplicate or the user is at ``limit``. Never evicts older facts."""
        await self.get_or_create_profile(user_id)
        # Serialise writers of one user (the profile row is the lock) so parallel confirmations cannot overshoot.
        await self._session.execute(
            select(PersonalAiProfileModel.user_id)
            .where(PersonalAiProfileModel.user_id == user_id)
            .with_for_update()
        )
        existing = (
            await self._session.scalars(
                select(PersonalAiMemoryModel.content).where(PersonalAiMemoryModel.user_id == user_id)
            )
        ).all()
        wanted = content.casefold()
        if any(item.casefold() == wanted for item in existing):
            return AddMemoryResult(AddMemoryStatus.DUPLICATE)
        if len(existing) >= limit:
            return AddMemoryResult(AddMemoryStatus.LIMIT_REACHED)
        row = PersonalAiMemoryModel(user_id=user_id, content=content, source=source)
        self._session.add(row)
        await self._session.flush()
        return AddMemoryResult(AddMemoryStatus.ADDED, row)

    async def list_memories(
        self, *, user_id: int, limit: int | None = None, offset: int = 0
    ) -> list[PersonalAiMemoryModel]:
        query = (
            select(PersonalAiMemoryModel)
            .where(PersonalAiMemoryModel.user_id == user_id)
            .order_by(PersonalAiMemoryModel.created_at.asc(), PersonalAiMemoryModel.id.asc())
            .offset(offset)
            # Rows are changed with Core UPDATEs (pin, last_used_at); never serve a stale identity-map copy.
            .execution_options(populate_existing=True)
        )
        if limit is not None:
            query = query.limit(limit)
        return list((await self._session.scalars(query)).all())

    async def count_memories(self, *, user_id: int) -> int:
        return int(
            await self._session.scalar(
                select(func.count()).select_from(PersonalAiMemoryModel).where(PersonalAiMemoryModel.user_id == user_id)
            )
            or 0
        )

    async def memory_items(self, *, user_id: int) -> list[MemoryItem]:
        rows = await self.list_memories(user_id=user_id)
        return [
            MemoryItem(
                id=row.id,
                content=row.content,
                pinned=row.pinned,
                last_used_at=row.last_used_at,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def delete_memory(self, *, user_id: int, memory_id: int) -> bool:
        result = await self._session.execute(
            delete(PersonalAiMemoryModel)
            .where(PersonalAiMemoryModel.user_id == user_id, PersonalAiMemoryModel.id == memory_id)
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)

    async def set_memory_pinned(self, *, user_id: int, memory_id: int, pinned: bool) -> bool:
        result = await self._session.execute(
            update(PersonalAiMemoryModel)
            .where(PersonalAiMemoryModel.user_id == user_id, PersonalAiMemoryModel.id == memory_id)
            .values(pinned=pinned)
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)

    async def touch_memories(self, *, user_id: int, memory_ids: list[int]) -> None:
        if not memory_ids:
            return
        await self._session.execute(
            update(PersonalAiMemoryModel)
            .where(PersonalAiMemoryModel.user_id == user_id, PersonalAiMemoryModel.id.in_(memory_ids))
            .values(last_used_at=func.now())
            .execution_options(synchronize_session=False)
        )

    # --- extraction cursor -------------------------------------------------------

    async def user_messages_after(
        self, *, user_id: int, thread: str, after_id: int, limit: int
    ) -> list[PersonalAiMessageModel]:
        """The user's own messages (never the assistant's) newer than ``after_id``, oldest first."""
        rows = (
            await self._session.scalars(
                select(PersonalAiMessageModel)
                .where(
                    PersonalAiMessageModel.user_id == user_id,
                    PersonalAiMessageModel.thread == thread,
                    PersonalAiMessageModel.role == "user",
                    PersonalAiMessageModel.id > after_id,
                )
                .order_by(PersonalAiMessageModel.id.asc())
                .limit(limit)
            )
        ).all()
        return list(rows)

    async def advance_extract_cursor(self, *, user_id: int, expected: int, new: int) -> bool:
        """Compare-and-set: only the writer that still sees ``expected`` moves the cursor."""
        result = await self._session.execute(
            update(PersonalAiProfileModel)
            .where(
                PersonalAiProfileModel.user_id == user_id,
                PersonalAiProfileModel.memory_extract_cursor == expected,
            )
            .values(memory_extract_cursor=new)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1
