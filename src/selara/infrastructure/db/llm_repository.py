from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from selara.infrastructure.db.models import (
    LlmAdminActionModel,
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

    async def get_all_messages_in_range(
        self,
        *,
        chat_id: int,
        period_start: datetime,
        period_end: datetime,
    ) -> list[LlmContextMessageModel]:
        stmt = (
            select(LlmContextMessageModel)
            .where(
                LlmContextMessageModel.chat_id == chat_id,
                LlmContextMessageModel.created_at >= period_start,
                LlmContextMessageModel.created_at <= period_end,
            )
            .order_by(LlmContextMessageModel.created_at.asc())
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
        row = await self._session.get(LlmAdminActionModel, action_id)
        if row is None or row.rolled_back_at is not None:
            return False
        row.rolled_back_at = datetime.now(timezone.utc)
        row.rolled_back_by_user_id = rolled_back_by_user_id
        await self._session.flush()
        return True

    # --- Glossary ---

    async def lookup_glossary_term(self, *, chat_id: int, term: str) -> LlmChatGlossaryModel | None:
        normalized = term.lower().strip()
        stmt = select(LlmChatGlossaryModel).where(
            LlmChatGlossaryModel.chat_id == chat_id,
            LlmChatGlossaryModel.term == normalized,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert_glossary_term(self, *, chat_id: int, term: str, definition: str) -> LlmChatGlossaryModel:
        normalized = term.lower().strip()
        stmt = (
            pg_insert(LlmChatGlossaryModel)
            .values(chat_id=chat_id, term=normalized, definition=definition)
            .on_conflict_do_update(
                constraint="uq_llm_glossary_chat_term",
                set_={"definition": definition, "updated_at": datetime.now(timezone.utc)},
            )
            .returning(LlmChatGlossaryModel)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.scalar_one()

    async def list_glossary(self, *, chat_id: int) -> list[LlmChatGlossaryModel]:
        stmt = (
            select(LlmChatGlossaryModel)
            .where(LlmChatGlossaryModel.chat_id == chat_id)
            .order_by(LlmChatGlossaryModel.term.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())
