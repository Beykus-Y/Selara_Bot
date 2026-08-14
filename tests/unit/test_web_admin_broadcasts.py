from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.core.config import Settings
from selara.core.web_auth import digest_admin_session_token
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


def _location_query_value(location: str, key: str) -> str | None:
    return parse_qs(urlsplit(location).query).get(key, [None])[0]


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


class HybridFakeBot:
    instances: list["HybridFakeBot"] = []

    def __init__(self, token: str) -> None:
        self.token = token
        self.id = 123456
        self.session = SimpleNamespace(close=AsyncMock())
        self.get_chat_member = AsyncMock(side_effect=self._get_chat_member)
        self.get_chat = AsyncMock(return_value=SimpleNamespace(available_reactions=None))
        self.send_message = AsyncMock()
        self.send_photo = AsyncMock(
            side_effect=[
                SimpleNamespace(
                    message_id=9201,
                    date=datetime(2026, 8, 14, 12, 1, tzinfo=timezone.utc),
                    photo=[SimpleNamespace(file_id="cached-photo", file_unique_id="unique-photo")],
                ),
                SimpleNamespace(
                    message_id=9202,
                    date=datetime(2026, 8, 14, 12, 2, tzinfo=timezone.utc),
                    photo=[SimpleNamespace(file_id="cached-photo", file_unique_id="unique-photo")],
                ),
            ]
        )
        self.__class__.instances.append(self)

    @staticmethod
    def _get_chat_member(*, chat_id: int, user_id: int):
        del user_id
        return SimpleNamespace(status="administrator" if chat_id == -1004001 else "member")


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
            session_token=digest_admin_session_token(
                secret=settings.resolved_web_auth_secret,
                token="admin-session",
            ),
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
        await getattr(app.router, "shutdown", app.router._shutdown)()

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
            session_token=digest_admin_session_token(
                secret=settings.resolved_web_auth_secret,
                token="admin-session-html",
            ),
            expires_at=now + timedelta(hours=2),
            now=now,
        )
        await activity_repo.upsert_activity(chat=chat, user=user, event_at=now - timedelta(hours=1))
        await session.commit()

    FakeBot.instances.clear()
    monkeypatch.setattr(web_app_module, "Bot", FakeBot)

    rich_body = (
        "<b>жирный</b> <strong>тоже жирный</strong> "
        "<i>курсив</i> <em>тоже курсив</em> "
        "<u>подчёркнутый</u> <ins>тоже подчёркнутый</ins> "
        "<s>зачёркнутый</s> <strike>тоже зачёркнутый</strike> <del>ещё зачёркнутый</del> "
        '<span class="tg-spoiler">спойлер</span> <tg-spoiler>ещё спойлер</tg-spoiler> '
        '<a href="https://example.com">ссылка</a> '
        '<tg-emoji emoji-id="5368324170671202286">👍</tg-emoji> '
        '<tg-time unix="1647531900" format="wDT">время</tg-time> '
        "<code>код</code> <pre><code class=\"language-python\">print(1)</code></pre> "
        "<blockquote>цитата</blockquote> <blockquote expandable>скрытая цитата</blockquote>"
    )

    app = web_app_module.create_web_app(settings=settings, session_factory=session_factory)
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.admin_session_cookie_name, "admin-session-html")
    try:
        response = await client.post(
            "/app/admin/broadcasts/send",
            data={
                "body": rich_body,
                "chat_ids": str(chat.telegram_chat_id),
            },
            follow_redirects=False,
        )
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 303
    assert len(FakeBot.instances) == 1
    assert FakeBot.instances[0].send_message.await_args_list[0].kwargs["text"] == rich_body

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("invalid_body", "error_fragment"),
    [
        ("<b>Буль<b>ка барабу<code>лька<code>", "Некорректный Telegram HTML"),
        ('<a href="javascript:alert(1)">нажми</a>', "Безопасные ссылки"),
    ],
)
async def test_admin_broadcast_send_rejects_invalid_telegram_html_before_send(
    monkeypatch: pytest.MonkeyPatch,
    invalid_body: str,
    error_fragment: str,
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
            session_token=digest_admin_session_token(
                secret=settings.resolved_web_auth_secret,
                token="admin-session-broken-html",
            ),
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
                "body": invalid_body,
                "chat_ids": str(chat.telegram_chat_id),
            },
            follow_redirects=False,
        )
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 303
    assert response.headers["location"].startswith("/app/admin?error=")
    assert _location_query_value(response.headers["location"], "error") is not None
    assert error_fragment in (_location_query_value(response.headers["location"], "error") or "")
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
            session_token=digest_admin_session_token(
                secret=settings.resolved_web_auth_secret,
                token="admin-session-none",
            ),
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
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 303
    assert _location_query_value(response.headers["location"], "error") == "Не выбрано ни одного чата для рассылки."
    assert FakeBot.instances == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_photo_broadcast_uses_native_for_admin_inline_for_member_and_reuses_file_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.now(timezone.utc)
    admin_chat = ChatSnapshot(telegram_chat_id=-1004001, chat_type="supergroup", title="Admin chat")
    member_chat = ChatSnapshot(telegram_chat_id=-1004002, chat_type="group", title="Member chat")
    user = UserSnapshot(telegram_user_id=801, username="hybrid", first_name="Hybrid", last_name=None, is_bot=False)
    async with session_factory() as session:
        auth_repo = SqlAlchemyAdminAuthRepository(session)
        repo = SqlAlchemyActivityRepository(session)
        await auth_repo.create_session(
            admin_user_id=settings.admin_user_id,
            session_token=digest_admin_session_token(secret=settings.resolved_web_auth_secret, token="hybrid-session"),
            expires_at=now + timedelta(hours=2),
            now=now,
        )
        await repo.upsert_activity(chat=admin_chat, user=user, event_at=now - timedelta(minutes=5))
        await repo.upsert_activity(chat=member_chat, user=user, event_at=now - timedelta(minutes=10))
        await session.commit()

    from io import BytesIO
    from PIL import Image

    photo = BytesIO()
    Image.new("RGB", (40, 30), "blue").save(photo, format="PNG")
    HybridFakeBot.instances.clear()
    monkeypatch.setattr(web_app_module, "Bot", HybridFakeBot)
    app = web_app_module.create_web_app(settings=settings, session_factory=session_factory)
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(settings.admin_session_cookie_name, "hybrid-session")
    try:
        response = await client.post(
            "/api/admin/broadcasts/send",
            data={
                "body": "Новость\n[reactions]\n👍 = Нравится\n👎 = Не нравится\n[/reactions]",
                "media_mode": "photo",
                "chat_ids": [str(admin_chat.telegram_chat_id), str(member_chat.telegram_chat_id)],
            },
            files={"photo": ("notice.png", photo.getvalue(), "image/png")},
        )
        detail_response = await client.get(f"/api/admin/broadcasts/{response.json().get('broadcast_id', 0)}")
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert response.status_code == 200, response.text
    assert detail_response.status_code == 200, detail_response.text
    assert detail_response.json()["page"]["anonymous_reaction_counts"] == []
    assert {item["reaction_mode"] for item in detail_response.json()["page"]["deliveries"]} == {"native", "inline"}
    bot = HybridFakeBot.instances[0]
    assert bot.send_photo.await_count == 2
    first, second = bot.send_photo.await_args_list
    assert first.kwargs["reply_markup"] is None
    assert second.kwargs["reply_markup"].inline_keyboard[0][0].callback_data.startswith("abr:")
    assert second.kwargs["photo"] == "cached-photo"
    assert first.kwargs["caption"] == second.kwargs["caption"]
    assert "<b>Реакции:</b>" in first.kwargs["caption"]

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        broadcasts = await repo.list_recent_admin_broadcasts(limit=1)
        stored = await repo.get_admin_broadcast(broadcast_id=broadcasts[0].id)
        deliveries = await repo.list_admin_broadcast_deliveries(broadcast_id=broadcasts[0].id)
        assert stored is not None
        assert stored.media_type == "photo"
        assert stored.media_file_id == "cached-photo"
        assert {item.chat_id: item.reaction_mode for item in deliveries} == {
            admin_chat.telegram_chat_id: "native",
            member_chat.telegram_chat_id: "inline",
        }

    await engine.dispose()
