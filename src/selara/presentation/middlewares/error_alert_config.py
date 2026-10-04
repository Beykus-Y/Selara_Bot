from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.core.config import Settings
from selara.infrastructure.db.models import AdminRuntimeSettingsModel

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ErrorAlertConfig:
    enabled: bool
    chat_id: int | None


_runtime_config = ErrorAlertConfig(enabled=False, chat_id=None)


def configure_error_alerts(enabled: bool, chat_id: int | None) -> None:
    """Replace the in-process alert destination after loading or saving it."""
    global _runtime_config
    _runtime_config = ErrorAlertConfig(enabled=bool(enabled and chat_id is not None), chat_id=chat_id)


def get_error_alert_config() -> ErrorAlertConfig:
    return _runtime_config


async def load_error_alert_config(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Load DB settings once at startup, retaining the environment fallback on DB errors."""
    configure_error_alerts(settings.error_alert_chat_id is not None, settings.error_alert_chat_id)
    try:
        async with session_factory() as session:
            runtime_settings = await session.get(AdminRuntimeSettingsModel, 1)
        if runtime_settings is not None:
            configure_error_alerts(
                runtime_settings.error_alerts_enabled,
                runtime_settings.error_alert_chat_id,
            )
    except Exception:
        logger.exception("Could not load operational alert settings; using ERROR_ALERT_CHAT_ID fallback")
