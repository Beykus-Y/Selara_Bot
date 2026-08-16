from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
import pytest

from selara.core.config import Settings
from selara.domain.entities import FamilyBundle, FamilyGraph, UserChatOverview, UserSnapshot
from selara.web import app as web_app_module

CHAT_ID = -2001


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


def _overview(chat_id: int, title: str) -> UserChatOverview:
    return UserChatOverview(
        chat_id=chat_id,
        chat_type="group",
        chat_title=title,
        bot_role=None,
        message_count=None,
        last_seen_at=None,
    )


@dataclass
class FamilyState:
    settings: Settings
    user: UserSnapshot
    activity_groups: list[UserChatOverview] = field(default_factory=list)
    admin_groups: list[UserChatOverview] = field(default_factory=list)
    bundle: FamilyBundle | None = None
    graph: FamilyGraph | None = None
    display_names: dict[int, str] = field(default_factory=dict)
    snapshots: dict[int, UserSnapshot] = field(default_factory=dict)


class FakeFamilyActivityRepo:
    def __init__(self, state: FamilyState) -> None:
        self._state = state

    async def list_user_admin_chats(self, *, user_id: int):
        return list(self._state.admin_groups)

    async def list_user_activity_chats(self, *, user_id: int, limit: int = 50):
        return list(self._state.activity_groups)

    async def get_effective_role_definition(self, *, chat_id: int, user_id: int):
        from selara.domain.entities import ChatRoleDefinition

        return ChatRoleDefinition(
            chat_id=chat_id, role_code="participant", title_ru="Участник", rank=0, permissions=(), is_system=True,
        )

    async def list_family_bundle(self, *, chat_id: int, user_id: int):
        _ = chat_id, user_id
        return self._state.bundle

    async def list_family_graph(self, *, chat_id: int, user_id: int):
        _ = chat_id, user_id
        return self._state.graph

    async def get_chat_display_name(self, *, chat_id: int, user_id: int):
        _ = chat_id
        return self._state.display_names.get(user_id)

    async def get_user_snapshot(self, *, user_id: int):
        return self._state.snapshots.get(user_id)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class FakeWebAuthRepo:
    def __init__(self, state: FamilyState) -> None:
        self._state = state

    async def get_user_by_session(self, *, session_digest: str, now, touch: bool):
        _ = session_digest, now, touch
        return self._state.user


class DummySession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def execute(self, stmt):
        _ = stmt
        raise AssertionError("execute should not be called in this test")


class DummySessionFactory:
    def __call__(self):
        session = DummySession()

        class _Manager:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Manager()


@asynccontextmanager
async def _web_client(monkeypatch, state: FamilyState):
    monkeypatch.setattr(web_app_module, "SqlAlchemyActivityRepository", lambda session: FakeFamilyActivityRepo(state))
    monkeypatch.setattr(web_app_module, "SqlAlchemyWebAuthRepository", lambda session: FakeWebAuthRepo(state))
    monkeypatch.setattr(web_app_module, "has_permission", _has_permission)

    app = web_app_module.create_web_app(settings=state.settings, session_factory=DummySessionFactory())
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(state.settings.web_session_cookie_name, "session-token")
    try:
        yield client
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()


async def _has_permission(*args, **kwargs):
    _ = args, kwargs
    return True, None, None


@pytest.mark.asyncio
async def test_family_page_resolves_a_real_focus_label_when_subject_has_no_relations(monkeypatch) -> None:
    # Regression guard: an isolated user (no spouse/parents/children/pets in
    # this chat) is a real, common case — list_family_graph then returns zero
    # nodes, so the subject's own label can't be looked up from the node list.
    # focus_label used to fall back to a raw "user:<id>" string instead of the
    # person's real display name.
    settings = _settings()
    state = FamilyState(
        settings=settings,
        user=UserSnapshot(telegram_user_id=77, username="viewer", first_name="View", last_name="Er", is_bot=False),
        activity_groups=[_overview(CHAT_ID, "Selara Hub")],
        bundle=FamilyBundle(
            subject_user_id=77,
            spouse_user_id=None,
            parents=(),
            grandparents=(),
            step_parents=(),
            siblings=(),
            children=(),
            pets=(),
            owners=(),
        ),
        graph=FamilyGraph(focus_user_id=77, node_user_ids=(), edges=()),
        display_names={77: "Viewer Display Name"},
    )

    async with _web_client(monkeypatch, state) as client:
        response = await client.get(f"/app/family/{CHAT_ID}")

    assert response.status_code == 200
    assert "Viewer Display Name" in response.text
    assert "user:77" not in response.text


@pytest.mark.asyncio
async def test_family_page_shows_empty_state_when_subject_has_no_relations(monkeypatch) -> None:
    settings = _settings()
    state = FamilyState(
        settings=settings,
        user=UserSnapshot(telegram_user_id=77, username="viewer", first_name="View", last_name="Er", is_bot=False),
        activity_groups=[_overview(CHAT_ID, "Selara Hub")],
        bundle=FamilyBundle(
            subject_user_id=77,
            spouse_user_id=None,
            parents=(),
            grandparents=(),
            step_parents=(),
            siblings=(),
            children=(),
            pets=(),
            owners=(),
        ),
        graph=FamilyGraph(focus_user_id=77, node_user_ids=(), edges=()),
        display_names={77: "Viewer Display Name"},
    )

    async with _web_client(monkeypatch, state) as client:
        response = await client.get(f"/app/family/{CHAT_ID}")

    assert response.status_code == 200
    assert "family-empty" in response.text
