from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from selara.core.config import Settings
from selara.web.app import create_web_app


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
async def test_healthz_checks_database_readiness() -> None:
    app = create_web_app(settings=_settings(), session_factory=_SessionFactory())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/healthz")
    await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_healthz_returns_service_unavailable_when_database_is_down() -> None:
    app = create_web_app(settings=_settings(), session_factory=_SessionFactory(RuntimeError("database down")))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/healthz")
    await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
