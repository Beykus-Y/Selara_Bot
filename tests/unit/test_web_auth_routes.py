from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import pytest

from selara.core.config import Settings
from selara.domain.entities import UserSnapshot
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


@dataclass
class AuthRouteState:
    settings: Settings
    user_from_code: UserSnapshot | None = None
    created_session_for: int | None = None
    revoked_session: bool = False


class FakeWebAuthRepo:
    def __init__(self, state: AuthRouteState) -> None:
        self._state = state

    async def purge_expired_state(self, *, now):
        _ = now
        return None

    async def consume_login_code(self, *, code_digest: str, now):
        _ = code_digest, now
        return self._state.user_from_code

    async def create_session(self, *, user_id: int, session_digest: str, expires_at, now):
        _ = session_digest, expires_at, now
        self._state.created_session_for = user_id
        return None

    async def revoke_session(self, *, session_digest: str, now):
        _ = session_digest, now
        self._state.revoked_session = True
        return None


class DummySession:
    async def commit(self) -> None:
        return None


class DummySessionFactory:
    def __call__(self):
        session = DummySession()

        class _Manager:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Manager()


@pytest.mark.asyncio
async def test_login_submit_returns_json_for_fetch_requests(monkeypatch) -> None:
    state = AuthRouteState(
        settings=_settings(),
        user_from_code=UserSnapshot(
            telegram_user_id=77,
            username="viewer",
            first_name="View",
            last_name="Er",
            is_bot=False,
        ),
    )
    monkeypatch.setattr(web_app_module, "SqlAlchemyWebAuthRepository", lambda session: FakeWebAuthRepo(state))

    app = web_app_module.create_web_app(settings=state.settings, session_factory=DummySessionFactory())
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        response = await client.post(
            "/login",
            content="code=123456",
            headers={
                "accept": "application/json",
                "content-type": "application/x-www-form-urlencoded",
                "x-requested-with": "fetch",
            },
        )
    finally:
        await client.aclose()
        await app.router.shutdown()

    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["redirect"].startswith("/app?flash=")
    assert state.created_session_for == 77
    assert state.settings.web_session_cookie_name in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_logout_returns_json_for_fetch_requests(monkeypatch) -> None:
    state = AuthRouteState(settings=_settings())
    monkeypatch.setattr(web_app_module, "SqlAlchemyWebAuthRepository", lambda session: FakeWebAuthRepo(state))

    app = web_app_module.create_web_app(settings=state.settings, session_factory=DummySessionFactory())
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(state.settings.web_session_cookie_name, "session-token")
    try:
        response = await client.post(
            "/logout",
            headers={
                "accept": "application/json",
                "x-requested-with": "fetch",
            },
        )
    finally:
        await client.aclose()
        await app.router.shutdown()

    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["redirect"].startswith("/login?flash=")
    assert state.revoked_session is True
    assert "Max-Age=0" in response.headers.get("set-cookie", "")
