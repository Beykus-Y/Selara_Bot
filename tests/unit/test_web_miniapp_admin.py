from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
import hashlib
import hmac
import json
import asyncio
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode

from selara.core.config import Settings
from selara.core.logging import configure_logging
from selara.domain.entities import UserSnapshot
from selara.infrastructure.db.models import (
    AdminBroadcastDeliveryModel,
    AdminBroadcastModel,
    AdminBroadcastReplyModel,
    ChatMemberCountSnapshotModel,
    ChatMetricsModel,
    ChatModel,
    OperationalAlertModel,
    UserFeatureRequestModel,
    UserChatMessageEventModel,
    UserChatActivityModel,
    UserModel,
)
from selara.web import app as web_app_module


def _settings(admin_user_id: int | None = 77) -> Settings:
    return Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
            "WEB_AUTH_SECRET": "test-secret",
            "WEB_BASE_URL": "http://testserver",
            "ADMIN_USER_ID": admin_user_id,
        }
    )


class _AsyncSessionProxy:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, statement):
        return self._session.execute(statement)

    async def get(self, entity, key):
        return self._session.get(entity, key)

    async def scalar(self, statement):
        return self._session.scalar(statement)

    def add(self, instance):
        self._session.add(instance)

    def add_all(self, instances):
        self._session.add_all(instances)

    async def flush(self):
        self._session.flush()

    async def commit(self) -> None:
        self._session.commit()

    async def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


class _SessionFactory:
    def __init__(self, engine) -> None:
        self._engine = engine

    def __call__(self):
        factory = self

        class _Manager:
            async def __aenter__(self):
                self.session = _AsyncSessionProxy(Session(factory._engine))
                return self.session

            async def __aexit__(self, exc_type, exc, tb):
                self.session.close()
                return False

        return _Manager()


class _FakeWebAuthRepository:
    current_user: UserSnapshot | None = None
    created_user_ids: list[int] = []

    def __init__(self, session) -> None:
        self.session = session

    async def get_user_by_session(self, *, session_digest, now, touch):
        _ = session_digest, now, touch
        return self.current_user

    async def purge_expired_state(self, *, now):
        _ = now

    async def upsert_user(self, *, user):
        self.created_user_ids.append(user.telegram_user_id)

    async def create_session(self, *, user_id, session_digest, expires_at, now):
        _ = session_digest, expires_at, now
        self.created_user_ids.append(user_id)


@asynccontextmanager
async def _client(monkeypatch, *, admin_user_id: int | None = 77, current_user: UserSnapshot | None = None):
    engine = create_engine("sqlite:///:memory:")
    UserChatMessageEventModel.metadata.create_all(
        engine,
        tables=[
            UserModel.__table__,
            ChatModel.__table__,
            UserChatMessageEventModel.__table__,
            ChatMetricsModel.__table__,
            ChatMemberCountSnapshotModel.__table__,
            UserFeatureRequestModel.__table__,
            OperationalAlertModel.__table__,
            AdminBroadcastModel.__table__,
            AdminBroadcastDeliveryModel.__table__,
            AdminBroadcastReplyModel.__table__,
            UserChatActivityModel.__table__,
        ],
    )
    session_factory = _SessionFactory(engine)
    _FakeWebAuthRepository.current_user = current_user
    _FakeWebAuthRepository.created_user_ids = []
    monkeypatch.setattr(web_app_module, "SqlAlchemyWebAuthRepository", _FakeWebAuthRepository)
    app = web_app_module.create_web_app(settings=_settings(admin_user_id), session_factory=session_factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        if current_user is not None:
            client.cookies.set(_settings().web_session_cookie_name, "signed-session")
        try:
            yield client, session_factory
        finally:
            await getattr(app.router, "shutdown", app.router._shutdown)()
            engine.dispose()


def _seed_audience(session_factory) -> None:
    now = datetime.now(timezone.utc)
    with Session(session_factory._engine) as session:
        session.add_all(
            [
                UserModel(telegram_user_id=77, is_bot=False),
                UserModel(telegram_user_id=80, is_bot=False),
                UserModel(telegram_user_id=90, is_bot=False),
                UserModel(telegram_user_id=999, is_bot=True),
                ChatModel(telegram_chat_id=-1001, type="supergroup", title="Первый чат"),
                ChatModel(telegram_chat_id=-1002, type="group", title="Второй чат"),
                ChatModel(telegram_chat_id=77, type="private", title=None),
                ChatMetricsModel(chat_id=-1001, active_members_count=12),
                ChatMetricsModel(chat_id=-1002, active_members_count=8),
            ]
        )
        session.add_all(
            [
                UserChatMessageEventModel(chat_id=-1001, user_id=80, sent_at=now - timedelta(days=2)),
                UserChatMessageEventModel(chat_id=-1002, user_id=80, sent_at=now - timedelta(days=1)),
                UserChatMessageEventModel(chat_id=77, user_id=90, sent_at=now - timedelta(days=3)),
                UserChatMessageEventModel(chat_id=-1001, user_id=77, sent_at=now - timedelta(days=40)),
                UserChatMessageEventModel(chat_id=-1001, user_id=999, sent_at=now - timedelta(days=2)),
            ]
        )
        session.commit()


def _telegram_init_data(*, user_id: int, auth_date: int, bot_token: str = "123456:TEST") -> str:
    fields = {
        "auth_date": str(auth_date),
        "user": json.dumps({"id": user_id, "first_name": "Owner", "username": "owner"}, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.mark.asyncio
async def test_miniapp_admin_api_requires_admin_session(monkeypatch) -> None:
    ordinary_user = UserSnapshot(telegram_user_id=80, username="member", first_name="User", last_name=None, is_bot=False)
    async with _client(monkeypatch, current_user=None) as (client, _session_factory):
        response = await client.get("/api/miniapp/admin/audience")
        client.cookies.set(_settings().web_session_cookie_name, "expired-session")
        expired = await client.get("/api/miniapp/admin/audience")
        assert response.status_code == 401
        assert expired.status_code == 401

    async with _client(monkeypatch, current_user=ordinary_user) as (client, _session_factory):
        response = await client.get("/api/miniapp/admin/audience", headers={"x-telegram-user-id": "77"})
        denied_broadcast = await client.post(
            "/api/miniapp/admin/broadcast/preview", json={"body": "Сообщение", "active_since_days": 3}
        )
        assert response.status_code == 403
        assert denied_broadcast.status_code == 403

    async with _client(monkeypatch, admin_user_id=None, current_user=ordinary_user) as (client, _session_factory):
        response = await client.get("/api/miniapp/admin/audience")
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_audience_counts_unique_users_and_keeps_group_total_explicit(monkeypatch) -> None:
    admin = UserSnapshot(telegram_user_id=77, username="owner", first_name="Admin", last_name=None, is_bot=False)
    async with _client(monkeypatch, current_user=admin) as (client, session_factory):
        _seed_audience(session_factory)
        response = await client.get("/api/miniapp/admin/audience?period_days=30")

    assert response.status_code == 200
    metrics = response.json()["metrics"]
    assert metrics["active_bot_users"]["value"] == 2
    assert metrics["total_bot_users"]["value"] == 3
    assert metrics["active_group_users"]["value"] == 1
    assert metrics["total_group_members"]["value"] is None
    assert metrics["total_group_members"]["known_active_members"] == 20
    assert "не равно полной аудитории" in metrics["total_group_members"]["note"]


@pytest.mark.asyncio
async def test_admin_audience_sums_fresh_telegram_member_snapshots_once_per_group(monkeypatch) -> None:
    admin = UserSnapshot(telegram_user_id=77, username="owner", first_name="Admin", last_name=None, is_bot=False)
    async with _client(monkeypatch, current_user=admin) as (client, session_factory):
        _seed_audience(session_factory)
        with Session(session_factory._engine) as session:
            snapshot_time = datetime.now(timezone.utc) - timedelta(hours=1)
            session.add_all(
                [
                    ChatMemberCountSnapshotModel(
                        chat_id=-1001, member_count=120, last_checked_at=snapshot_time, last_success_at=snapshot_time
                    ),
                    ChatMemberCountSnapshotModel(
                        chat_id=-1002, member_count=35, last_checked_at=snapshot_time, last_success_at=snapshot_time
                    ),
                ]
            )
            session.commit()
        response = await client.get("/api/miniapp/admin/audience?period_days=30")

    assert response.status_code == 200
    member_total = response.json()["metrics"]["total_group_members"]
    assert member_total["value"] == 155
    assert member_total["status"] == "available"
    assert member_total["checked_groups"] == member_total["total_groups"] == 2


@pytest.mark.asyncio
async def test_miniapp_admin_rejects_unsupported_audience_period(monkeypatch) -> None:
    admin = UserSnapshot(telegram_user_id=77, username="owner", first_name="Admin", last_name=None, is_bot=False)
    async with _client(monkeypatch, current_user=admin) as (client, _session_factory):
        response = await client.get("/api/miniapp/admin/audience?period_days=2")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_admin_feedback_can_be_filtered_viewed_and_resolved_without_full_reload(monkeypatch) -> None:
    admin = UserSnapshot(telegram_user_id=77, username="owner", first_name="Admin", last_name=None, is_bot=False)
    async with _client(monkeypatch, current_user=admin) as (client, session_factory):
        now = datetime.now(timezone.utc)
        with Session(session_factory._engine) as session:
            session.add(UserModel(telegram_user_id=80, username="writer", first_name="Writer", is_bot=False))
            session.add(
                UserFeatureRequestModel(
                    id=412,
                    user_id=80,
                    title="Новая идея",
                    details="Полное описание обращения",
                    status="open",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
        page = await client.get("/api/miniapp/admin/feedback?status=open&search=writer")
        detail = await client.get("/api/miniapp/admin/feedback/412")
        resolved = await client.post("/api/miniapp/admin/feedback/412/resolve")
        reopened = await client.post("/api/miniapp/admin/feedback/412/reopen")

    assert page.status_code == 200
    assert page.json()["items"][0]["id"] == 412
    assert page.json()["items"][0]["user"]["username"] == "writer"
    assert detail.json()["details"] == "Полное описание обращения"
    assert resolved.json()["status"] == "resolved"
    assert reopened.json()["status"] == "open"


@pytest.mark.asyncio
async def test_admin_alert_and_log_apis_redact_secrets_and_paginate(monkeypatch) -> None:
    admin = UserSnapshot(telegram_user_id=77, username="owner", first_name="Admin", last_name=None, is_bot=False)
    async with _client(monkeypatch, current_user=admin) as (client, session_factory):
        configure_logging(_settings())
        diagnostic_logger = logging.getLogger("test_admin_log_buffer")
        diagnostic_logger.setLevel(logging.INFO)
        diagnostic_logger.info("selara-log-check info")
        diagnostic_logger.warning(
            "selara-log-check Authorization: Bearer hidden Bot_Token=abc123 session=logged-secret"
        )
        with Session(session_factory._engine) as session:
            session.add(
                OperationalAlertModel(
                    id=9,
                    severity="error",
                    source="selara.handlers",
                    fingerprint="f00baa",
                    message=(
                        "failed with Bot_Token=abc123 Authorization: Bearer hidden "
                        "Authorization: Basic dXNlcjpwYXNz password=hunter api_key=secret session_token=xyz"
                    ),
                    context_json={"event": "Message", "command": "/help"},
                    sanitized_traceback="Cookie: sid=private\nSet-Cookie: session=secret\nDATABASE_URL=postgresql://u:pass@db/app",
                    created_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
        alerts_page = await client.get("/api/miniapp/admin/alerts?limit=1")
        alert_detail = await client.get("/api/miniapp/admin/alerts/9")
        logs_page = await client.get("/api/miniapp/admin/logs?level=warning&search=selara-log-check")
        logs_next = await client.get(f"/api/miniapp/admin/logs?level=all&before_id={logs_page.json()['items'][0]['id']}&search=selara-log-check")

    assert alerts_page.status_code == 200
    assert alerts_page.json()["items"][0]["id"] == 9
    assert all(value not in alerts_page.text for value in ("abc123", "hidden", "dXNlcjpwYXNz", "hunter", "secret", "xyz"))
    assert all(value not in alert_detail.text for value in ("private", "session=secret", "u:pass", "db/app"))
    assert "[REDACTED]" in alert_detail.text
    assert logs_page.status_code == 200
    assert logs_page.json()["items"][0]["level"] == "warning"
    assert "[REDACTED]" in logs_page.text
    assert all(value not in logs_page.text for value in ("abc123", "hidden", "logged-secret"))
    assert logs_next.json()["items"][0]["level"] == "info"


@pytest.mark.asyncio
async def test_admin_broadcast_preview_does_not_send_and_send_requires_explicit_confirmation(monkeypatch) -> None:
    admin = UserSnapshot(telegram_user_id=77, username="owner", first_name="Admin", last_name=None, is_bot=False)
    async with _client(monkeypatch, current_user=admin) as (client, session_factory):
        preview = await client.post(
            "/api/miniapp/admin/broadcast/preview",
            json={"body": "<b>Новый релиз</b>", "active_since_days": 3},
        )
        send_without_confirmation = await client.post(
            "/api/miniapp/admin/broadcast",
            json={
                "body": "<b>Новый релиз</b>",
                "active_since_days": 3,
                "confirm": False,
                "idempotency_key": "0123456789abcdef0123",
            },
        )
        confirmed_empty_audience = await client.post(
            "/api/miniapp/admin/broadcast",
            json={
                "body": "<b>Новый релиз</b>",
                "active_since_days": 3,
                "confirm": True,
                "idempotency_key": "0123456789abcdef0124",
                "preview_token": preview.json()["preview_token"],
            },
        )
        with Session(session_factory._engine) as session:
            created_broadcasts = session.query(AdminBroadcastModel).count()

    assert preview.status_code == 200
    assert preview.json()["rendered_text"] == "<b>Новый релиз</b>"
    assert preview.json()["target_count"] == 0
    assert send_without_confirmation.status_code == 422
    assert confirmed_empty_audience.status_code == 422
    assert created_broadcasts == 0


@pytest.mark.asyncio
async def test_admin_broadcast_is_idempotent_and_reports_async_progress(monkeypatch) -> None:
    admin = UserSnapshot(telegram_user_id=77, username="owner", first_name="Admin", last_name=None, is_bot=False)

    class FakeBot:
        def __init__(self):
            self.sent = 0
            self.session = SimpleNamespace(close=self.close)

        async def close(self):
            return None

        async def send_message(self, **kwargs):
            _ = kwargs
            self.sent += 1
            return SimpleNamespace(message_id=700 + self.sent, date=datetime.now(timezone.utc))

    bot = FakeBot()
    monkeypatch.setattr(web_app_module, "Bot", lambda token: bot)
    async with _client(monkeypatch, current_user=admin) as (client, session_factory):
        now = datetime.now(timezone.utc)
        with Session(session_factory._engine) as session:
            session.add_all(
                [
                    UserModel(telegram_user_id=80, username="writer", first_name="Writer", is_bot=False),
                    ChatModel(telegram_chat_id=-1001, type="supergroup", title="Активная группа"),
                    UserChatActivityModel(chat_id=-1001, user_id=80, message_count=5, last_seen_at=now),
                ]
            )
            session.commit()
        preview = await client.post(
            "/api/miniapp/admin/broadcast/preview",
            json={"body": "<b>Проверка</b>", "active_since_days": 3},
        )
        payload = {
            "body": "<b>Проверка</b>",
            "active_since_days": 3,
            "confirm": True,
            "idempotency_key": "0123456789abcdef01234567",
            "preview_token": preview.json()["preview_token"],
        }
        started = await client.post("/api/miniapp/admin/broadcast", json=payload)
        duplicate = await client.post("/api/miniapp/admin/broadcast", json=payload)
        for _ in range(20):
            status = await client.get(f"/api/miniapp/admin/broadcast/{started.json()['broadcast_id']}")
            if status.json().get("status") == "completed":
                break
            await asyncio.sleep(0.01)
        history = await client.get("/api/miniapp/admin/broadcasts?limit=10")

    assert preview.status_code == 200 and preview.json()["target_count"] == 1
    assert started.status_code == 200 and started.json()["status"] == "sending"
    assert duplicate.json()["duplicate"] is True
    assert status.json()["status"] == "completed"
    assert status.json()["sent_count"] == 1 and status.json()["failed_count"] == 0
    assert history.json()["items"][0]["sent_count"] == 1
    assert bot.sent == 1


@pytest.mark.asyncio
async def test_miniapp_me_exposes_only_admin_permission_flag(monkeypatch) -> None:
    admin = UserSnapshot(telegram_user_id=77, username="owner", first_name="Admin", last_name=None, is_bot=False)
    async with _client(monkeypatch, current_user=admin) as (client, _session_factory):
        response = await client.get("/api/miniapp/me")

    assert response.status_code == 200
    assert response.json()["permissions"] == {"admin": True}
    assert "admin_user_id" not in response.json()


@pytest.mark.asyncio
async def test_miniapp_session_uses_verified_telegram_id_for_admin_permission(monkeypatch) -> None:
    async with _client(monkeypatch, current_user=None) as (client, _session_factory):
        init_data = _telegram_init_data(user_id=77, auth_date=int(datetime.now(timezone.utc).timestamp()))
        response = await client.post("/api/miniapp/session", data={"init_data": init_data})
        tampered_fields = parse_qs(init_data)
        tampered_user = json.loads(tampered_fields["user"][0])
        tampered_user["id"] = 80
        tampered_fields["user"] = [json.dumps(tampered_user, separators=(",", ":"))]
        tampered_init_data = urlencode({key: values[0] for key, values in tampered_fields.items()})
        tampered = await client.post(
            "/api/miniapp/session",
            data={"init_data": tampered_init_data},
        )
        created_user_ids = list(_FakeWebAuthRepository.created_user_ids)

    assert response.status_code == 200
    assert response.json()["permissions"] == {"admin": True}
    assert tampered.status_code == 401
    assert created_user_ids == [77, 77]
