import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository

logger = logging.getLogger(__name__)


async def run_message_event_backfill(session_factory: async_sessionmaker[AsyncSession]) -> None:
    try:
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            chat_ids = await repo.list_message_event_sync_chat_ids()
            await session.commit()
    except Exception:
        logger.exception("Failed to collect activity event sync candidates")
        return

    for chat_id in chat_ids:
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            try:
                synced = await repo.backfill_message_events_for_chat(chat_id=chat_id)
                await session.commit()
                logger.info(
                    "Activity event sync finished",
                    extra={"chat_id": chat_id, "synced": synced},
                )
            except Exception as exc:
                await session.rollback()
                try:
                    await repo.mark_chat_message_event_sync_failed(chat_id=chat_id, error=str(exc))
                    await session.commit()
                except Exception:
                    await session.rollback()
                logger.exception("Activity event sync failed", extra={"chat_id": chat_id})
