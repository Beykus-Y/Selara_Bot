from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

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
from selara.core.bot_runtime import mark_bot_polling_started, mark_bot_polling_stopped
from selara.core.logging import configure_logging
from selara.domain.entities import UserSnapshot
from selara.infrastructure.db.models import (
    AdminBroadcastDeliveryModel,
    AdminBroadcastModel,
    AdminBroadcastReplyModel,
    AiFeatureInvocationModel,
    ChatEntitlementModel,
    UserEntitlementModel,
    ChatMemberCountSnapshotModel,
    ChatMetricsModel,
    ChatModel,
    LlmUsageLogModel,
    OperationalAlertModel,
    SelaraAiPaymentModel,
    SelaraAiPurchaseIntentModel,
    UserFeatureRequestModel,
    UserChatMessageEventModel,
    UserChatActivityModel,
    UserModel,
)
from selara.infrastructure.db.selara_ai_payment_refund import SelaraAiPaymentRefundModel
from selara.web import app as web_app_module


def _settings(admin_user_id: int | None = 77, **extra) -> Settings:
    return Settings.model_validate(
        {
            **extra,
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
async def _client(
    monkeypatch, *, admin_user_id: int | None = 77, current_user: UserSnapshot | None = None, **settings_extra
):
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
            AiFeatureInvocationModel.__table__,
            LlmUsageLogModel.__table__,
            ChatEntitlementModel.__table__,
            UserEntitlementModel.__table__,
            SelaraAiPurchaseIntentModel.__table__,
            SelaraAiPaymentModel.__table__,
            SelaraAiPaymentRefundModel.__table__,
        ],
    )
    session_factory = _SessionFactory(engine)
    _FakeWebAuthRepository.current_user = current_user
    _FakeWebAuthRepository.created_user_ids = []
    monkeypatch.setattr(web_app_module, "SqlAlchemyWebAuthRepository", _FakeWebAuthRepository)
    app = web_app_module.create_web_app(
        settings=_settings(admin_user_id, **settings_extra), session_factory=session_factory
    )
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
                UserModel(telegram_user_id=91, is_bot=False),
                UserModel(telegram_user_id=999, is_bot=True),
                ChatModel(telegram_chat_id=-1001, type="supergroup", title="Первый чат"),
                ChatModel(telegram_chat_id=-1002, type="group", title="Второй чат"),
                ChatModel(telegram_chat_id=90, type="private", title=None),
                ChatModel(telegram_chat_id=91, type="private", title=None),
                ChatMetricsModel(chat_id=-1001, active_members_count=12),
                ChatMetricsModel(chat_id=-1002, active_members_count=8),
            ]
        )
        session.add_all(
            [
                UserChatMessageEventModel(chat_id=-1001, user_id=80, sent_at=now - timedelta(days=2)),
                UserChatMessageEventModel(chat_id=-1002, user_id=80, sent_at=now - timedelta(days=1)),
                UserChatMessageEventModel(chat_id=90, user_id=90, sent_at=now - timedelta(days=3)),
                UserChatMessageEventModel(chat_id=91, user_id=91, sent_at=now - timedelta(days=120)),
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
    assert metrics["active_bot_users"]["value"] == 1
    assert metrics["total_bot_users"]["value"] == 2
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
async def test_admin_audience_exposes_inaccessible_groups_without_retrying_them_as_missing(monkeypatch) -> None:
    admin = UserSnapshot(telegram_user_id=77, username="owner", first_name="Admin", last_name=None, is_bot=False)
    async with _client(monkeypatch, current_user=admin) as (client, session_factory):
        _seed_audience(session_factory)
        with Session(session_factory._engine) as session:
            inaccessible = ChatModel(
                telegram_chat_id=-1003,
                type="supergroup",
                title="Removed group",
                is_bot_member=False,
            )
            session.add(inaccessible)
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
    members = response.json()["metrics"]["total_group_members"]
    assert members["status"] == "available"
    assert members["value"] == 155
    assert members["checked_groups"] == members["total_groups"] == 2
    assert members["inaccessible_groups"] == 1


@pytest.mark.asyncio
async def test_admin_broadcast_empty_multipart_group_selection_stays_empty(monkeypatch) -> None:
    admin = UserSnapshot(telegram_user_id=77, username="owner", first_name="Admin", last_name=None, is_bot=False)
    async with _client(monkeypatch, current_user=admin) as (client, session_factory):
        _seed_audience(session_factory)
        response = await client.post(
            "/api/miniapp/admin/broadcast/preview",
            files=[
                ("body", (None, "Announcement")),
                ("active_since_days", (None, "30")),
                ("media_mode", (None, "text")),
                ("chat_ids", (None, "")),
            ],
        )

    assert response.status_code == 200
    assert response.json()["target_count"] == 0


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
            self.chat_ids = []
            self.session = SimpleNamespace(close=self.close)

        async def close(self):
            return None

        async def send_message(self, **kwargs):
            self.sent += 1
            self.chat_ids.append(kwargs["chat_id"])
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
        with Session(session_factory._engine) as session:
            session.add(ChatModel(telegram_chat_id=-1002, type="supergroup", title="Добавлена после preview"))
            session.add(UserChatActivityModel(chat_id=-1002, user_id=80, message_count=1, last_seen_at=now))
            session.commit()
        payload = {
            "body": "<b>Проверка</b>",
            "active_since_days": 3,
            "confirm": True,
            "idempotency_key": "0123456789abcdef01234567",
            "preview_token": preview.json()["preview_token"],
        }
        started, duplicate = await asyncio.gather(
            client.post("/api/miniapp/admin/broadcast", json=payload),
            client.post("/api/miniapp/admin/broadcast", json=payload),
        )
        changed_preview = await client.post(
            "/api/miniapp/admin/broadcast/preview",
            json={"body": "<b>Другой текст</b>", "active_since_days": 3},
        )
        reused_key_with_changes = await client.post(
            "/api/miniapp/admin/broadcast",
            json={**payload, "body": "<b>Другой текст</b>", "preview_token": changed_preview.json()["preview_token"]},
        )
        for _ in range(20):
            status = await client.get(f"/api/miniapp/admin/broadcast/{started.json()['broadcast_id']}")
            if status.json().get("status") == "completed":
                break
            await asyncio.sleep(0.01)
        history = await client.get("/api/miniapp/admin/broadcasts?limit=10")

    assert preview.status_code == 200 and preview.json()["target_count"] == 1
    assert started.status_code == duplicate.status_code == 200
    assert changed_preview.status_code == 200
    assert reused_key_with_changes.status_code == 409
    assert started.json()["broadcast_id"] == duplicate.json()["broadcast_id"]
    assert sorted([started.json().get("duplicate", False), duplicate.json().get("duplicate", False)]) == [False, True]
    assert status.json()["status"] == "completed"
    assert status.json()["sent_count"] == 1 and status.json()["failed_count"] == 0
    assert history.json()["items"][0]["sent_count"] == 1
    assert bot.sent == 1
    assert bot.chat_ids == [-1001]


@pytest.mark.asyncio
async def test_admin_health_uses_telegram_polling_and_api_probe(monkeypatch) -> None:
    admin = UserSnapshot(telegram_user_id=77, username="owner", first_name="Admin", last_name=None, is_bot=False)

    class FakeBot:
        def __init__(self, token):
            _ = token
            self.session = SimpleNamespace(close=self.close)

        async def get_me(self):
            return SimpleNamespace(id=123456)

        async def close(self):
            return None

    monkeypatch.setattr(web_app_module, "Bot", FakeBot)
    mark_bot_polling_started()
    try:
        async with _client(monkeypatch, current_user=admin) as (client, _session_factory):
            response = await client.get("/api/miniapp/admin/health")
    finally:
        mark_bot_polling_stopped()

    assert response.status_code == 200
    telegram = response.json()["components"]["telegram_bot"]
    assert telegram["status"] == "healthy"
    assert isinstance(telegram["latency_ms"], int)
    assert telegram["last_success_at"] is not None


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


_AI_ADMIN_ROUTES = (
    "/api/miniapp/admin/ai/summary",
    "/api/miniapp/admin/ai/breakdown",
    "/api/miniapp/admin/ai/readiness",
    "/api/miniapp/admin/monetization/summary",
    "/api/miniapp/admin/monetization/payments",
    "/api/miniapp/admin/monetization/payments/1",
    "/api/miniapp/admin/monetization/entitlements",
    "/api/miniapp/admin/monetization/personal-config",
)


@pytest.mark.asyncio
async def test_ai_and_monetization_routes_are_owner_only(monkeypatch) -> None:
    group_admin = UserSnapshot(telegram_user_id=80, username="gadmin", first_name="Group", last_name=None, is_bot=False)
    async with _client(monkeypatch, current_user=None) as (client, _session_factory):
        for route in _AI_ADMIN_ROUTES:
            assert (await client.get(route)).status_code == 401, route

    async with _client(monkeypatch, current_user=group_admin) as (client, session_factory):
        with Session(session_factory._engine) as session:
            session.add(
                ChatEntitlementModel(
                    chat_id=-1001, product_key="selara_ai_monthly", status="active",
                    valid_from=datetime.now(timezone.utc), valid_until=datetime.now(timezone.utc) + timedelta(days=3),
                )
            )
            session.commit()
        for route in _AI_ADMIN_ROUTES:
            assert (await client.get(route)).status_code == 403, route

    async with _client(monkeypatch, admin_user_id=None, current_user=group_admin) as (client, _session_factory):
        for route in _AI_ADMIN_ROUTES:
            assert (await client.get(route)).status_code == 403, route


@pytest.mark.asyncio
async def test_ai_and_monetization_routes_validate_inputs(monkeypatch) -> None:
    admin = UserSnapshot(telegram_user_id=77, username="owner", first_name="Admin", last_name=None, is_bot=False)
    async with _client(monkeypatch, current_user=admin) as (client, _session_factory):
        for route in (
            "/api/miniapp/admin/ai/summary",
            "/api/miniapp/admin/ai/breakdown",
            "/api/miniapp/admin/monetization/summary",
        ):
            assert (await client.get(route, params={"period_days": 14})).status_code == 422, route
        base = "/api/miniapp/admin/monetization/payments"
        assert (await client.get(base, params={"state": "bogus"})).status_code == 422
        assert (await client.get(base, params={"refund": "bogus"})).status_code == 422
        assert (await client.get(base, params={"period_days": 5})).status_code == 422
        assert (await client.get(base, params={"cursor": "garbage"})).status_code == 422
        assert (await client.get(base, params={"limit": 500})).status_code == 422
        assert (await client.get(f"{base}/999")).status_code == 404


@pytest.mark.asyncio
async def test_ai_summary_empty_period_is_not_an_error_and_cost_is_a_decimal_string(monkeypatch) -> None:
    admin = UserSnapshot(telegram_user_id=77, username="owner", first_name="Admin", last_name=None, is_bot=False)
    async with _client(monkeypatch, current_user=admin) as (client, _session_factory):
        summary = (await client.get("/api/miniapp/admin/ai/summary", params={"period_days": 30})).json()
        breakdown = (await client.get("/api/miniapp/admin/ai/breakdown")).json()
        monetization = (await client.get("/api/miniapp/admin/monetization/summary")).json()
        payments = (await client.get("/api/miniapp/admin/monetization/payments")).json()
        entitlements = (await client.get("/api/miniapp/admin/monetization/entitlements")).json()

    assert summary["invocations"] == 0 and summary["provider_calls"] == 0
    assert summary["unknown_cost_calls"] == 0 and Decimal(summary["known_cost_usd"]) == 0
    assert summary["average_known_cost_per_invocation_usd"] is None
    assert breakdown["features"] == [] and breakdown["models"] == []
    assert monetization["stars_revenue"] == 0 and monetization["active_paid_chats"] == 0
    assert monetization["refunds"] == {"pending": 0, "refunded": 0, "failed": 0}
    assert payments["items"] == [] and payments["next_cursor"] is None
    assert entitlements["items"] == []


@pytest.mark.asyncio
async def test_ai_readiness_reports_missing_price_without_exposing_secrets(monkeypatch) -> None:
    admin = UserSnapshot(telegram_user_id=77, username="owner", first_name="Admin", last_name=None, is_bot=False)
    async with _client(monkeypatch, current_user=admin) as (client, _session_factory):
        response = await client.get("/api/miniapp/admin/ai/readiness")

    body = response.json()
    checks = {item["key"]: item for item in body["checks"]}
    assert body["checkout"]["configured"] is False
    assert checks["stars_price"]["status"] == "missing"
    assert "SELARA_AI_PRICE_STARS" in checks["stars_price"]["detail"]
    assert checks["llm_provider"]["status"] == "unavailable"
    assert "123456:TEST" not in response.text and "test-secret" not in response.text


@pytest.mark.asyncio
async def test_ai_readiness_separates_price_from_checkout_and_normalizes_provider_key(monkeypatch) -> None:
    admin = UserSnapshot(telegram_user_id=77, username="owner", first_name="Admin", last_name=None, is_bot=False)

    async def checks(**settings_extra):
        async with _client(monkeypatch, current_user=admin, **settings_extra) as (client, _session_factory):
            body = (await client.get("/api/miniapp/admin/ai/readiness")).json()
        return body, {item["key"]: item for item in body["checks"]}

    # Price set but provider disabled: the price row stays about the price, checkout is off.
    body, rows = await checks(SELARA_AI_PRICE_STARS=100, LLM_ENABLED="false", LLM_API_KEY="key")
    assert rows["stars_price"]["status"] == "ok" and "Checkout" not in rows["stars_price"]["detail"]
    assert rows["llm_provider"]["status"] == "unavailable"
    assert rows["checkout"]["status"] == "unavailable" and body["checkout"]["configured"] is False

    # A whitespace-only key is not configured, exactly like /premium.
    body, rows = await checks(SELARA_AI_PRICE_STARS=100, LLM_ENABLED="true", LLM_API_KEY="   ")
    assert rows["llm_provider"]["status"] == "unavailable"
    assert rows["checkout"]["status"] == "unavailable" and body["checkout"]["configured"] is False

    body, rows = await checks(SELARA_AI_PRICE_STARS=100, LLM_ENABLED="true", LLM_API_KEY="key")
    assert rows["llm_provider"]["status"] == "ok" and rows["checkout"]["status"] == "ok"
    assert body["checkout"]["configured"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [{"LLM_MODEL": ""}, {"LLM_SUMMARY_MODEL": ""}, {"LLM_TIMEOUT_SECONDS": "0"}, {"LLM_API_KEY": " "}],
)
async def test_ai_readiness_and_checkout_agree_on_invalid_llm_config(monkeypatch, extra) -> None:
    admin = UserSnapshot(telegram_user_id=77, username="owner", first_name="Admin", last_name=None, is_bot=False)
    async with _client(
        monkeypatch, current_user=admin, SELARA_AI_PRICE_STARS=100, LLM_ENABLED="true",
        **{"LLM_API_KEY": "key", **extra},
    ) as (client, _session_factory):
        body = (await client.get("/api/miniapp/admin/ai/readiness")).json()
    rows = {item["key"]: item for item in body["checks"]}
    assert rows["llm_provider"]["status"] == "unavailable"
    assert rows["checkout"]["status"] == "unavailable" and body["checkout"]["configured"] is False
