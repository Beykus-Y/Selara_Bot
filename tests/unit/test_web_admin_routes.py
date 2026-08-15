from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from selara.core.config import Settings
from selara.core.web_auth import digest_admin_session_token
from selara.infrastructure.db.models import (
    ChatModel,
    MarriageModel,
    MessageArchiveModel,
    UserChatActivityModel,
    UserFeatureRequestModel,
    UserModel,
)
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


def _location_query_value(location: str, key: str) -> str | None:
    return parse_qs(urlsplit(location).query).get(key, [None])[0]


class FakeAdminAuthRepo:
    def __init__(self, admin_user_id: int | None) -> None:
        self._admin_user_id = admin_user_id

    async def get_admin_by_session(self, *, session_token: str, now: datetime, touch: bool):
        _ = session_token, now, touch
        return self._admin_user_id


class CapturingAdminAuthRepo:
    def __init__(self) -> None:
        self.created_session_token: str | None = None

    async def purge_expired_state(self, *, now: datetime) -> None:
        _ = now

    async def create_session(
        self,
        *,
        admin_user_id: int,
        session_token: str,
        expires_at: datetime,
        now: datetime,
    ) -> None:
        _ = admin_user_id, expires_at, now
        self.created_session_token = session_token


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


class FakeArchivePhotoBot:
    instances: list["FakeArchivePhotoBot"] = []

    def __init__(self, token: str) -> None:
        self.token = token
        self.session = SimpleNamespace(close=AsyncMock())
        self.download = AsyncMock(side_effect=self._download)
        self.__class__.instances.append(self)

    @staticmethod
    async def _download(file_id: str, *, destination) -> object:
        assert file_id == "photo-large"
        destination.write(b"\xff\xd8archive-preview\xff\xd9")
        return destination


class FailingArchivePhotoBot(FakeArchivePhotoBot):
    instances: list["FailingArchivePhotoBot"] = []

    @staticmethod
    async def _download(file_id: str, *, destination) -> object:
        _ = file_id, destination
        raise RuntimeError("Telegram file expired")


@pytest.mark.parametrize(
    ("message_type", "raw_message_json", "expected_title", "expected_facts"),
    [
        (
            "photo",
            {"photo": [{"width": 320, "height": 180, "file_size": 2048}]},
            "Фото",
            ["320×180", "2.0 КБ"],
        ),
        (
            "document",
            {
                "document": {
                    "file_name": "report.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 2 * 1024 * 1024,
                }
            },
            "Документ",
            ["report.pdf", "application/pdf", "2.0 МБ"],
        ),
        (
            "audio",
            {"audio": {"title": "Track", "performer": "Artist", "duration": 125}},
            "Аудио",
            ["Artist — Track", "2:05"],
        ),
        (
            "sticker",
            {"sticker": {"emoji": "✨", "set_name": "Selara", "is_animated": True}},
            "Стикер",
            ["✨", "Набор: Selara", "Анимированный"],
        ),
    ],
)
def test_admin_archive_builds_safe_media_metadata(
    message_type: str,
    raw_message_json: dict[str, object],
    expected_title: str,
    expected_facts: list[str],
) -> None:
    media_info = web_app_module._admin_archive_media_info(
        message_type=message_type,
        raw_message_json=raw_message_json,
    )

    assert media_info is not None
    assert media_info["title"] == expected_title
    assert media_info["facts"] == expected_facts


def test_admin_archive_does_not_create_media_card_for_plain_text() -> None:
    assert (
        web_app_module._admin_archive_media_info(
            message_type="text",
            raw_message_json={"text": "hello"},
        )
        is None
    )


def test_admin_archive_photo_reference_uses_largest_safe_photo_without_exposing_id() -> None:
    raw_message_json = {
        "photo": [
            {"file_id": "photo-small", "width": 90, "height": 90, "file_size": 512},
            {
                "file_id": "photo-large",
                "width": 1280,
                "height": 720,
                "file_size": 2048,
            },
        ]
    }

    reference = web_app_module._admin_archive_photo_reference(raw_message_json)
    media_info = web_app_module._admin_archive_media_info(
        message_type="photo",
        raw_message_json=raw_message_json,
    )

    assert reference == {"file_id": "photo-large", "file_size": 2048}
    assert media_info is not None
    assert "photo-large" not in repr(media_info)


@pytest.mark.asyncio
async def test_admin_archive_photo_endpoint_requires_admin_and_streams_private_image(
    monkeypatch,
) -> None:
    settings = _settings()
    photo = MessageArchiveModel(
        id=31,
        chat_id=-100500,
        user_id=101,
        telegram_message_id=7001,
        snapshot_kind="created",
        snapshot_at=datetime(2026, 4, 8, 18, 25, tzinfo=timezone.utc),
        sent_at=datetime(2026, 4, 8, 18, 25, tzinfo=timezone.utc),
        edited_at=None,
        message_type="photo",
        text=None,
        caption="Preview",
        raw_message_json={
            "photo": [
                {"file_id": "photo-small", "file_size": 512},
                {"file_id": "photo-large", "file_size": 2048},
            ]
        },
        snapshot_hash="photo-hash",
        created_at=datetime(2026, 4, 8, 18, 25, tzinfo=timezone.utc),
    )
    authorized_session = FakeSession(records={(MessageArchiveModel, photo.id): photo})
    unauthorized_session = FakeSession(records={(MessageArchiveModel, photo.id): photo})
    monkeypatch.setattr(
        web_app_module,
        "SqlAlchemyAdminAuthRepository",
        lambda session: FakeAdminAuthRepo(settings.admin_user_id),
    )
    FakeArchivePhotoBot.instances.clear()
    monkeypatch.setattr(web_app_module, "Bot", FakeArchivePhotoBot)

    authorized_app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(authorized_session),
    )
    authorized_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=authorized_app),
        base_url="http://testserver",
    )
    authorized_client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        response = await authorized_client.get(f"/app/admin/archive/media/{photo.id}")
    finally:
        await authorized_client.aclose()
        await getattr(authorized_app.router, "shutdown", authorized_app.router._shutdown)()

    assert response.status_code == 200
    assert response.content == b"\xff\xd8archive-preview\xff\xd9"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=300"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert FakeArchivePhotoBot.instances[0].download.await_args.args == ("photo-large",)

    unauthorized_app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(unauthorized_session),
    )
    unauthorized_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=unauthorized_app),
        base_url="http://testserver",
    )
    try:
        unauthorized = await unauthorized_client.get(
            f"/app/admin/archive/media/{photo.id}"
        )
    finally:
        await unauthorized_client.aclose()
        await getattr(unauthorized_app.router, "shutdown", unauthorized_app.router._shutdown)()

    assert unauthorized.status_code == 401
    assert unauthorized_session.get_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message_type", "raw_message_json", "bot_class", "expected_status"),
    [
        ("text", {"text": "not a photo"}, FakeArchivePhotoBot, 404),
        (
            "photo",
            {"photo": [{"file_id": "photo-large", "file_size": 11 * 1024 * 1024}]},
            FakeArchivePhotoBot,
            413,
        ),
        (
            "photo",
            {"photo": [{"file_id": "photo-large", "file_size": 2048}]},
            FailingArchivePhotoBot,
            404,
        ),
    ],
)
async def test_admin_archive_photo_endpoint_rejects_unsafe_or_unavailable_media(
    monkeypatch,
    message_type: str,
    raw_message_json: dict[str, object],
    bot_class: type[FakeArchivePhotoBot],
    expected_status: int,
) -> None:
    settings = _settings()
    snapshot = MessageArchiveModel(
        id=32,
        chat_id=-100500,
        user_id=101,
        telegram_message_id=7002,
        snapshot_kind="created",
        snapshot_at=datetime(2026, 4, 8, 18, 25, tzinfo=timezone.utc),
        sent_at=datetime(2026, 4, 8, 18, 25, tzinfo=timezone.utc),
        edited_at=None,
        message_type=message_type,
        text="not a photo" if message_type == "text" else None,
        caption=None,
        raw_message_json=raw_message_json,
        snapshot_hash="unsafe-photo-hash",
        created_at=datetime(2026, 4, 8, 18, 25, tzinfo=timezone.utc),
    )
    session = FakeSession(records={(MessageArchiveModel, snapshot.id): snapshot})
    monkeypatch.setattr(
        web_app_module,
        "SqlAlchemyAdminAuthRepository",
        lambda session: FakeAdminAuthRepo(settings.admin_user_id),
    )
    bot_class.instances.clear()
    monkeypatch.setattr(web_app_module, "Bot", bot_class)
    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(session),
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )
    client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        response = await client.get(f"/app/admin/archive/media/{snapshot.id}")
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == expected_status
    if expected_status in {404, 413} and bot_class is FakeArchivePhotoBot:
        assert bot_class.instances == []


def test_admin_archive_cursor_round_trip_and_malformed_value() -> None:
    snapshot_at = datetime(2026, 4, 8, 18, 25, 30, tzinfo=timezone.utc)

    cursor = web_app_module._encode_admin_archive_cursor(
        snapshot_at=snapshot_at,
        snapshot_id=778,
    )

    assert web_app_module._decode_admin_archive_cursor(cursor) == (snapshot_at, 778)
    assert web_app_module._decode_admin_archive_cursor("not-a-valid-cursor") is None


@pytest.mark.parametrize(
    ("count", "expected"),
    [(1, "задача"), (2, "задачи"), (4, "задачи"), (5, "задач"), (11, "задач"), (21, "задача")],
)
def test_russian_plural_handles_admin_metric_edges(count: int, expected: str) -> None:
    assert (
        web_app_module._russian_plural(
            count,
            one="задача",
            few="задачи",
            many="задач",
        )
        == expected
    )


def test_admin_feature_request_presenter_builds_visible_lifecycle() -> None:
    created_at = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)
    done_at = datetime(2026, 8, 15, 11, 30, tzinfo=timezone.utc)
    row = UserFeatureRequestModel(
        id=41,
        user_id=101,
        title="Улучшить историю",
        details="Показывать сообщения как диалог.",
        status="done",
        done_at=done_at,
        created_at=created_at,
        updated_at=done_at,
    )

    item = web_app_module._build_feature_request_item(
        row=row,
        author_label="Alice",
    )

    assert item["updated_at"] == web_app_module.format_datetime(done_at)
    assert item["status_history"] == [
        {
            "label": "Заявка создана",
            "time": web_app_module.format_datetime(created_at),
            "tone": "neutral",
        },
        {
            "label": "Отмечена как сделанная",
            "time": web_app_module.format_datetime(done_at),
            "tone": "done",
        },
    ]


@pytest.mark.asyncio
async def test_admin_feedback_filter_is_validated_and_applied(monkeypatch) -> None:
    settings = _settings()
    request_row = UserFeatureRequestModel(
        id=41,
        user_id=101,
        title="Улучшить историю",
        details="Показывать сообщения как диалог.",
        status="open",
        done_at=None,
        created_at=datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc),
    )
    author = UserModel(
        telegram_user_id=101,
        username="alice",
        first_name="Alice",
        last_name=None,
    )
    filtered_session = FakeSession(
        execute_results=[
            FakeExecuteResult(rows=[(request_row, author)]),
            FakeExecuteResult(scalar_value=1),
            FakeExecuteResult(scalar_value=0),
            FakeExecuteResult(rows=[]),
            FakeExecuteResult(rows=[]),
        ]
    )
    invalid_session = FakeSession(
        execute_results=[
            FakeExecuteResult(rows=[]),
            FakeExecuteResult(scalar_value=0),
            FakeExecuteResult(scalar_value=0),
            FakeExecuteResult(rows=[]),
            FakeExecuteResult(rows=[]),
        ]
    )
    monkeypatch.setattr(
        web_app_module,
        "SqlAlchemyAdminAuthRepository",
        lambda session: FakeAdminAuthRepo(settings.admin_user_id),
    )
    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(filtered_session, invalid_session),
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )
    client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        filtered = await client.get("/app/admin?feedback_status=open")
        invalid = await client.get("/app/admin?feedback_status=broken")
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert filtered.status_code == 200
    assert 'data-feedback-filter="open" aria-current="page"' in filtered.text
    assert "Улучшить историю" in filtered.text
    assert "user_feature_requests.status" in str(filtered_session.execute_calls[0])
    assert invalid.status_code == 200
    assert "Неизвестный фильтр заявок" in invalid.text
    assert 'data-feedback-filter="all" aria-current="page"' in invalid.text


@pytest.mark.asyncio
async def test_admin_feedback_status_update_preserves_safe_filter_and_anchor(
    monkeypatch,
) -> None:
    settings = _settings()
    request_row = UserFeatureRequestModel(
        id=41,
        user_id=101,
        title="Улучшить историю",
        details="Показывать сообщения как диалог.",
        status="open",
        done_at=None,
        created_at=datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc),
    )
    session = FakeSession(records={(UserFeatureRequestModel, 41): request_row})
    monkeypatch.setattr(
        web_app_module,
        "SqlAlchemyAdminAuthRepository",
        lambda current_session: FakeAdminAuthRepo(settings.admin_user_id),
    )
    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(session),
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    )
    client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        response = await client.post(
            "/app/admin/feedback/41/status",
            data={"status": "done", "feedback_status": "open"},
        )
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/app/admin?feedback_status=open&flash=")
    assert location.endswith("#feedback")
    assert request_row.status == "done"
    assert request_row.done_at is not None


@pytest.mark.asyncio
async def test_admin_login_page_has_public_navigation_only(monkeypatch) -> None:
    settings = _settings()
    monkeypatch.setattr(
        web_app_module,
        "SqlAlchemyAdminAuthRepository",
        lambda session: FakeAdminAuthRepo(None),
    )
    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(FakeSession()),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        response = await client.get("/app/admin/login")
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert 'action="/logout"' not in response.text
    assert 'href="/app/admin"' not in response.text
    assert 'href="/"' in response.text


@pytest.mark.asyncio
async def test_admin_page_has_shared_section_navigation(monkeypatch) -> None:
    settings = _settings()
    auth_session = FakeSession(
        execute_results=[
            FakeExecuteResult(rows=[]),
            FakeExecuteResult(scalar_value=0),
            FakeExecuteResult(scalar_value=0),
            FakeExecuteResult(
                rows=[(-1001001, "supergroup", "Test chat", datetime.now(timezone.utc))]
            ),
            FakeExecuteResult(rows=[]),
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
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert 'aria-label="Разделы админки"' in response.text
    assert 'href="/app/admin" aria-current="page"' in response.text
    assert 'href="/app/admin#broadcasts"' in response.text
    assert 'href="/app/admin/table/messages_compact"' in response.text
    assert 'href="/app/admin#database"' in response.text
    assert 'action="/app/admin/logout"' in response.text
    assert response.text.count('action="/app/admin/logout"') == 1
    assert 'action="/logout"' not in response.text
    assert 'data-admin-overview' in response.text
    assert 'href="#broadcasts"' in response.text
    assert 'href="#feedback"' in response.text
    assert 'href="/app/admin/table/chat_audit_logs"' in response.text
    assert 'data-admin-backup-dialog' in response.text
    assert 'src="/static/admin-overview.js"' in response.text
    assert 'href="/static/admin-overview.css"' in response.text


@pytest.mark.asyncio
async def test_admin_overview_renders_error_and_flash_banners(monkeypatch) -> None:
    settings = _settings()

    def _auth_session() -> FakeSession:
        return FakeSession(
            execute_results=[
                FakeExecuteResult(rows=[]),
                FakeExecuteResult(scalar_value=0),
                FakeExecuteResult(scalar_value=0),
                FakeExecuteResult(rows=[]),
                FakeExecuteResult(rows=[]),
            ]
        )

    monkeypatch.setattr(
        web_app_module,
        "SqlAlchemyAdminAuthRepository",
        lambda session: FakeAdminAuthRepo(settings.admin_user_id),
    )

    error_app = web_app_module.create_web_app(
        settings=settings, session_factory=QueueSessionFactory(_auth_session())
    )
    error_transport = httpx.ASGITransport(app=error_app)
    error_client = httpx.AsyncClient(transport=error_transport, base_url="http://testserver")
    error_client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        error_response = await error_client.get("/app/admin", params={"error": "Не удалось выполнить операцию."})
    finally:
        await error_client.aclose()
        await getattr(error_app.router, "shutdown", error_app.router._shutdown)()

    assert error_response.status_code == 200
    assert 'class="banner banner-error" role="alert"' in error_response.text
    assert "Не удалось выполнить операцию." in error_response.text

    flash_app = web_app_module.create_web_app(
        settings=settings, session_factory=QueueSessionFactory(_auth_session())
    )
    flash_transport = httpx.ASGITransport(app=flash_app)
    flash_client = httpx.AsyncClient(transport=flash_transport, base_url="http://testserver")
    flash_client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        flash_response = await flash_client.get("/app/admin", params={"flash": "Операция выполнена."})
    finally:
        await flash_client.aclose()
        await getattr(flash_app.router, "shutdown", flash_app.router._shutdown)()

    assert flash_response.status_code == 200
    assert 'class="banner banner-ok" role="status"' in flash_response.text
    assert "Операция выполнена." in flash_response.text


@pytest.mark.asyncio
async def test_admin_page_lists_all_mapped_tables(monkeypatch) -> None:
    settings = _settings()
    auth_session = FakeSession(
        execute_results=[
            FakeExecuteResult(rows=[]),
            FakeExecuteResult(scalar_value=0),
            FakeExecuteResult(scalar_value=0),
            FakeExecuteResult(
                rows=[(-1001001, "supergroup", "Test chat", datetime.now(timezone.utc))]
            ),
            FakeExecuteResult(rows=[]),
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
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert "Активность и пользователи" in response.text
    assert "Экономика" in response.text
    assert "Дневная активность пользователей" in response.text
    assert "Архив сообщений · полезное" in response.text
    assert "Использование действий отношений" in response.text
    assert "Коды входа веб-панели" in response.text
    assert 'action="/app/admin/request-backup"' in response.text
    assert "Системная рассылка" in response.text
    assert 'action="/app/admin/broadcasts/send"' in response.text
    assert 'enctype="multipart/form-data"' in response.text
    assert 'name="media_mode"' in response.text
    assert 'value="text"' in response.text
    assert 'value="photo"' in response.text
    assert 'type="file"' in response.text
    assert 'name="photo"' in response.text
    assert 'accept="image/jpeg,image/png"' in response.text
    assert 'data-broadcast-photo-field' in response.text
    assert 'data-broadcast-reactions-toggle' in response.text
    assert 'data-broadcast-reaction-list' in response.text
    assert 'data-broadcast-add-reaction' in response.text
    assert 'name="reaction_emoji"' in response.text
    assert 'name="reaction_label"' in response.text
    assert 'name="chat_ids"' in response.text
    assert 'data-broadcast-target-search' in response.text
    assert 'data-broadcast-selected-count' in response.text
    assert 'data-broadcast-form-error' in response.text
    assert 'role="alert"' in response.text
    assert 'data-broadcast-submit' in response.text
    assert 'data-broadcast-live-preview' in response.text
    assert 'data-broadcast-preview-body' in response.text
    assert 'data-broadcast-preview-photo' in response.text
    assert 'data-broadcast-confirm-dialog' in response.text
    assert 'data-broadcast-confirm-submit' in response.text
    assert 'data-broadcast-confirm-cancel' in response.text
    assert 'src="/static/admin-broadcast.js"' in response.text
    assert 'data-table-search-input' in response.text
    assert 'data-table-search-card' in response.text
    assert 'data-table-search-text="чаты chats"' in response.text
    assert 'src="/static/admin-table-search.js"' in response.text


@pytest.mark.asyncio
async def test_admin_login_invalid_password_redirect_keeps_readable_query_text() -> None:
    settings = _settings()

    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        response = await client.post(
            "/app/admin/login",
            data={"password": "wrong-password"},
            follow_redirects=False,
        )
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 303
    assert response.headers["location"].startswith("/app/admin/login?error=")
    assert _location_query_value(response.headers["location"], "error") == "Неверный пароль."


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_password", ["change-me", "__GENERATE_A_LONG_RANDOM_PASSWORD__"])
async def test_admin_login_rejects_unsafe_configured_passwords(configured_password: str) -> None:
    settings = _settings().model_copy(update={"admin_password": configured_password})
    app = web_app_module.create_web_app(settings=settings, session_factory=QueueSessionFactory())
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        response = await client.post("/api/admin/login", data={"password": configured_password})
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_login_accepts_configured_short_password(monkeypatch) -> None:
    settings = _settings().model_copy(update={"admin_password": "short"})
    repo = CapturingAdminAuthRepo()
    monkeypatch.setattr(web_app_module, "SqlAlchemyAdminAuthRepository", lambda session: repo)
    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(FakeSession()),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        response = await client.post("/api/admin/login", data={"password": "short"})
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert repo.created_session_token is not None


@pytest.mark.asyncio
async def test_admin_login_stores_only_session_digest(monkeypatch) -> None:
    settings = _settings()
    repo = CapturingAdminAuthRepo()
    monkeypatch.setattr(web_app_module, "SqlAlchemyAdminAuthRepository", lambda session: repo)

    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(FakeSession()),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        response = await client.post(
            "/api/admin/login",
            data={"password": "admin-secret"},
        )
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    cookie_token = response.cookies.get(settings.admin_session_cookie_name)
    assert response.status_code == 200
    assert cookie_token
    assert repo.created_session_token == digest_admin_session_token(
        secret=settings.resolved_web_auth_secret,
        token=cookie_token,
    )
    assert repo.created_session_token != cookie_token


@pytest.mark.asyncio
async def test_admin_login_is_rate_limited_by_client() -> None:
    settings = _settings().model_copy(update={"web_login_attempt_limit": 2})
    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        responses = [
            await client.post(
                "/api/admin/login",
                data={"password": "wrong-password"},
            )
            for _ in range(3)
        ]
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert [response.status_code for response in responses] == [401, 401, 429]


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
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert "alice" in response.text
    assert "Пользователи" in response.text
    assert "Alice" in response.text
    assert len(data_session.execute_calls) == 3


@pytest.mark.asyncio
async def test_admin_table_page_clamps_invalid_page_param(monkeypatch) -> None:
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
        response = await client.get("/app/admin/table/users?page=not-a-number")
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert "Alice" in response.text


@pytest.mark.asyncio
async def test_admin_table_page_encodes_pagination_and_filter_links(monkeypatch) -> None:
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
            FakeExecuteResult(scalar_value=120),
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
        response = await client.get("/app/admin/table/users?username=a%26b")
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert "page=2" in response.text
    assert "username=a%26b" in response.text
    assert "&username=a&b\"" not in response.text


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
        await getattr(app.router, "shutdown", app.router._shutdown)()

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
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert "/app/admin/table/user_chat_activity/edit?chat_id=30&amp;user_id=10" in response.text
    assert "/app/admin/table/user_chat_activity/delete?chat_id=30&amp;user_id=10" in response.text


@pytest.mark.asyncio
async def test_admin_table_page_renders_messages_table_with_json_payload(monkeypatch) -> None:
    settings = _settings()
    auth_session = FakeSession()
    archived_message = MessageArchiveModel(
        id=1,
        chat_id=-100500,
        user_id=101,
        telegram_message_id=777,
        snapshot_kind="created",
        snapshot_at=datetime(2026, 4, 8, 18, 24),
        sent_at=datetime(2026, 4, 8, 18, 24),
        edited_at=None,
        message_type="text",
        text="hello world",
        caption=None,
        raw_message_json={"message_id": 777, "text": "hello world"},
        snapshot_hash="hash-1",
        created_at=datetime(2026, 4, 8, 18, 24),
    )
    data_session = FakeSession(
        execute_results=[
            FakeExecuteResult(rows=[archived_message]),
            FakeExecuteResult(scalar_value=1),
            FakeExecuteResult(
                rows=[
                    UserModel(telegram_user_id=101, username="alice", first_name="Alice", last_name=None, is_bot=False),
                ]
            ),
            FakeExecuteResult(
                rows=[
                    ChatModel(telegram_chat_id=-100500, type="supergroup", title="Archive Chat"),
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
        response = await client.get("/app/admin/table/messages")
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert "Архив сообщений" in response.text
    assert "Archive Chat" in response.text
    assert "Alice" in response.text
    assert "hello world" in response.text
    assert "message_id" in response.text


@pytest.mark.asyncio
async def test_admin_table_page_renders_compact_messages_view(monkeypatch) -> None:
    settings = _settings()
    auth_session = FakeSession()
    created_snapshot = MessageArchiveModel(
        id=1,
        chat_id=-100500,
        user_id=101,
        telegram_message_id=778,
        snapshot_kind="created",
        snapshot_at=datetime(2026, 4, 8, 18, 24),
        sent_at=datetime(2026, 4, 8, 18, 24),
        edited_at=None,
        message_type="text",
        text="draft answer",
        caption=None,
        raw_message_json={"message_id": 778, "text": "draft answer"},
        snapshot_hash="hash-1",
        created_at=datetime(2026, 4, 8, 18, 24),
    )
    archived_message = MessageArchiveModel(
        id=2,
        chat_id=-100500,
        user_id=101,
        telegram_message_id=778,
        snapshot_kind="edited",
        snapshot_at=datetime(2026, 4, 8, 18, 25),
        sent_at=datetime(2026, 4, 8, 18, 24),
        edited_at=datetime(2026, 4, 8, 18, 25),
        message_type="text",
        text="answer text",
        caption=None,
        raw_message_json={
            "message_id": 778,
            "text": "answer text",
            "reply_to_message": {
                "message_id": 777,
                "from": {"id": 202, "username": "bob", "first_name": "Bob"},
                "text": "original question",
            },
        },
        snapshot_hash="hash-2",
        created_at=datetime(2026, 4, 8, 18, 25),
    )
    followup_message = MessageArchiveModel(
        id=3,
        chat_id=-100500,
        user_id=101,
        telegram_message_id=779,
        snapshot_kind="created",
        snapshot_at=datetime(2026, 4, 8, 18, 26),
        sent_at=datetime(2026, 4, 8, 18, 26),
        edited_at=None,
        message_type="document",
        text=None,
        caption="follow-up report",
        raw_message_json={
            "message_id": 779,
            "caption": "follow-up report",
            "document": {
                "file_name": "report.pdf",
                "mime_type": "application/pdf",
                "file_size": 2 * 1024 * 1024,
            },
        },
        snapshot_hash="hash-3",
        created_at=datetime(2026, 4, 8, 18, 26),
    )
    delayed_message = MessageArchiveModel(
        id=4,
        chat_id=-100500,
        user_id=101,
        telegram_message_id=780,
        snapshot_kind="created",
        snapshot_at=datetime(2026, 4, 8, 18, 40),
        sent_at=datetime(2026, 4, 8, 18, 40),
        edited_at=None,
        message_type="text",
        text="later answer",
        caption=None,
        raw_message_json={"message_id": 780, "text": "later answer"},
        snapshot_hash="hash-4",
        created_at=datetime(2026, 4, 8, 18, 40),
    )
    data_session = FakeSession(
        execute_results=[
            FakeExecuteResult(
                rows=[delayed_message, followup_message, archived_message, created_snapshot]
            ),
            FakeExecuteResult(
                rows=[delayed_message, followup_message, archived_message, created_snapshot]
            ),
            FakeExecuteResult(scalar_value=4),
            FakeExecuteResult(
                rows=[
                    UserModel(telegram_user_id=101, username="alice", first_name="Alice", last_name=None, is_bot=False),
                ]
            ),
            FakeExecuteResult(
                rows=[
                    ChatModel(telegram_chat_id=-100500, type="supergroup", title="Archive Chat"),
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
        response = await client.get("/app/admin/table/messages_compact")
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert "Архив сообщений" in response.text
    assert "Archive Chat" in response.text
    assert "Alice" in response.text
    assert "@bob" in response.text
    assert "original question" in response.text
    assert "raw_message_json" not in response.text
    assert 'class="archive-layout' in response.text
    assert 'class="archive-message' in response.text
    assert 'class="admin-data-table"' not in response.text
    assert response.text.count('class="archive-message archive-author-') == 3
    assert 'class="archive-edit-history"' in response.text
    assert "Снимков в выборке: 2" in response.text
    assert "draft answer" in response.text
    assert "/app/admin/table/messages?id=1" in response.text
    assert response.text.count(" is-continuation") == 1
    assert "follow-up report" in response.text
    assert "report.pdf" in response.text
    assert "application/pdf" in response.text
    assert "2.0 МБ" in response.text
    assert 'src="/static/admin-archive.js"' in response.text


@pytest.mark.asyncio
async def test_admin_archive_applies_and_preserves_extended_filters(monkeypatch) -> None:
    settings = _settings()
    auth_session = FakeSession()
    archived_message = MessageArchiveModel(
        id=3,
        chat_id=-100500,
        user_id=101,
        telegram_message_id=779,
        snapshot_kind="edited",
        snapshot_at=datetime(2026, 4, 8, 18, 25, tzinfo=timezone.utc),
        sent_at=datetime(2026, 4, 8, 18, 24, tzinfo=timezone.utc),
        edited_at=datetime(2026, 4, 8, 18, 25, tzinfo=timezone.utc),
        message_type="text",
        text="answer text",
        caption=None,
        raw_message_json={
            "message_id": 779,
            "text": "answer text",
            "reply_to_message": {"message_id": 778, "text": "question"},
        },
        snapshot_hash="hash-3",
        created_at=datetime(2026, 4, 8, 18, 25, tzinfo=timezone.utc),
    )
    other_chat_message = MessageArchiveModel(
        id=4,
        chat_id=-100700,
        user_id=202,
        telegram_message_id=880,
        snapshot_kind="created",
        snapshot_at=datetime(2026, 4, 7, 12, 0, tzinfo=timezone.utc),
        sent_at=datetime(2026, 4, 7, 12, 0, tzinfo=timezone.utc),
        edited_at=None,
        message_type="photo",
        text=None,
        caption="other chat",
        raw_message_json={"message_id": 880, "caption": "other chat"},
        snapshot_hash="hash-4",
        created_at=datetime(2026, 4, 7, 12, 0, tzinfo=timezone.utc),
    )
    data_session = FakeSession(
        execute_results=[
            FakeExecuteResult(rows=[archived_message, other_chat_message]),
            FakeExecuteResult(rows=[archived_message]),
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
            FakeExecuteResult(
                rows=[
                    ChatModel(telegram_chat_id=-100500, type="supergroup", title="Archive Chat"),
                    ChatModel(telegram_chat_id=-100700, type="supergroup", title="Other Chat"),
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
        response = await client.get(
            "/app/admin/table/messages_compact",
            params={
                "chat_id": "-100500",
                "user_id": "101",
                "text": "answer",
                "date_from": "2026-04-01",
                "date_to": "2026-04-09",
                "message_type": "text",
                "snapshot_kind": "edited",
                "has_reply": "yes",
            },
        )
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert 'name="date_from"' in response.text
    assert 'value="2026-04-01"' in response.text
    assert 'name="date_to"' in response.text
    assert 'value="2026-04-09"' in response.text
    assert 'option value="text" selected' in response.text
    assert 'option value="video_note"' in response.text
    assert 'option value="yes" selected' in response.text
    assert '<mark class="archive-search-hit">answer</mark> text' in response.text
    assert (
        'href="/app/admin/table/messages_compact?chat_id=-100700&amp;user_id=101'
        '&amp;text=answer&amp;date_from=2026-04-01&amp;date_to=2026-04-09'
        '&amp;message_type=text&amp;snapshot_kind=edited&amp;has_reply=yes"'
    ) in response.text

    filtered_statement = str(data_session.execute_calls[1])
    assert "messages.snapshot_at >=" in filtered_statement
    assert "messages.snapshot_at <" in filtered_statement
    assert "messages.message_type" in filtered_statement
    assert "messages.raw_message_json" in filtered_statement


@pytest.mark.asyncio
async def test_admin_archive_reports_malformed_filter_values(monkeypatch) -> None:
    settings = _settings()
    auth_session = FakeSession()
    data_session = FakeSession(
        execute_results=[
            FakeExecuteResult(rows=[]),
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
        session_factory=QueueSessionFactory(auth_session, data_session),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        response = await client.get(
            "/app/admin/table/messages_compact",
            params={
                "page": "not-a-page",
                "chat_id": "not-a-chat",
                "user_id": "not-a-user",
                "date_from": "2026-99-99",
                "date_to": "also-not-a-date",
                "message_type": "totally_unknown",
                "snapshot_kind": "unknown",
                "has_reply": "perhaps",
                "before": "broken-cursor",
            },
        )
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert 'class="archive-filter-errors" role="alert"' in response.text
    assert "ID чата должен быть целым числом" in response.text
    assert "ID автора должен быть целым числом" in response.text
    assert "Начальная дата имеет неверный формат" in response.text
    assert "Конечная дата имеет неверный формат" in response.text
    assert "Неизвестный тип сообщения" in response.text
    assert "Неизвестный вид снимка" in response.text
    assert "Неизвестный фильтр ответов" in response.text
    assert "Ссылка на предыдущую страницу устарела или повреждена" in response.text


@pytest.mark.asyncio
async def test_admin_archive_uses_stable_cursor_pagination(monkeypatch) -> None:
    settings = _settings()
    auth_session = FakeSession()
    first_snapshot_at = datetime(2026, 4, 8, 18, 30, tzinfo=timezone.utc)
    archived_messages = [
        MessageArchiveModel(
            id=index + 1,
            chat_id=-100500,
            user_id=101,
            telegram_message_id=1000 + index,
            snapshot_kind="created",
            snapshot_at=first_snapshot_at - timedelta(minutes=index),
            sent_at=first_snapshot_at - timedelta(minutes=index),
            edited_at=None,
            message_type="text",
            text=f"message {index}",
            caption=None,
            raw_message_json={"message_id": 1000 + index, "text": f"message {index}"},
            snapshot_hash=f"hash-{index}",
            created_at=first_snapshot_at - timedelta(minutes=index),
        )
        for index in range(51)
    ]
    data_session = FakeSession(
        execute_results=[
            FakeExecuteResult(rows=[archived_messages[0]]),
            FakeExecuteResult(rows=archived_messages),
            FakeExecuteResult(scalar_value=120),
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
            FakeExecuteResult(
                rows=[ChatModel(telegram_chat_id=-100500, type="supergroup", title="Archive Chat")]
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
        response = await client.get(
            "/app/admin/table/messages_compact",
            params={"chat_id": "-100500"},
        )
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert response.text.count('class="archive-message archive-author-') == 50
    assert "before=" in response.text
    assert "Старее →" in response.text
    assert "page=2" not in response.text
    assert "Показано сообщений: 50" in response.text


@pytest.mark.asyncio
async def test_admin_archive_applies_cursor_boundary_and_builds_newer_link(monkeypatch) -> None:
    settings = _settings()
    auth_session = FakeSession()
    boundary_at = datetime(2026, 4, 8, 18, 0, tzinfo=timezone.utc)
    older_message = MessageArchiveModel(
        id=40,
        chat_id=-100500,
        user_id=101,
        telegram_message_id=1040,
        snapshot_kind="created",
        snapshot_at=boundary_at - timedelta(minutes=1),
        sent_at=boundary_at - timedelta(minutes=1),
        edited_at=None,
        message_type="text",
        text="older message",
        caption=None,
        raw_message_json={"message_id": 1040, "text": "older message"},
        snapshot_hash="hash-older",
        created_at=boundary_at - timedelta(minutes=1),
    )
    data_session = FakeSession(
        execute_results=[
            FakeExecuteResult(rows=[older_message]),
            FakeExecuteResult(rows=[older_message]),
            FakeExecuteResult(scalar_value=120),
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
            FakeExecuteResult(
                rows=[ChatModel(telegram_chat_id=-100500, type="supergroup", title="Archive Chat")]
            ),
        ]
    )
    monkeypatch.setattr(
        web_app_module,
        "SqlAlchemyAdminAuthRepository",
        lambda session: FakeAdminAuthRepo(settings.admin_user_id),
    )
    cursor = web_app_module._encode_admin_archive_cursor(
        snapshot_at=boundary_at,
        snapshot_id=41,
    )

    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(auth_session, data_session),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        response = await client.get(
            "/app/admin/table/messages_compact",
            params={"chat_id": "-100500", "before": cursor},
        )
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert "← Новее" in response.text
    assert "after=" in response.text
    filtered_statement = str(data_session.execute_calls[1])
    assert "messages.snapshot_at <" in filtered_statement
    assert "messages.id <" in filtered_statement


@pytest.mark.asyncio
async def test_admin_archive_jump_endpoint_requires_admin(monkeypatch) -> None:
    settings = _settings()
    unauthorized_session = FakeSession()
    monkeypatch.setattr(
        web_app_module,
        "SqlAlchemyAdminAuthRepository",
        lambda session: FakeAdminAuthRepo(None),
    )

    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(unauthorized_session),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        response = await client.get("/app/admin/archive/jump/1")
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 303
    assert urlsplit(response.headers["location"]).path == "/app/admin/login"
    assert unauthorized_session.get_calls == []


@pytest.mark.asyncio
async def test_admin_archive_jump_endpoint_redirects_to_computed_page_and_highlight(
    monkeypatch,
) -> None:
    settings = _settings()
    target = MessageArchiveModel(
        id=42,
        chat_id=-100500,
        user_id=101,
        telegram_message_id=900,
        snapshot_kind="created",
        snapshot_at=datetime(2026, 4, 8, 18, 30, tzinfo=timezone.utc),
        sent_at=datetime(2026, 4, 8, 18, 30, tzinfo=timezone.utc),
        edited_at=None,
        message_type="text",
        text="target message",
        caption=None,
        raw_message_json={"message_id": 900, "text": "target message"},
        snapshot_hash="hash-target",
        created_at=datetime(2026, 4, 8, 18, 30, tzinfo=timezone.utc),
    )
    data_session = FakeSession(
        records={(MessageArchiveModel, target.id): target},
        execute_results=[FakeExecuteResult(scalar_value=1)],
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
    client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        response = await client.get(f"/app/admin/archive/jump/{target.id}")
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 303
    location = response.headers["location"]
    assert urlsplit(location).path == "/app/admin/table/messages_compact"
    assert _location_query_value(location, "chat_id") == "-100500"
    assert _location_query_value(location, "page") == "1"
    assert _location_query_value(location, "highlight") == "42"
    rank_statement = str(data_session.execute_calls[0])
    assert "messages.chat_id" in rank_statement
    assert "messages.snapshot_at >" in rank_statement


@pytest.mark.asyncio
async def test_admin_archive_jump_endpoint_computes_later_page_for_older_message(
    monkeypatch,
) -> None:
    settings = _settings()
    target = MessageArchiveModel(
        id=43,
        chat_id=-100500,
        user_id=101,
        telegram_message_id=901,
        snapshot_kind="created",
        snapshot_at=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
        sent_at=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
        edited_at=None,
        message_type="text",
        text="older target",
        caption=None,
        raw_message_json={"message_id": 901, "text": "older target"},
        snapshot_hash="hash-older-target",
        created_at=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
    )
    data_session = FakeSession(
        records={(MessageArchiveModel, target.id): target},
        execute_results=[FakeExecuteResult(scalar_value=57)],
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
    client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        response = await client.get(f"/app/admin/archive/jump/{target.id}")
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 303
    assert _location_query_value(response.headers["location"], "page") == "2"


@pytest.mark.asyncio
async def test_admin_archive_jump_endpoint_missing_message_redirects_with_error(
    monkeypatch,
) -> None:
    settings = _settings()
    data_session = FakeSession()
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
    client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        response = await client.get("/app/admin/archive/jump/999")
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 303
    location = response.headers["location"]
    assert urlsplit(location).path == "/app/admin/table/messages_compact"
    assert _location_query_value(location, "error") == "Сообщение не найдено в архиве."
    assert data_session.execute_calls == []


@pytest.mark.asyncio
async def test_admin_archive_highlight_param_renders_jump_target_and_context_link(
    monkeypatch,
) -> None:
    settings = _settings()
    auth_session = FakeSession()
    message = MessageArchiveModel(
        id=55,
        chat_id=-100500,
        user_id=101,
        telegram_message_id=950,
        snapshot_kind="created",
        snapshot_at=datetime(2026, 4, 8, 18, 30, tzinfo=timezone.utc),
        sent_at=datetime(2026, 4, 8, 18, 30, tzinfo=timezone.utc),
        edited_at=None,
        message_type="text",
        text="findable answer",
        caption=None,
        raw_message_json={"message_id": 950, "text": "findable answer"},
        snapshot_hash="hash-findable",
        created_at=datetime(2026, 4, 8, 18, 30, tzinfo=timezone.utc),
    )
    data_session = FakeSession(
        execute_results=[
            FakeExecuteResult(rows=[message]),
            FakeExecuteResult(rows=[message]),
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
            FakeExecuteResult(
                rows=[ChatModel(telegram_chat_id=-100500, type="supergroup", title="Archive Chat")]
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
        response = await client.get(
            "/app/admin/table/messages_compact",
            params={"chat_id": "-100500", "text": "findable", "highlight": "55"},
        )
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert 'id="archive-msg-55"' in response.text
    assert 'data-message-id="55"' in response.text
    assert 'tabindex="-1"' in response.text
    assert 'data-archive-highlight-target="55"' in response.text
    assert (
        'href="/app/admin/archive/jump/55" class="button ghost small archive-context-jump"'
        in response.text
    )


@pytest.mark.asyncio
async def test_admin_archive_context_jump_link_hidden_without_active_filters(
    monkeypatch,
) -> None:
    settings = _settings()
    auth_session = FakeSession()
    message = MessageArchiveModel(
        id=56,
        chat_id=-100500,
        user_id=101,
        telegram_message_id=951,
        snapshot_kind="created",
        snapshot_at=datetime(2026, 4, 8, 18, 30, tzinfo=timezone.utc),
        sent_at=datetime(2026, 4, 8, 18, 30, tzinfo=timezone.utc),
        edited_at=None,
        message_type="text",
        text="plain answer",
        caption=None,
        raw_message_json={"message_id": 951, "text": "plain answer"},
        snapshot_hash="hash-plain",
        created_at=datetime(2026, 4, 8, 18, 30, tzinfo=timezone.utc),
    )
    data_session = FakeSession(
        execute_results=[
            FakeExecuteResult(rows=[message]),
            FakeExecuteResult(rows=[message]),
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
            FakeExecuteResult(
                rows=[ChatModel(telegram_chat_id=-100500, type="supergroup", title="Archive Chat")]
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
        response = await client.get(
            "/app/admin/table/messages_compact",
            params={"chat_id": "-100500"},
        )
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert "archive-context-jump" not in response.text
    assert 'data-archive-highlight-target' not in response.text


@pytest.mark.asyncio
async def test_admin_table_edit_page_requires_admin_session() -> None:
    settings = _settings()
    auth_session = FakeSession()
    app = web_app_module.create_web_app(
        settings=settings,
        session_factory=QueueSessionFactory(auth_session),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        response = await client.get(
            "/app/admin/table/marriages/edit?id=2",
            follow_redirects=False,
        )
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 303
    assert response.headers["location"] == "/app/admin/login"
    assert auth_session.get_calls == []


@pytest.mark.asyncio
async def test_admin_table_edit_page_reads_record_id_from_query(monkeypatch) -> None:
    settings = _settings()
    auth_session = FakeSession()
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
        session_factory=QueueSessionFactory(auth_session, data_session),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        response = await client.get("/app/admin/table/marriages/edit?id=2")
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

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
        await getattr(app.router, "shutdown", app.router._shutdown)()

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
        await getattr(app.router, "shutdown", app.router._shutdown)()

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
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 303
    assert response.headers["location"].startswith("/app/admin?flash=")
    backup_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_archive_shows_empty_state_when_no_chats_archived(monkeypatch) -> None:
    settings = _settings()
    auth_session = FakeSession()
    data_session = FakeSession(
        execute_results=[
            FakeExecuteResult(rows=[]),
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
        session_factory=QueueSessionFactory(auth_session, data_session),
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        response = await client.get("/app/admin/table/messages_compact")
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert "Архив пока пуст" in response.text
    assert "Выберите чат" in response.text
    assert 'class="archive-message' not in response.text


@pytest.mark.asyncio
async def test_admin_archive_shows_filtered_empty_state_with_reset_link(monkeypatch) -> None:
    settings = _settings()
    auth_session = FakeSession()
    data_session = FakeSession(
        execute_results=[
            FakeExecuteResult(rows=[]),
            FakeExecuteResult(rows=[]),
            FakeExecuteResult(scalar_value=0),
            FakeExecuteResult(
                rows=[ChatModel(telegram_chat_id=-100500, type="supergroup", title="Archive Chat")]
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
        response = await client.get(
            "/app/admin/table/messages_compact",
            params={"chat_id": "-100500", "text": "текст которого точно нет"},
        )
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200
    assert "По этим фильтрам ничего не найдено" in response.text
    assert "Сбросить фильтры" in response.text
    assert 'href="/app/admin/table/messages_compact?chat_id=-100500"' in response.text
