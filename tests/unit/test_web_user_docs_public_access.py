"""Regression guard for docs/WEB_UI_MODERNIZATION_TODO.md stage 3 "Пользовательская
документация": "Публичный доступ без admin session."

_load_user_from_request only queries the DB when a session cookie is present
(selara/web/app.py:1203-1212); /app/docs/user never redirects based on the
result. This proves the real route over real ASGI dispatch, not just by
reading the code — a session_factory that raises on use is enough to prove no
DB round-trip (and therefore no auth check) happens for an anonymous request.
"""

import httpx
import pytest

from selara.core.config import Settings
from selara.web import app as web_app_module


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
            "BOT_USERNAME": "selara_test_bot",
            "WEB_AUTH_SECRET": "secret",
            "WEB_BASE_URL": "http://127.0.0.1:8080",
        }
    )


class _NoCommitSession:
    async def commit(self) -> None:
        return None


class _AnonymousSessionFactory:
    """A session that would fail if any auth/db lookup were attempted for an
    anonymous request — the docs route must never need one when there's no
    session cookie."""

    def __call__(self):
        session = _NoCommitSession()

        class _Manager:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Manager()


@pytest.mark.asyncio
async def test_user_docs_page_is_reachable_without_a_session_cookie() -> None:
    app = web_app_module.create_web_app(settings=_settings(), session_factory=_AnonymousSessionFactory())
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        response = await client.get("/app/docs/user")
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert "Документация пользователя" in response.text
    # No admin-session cookie was sent and none should be required or set.
    assert "set-cookie" not in {key.lower() for key in response.headers.keys()}


@pytest.mark.asyncio
async def test_user_docs_page_api_is_reachable_without_a_session_cookie() -> None:
    app = web_app_module.create_web_app(settings=_settings(), session_factory=_AnonymousSessionFactory())
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        response = await client.get("/api/app/docs/user")
    finally:
        await client.aclose()

    assert response.status_code == 200
