"""Regression guard for docs/WEB_UI_MODERNIZATION_TODO.md stage 3
"Администраторская документация": "Доступ только в подходящем защищённом
контексте."

Found while working on the RP-actions/deep-links slice: /app/docs/admin used
the exact same context-builder shape as the now-intentionally-public
/app/docs/user (redirect_path was always None), so it was just as reachable
without any session — never actually gated behind login like the rest of the
"/app" cabinet (e.g. /app itself, per _build_home_page_context). Fixed to
require a regular user web session, the same bar as /app — not the separate
ADMIN_PASSWORD-gated /app/admin control panel, which is a different concept
(bot-wide operator tools vs. per-chat admin documentation).
"""

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


class _NoCommitSession:
    async def commit(self) -> None:
        return None


class _SessionFactory:
    def __call__(self):
        session = _NoCommitSession()

        class _Manager:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Manager()


@pytest.mark.asyncio
async def test_admin_docs_page_redirects_to_login_without_a_session_cookie() -> None:
    app = web_app_module.create_web_app(settings=_settings(), session_factory=_SessionFactory())
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False)
    try:
        response = await client.get("/app/docs/admin")
    finally:
        await client.aclose()

    assert response.status_code in (302, 303, 307)
    assert response.headers["location"].startswith("/login")


@pytest.mark.asyncio
async def test_admin_docs_page_renders_with_a_valid_session_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    user = UserSnapshot(
        telegram_user_id=77,
        username="viewer",
        first_name="View",
        last_name="Er",
        is_bot=False,
    )

    class FakeWebAuthRepo:
        async def get_user_by_session(self, *, session_digest: str, now, touch: bool):
            return user

    monkeypatch.setattr(web_app_module, "SqlAlchemyWebAuthRepository", lambda session: FakeWebAuthRepo())

    app = web_app_module.create_web_app(settings=_settings(), session_factory=_SessionFactory())
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(_settings().web_session_cookie_name, "session-token")
    try:
        response = await client.get("/app/docs/admin")
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert "Документация администратора" in response.text
