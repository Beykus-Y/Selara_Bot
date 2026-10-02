"""Real row locks must deny a concurrent replay, including stale identity maps."""
import base64
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import ChatModel
from selara.infrastructure.db.artifact_repository import ArtifactRepository
from selara.infrastructure.llm.artifact_tools import ArtifactRequestContext, send_artifact
from selara.infrastructure.llm.tools import ToolCall


@pytest.mark.asyncio
async def test_pending_delivery_is_visible_to_a_second_session_with_cached_artifact():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as first, factory() as second:
            first.add(ChatModel(telegram_chat_id=1, type="supergroup", title="Chat"))
            await first.commit()
            repo = ArtifactRepository(first)
            row = await repo.create(chat_id=1, thread_id=None, creator_id=10, title="T",
                pages=[base64.b64encode(b"test photo").decode()], source={})
            await first.commit()
            other_repo = ArtifactRepository(second)
            cached = await other_repo.get(artifact_id=row.id, chat_id=1, thread_id=None)
            assert cached.delivery == {}
            row.delivery = {"pending": "another-request", "next": 0, "message_ids": []}
            await first.commit()
            ctx = ArtifactRequestContext(repository=other_repo, renderer_url="", chat_id=1,
                creator_id=10, message_id=100)
            bot = AsyncMock()
            result = await send_artifact(ToolCall(name="send_artifact", arguments={"artifact_id": row.id}, call_id="test"),
                artifact_context=ctx, bot=bot)
            assert not result.success and "uncertain" in result.result_text
            bot.send_photo.assert_not_awaited()
    finally:
        await engine.dispose()
