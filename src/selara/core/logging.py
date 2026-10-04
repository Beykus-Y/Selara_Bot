import logging
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any

from selara.core.config import Settings
from selara.infrastructure.security.redaction import redact_sensitive_text


class AdminLogBuffer(logging.Handler):
    """Small process-local diagnostic buffer; never reads unbounded stdout/journald."""

    def __init__(self, capacity: int = 1000) -> None:
        super().__init__(level=logging.INFO)
        self._records: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._next_id = 1
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = redact_sensitive_text(self.format(record))[:4000]
            with self._lock:
                self._records.append(
                    {
                        "id": self._next_id,
                        "level": record.levelname.lower(),
                        "source": record.name[:160],
                        "message": message,
                        "created_at": datetime.fromtimestamp(record.created, UTC).isoformat(),
                    }
                )
                self._next_id += 1
        except Exception:
            self.handleError(record)

    def list_records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._records]


_admin_log_buffer = AdminLogBuffer()


def get_admin_log_buffer() -> AdminLogBuffer:
    return _admin_log_buffer


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    root_logger = logging.getLogger()
    if _admin_log_buffer not in root_logger.handlers:
        root_logger.addHandler(_admin_log_buffer)
