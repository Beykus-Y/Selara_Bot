import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramMigrateToChat, TelegramNetworkError
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.presentation.chat_migration import apply_chat_migration, extract_migration_ids_from_exception

logger = logging.getLogger(__name__)


def _is_stale_callback_query_error(exc: TelegramBadRequest) -> bool:
    error_text = str(exc).lower()
    return "query is too old" in error_text or "query id is invalid" in error_text


class ErrorHandlerMiddleware(BaseMiddleware):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except TelegramMigrateToChat as exc:
            await self._handle_migrate_error(event=event, data=data, exc=exc, source="handler")
            return None
        except TelegramBadRequest as exc:
            if _is_stale_callback_query_error(exc):
                logger.info("Skipped stale callback query response", extra={"event_type": type(event).__name__})
                return None
            logger.exception("Unhandled Telegram bad request while processing update")
            await self._send_fallback_error(event=event, data=data)
            return None
        except TelegramNetworkError:
            logger.warning("Telegram network error while processing update")
            return None
        except Exception:
            logger.exception("Unhandled exception while processing update")
            await self._send_fallback_error(event=event, data=data)
            return None

    async def _send_fallback_error(self, *, event: Any, data: dict[str, Any]) -> None:
        try:
            if isinstance(event, Message):
                await event.answer("Произошла ошибка, попробуйте позже.")
                return
            if isinstance(event, CallbackQuery):
                await event.answer("Произошла ошибка, попробуйте позже.", show_alert=True)
                return
        except TelegramMigrateToChat as exc:
            await self._handle_migrate_error(event=event, data=data, exc=exc, source="fallback_error")
        except TelegramBadRequest as exc:
            if _is_stale_callback_query_error(exc):
                logger.info("Skipped fallback response for stale callback query", extra={"event_type": type(event).__name__})
                return
            logger.exception("Failed to send fallback error response")
        except TelegramNetworkError:
            logger.warning("Skipped fallback error response because Telegram API is unavailable")
        except Exception:
            logger.exception("Failed to send fallback error response")

    async def _handle_migrate_error(
        self,
        *,
        event: Any,
        data: dict[str, Any],
        exc: TelegramMigrateToChat,
        source: str,
    ) -> None:
        old_chat_id, new_chat_id = extract_migration_ids_from_exception(event, exc)
        if old_chat_id is None or new_chat_id is None:
            logger.warning(
                "Telegram chat migration detected but ids were not resolved",
                extra={"source": source, "error": str(exc)},
            )
            return

        try:
            if self._session_factory is None:
                await apply_chat_migration(
                    event=event,
                    data=data,
                    old_chat_id=old_chat_id,
                    new_chat_id=new_chat_id,
                    reason=f"telegram_exception:{source}",
                )
                return

            async with self._session_factory() as migration_session:
                migration_data = dict(data)
                migration_data["db_session"] = migration_session
                try:
                    await apply_chat_migration(
                        event=event,
                        data=migration_data,
                        old_chat_id=old_chat_id,
                        new_chat_id=new_chat_id,
                        reason=f"telegram_exception:{source}",
                    )
                except Exception:
                    await migration_session.rollback()
                    raise
                await migration_session.commit()
        except Exception:
            logger.exception(
                "Failed to apply chat migration",
                extra={"old_chat_id": old_chat_id, "new_chat_id": new_chat_id, "source": source},
            )
