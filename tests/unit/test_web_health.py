from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from selara.core.config import Settings
from selara.web.app import create_web_app
import selara.web.app as web_app_module
from selara.web import readiness


@pytest.fixture
def healthy_runtime(monkeypatch):
    monkeypatch.setattr(web_app_module, "redis_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(web_app_module, "GAME_STORE", SimpleNamespace(redis_recovery_state="connected"))
    monkeypatch.setattr(web_app_module, "get_bot_polling_runtime_state", lambda: {
        "running": True, "heartbeat_at": datetime.now(timezone.utc),
    })


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost/test",
        }
    )


class _SessionFactory:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def __call__(self):
        error = self.error

        class _Session:
            async def execute(self, statement):
                _ = statement
                if error is not None:
                    raise error
                return SimpleNamespace()

        class _Manager:
            async def __aenter__(self):
                return _Session()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _Manager()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
async def test_healthz_checks_database_readiness(healthy_runtime, path) -> None:
    app = create_web_app(settings=_settings(), session_factory=_SessionFactory())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(path)
    await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": True, "redis": True, "polling": True, "game_store_redis": "connected"},
    }
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_healthz_returns_service_unavailable_when_database_is_down(healthy_runtime) -> None:
    app = create_web_app(settings=_settings(), session_factory=_SessionFactory(RuntimeError("database down")))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/healthz")
    await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": False, "redis": True, "polling": True, "game_store_redis": "connected"},
    }


@pytest.mark.parametrize("path", ["/readyz", "/healthz"])
@pytest.mark.parametrize("failure", ["polling-dead", "polling-stale", "polling-no-heartbeat", "redis-down"])
async def test_readiness_requires_polling_and_redis(healthy_runtime, monkeypatch, path, failure):
    if failure.startswith("polling"):
        monkeypatch.setattr(web_app_module, "get_bot_polling_runtime_state", lambda: {
            "running": failure != "polling-dead",
            "heartbeat_at": None if failure == "polling-no-heartbeat" else datetime.now(timezone.utc) - timedelta(seconds=46),
        })
    else:
        monkeypatch.setattr(web_app_module, "redis_ready", AsyncMock(return_value=False))
    app = create_web_app(settings=_settings(), session_factory=_SessionFactory())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as client:
        response = await client.get(path)
    await getattr(app.router, "shutdown", app.router._shutdown)()
    assert response.status_code == 503
    assert response.json()["checks"]["polling" if failure.startswith("polling") else "redis"] is False


@pytest.mark.parametrize("path", ["/readyz", "/healthz"])
@pytest.mark.parametrize("store_state", ["degraded", "recovering"])
async def test_degraded_game_store_is_reported_without_failing_readiness(healthy_runtime, monkeypatch, path, store_state):
    monkeypatch.setattr(web_app_module, "GAME_STORE", SimpleNamespace(redis_recovery_state=store_state))
    app = create_web_app(settings=_settings(), session_factory=_SessionFactory())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as client:
        response = await client.get(path)
    await getattr(app.router, "shutdown", app.router._shutdown)()
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": True, "redis": True, "polling": True, "game_store_redis": store_state},
    }


async def test_liveness_does_not_probe_dependencies(monkeypatch):
    database = AsyncMock(side_effect=AssertionError("Liveness must not access DB"))
    redis = AsyncMock(side_effect=AssertionError("Liveness must not access Redis"))
    monkeypatch.setattr(web_app_module, "database_ready", database)
    monkeypatch.setattr(web_app_module, "redis_ready", redis)
    app = create_web_app(settings=_settings(), session_factory=_SessionFactory())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as client:
        response = await client.get("/livez")
    await getattr(app.router, "shutdown", app.router._shutdown)()
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    database.assert_not_awaited()
    redis.assert_not_awaited()


@pytest.mark.parametrize("offset,expected", [(0, True), (-45, True), (-46, False), (1, False)])
def test_polling_heartbeat_age_boundary(offset, expected):
    now = datetime.now(timezone.utc)
    assert readiness.polling_ready({"running": True, "heartbeat_at": now + timedelta(seconds=offset)}, now=now) is expected


async def test_redis_failure_closes_connection_without_leaking_credentials(monkeypatch):
    client = SimpleNamespace(ping=AsyncMock(side_effect=RuntimeError("secret redis password")), aclose=AsyncMock())
    monkeypatch.setattr(readiness.Redis, "from_url", lambda *a, **kw: client)
    assert await readiness.redis_ready("redis://user:secret@localhost/0") is False
    client.aclose.assert_awaited_once()


async def test_probe_timeout_is_not_a_success(monkeypatch):
    def expired_timeout(_seconds):
        class Expired:
            async def __aenter__(self):
                raise TimeoutError

            async def __aexit__(self, *args):
                return False
        return Expired()

    monkeypatch.setattr(readiness.asyncio, "timeout", expired_timeout)
    assert await readiness.database_ready(_SessionFactory()) is False
    assert await readiness.redis_ready("redis://localhost/0") is False
