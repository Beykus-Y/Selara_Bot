import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from selara.core.config import Settings
from selara.infrastructure.db import session as session_module
from selara.infrastructure.db.session import (
    DB_CONNECT_TIMEOUT,
    DB_POOL_TIMEOUT,
    DB_UNAVAILABLE,
    DatabaseAdmission,
    DatabaseFailure,
    classify_database_failure,
    create_engine,
)
from selara.presentation.middlewares import error_handler as error_module
from selara.presentation.middlewares.error_alert_config import configure_error_alerts
from selara.presentation.middlewares.error_handler import ErrorHandlerMiddleware


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "BOT_TOKEN": "token",
        "DATABASE_URL": "postgresql+asyncpg://selara:secret@db:5432/selara",
    }
    values.update(overrides)
    return Settings(**values)


def _capture_engine_arguments(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {}

    def fake_create_async_engine(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(session_module, "create_async_engine", fake_create_async_engine)
    return captured


def test_postgres_engine_bounds_connect_command_and_pool_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_engine_arguments(monkeypatch)

    create_engine(_settings(DB_CONNECT_TIMEOUT_SECONDS="7", DB_COMMAND_TIMEOUT_SECONDS="90", DB_POOL_TIMEOUT_SECONDS="12"))

    assert captured["pool_size"] == 10
    assert captured["max_overflow"] == 20
    assert captured["pool_timeout"] == 12
    assert captured["connect_args"] == {
        "timeout": 7,
        "command_timeout": 90,
        "server_settings": {"application_name": "selara-bot"},
    }


def test_other_drivers_get_no_postgres_only_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_engine_arguments(monkeypatch)

    create_engine(_settings(DATABASE_URL="sqlite+aiosqlite:///test.db"))

    assert captured["connect_args"] == {}


def test_admission_defaults_to_twenty_updates_under_a_thirty_connection_pool() -> None:
    admission = DatabaseAdmission.from_settings(_settings())

    assert admission.max_concurrent == 20


def test_admission_always_leaves_one_pooled_connection_free() -> None:
    admission = DatabaseAdmission.from_settings(_settings(DB_MAX_CONCURRENT_UPDATES="50"))

    assert admission.max_concurrent == 29


def test_pool_timeout_is_reported_at_pool_checkout() -> None:
    failure = classify_database_failure(PoolTimeoutError("QueuePool limit of size 10 overflow 20 reached"))

    assert failure == DatabaseFailure("db_pool_timeout", "pool_checkout")
    assert failure == DatabaseFailure(DB_POOL_TIMEOUT, "pool_checkout")


def test_lost_database_connection_is_reported_as_unavailable_at_query() -> None:
    error = OperationalError("SELECT 1", {}, Exception("server closed the connection unexpectedly"))

    assert classify_database_failure(error) == DatabaseFailure(DB_UNAVAILABLE, "query")


def _raised_from(filename: str, function_name: str, error_type: type[BaseException]) -> BaseException:
    """Raises from a function compiled under a driver's file name, so the traceback looks like the driver's."""
    namespace: dict[str, object] = {}
    source = f"def {function_name}():\n    raise {error_type.__name__}('driver failure')\n"
    exec(compile(source, filename, "exec"), namespace)
    try:
        namespace[function_name]()  # type: ignore[operator]
    except BaseException as exc:
        return exc
    raise AssertionError("the helper function did not raise")


def test_connect_timeout_inside_the_driver_is_reported_at_connect() -> None:
    error = _raised_from("/venv/lib/python3.13/site-packages/asyncpg/connection.py", "connect", TimeoutError)

    assert classify_database_failure(error) == DatabaseFailure(DB_CONNECT_TIMEOUT, "connect")


def test_query_timeout_inside_the_driver_is_reported_as_unavailable() -> None:
    error = _raised_from("/venv/lib/python3.13/site-packages/asyncpg/protocol/protocol.pyx", "execute", TimeoutError)

    assert classify_database_failure(error) == DatabaseFailure(DB_UNAVAILABLE, "query")


def test_errors_outside_the_database_layer_are_not_classified() -> None:
    assert classify_database_failure(TimeoutError("aiogram request timed out")) is None
    assert classify_database_failure(ValueError("bad input")) is None


@pytest.mark.asyncio
async def test_pool_exhaustion_alert_reaches_telegram_without_opening_a_pooled_session() -> None:
    error_module._recent_alerts.clear()
    configure_error_alerts(True, -100123)
    bot = SimpleNamespace(send_message=AsyncMock())
    opened_sessions: list[object] = []

    def refuse_session():
        opened_sessions.append(object())
        raise AssertionError("an alert for a database failure must not open a pooled session")

    event = SimpleNamespace(
        bot=bot,
        chat=SimpleNamespace(id=-1001, title="Смешные ребята"),
        from_user=SimpleNamespace(id=7, full_name="Илья"),
        text=None,
    )

    async def exhausted(_event, _data):
        raise PoolTimeoutError("QueuePool limit of size 10 overflow 20 reached, connection timed out, timeout 30.00")

    await ErrorHandlerMiddleware(refuse_session)(exhausted, event, {})
    await asyncio.gather(*list(error_module._pending_alert_tasks))

    assert opened_sessions == []
    bot.send_message.assert_awaited_once()
    alert = bot.send_message.await_args.kwargs["text"]
    assert "Категория БД: db_pool_timeout (стадия: pool_checkout)" in alert
