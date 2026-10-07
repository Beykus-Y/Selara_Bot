"""Real-backend CRUD coverage for the generic DB explorer (admin_table.html).

Unlike the FakeSession-based route tests in test_web_admin_routes.py, these run
against a genuine sqlite database through the real FastAPI app (same pattern
used for broadcast send in test_web_admin_broadcasts.py), so an update/delete
here proves an actual row was actually mutated/removed — not just that a mock
was called with the right arguments.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.core.config import Settings
from selara.core.web_auth import digest_admin_session_token
from selara.infrastructure.db.admin_auth import SqlAlchemyAdminAuthRepository
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import ChatAuditLogModel, UserFeatureRequestModel, UserModel
from selara.web import app as web_app_module

pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
            "BOT_USERNAME": "selara_test_bot",
            "WEB_AUTH_SECRET": "secret",
            "WEB_BASE_URL": "http://127.0.0.1:8080",
            "ADMIN_PASSWORD": "admin-secret",
            "ADMIN_USER_ID": 77,
        }
    )


async def _seeded_client(session_token: str = "admin-crud-session"):
    settings = _settings()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        auth_repo = SqlAlchemyAdminAuthRepository(session)
        await auth_repo.create_session(
            admin_user_id=settings.admin_user_id,
            session_token=digest_admin_session_token(
                secret=settings.resolved_web_auth_secret, token=session_token
            ),
            expires_at=now + timedelta(hours=2),
            now=now,
        )
        session.add(UserModel(telegram_user_id=901, username="crud_author", first_name="Crud", last_name=None))
        session.add(
            UserFeatureRequestModel(
                id=501,
                user_id=901,
                title="Изначальный заголовок заявки",
                details="Исходное описание.",
                status="open",
            )
        )
        await session.commit()

    app = web_app_module.create_web_app(settings=settings, session_factory=session_factory)
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.admin_session_cookie_name, session_token)
    return client, engine, session_factory


@pytest.mark.asyncio
async def test_admin_table_update_persists_real_row_change() -> None:
    client, engine, session_factory = await _seeded_client()
    try:
        response = await client.post(
            "/app/admin/table/user_feature_requests/update",
            data={"id": "501", "status": "done", "title": "Обновлённый заголовок заявки"},
            follow_redirects=False,
        )
    finally:
        await client.aclose()

    assert response.status_code == 303
    assert response.headers["location"].startswith("/app/admin/table/user_feature_requests")

    async with session_factory() as session:
        row = await session.get(UserFeatureRequestModel, 501)
        assert row is not None
        assert row.status == "done"
        assert row.title == "Обновлённый заголовок заявки"
        assert row.details == "Исходное описание."  # untouched field survives

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_table_update_rejects_unauthenticated_request_without_mutating() -> None:
    client, engine, session_factory = await _seeded_client()
    client.cookies.clear()
    try:
        response = await client.post(
            "/app/admin/table/user_feature_requests/update",
            data={"id": "501", "status": "done"},
            follow_redirects=False,
        )
    finally:
        await client.aclose()

    assert response.status_code == 303
    assert response.headers["location"] == "/app/admin/login"

    async with session_factory() as session:
        row = await session.get(UserFeatureRequestModel, 501)
        assert row is not None
        assert row.status == "open"

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_table_delete_removes_real_row() -> None:
    client, engine, session_factory = await _seeded_client()
    try:
        response = await client.post(
            "/app/admin/table/user_feature_requests/delete",
            data={"id": "501"},
            follow_redirects=False,
        )
    finally:
        await client.aclose()

    assert response.status_code == 303
    assert response.headers["location"].startswith("/app/admin/table/user_feature_requests")

    async with session_factory() as session:
        row = await session.get(UserFeatureRequestModel, 501)
        assert row is None
        remaining = (await session.execute(select(UserFeatureRequestModel))).scalars().all()
        assert remaining == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_table_delete_missing_record_reports_error_without_crashing() -> None:
    client, engine, session_factory = await _seeded_client()
    try:
        response = await client.post(
            "/app/admin/table/user_feature_requests/delete",
            data={"id": "999999"},
            follow_redirects=False,
        )
    finally:
        await client.aclose()

    assert response.status_code == 303
    assert "error=" in response.headers["location"]

    async with session_factory() as session:
        row = await session.get(UserFeatureRequestModel, 501)
        assert row is not None  # the real, existing row is untouched

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_table_edit_page_reflects_real_row_after_update() -> None:
    client, engine, session_factory = await _seeded_client()
    try:
        await client.post(
            "/app/admin/table/user_feature_requests/update",
            data={"id": "501", "title": "Заголовок для проверки формы"},
            follow_redirects=False,
        )
        edit_response = await client.get(
            "/app/admin/table/user_feature_requests/edit",
            params={"id": "501"},
        )
    finally:
        await client.aclose()

    assert edit_response.status_code == 200
    assert "Заголовок для проверки формы" in edit_response.text

    await engine.dispose()


async def _audit_entries(session_factory, action_code: str) -> list[tuple[int | None, str]]:
    """Read audit entries in a fresh session opened after the request.

    A new session guarantees the entry came from a durable commit, not from a
    flush that is still sitting in the (now closed) request transaction.
    """
    async with session_factory() as session:
        rows = (
            (await session.execute(select(ChatAuditLogModel).where(ChatAuditLogModel.action_code == action_code)))
            .scalars()
            .all()
        )
        return [(row.actor_user_id, row.description) for row in rows]


@pytest.mark.asyncio
async def test_admin_table_update_commits_mutation_and_audit_atomically() -> None:
    client, engine, session_factory = await _seeded_client()
    try:
        response = await client.post(
            "/app/admin/table/user_feature_requests/update",
            data={"id": "501", "status": "done", "title": "Обновлённый заголовок заявки"},
            follow_redirects=False,
        )
    finally:
        await client.aclose()

    assert response.status_code == 303

    async with session_factory() as session:
        row = await session.get(UserFeatureRequestModel, 501)
        assert row is not None
        assert row.status == "done"

    entries = await _audit_entries(session_factory, "admin_record_updated")
    assert len(entries) == 1
    actor_user_id, description = entries[0]
    assert actor_user_id == 77
    assert "updated record id=501" in description
    assert "user_feature_requests" in description

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_table_delete_commits_deletion_and_audit_atomically() -> None:
    client, engine, session_factory = await _seeded_client()
    try:
        response = await client.post(
            "/app/admin/table/user_feature_requests/delete",
            data={"id": "501"},
            follow_redirects=False,
        )
    finally:
        await client.aclose()

    assert response.status_code == 303

    async with session_factory() as session:
        row = await session.get(UserFeatureRequestModel, 501)
        assert row is None

    entries = await _audit_entries(session_factory, "admin_record_deleted")
    assert len(entries) == 1
    actor_user_id, description = entries[0]
    assert actor_user_id == 77
    assert "deleted record id=501" in description
    assert "user_feature_requests" in description

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_table_api_update_commits_mutation_and_audit_atomically() -> None:
    client, engine, session_factory = await _seeded_client()
    try:
        response = await client.post(
            "/api/admin/table/user_feature_requests/update",
            data={"id": "501", "status": "done"},
            follow_redirects=False,
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert response.json()["ok"] is True

    async with session_factory() as session:
        row = await session.get(UserFeatureRequestModel, 501)
        assert row is not None
        assert row.status == "done"

    entries = await _audit_entries(session_factory, "admin_record_updated")
    assert len(entries) == 1
    assert entries[0][0] == 77

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_table_api_delete_commits_deletion_and_audit_atomically() -> None:
    client, engine, session_factory = await _seeded_client()
    try:
        response = await client.post(
            "/api/admin/table/user_feature_requests/delete",
            data={"id": "501"},
            follow_redirects=False,
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert response.json()["ok"] is True

    async with session_factory() as session:
        row = await session.get(UserFeatureRequestModel, 501)
        assert row is None

    entries = await _audit_entries(session_factory, "admin_record_deleted")
    assert len(entries) == 1
    assert entries[0][0] == 77

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_table_update_rolls_back_mutation_and_audit_when_audit_write_fails(
    monkeypatch,
) -> None:
    client, engine, session_factory = await _seeded_client()

    async def _failing_log_chat_action(*args, **kwargs):
        raise RuntimeError("audit insert failed")

    monkeypatch.setattr(web_app_module, "log_chat_action", _failing_log_chat_action)
    try:
        with pytest.raises(RuntimeError):
            await client.post(
                "/app/admin/table/user_feature_requests/update",
                data={"id": "501", "status": "done"},
                follow_redirects=False,
            )
    finally:
        await client.aclose()

    # Ни мутации, ни audit-записи: обе части одной транзакции откатились.
    async with session_factory() as session:
        row = await session.get(UserFeatureRequestModel, 501)
        assert row is not None
        assert row.status == "open"
        remaining = (await session.execute(select(ChatAuditLogModel))).scalars().all()
        assert remaining == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_table_api_delete_rolls_back_deletion_and_audit_when_audit_write_fails(
    monkeypatch,
) -> None:
    client, engine, session_factory = await _seeded_client()

    async def _failing_log_chat_action(*args, **kwargs):
        raise RuntimeError("audit insert failed")

    monkeypatch.setattr(web_app_module, "log_chat_action", _failing_log_chat_action)
    try:
        with pytest.raises(RuntimeError):
            await client.post(
                "/api/admin/table/user_feature_requests/delete",
                data={"id": "501"},
                follow_redirects=False,
            )
    finally:
        await client.aclose()

    # Ни удаления, ни audit-записи: обе части одной транзакции откатились.
    async with session_factory() as session:
        row = await session.get(UserFeatureRequestModel, 501)
        assert row is not None
        assert row.status == "open"
        remaining = (await session.execute(select(ChatAuditLogModel))).scalars().all()
        assert remaining == []

    await engine.dispose()
