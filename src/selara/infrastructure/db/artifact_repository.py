"""Chat-scoped persistent artifacts. No lookup by ID without a chat boundary."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from selara.infrastructure.db.models import LlmArtifactModel


class ArtifactRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, chat_id: int, thread_id: int | None, creator_id: int,
                     title: str, pages: list[str], source: dict) -> LlmArtifactModel:
        now = datetime.now(timezone.utc)
        await self.session.execute(delete(LlmArtifactModel).where(LlmArtifactModel.expires_at <= now))
        # Serialize the per-chat quota even across concurrent agent requests.
        from selara.infrastructure.db.models import ChatModel
        await self.session.execute(select(ChatModel).where(ChatModel.telegram_chat_id == chat_id).with_for_update())
        count = await self.session.scalar(select(func.count()).select_from(LlmArtifactModel).where(
            LlmArtifactModel.chat_id == chat_id))
        if (count or 0) >= 30:
            raise ValueError("Лимит хранения: 30 артефактов чата за 7 дней.")
        row = LlmArtifactModel(id=str(uuid4()), chat_id=chat_id, thread_id=thread_id,
                              creator_id=creator_id, title=title, pages=pages, source=source,
                              expires_at=now + timedelta(days=7), delivery={})
        self.session.add(row)
        await self.session.flush()
        return row

    async def get(self, *, artifact_id: str, chat_id: int, thread_id: int | None,
                  lock: bool = False) -> LlmArtifactModel | None:
        stmt = select(LlmArtifactModel).where(
            LlmArtifactModel.id == artifact_id, LlmArtifactModel.chat_id == chat_id,
            LlmArtifactModel.thread_id == thread_id,
            LlmArtifactModel.expires_at > datetime.now(timezone.utc))
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(stmt)
