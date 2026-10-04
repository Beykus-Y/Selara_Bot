"""In-process runtime signal for the Telegram polling loop."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

_lock = threading.Lock()
_polling_running = False
_polling_started_at: datetime | None = None
_polling_heartbeat_at: datetime | None = None


def mark_bot_polling_started() -> None:
    global _polling_running, _polling_started_at, _polling_heartbeat_at
    now = datetime.now(UTC)
    with _lock:
        _polling_running = True
        _polling_started_at = now
        _polling_heartbeat_at = now


def mark_bot_polling_stopped() -> None:
    global _polling_running
    with _lock:
        _polling_running = False


def refresh_bot_polling_heartbeat() -> None:
    global _polling_heartbeat_at
    with _lock:
        if _polling_running:
            _polling_heartbeat_at = datetime.now(UTC)


def get_bot_polling_runtime_state() -> dict[str, datetime | bool | None]:
    with _lock:
        return {
            "running": _polling_running,
            "started_at": _polling_started_at,
            "heartbeat_at": _polling_heartbeat_at,
        }
