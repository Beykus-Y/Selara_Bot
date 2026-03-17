from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from selara.core.config import Settings
from selara.infrastructure.db.models import ChatModel, MarriageModel, UserChatActivityModel, UserModel
from selara.web import app as web_app_module


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


class FakeAdminAuthRepo:
    def __init__(self, admin_user_id: int | None) -> None:
        self._admin_user_id = admin_user_id

    async def get_admin_by_session(self, *, session_token: str, now: datetime, touch: bool):
        _ = session_token, now, touch
        return self._admin_user_id


class FakeExecuteResult:
    def __init__(self, *, rows=None, scalar_value=None) -> None:
        self._rows = list(rows or [])
        self._scalar_value = scalar_value

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar_value

    def scalar_one(self):
        return self._scalar_value


class FakeSession:
    def __init__(self, *, execute_results=None, records=None) -> None:
        self._execute_results = list(execute_results or [])
        self._records = dict(records or {})
        self.execute_calls = []
        self.get_calls = []
        self.commit_calls = 0

    async def execute(self, stmt):
        self.execute_calls.append(stmt)
        if not self._execute_results:
            raise AssertionError("Unexpected execute call")
        return self._execute_results.pop(0)

    async def get(self, model_class, record_id):
        self.get_calls.append((model_class, record_id))
        return self._records.get((model_class, record_id))

    async def commit(self) -> None:
        self.commit_calls += 1


class QueueSessionFactory:
    def __init__(self, *sessions: FakeSession) -> None:
        self._sessions = list(sessions)

    def __call__(self):
        if not self._sessions:
            raise AssertionError("Unexpected session_factory call")
        session = self._sessions.pop(0)

        class _Manager:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Manager()


@pytest.mark.asyncio
async def test_admin_page_lists_all_mapped_tables(monkeypatch) -> None:
    settings = _settings()
    auth_session = FakeSession(
        execute_results=[
            FakeExecuteResult(rows=[]),
            FakeExecuteResult(scalar_value=0),
        ]
    )
    monkeypatch.setattr(
        web_app_module,
        "SqlAlchemyAdminAuthRepository",
        lambda session: FakeAdminAuthRepo(settings.admin_user_id),
    )

    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(auth_session),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        response = await client.get("/app/admin")
    finally:
        await client.aclose()
        await app.router.shutdown()

    assert response.status_code == 200
    assert "Активность и пользователи" in response.text
    assert "Экономика" in response.text
    assert "Дневная активность пользователей" in response.text
    assert "Использование действий отношений" in response.text
    assert "Коды входа веб-панели" in response.text
    assert 'action="/app/admin/request-backup"' in response.text


@pytest.mark.asyncio
async def test_admin_table_page_renders_with_column_filters(monkeypatch) -> None:
    settings = _settings()
    auth_session = FakeSession()
    data_session = FakeSession(
        execute_results=[
            FakeExecuteResult(
                rows=[
                    UserModel(
                        telegram_user_id=101,
                        username="alice",
                        first_name="Alice",
                        last_name=None,
                        is_bot=False,
                    )
                ]
            ),
            FakeExecuteResult(scalar_value=1),
            FakeExecuteResult(
                rows=[
                    UserModel(
                        telegram_user_id=101,
                        username="alice",
                        first_name="Alice",
                        last_name=None,
                        is_bot=False,
                    )
                ]
            ),
        ]
    )
    monkeypatch.setattr(
        web_app_module,
        "SqlAlchemyAdminAuthRepository",
        lambda session: FakeAdminAuthRepo(settings.admin_user_id),
    )

    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(auth_session, data_session),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        response = await client.get("/app/admin/table/users?username=alice")
    finally:
        await client.aclose()
        await app.router.shutdown()

    assert response.status_code == 200
    assert "alice" in response.text
    assert "Пользователи" in response.text
    assert "Alice" in response.text
    assert len(data_session.execute_calls) == 3


@pytest.mark.asyncio
async def test_admin_table_page_shows_reference_labels(monkeypatch) -> None:
    settings = _settings()
    auth_session = FakeSession()
    marriage = MarriageModel(
        id=2,
        user_low_id=101,
        user_high_id=202,
        chat_id=-100500,
        is_active=True,
        married_at=datetime(2026, 2, 27, 9, 6),
        ended_at=None,
        ended_by_user_id=999,
        ended_reason=None,
        affection_points=5,
        last_affection_at=None,
        last_affection_by_user_id=None,
        updated_at=datetime(2026, 3, 8, 9, 6),
    )
    data_session = FakeSession(
        execute_results=[
            FakeExecuteResult(rows=[marriage]),
            FakeExecuteResult(scalar_value=1),
            FakeExecuteResult(
                rows=[
                    UserModel(telegram_user_id=101, username="alice", first_name="Alice", last_name=None, is_bot=False),
                    UserModel(telegram_user_id=202, username=None, first_name="Bob", last_name="Stone", is_bot=False),
                ]
            ),
            FakeExecuteResult(
                rows=[
                    ChatModel(telegram_chat_id=-100500, type="supergroup", title="Test Marriage Chat"),
                ]
            ),
        ]
    )
    monkeypatch.setattr(
        web_app_module,
        "SqlAlchemyAdminAuthRepository",
        lambda session: FakeAdminAuthRepo(settings.admin_user_id),
    )

    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(auth_session, data_session),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        response = await client.get("/app/admin/table/marriages")
    finally:
        await client.aclose()
        await app.router.shutdown()

    assert response.status_code == 200
    assert "Alice" in response.text
    assert "Bob Stone" in response.text
    assert "Test Marriage Chat" in response.text
    assert "не найден в users" in response.text


@pytest.mark.asyncio
async def test_admin_table_page_builds_composite_pk_links(monkeypatch) -> None:
    settings = _settings()
    auth_session = FakeSession()
    activity = UserChatActivityModel(
        chat_id=30,
        user_id=10,
        message_count=5,
        is_active_member=True,
        last_seen_at=datetime(2026, 3, 8, 9, 6),
        display_name_override=None,
        title_prefix=None,
        created_at=datetime(2026, 3, 8, 9, 6),
        updated_at=datetime(2026, 3, 8, 9, 6),
    )
    data_session = FakeSession(
        execute_results=[
            FakeExecuteResult(rows=[activity]),
            FakeExecuteResult(scalar_value=1),
            FakeExecuteResult(rows=[UserModel(telegram_user_id=10, username="alice", first_name="Alice", last_name=None, is_bot=False)]),
            FakeExecuteResult(rows=[ChatModel(telegram_chat_id=30, type="group", title="Family Chat")]),
        ]
    )
    monkeypatch.setattr(
        web_app_module,
        "SqlAlchemyAdminAuthRepository",
        lambda session: FakeAdminAuthRepo(settings.admin_user_id),
    )

    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(auth_session, data_session),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        response = await client.get("/app/admin/table/user_chat_activity")
    finally:
        await client.aclose()
        await app.router.shutdown()

    assert response.status_code == 200
    assert "/app/admin/table/user_chat_activity/edit?chat_id=30&amp;user_id=10" in response.text
    assert "/app/admin/table/user_chat_activity/delete?chat_id=30&amp;user_id=10" in response.text


@pytest.mark.asyncio
async def test_admin_table_edit_page_reads_record_id_from_query(monkeypatch) -> None:
    settings = _settings()
    marriage = MarriageModel(
        id=2,
        user_low_id=10,
        user_high_id=20,
        chat_id=30,
        is_active=True,
        married_at=datetime(2026, 2, 27, 9, 6),
        ended_at=None,
        ended_by_user_id=None,
        ended_reason=None,
        affection_points=5,
        last_affection_at=None,
        last_affection_by_user_id=None,
        updated_at=datetime(2026, 3, 8, 9, 6),
    )
    data_session = FakeSession(
        execute_results=[
            FakeExecuteResult(
                rows=[
                    UserModel(telegram_user_id=10, username="alice", first_name="Alice", last_name=None, is_bot=False),
                    UserModel(telegram_user_id=20, username=None, first_name="Bob", last_name="Stone", is_bot=False),
                ]
            ),
            FakeExecuteResult(rows=[ChatModel(telegram_chat_id=30, type="group", title="Family Chat")]),
        ],
        records={(MarriageModel, 2): marriage},
    )
    monkeypatch.setattr(
        web_app_module,
        "SqlAlchemyAdminAuthRepository",
        lambda session: FakeAdminAuthRepo(settings.admin_user_id),
    )

    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(data_session),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        response = await client.get("/app/admin/table/marriages/edit?id=2")
    finally:
        await client.aclose()
        await app.router.shutdown()

    assert response.status_code == 200
    assert 'name="id" value="2"' in response.text
    assert 'step="1"' in response.text
    assert "UTC" in response.text
    assert "Alice" in response.text
    assert "Bob Stone" in response.text
    assert "Family Chat" in response.text
    assert data_session.get_calls == [(MarriageModel, 2)]


@pytest.mark.asyncio
async def test_admin_table_update_supports_composite_primary_keys(monkeypatch) -> None:
    settings = _settings()
    auth_session = FakeSession()
    activity = UserChatActivityModel(
        chat_id=30,
        user_id=10,
        message_count=5,
        is_active_member=True,
        last_seen_at=datetime(2026, 3, 8, 9, 6),
        display_name_override=None,
        title_prefix=None,
        created_at=datetime(2026, 3, 8, 9, 6),
        updated_at=datetime(2026, 3, 8, 9, 6),
    )
    data_session = FakeSession(records={(UserChatActivityModel, (30, 10)): activity})
    log_chat_action_mock = AsyncMock()

    monkeypatch.setattr(
        web_app_module,
        "SqlAlchemyAdminAuthRepository",
        lambda session: FakeAdminAuthRepo(settings.admin_user_id),
    )
    monkeypatch.setattr(web_app_module, "SqlAlchemyActivityRepository", lambda session: object())
    monkeypatch.setattr(web_app_module, "log_chat_action", log_chat_action_mock)

    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(auth_session, data_session),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        response = await client.post(
            "/app/admin/table/user_chat_activity/update",
            content="chat_id=30&user_id=10&message_count=12&is_active_member=false",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    finally:
        await client.aclose()
        await app.router.shutdown()

    assert response.status_code == 303
    assert activity.message_count == 12
    assert activity.is_active_member is False
    assert data_session.get_calls == [(UserChatActivityModel, (30, 10))]
    log_chat_action_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_table_update_converts_blank_datetime_fields_to_none(monkeypatch) -> None:
    settings = _settings()
    auth_session = FakeSession()
    marriage = MarriageModel(
        id=2,
        user_low_id=10,
        user_high_id=20,
        chat_id=30,
        is_active=True,
        married_at=datetime(2026, 1, 1, 8, 0),
        ended_at=datetime(2026, 2, 1, 8, 0),
        ended_by_user_id=None,
        ended_reason="old",
        affection_points=5,
        last_affection_at=datetime(2026, 2, 2, 8, 0),
        last_affection_by_user_id=None,
        updated_at=datetime(2026, 2, 3, 8, 0),
    )
    data_session = FakeSession(records={(MarriageModel, 2): marriage})
    log_chat_action_mock = AsyncMock()

    monkeypatch.setattr(
        web_app_module,
        "SqlAlchemyAdminAuthRepository",
        lambda session: FakeAdminAuthRepo(settings.admin_user_id),
    )
    monkeypatch.setattr(web_app_module, "SqlAlchemyActivityRepository", lambda session: object())
    monkeypatch.setattr(web_app_module, "log_chat_action", log_chat_action_mock)

    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(auth_session, data_session),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        response = await client.post(
            "/app/admin/table/marriages/update",
            content=(
                "id=2&married_at=2026-02-27T09%3A06&ended_at=&ended_reason="
                "&last_affection_at=&updated_at=2026-03-08T09%3A06"
            ),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    finally:
        await client.aclose()
        await app.router.shutdown()

    assert response.status_code == 303
    assert marriage.married_at == datetime(2026, 2, 27, 9, 6, tzinfo=timezone.utc)
    assert marriage.ended_at is None
    assert marriage.ended_reason == ""
    assert marriage.last_affection_at is None
    assert marriage.updated_at == datetime(2026, 3, 8, 9, 6, tzinfo=timezone.utc)
    log_chat_action_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_request_backup_calls_runtime_backup(monkeypatch) -> None:
    settings = _settings()
    auth_session = FakeSession()
    backup_mock = AsyncMock()

    monkeypatch.setattr(
        web_app_module,
        "SqlAlchemyAdminAuthRepository",
        lambda session: FakeAdminAuthRepo(settings.admin_user_id),
    )
    monkeypatch.setattr(web_app_module, "send_daily_backup", backup_mock)

    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(auth_session),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        response = await client.post("/app/admin/request-backup")
    finally:
        await client.aclose()
        await app.router.shutdown()

    assert response.status_code == 303
    assert response.headers["location"].startswith("/app/admin?flash=")
    backup_mock.assert_awaited_once()
