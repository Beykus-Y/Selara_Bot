import hashlib
import logging
import re
import traceback
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramMigrateToChat, TelegramNetworkError
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.infrastructure.db.models import AdminRuntimeSettingsModel
from selara.presentation.chat_migration import apply_chat_migration, extract_migration_ids_from_exception

logger = logging.getLogger(__name__)
_ALERT_DEDUPE_WINDOW = timedelta(minutes=15)
_recent_alerts: dict[str, datetime] = {}


async def notify_operational_error(
    *, session_factory: async_sessionmaker[AsyncSession] | None, event: Any, exc: Exception, bot: Any = None
) -> None:
    if session_factory is None:
        return
    try:
        async with session_factory() as session:
            settings = await session.get(AdminRuntimeSettingsModel, 1)
        if settings is None or not settings.error_alerts_enabled or settings.error_alert_chat_id is None:
            return

        now = datetime.now(timezone.utc)
        frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
        signature_source = "|".join(
            [type(exc).__module__ + "." + type(exc).__qualname__]
            + [f"{frame.filename}:{frame.name}" for frame in frames[-8:]]
            + [re.sub(r"\b-?\d+\b", "<n>", str(exc)).strip()]
        )
        fingerprint = hashlib.sha256(signature_source.encode("utf-8", errors="replace")).hexdigest()[:12]
        previous = _recent_alerts.get(fingerprint)
        if previous is not None and now - previous < _ALERT_DEDUPE_WINDOW:
            return
        _recent_alerts[fingerprint] = now
        for key, sent_at in tuple(_recent_alerts.items()):
            if now - sent_at >= _ALERT_DEDUPE_WINDOW:
                _recent_alerts.pop(key, None)

        chat = getattr(event, "chat", None) or getattr(getattr(event, "message", None), "chat", None)
        user = getattr(event, "from_user", None)
        if user is None:
            user = getattr(getattr(event, "message", None), "from_user", None)
        raw_text = getattr(event, "text", None) or getattr(event, "caption", None)
        if raw_text is None:
            raw_text = getattr(getattr(event, "message", None), "text", None)
        command = (
            str(raw_text).strip().splitlines()[0][:180]
            if raw_text and str(raw_text).strip().startswith("/")
            else "—"
        )
        lines = [
            "🚨 Selara: ошибка при обработке обновления",
            f"Время: {now.isoformat(timespec='seconds')}",
            f"Событие: {type(event).__name__}",
            f"Команда: {command}",
            f"Чат: {getattr(chat, 'title', None) or '—'} (id={getattr(chat, 'id', '—')})",
            f"Пользователь: {getattr(user, 'full_name', None) or '—'} (id={getattr(user, 'id', '—')})",
            f"Ошибка: {type(exc).__name__}: {str(exc)[:500]}",
            f"Отпечаток: {fingerprint}",
        ]
        if frames:
            lines.extend(["", "Traceback:", "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-2200:]])
        target_bot = bot or getattr(event, "bot", None)
        if target_bot is not None:
            await target_bot.send_message(chat_id=settings.error_alert_chat_id, text="\n".join(lines)[:3900])
    except Exception:
        logger.exception("Failed to send operational error alert")


def _is_stale_callback_query_error(exc: TelegramBadRequest) -> bool:
    error_text = str(exc).lower()
    return "query is too old" in error_text or "query id is invalid" in error_text


def _is_closed_topic_error(exc: TelegramBadRequest) -> bool:
    error_text = str(exc).lower()
    return "topic_closed" in error_text or "topic is closed" in error_text


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
            if _is_closed_topic_error(exc):
                logger.info("Skipped response to closed topic", extra={"event_type": type(event).__name__})
                return None
            logger.exception("Unhandled Telegram bad request while processing update")
            await self._notify_admin(event=event, data=data, exc=exc)
            await self._send_fallback_error(event=event, data=data)
            return None
        except TelegramNetworkError as exc:
            logger.warning("Telegram network error while processing update")
            await self._notify_admin(event=event, data=data, exc=exc)
            return None
        except Exception as exc:
            logger.exception("Unhandled exception while processing update")
            await self._notify_admin(event=event, data=data, exc=exc)
            await self._send_fallback_error(event=event, data=data)
            return None

    async def _notify_admin(self, *, event: Any, data: dict[str, Any], exc: Exception) -> None:
        await notify_operational_error(
            session_factory=self._session_factory,
            event=event,
            exc=exc,
            bot=data.get("bot"),
        )

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
