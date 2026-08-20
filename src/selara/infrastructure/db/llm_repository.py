from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from selara.infrastructure.db.models import (
    LlmAdminActionModel,
    LlmChatGlossaryHistoryModel,
    LlmChatGlossaryModel,
    LlmContextMessageModel,
    LlmContextSummaryModel,
)


class LlmRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Context messages ---

    async def add_context_message(
        self,
        *,
        chat_id: int,
        role: str,
        content: str,
        is_context: bool,
        admin_user_id: int | None = None,
        tool_call_id: str | None = None,
    ) -> LlmContextMessageModel:
        row = LlmContextMessageModel(
            chat_id=chat_id,
            role=role,
            content=content,
            is_context=is_context,
            admin_user_id=admin_user_id,
            tool_call_id=tool_call_id,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_last_user_message_at(self, *, chat_id: int, admin_user_id: int) -> datetime | None:
        """#3: backs the LLM assistant's per-(chat, admin) cooldown -- DB-backed
        (unlike the STT cooldown, which has no comparable persisted per-message
        log) so it survives restarts and works across multiple bot processes."""
        from sqlalchemy import func as sqlfunc
        stmt = select(sqlfunc.max(LlmContextMessageModel.created_at)).where(
            LlmContextMessageModel.chat_id == chat_id,
            LlmContextMessageModel.admin_user_id == admin_user_id,
            LlmContextMessageModel.role == "user",
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def count_uncompressed_context_messages(self, *, chat_id: int) -> int:
        from sqlalchemy import func as sqlfunc
        stmt = select(sqlfunc.count()).where(
            LlmContextMessageModel.chat_id == chat_id,
            LlmContextMessageModel.is_context.is_(True),
            LlmContextMessageModel.compressed.is_(False),
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def get_uncompressed_context_messages(
        self, *, chat_id: int, limit: int | None = None
    ) -> list[LlmContextMessageModel]:
        stmt = (
            select(LlmContextMessageModel)
            .where(
                LlmContextMessageModel.chat_id == chat_id,
                LlmContextMessageModel.is_context.is_(True),
                LlmContextMessageModel.compressed.is_(False),
            )
            .order_by(LlmContextMessageModel.created_at.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())

    async def mark_messages_compressed(self, *, message_ids: list[int]) -> None:
        if not message_ids:
            return
        stmt = (
            update(LlmContextMessageModel)
            .where(LlmContextMessageModel.id.in_(message_ids))
            .values(compressed=True)
        )
        await self._session.execute(stmt)

    async def reset_context(self, *, chat_id: int) -> int:
        """#11: manual escape hatch -- unlike the automatic
        threshold-triggered summarization (maybe_compress), which rolls
        forward potentially-bad context, this discards it outright. Marks
        remaining uncompressed context messages as compressed (so
        load_context stops returning them) and deletes any summaries for
        the chat, rather than deleting the message rows themselves (keeps
        them available via get_history)."""
        result = await self._session.execute(
            update(LlmContextMessageModel)
            .where(
                LlmContextMessageModel.chat_id == chat_id,
                LlmContextMessageModel.is_context.is_(True),
                LlmContextMessageModel.compressed.is_(False),
            )
            .values(compressed=True)
        )
        await self._session.execute(
            delete(LlmContextSummaryModel).where(LlmContextSummaryModel.chat_id == chat_id)
        )
        await self._session.flush()
        return result.rowcount

    async def get_all_messages_in_range(
        self,
        *,
        chat_id: int,
        period_start: datetime,
        period_end: datetime,
        limit: int = 500,
    ) -> list[LlmContextMessageModel]:
        stmt = (
            select(LlmContextMessageModel)
            .where(
                LlmContextMessageModel.chat_id == chat_id,
                LlmContextMessageModel.created_at >= period_start,
                LlmContextMessageModel.created_at <= period_end,
            )
            .order_by(LlmContextMessageModel.created_at.asc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    # --- Summaries ---

    async def add_summary(
        self,
        *,
        chat_id: int,
        content: str,
        period_start: datetime,
        period_end: datetime,
        messages_count: int,
        level: int = 1,
    ) -> LlmContextSummaryModel:
        row = LlmContextSummaryModel(
            chat_id=chat_id,
            content=content,
            period_start=period_start,
            period_end=period_end,
            messages_count=messages_count,
            level=level,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_latest_summary(self, *, chat_id: int) -> LlmContextSummaryModel | None:
        stmt = (
            select(LlmContextSummaryModel)
            .where(LlmContextSummaryModel.chat_id == chat_id)
            .order_by(
                LlmContextSummaryModel.period_end.desc(),
                LlmContextSummaryModel.id.desc(),
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_summaries_in_range(
        self,
        *,
        chat_id: int,
        period_start: datetime,
        period_end: datetime,
    ) -> list[LlmContextSummaryModel]:
        stmt = (
            select(LlmContextSummaryModel)
            .where(
                LlmContextSummaryModel.chat_id == chat_id,
                LlmContextSummaryModel.period_end >= period_start,
                LlmContextSummaryModel.period_start <= period_end,
            )
            .order_by(LlmContextSummaryModel.period_start.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    # --- Admin actions ---

    async def add_admin_action(
        self,
        *,
        chat_id: int,
        admin_user_id: int,
        tool_name: str,
        action_description: str,
        undo_payload: dict | None,
    ) -> LlmAdminActionModel:
        row = LlmAdminActionModel(
            chat_id=chat_id,
            admin_user_id=admin_user_id,
            tool_name=tool_name,
            action_description=action_description,
            undo_payload_json=undo_payload,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_admin_action(self, *, action_id: int) -> LlmAdminActionModel | None:
        return await self._session.get(LlmAdminActionModel, action_id)

    async def mark_rolled_back(
        self, *, action_id: int, rolled_back_by_user_id: int
    ) -> bool:
        """Atomically claim the rollback (fixes #35): an
        UPDATE ... WHERE rolled_back_at IS NULL is a single statement, so
        Postgres row-locking guarantees at most one of two concurrent
        callers can ever observe rowcount == 1, unlike the previous
        read-then-write which let both concurrent clicks pass the check
        and both fire a non-idempotent undo (unwarn/unpred)."""
        stmt = (
            update(LlmAdminActionModel)
            .where(
                LlmAdminActionModel.id == action_id,
                LlmAdminActionModel.rolled_back_at.is_(None),
            )
            .values(
                rolled_back_at=datetime.now(timezone.utc),
                rolled_back_by_user_id=rolled_back_by_user_id,
            )
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount == 1

    async def clear_rollback_claim(self, *, action_id: int) -> None:
        """Release a claim taken by mark_rolled_back when the rollback
        itself then fails validation/authorization (no side effect
        occurred), so the action can be retried instead of being
        permanently and incorrectly marked as rolled back."""
        row = await self._session.get(LlmAdminActionModel, action_id)
        if row is None:
            return
        row.rolled_back_at = None
        row.rolled_back_by_user_id = None
        await self._session.flush()

    # --- Glossary ---

    async def lookup_glossary_term(self, *, chat_id: int, term: str) -> LlmChatGlossaryModel | None:
        normalized = term.lower().strip()
        stmt = select(LlmChatGlossaryModel).where(
            LlmChatGlossaryModel.chat_id == chat_id,
            LlmChatGlossaryModel.term == normalized,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert_glossary_term(
        self, *, chat_id: int, term: str, definition: str, actor_user_id: int | None = None,
    ) -> LlmChatGlossaryModel:
        """#17/#18: tracks who wrote/last edited an entry, and records the
        replaced definition in llm_chat_glossary_history before overwriting
        it, so a poisoned or otherwise-bad edit can be inspected/recovered
        instead of silently lost. Uses a plain select-then-write (not the
        previous single-statement upsert) because capturing the pre-update
        value for history requires seeing it before the overwrite -- a
        narrow TOCTOU window on concurrent writes to the *same* term is
        acceptable here (audit trail, not a security/consistency-critical
        path like the moderation-action locks elsewhere in this codebase)."""
        normalized = term.lower().strip()
        existing = await self._session.execute(
            select(LlmChatGlossaryModel).where(
                LlmChatGlossaryModel.chat_id == chat_id,
                LlmChatGlossaryModel.term == normalized,
            )
        )
        row = existing.scalar_one_or_none()
        if row is not None:
            self._session.add(LlmChatGlossaryHistoryModel(
                chat_id=chat_id,
                term=normalized,
                previous_definition=row.definition,
                changed_by_user_id=actor_user_id,
            ))
            row.definition = definition
            row.updated_by_user_id = actor_user_id
            row.updated_at = datetime.now(timezone.utc)
        else:
            row = LlmChatGlossaryModel(
                chat_id=chat_id,
                term=normalized,
                definition=definition,
                created_by_user_id=actor_user_id,
                updated_by_user_id=actor_user_id,
            )
            self._session.add(row)
        await self._session.flush()
        return row

    async def list_glossary(self, *, chat_id: int) -> list[LlmChatGlossaryModel]:
        stmt = (
            select(LlmChatGlossaryModel)
            .where(LlmChatGlossaryModel.chat_id == chat_id)
            .order_by(LlmChatGlossaryModel.term.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def delete_glossary_term(self, *, chat_id: int, term: str) -> bool:
        """#6: recovery path for a poisoned glossary entry."""
        normalized = term.lower().strip()
        result = await self._session.execute(
            delete(LlmChatGlossaryModel).where(
                LlmChatGlossaryModel.chat_id == chat_id,
                LlmChatGlossaryModel.term == normalized,
            )
        )
        await self._session.flush()
        return result.rowcount == 1

    async def get_glossary_history(
        self, *, chat_id: int, term: str, limit: int = 10,
    ) -> list[LlmChatGlossaryHistoryModel]:
        """#18: lets an admin see what a poisoned/bad entry looked like
        before the most recent edit(s)."""
        normalized = term.lower().strip()
        stmt = (
            select(LlmChatGlossaryHistoryModel)
            .where(
                LlmChatGlossaryHistoryModel.chat_id == chat_id,
                LlmChatGlossaryHistoryModel.term == normalized,
            )
            .order_by(LlmChatGlossaryHistoryModel.changed_at.desc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())
