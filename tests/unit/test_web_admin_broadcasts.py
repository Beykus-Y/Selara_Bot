from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.core.config import Settings
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.admin_auth import SqlAlchemyAdminAuthRepository
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
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


class FakeBot:
    instances: list["FakeBot"] = []

    def __init__(self, token: str) -> None:
        self.token = token
        self.session = SimpleNamespace(close=AsyncMock())
        self.send_message = AsyncMock(
            side_effect=[
                SimpleNamespace(message_id=9101, date=datetime(2026, 4, 11, 12, 1, tzinfo=timezone.utc)),
                SimpleNamespace(message_id=9102, date=datetime(2026, 4, 11, 12, 2, tzinfo=timezone.utc)),
            ]
        )
        self.__class__.instances.append(self)


@pytest.mark.asyncio
async def test_admin_broadcast_send_creates_deliveries_and_tracks_replies(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.now(timezone.utc)
    active_chat_one = ChatSnapshot(telegram_chat_id=-1001001, chat_type="group", title="Alpha")
    active_chat_two = ChatSnapshot(telegram_chat_id=-1001002, chat_type="supergroup", title="Beta")
    stale_chat = ChatSnapshot(telegram_chat_id=-1001003, chat_type="group", title="Gamma")
    alpha_user = UserSnapshot(telegram_user_id=501, username="alpha", first_name="Alpha", last_name=None, is_bot=False)
    beta_user = UserSnapshot(telegram_user_id=502, username="beta", first_name="Beta", last_name=None, is_bot=False)

    async with session_factory() as session:
        auth_repo = SqlAlchemyAdminAuthRepository(session)
        activity_repo = SqlAlchemyActivityRepository(session)
        await auth_repo.create_session(
            admin_user_id=settings.admin_user_id,
            session_token="admin-session",
            expires_at=now + timedelta(hours=2),
            now=now,
        )
        await activity_repo.upsert_activity(chat=active_chat_one, user=alpha_user, event_at=now - timedelta(hours=4))
        await activity_repo.upsert_activity(chat=active_chat_two, user=beta_user, event_at=now - timedelta(days=2))
        await activity_repo.upsert_activity(chat=stale_chat, user=alpha_user, event_at=now - timedelta(days=5))
        await session.commit()

    FakeBot.instances.clear()
    monkeypatch.setattr(web_app_module, "Bot", FakeBot)

    app = web_app_module.create_web_app(settings=settings, session_factory=session_factory)
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.admin_session_cookie_name, "admin-session")
    try:
        response = await client.post(
            "/app/admin/broadcasts/send",
            data={
                "body": "Спасибо за использование Selara",
                "chat_ids": str(active_chat_one.telegram_chat_id),
            },
            follow_redirects=False,
        )
    finally:
        await client.aclose()
        await app.router.shutdown()

    assert response.status_code == 303
    assert response.headers["location"].startswith("/app/admin/broadcasts/")

    assert len(FakeBot.instances) == 1
    fake_bot = FakeBot.instances[0]
    assert fake_bot.send_message.await_count == 1
    sent_chat_ids = [call.kwargs["chat_id"] for call in fake_bot.send_message.await_args_list]
    assert sent_chat_ids == [active_chat_one.telegram_chat_id]
    assert fake_bot.send_message.await_args_list[0].kwargs["text"] == "Спасибо за использование Selara"

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        broadcasts = await repo.list_recent_admin_broadcasts(limit=5)

        assert len(broadcasts) == 1
        broadcast = broadcasts[0]
        assert broadcast.body == "Спасибо за использование Selara"
        assert broadcast.target_count == 1
        assert broadcast.sent_count == 1
        assert broadcast.failed_count == 0

        deliveries = await repo.list_admin_broadcast_deliveries(broadcast_id=broadcast.id)
        assert {item.chat_id for item in deliveries} == {active_chat_one.telegram_chat_id}
        assert all(item.status == "sent" for item in deliveries)
        assert active_chat_two.telegram_chat_id not in {item.chat_id for item in deliveries}
        assert stale_chat.telegram_chat_id not in {item.chat_id for item in deliveries}

        inserted = await repo.record_admin_broadcast_reply(
            chat=active_chat_one,
            user=alpha_user,
            reply_to_message_id=deliveries[0].telegram_message_id or 0,
            telegram_message_id=9911,
            message_type="text",
            text="Вам тоже спасибо",
            caption=None,
            raw_message_json={"message_id": 9911, "text": "Вам тоже спасибо"},
            sent_at=now,
        )
        assert inserted is True

        replies = await repo.list_admin_broadcast_replies(broadcast_id=broadcast.id)
        assert len(replies) == 1
        assert replies[0].chat_id == active_chat_one.telegram_chat_id
        assert replies[0].user.telegram_user_id == alpha_user.telegram_user_id
        assert replies[0].text == "Вам тоже спасибо"

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_broadcast_send_accepts_telegram_html(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.now(timezone.utc)
    chat = ChatSnapshot(telegram_chat_id=-1002001, chat_type="group", title="HTML Chat")
    user = UserSnapshot(telegram_user_id=601, username="html", first_name="Html", last_name=None, is_bot=False)

    async with session_factory() as session:
        auth_repo = SqlAlchemyAdminAuthRepository(session)
        activity_repo = SqlAlchemyActivityRepository(session)
        await auth_repo.create_session(
            admin_user_id=settings.admin_user_id,
            session_token="admin-session-html",
            expires_at=now + timedelta(hours=2),
            now=now,
        )
        await activity_repo.upsert_activity(chat=chat, user=user, event_at=now - timedelta(hours=1))
        await session.commit()

    FakeBot.instances.clear()
    monkeypatch.setattr(web_app_module, "Bot", FakeBot)

    app = web_app_module.create_web_app(settings=settings, session_factory=session_factory)
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.admin_session_cookie_name, "admin-session-html")
    try:
        response = await client.post(
            "/app/admin/broadcasts/send",
            data={
                "body": "<b>Бульбулятор</b>",
                "chat_ids": str(chat.telegram_chat_id),
            },
            follow_redirects=False,
        )
    finally:
        await client.aclose()
        await app.router.shutdown()

    assert response.status_code == 303
    assert len(FakeBot.instances) == 1
    assert FakeBot.instances[0].send_message.await_args_list[0].kwargs["text"] == "<b>Бульбулятор</b>"

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_broadcast_send_rejects_invalid_telegram_html_before_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.now(timezone.utc)
    chat = ChatSnapshot(telegram_chat_id=-1002101, chat_type="group", title="Broken HTML Chat")
    user = UserSnapshot(telegram_user_id=611, username="broken", first_name="Broken", last_name=None, is_bot=False)

    async with session_factory() as session:
        auth_repo = SqlAlchemyAdminAuthRepository(session)
        activity_repo = SqlAlchemyActivityRepository(session)
        await auth_repo.create_session(
            admin_user_id=settings.admin_user_id,
            session_token="admin-session-broken-html",
            expires_at=now + timedelta(hours=2),
            now=now,
        )
        await activity_repo.upsert_activity(chat=chat, user=user, event_at=now - timedelta(hours=1))
        await session.commit()

    FakeBot.instances.clear()
    monkeypatch.setattr(web_app_module, "Bot", FakeBot)

    app = web_app_module.create_web_app(settings=settings, session_factory=session_factory)
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.admin_session_cookie_name, "admin-session-broken-html")
    try:
        response = await client.post(
            "/app/admin/broadcasts/send",
            data={
                "body": "<b>Буль<b>ка барабу<code>лька<code>",
                "chat_ids": str(chat.telegram_chat_id),
            },
            follow_redirects=False,
        )
    finally:
        await client.aclose()
        await app.router.shutdown()

    assert response.status_code == 303
    assert response.headers["location"].startswith("/app/admin?error=")
    assert "Некорректный%20Telegram%20HTML" in response.headers["location"]
    assert FakeBot.instances == []

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        broadcasts = await repo.list_recent_admin_broadcasts(limit=5)
        assert broadcasts == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_broadcast_send_with_no_selected_chats_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.now(timezone.utc)
    chat = ChatSnapshot(telegram_chat_id=-1003001, chat_type="group", title="Only Chat")
    user = UserSnapshot(telegram_user_id=701, username="solo", first_name="Solo", last_name=None, is_bot=False)

    async with session_factory() as session:
        auth_repo = SqlAlchemyAdminAuthRepository(session)
        activity_repo = SqlAlchemyActivityRepository(session)
        await auth_repo.create_session(
            admin_user_id=settings.admin_user_id,
            session_token="admin-session-none",
            expires_at=now + timedelta(hours=2),
            now=now,
        )
        await activity_repo.upsert_activity(chat=chat, user=user, event_at=now - timedelta(hours=1))
        await session.commit()

    FakeBot.instances.clear()
    monkeypatch.setattr(web_app_module, "Bot", FakeBot)

    app = web_app_module.create_web_app(settings=settings, session_factory=session_factory)
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.admin_session_cookie_name, "admin-session-none")
    try:
        response = await client.post(
            "/app/admin/broadcasts/send",
            data={"body": "Никому не отправлять"},
            follow_redirects=False,
        )
    finally:
        await client.aclose()
        await app.router.shutdown()

    assert response.status_code == 303
    assert "Не%20выбрано%20ни%20одного%20чата" in response.headers["location"]
    assert FakeBot.instances == []

    await engine.dispose()
