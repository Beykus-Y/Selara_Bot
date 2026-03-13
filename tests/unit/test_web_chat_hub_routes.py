from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from selara.core.chat_settings import ChatSettings, default_chat_settings
from selara.core.config import Settings
from selara.core.text_aliases import ALIAS_MODE_DEFAULT
from selara.application.dto import RepStats
from selara.domain.entities import ActivityStats, ChatActivitySummary, ChatRoleDefinition, LeaderboardItem, UserChatOverview, UserSnapshot
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
class ChatHubState:
    settings: Settings
    user: UserSnapshot
    activity_groups: list[UserChatOverview] = field(default_factory=list)
    admin_groups: list[UserChatOverview] = field(default_factory=list)
    chat_settings_by_chat: dict[int, ChatSettings] = field(default_factory=dict)
    alias_mode_by_chat: dict[int, str] = field(default_factory=dict)
    summaries: dict[int, ChatActivitySummary] = field(default_factory=dict)
    leaderboards: dict[tuple[int, str, str], list[LeaderboardItem]] = field(default_factory=dict)
    daily_activity: list[dict[str, object]] = field(default_factory=list)
    richest_payload: dict[str, object] | None = None
    audit_log_calls: list[dict[str, object]] = field(default_factory=list)


class FakeActivityRepo:
    def __init__(self, state: ChatHubState) -> None:
        self._state = state

    async def list_user_admin_chats(self, *, user_id: int):
        return list(self._state.admin_groups)

    async def list_user_activity_chats(self, *, user_id: int, limit: int = 50):
        return list(self._state.activity_groups)

    async def get_chat_settings(self, *, chat_id: int):
        return self._state.chat_settings_by_chat.get(chat_id)

    async def get_chat_alias_mode(self, *, chat_id: int):
        return self._state.alias_mode_by_chat.get(chat_id, ALIAS_MODE_DEFAULT)

    async def get_chat_activity_summary(self, *, chat_id: int):
        return self._state.summaries[chat_id]

    async def get_effective_role_definition(self, *, chat_id: int, user_id: int):
        _ = chat_id, user_id
        return ChatRoleDefinition(
            chat_id=chat_id,
            role_code="participant",
            title_ru="Участник",
            rank=0,
            permissions=(),
            is_system=True,
        )

    async def list_chat_role_definitions(self, *, chat_id: int):
        _ = chat_id
        return []

    async def list_command_access_rules(self, *, chat_id: int):
        _ = chat_id
        return []

    async def list_chat_aliases(self, *, chat_id: int):
        _ = chat_id
        return []

    async def list_chat_triggers(self, *, chat_id: int):
        _ = chat_id
        return []

    async def list_audit_logs(self, *, chat_id: int, limit: int = 10):
        _ = chat_id, limit
        return []

    async def get_top(self, *, chat_id: int, limit: int):
        _ = chat_id, limit
        return []

    async def get_leaderboard(
        self,
        *,
        chat_id: int,
        mode: str,
        period: str,
        since,
        limit: int,
        karma_weight: float,
        activity_weight: float,
    ):
        _ = since, limit, karma_weight, activity_weight
        return list(self._state.leaderboards.get((chat_id, mode, period), []))

    async def set_chat_alias_mode(self, *, chat, mode: str):
        self._state.alias_mode_by_chat[chat.telegram_chat_id] = mode
        return mode

    async def add_audit_log(
        self,
        *,
        chat,
        action_code: str,
        description: str,
        actor_user_id: int | None = None,
        target_user_id: int | None = None,
        meta_json=None,
    ):
        self._state.audit_log_calls.append(
            {
                "chat_id": chat.telegram_chat_id,
                "action_code": action_code,
                "description": description,
                "actor_user_id": actor_user_id,
                "target_user_id": target_user_id,
                "meta_json": meta_json,
            }
        )
        return None


class FakeEconomyRepo:
    def __init__(self, state: ChatHubState) -> None:
        self._state = state

    async def resolve_scope(self, *, mode: str, chat_id: int | None, user_id: int):
        _ = mode, user_id
        if chat_id is None:
            return None, "chat_id is required"
        return SimpleNamespace(scope_id=f"chat:{chat_id}"), None

    async def get_account(self, *, scope, user_id: int):
        _ = scope, user_id
        return None


class FakeWebAuthRepo:
    def __init__(self, state: ChatHubState) -> None:
        self._state = state

    async def get_user_by_session(self, *, session_digest: str, now, touch: bool):
        _ = session_digest, now, touch
        return self._state.user


class DummySession:
    async def commit(self) -> None:
        return None

    async def execute(self, stmt):
        raise AssertionError(f"Unexpected raw SQL execution in test: {stmt!r}")


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
async def _web_client(monkeypatch, state: ChatHubState):
    async def _daily_activity(session, *, chat_id: int, days: int = 7):
        _ = session, chat_id, days
        return state.daily_activity

    async def _richest_payload(session, *, scope_id: str, chat_id: int):
        _ = session, scope_id, chat_id
        return state.richest_payload

    async def _my_stats(repo, *, chat_id: int, user_id: int):
        _ = repo
        return ActivityStats(
            chat_id=chat_id,
            user_id=user_id,
            message_count=0,
            last_seen_at=datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc),
            first_name=state.user.first_name,
            last_name=state.user.last_name,
            username=state.user.username,
        )

    async def _rep_stats(
        repo,
        *,
        chat_id: int,
        user_id: int,
        limit: int,
        karma_weight: float,
        activity_weight: float,
        days: int,
    ):
        _ = repo, chat_id, user_id, limit, karma_weight, activity_weight, days
        return RepStats(
            user_id=state.user.telegram_user_id,
            karma_all=0,
            karma_7d=0,
            activity_1d=0,
            activity_all=0,
            activity_7d=0,
            activity_30d=0,
            rank_all=None,
            rank_7d=None,
        )

    monkeypatch.setattr(web_app_module, "SqlAlchemyActivityRepository", lambda session: FakeActivityRepo(state))
    monkeypatch.setattr(web_app_module, "SqlAlchemyEconomyRepository", lambda session: FakeEconomyRepo(state))
    monkeypatch.setattr(web_app_module, "SqlAlchemyWebAuthRepository", lambda session: FakeWebAuthRepo(state))
    monkeypatch.setattr(web_app_module, "has_permission", _has_permission)
    monkeypatch.setattr(web_app_module, "_build_chat_daily_activity_series", _daily_activity)
    monkeypatch.setattr(web_app_module, "_build_richest_user_payload", _richest_payload)
    monkeypatch.setattr(web_app_module, "get_my_stats", _my_stats)
    monkeypatch.setattr(web_app_module, "get_rep_stats", _rep_stats)

    app = web_app_module.create_web_app(settings=state.settings, session_factory=DummySessionFactory())
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client.cookies.set(state.settings.web_session_cookie_name, "session-token")
    try:
        yield client
    finally:
        await client.aclose()
        await app.router.shutdown()


async def _has_permission(*args, **kwargs):
    _ = args, kwargs
    return True, None, None


def _leaderboard_item(user_id: int, name: str, *, username: str | None, activity: int, karma: int, score: float) -> LeaderboardItem:
    return LeaderboardItem(
        user_id=user_id,
        username=username,
        first_name=name,
        last_name=None,
        activity_value=activity,
        karma_value=karma,
        hybrid_score=score,
        last_seen_at=datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc),
        chat_display_name=None,
    )


@pytest.mark.asyncio
async def test_chat_overview_api_returns_live_summary(monkeypatch) -> None:
    settings = _settings()
    state = ChatHubState(
        settings=settings,
        user=UserSnapshot(telegram_user_id=77, username="viewer", first_name="View", last_name="Er", is_bot=False),
        activity_groups=[_overview(-1001, "Selara Hub")],
        summaries={
            -1001: ChatActivitySummary(
                chat_id=-1001,
                participants_count=12,
                total_messages=345,
                last_activity_at=datetime(2026, 3, 9, 10, 30, tzinfo=timezone.utc),
            )
        },
        leaderboards={
            (-1001, "activity", "day"): [
                _leaderboard_item(101, "Hero", username="hero", activity=18, karma=4, score=18.0),
            ]
        },
        daily_activity=[
            {"date": "2026-03-03", "label": "03.03", "messages": 11},
            {"date": "2026-03-04", "label": "04.03", "messages": 21},
        ],
        richest_payload={"label": "Rich User", "balance": 999},
    )
    state.chat_settings_by_chat[-1001] = default_chat_settings(settings)

    async with _web_client(monkeypatch, state) as client:
        response = await client.get("/api/chat/-1001/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["summary"]["participants_count"] == 12
    assert payload["daily_activity"][0]["messages"] == 11
    assert payload["hero_of_day"]["messages"] == 18
    assert payload["richest_of_day"]["balance"] == 999


@pytest.mark.asyncio
async def test_chat_leaderboard_api_supports_find_me_and_search(monkeypatch) -> None:
    settings = _settings()
    rows = [
        _leaderboard_item(user_id=index, name=f"User{index}", username=f"user{index}", activity=100 - index, karma=index % 7, score=float(100 - index))
        for index in range(1, 55)
    ]
    rows[52] = _leaderboard_item(user_id=77, name="Viewer", username="viewer", activity=42, karma=5, score=42.5)
    rows[10] = _leaderboard_item(user_id=500, name="Alpha", username="alpha", activity=88, karma=9, score=91.0)

    state = ChatHubState(
        settings=settings,
        user=UserSnapshot(telegram_user_id=77, username="viewer", first_name="View", last_name="Er", is_bot=False),
        activity_groups=[_overview(-1001, "Selara Hub")],
        leaderboards={(-1001, "mix", "all"): rows},
    )
    state.chat_settings_by_chat[-1001] = default_chat_settings(settings)

    async with _web_client(monkeypatch, state) as client:
        find_me_response = await client.get("/api/chat/-1001/leaderboard", params={"mode": "mix", "find_me": "1"})
        search_response = await client.get("/api/chat/-1001/leaderboard", params={"mode": "mix", "q": "alpha"})

    assert find_me_response.status_code == 200
    find_me_payload = find_me_response.json()
    assert find_me_payload["ok"] is True
    assert find_me_payload["page"] == 2
    assert find_me_payload["my_rank"] == 53
    assert any(row["user_id"] == 77 and row["is_me"] for row in find_me_payload["rows"])

    assert search_response.status_code == 200
    search_payload = search_response.json()
    assert search_payload["total_rows"] == 1
    assert search_payload["rows"][0]["user_id"] == 500


@pytest.mark.asyncio
async def test_chat_settings_api_includes_alias_mode_setting(monkeypatch) -> None:
    settings = _settings()
    state = ChatHubState(
        settings=settings,
        user=UserSnapshot(telegram_user_id=77, username="viewer", first_name="View", last_name="Er", is_bot=False),
        activity_groups=[_overview(-1001, "Selara Hub")],
        summaries={
            -1001: ChatActivitySummary(
                chat_id=-1001,
                participants_count=12,
                total_messages=345,
                last_activity_at=datetime(2026, 3, 9, 10, 30, tzinfo=timezone.utc),
            )
        },
        alias_mode_by_chat={-1001: "aliases_if_exists"},
    )
    state.chat_settings_by_chat[-1001] = default_chat_settings(settings)

    async with _web_client(monkeypatch, state) as client:
        response = await client.get("/api/chat/-1001/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["alias_mode_setting"]["key"] == "alias_mode"
    assert payload["alias_mode_setting"]["current_value"] == "aliases_if_exists"
    assert payload["alias_mode_setting"]["current_value_display"] == "только алиасы группы"


@pytest.mark.asyncio
async def test_update_chat_setting_persists_alias_mode(monkeypatch) -> None:
    settings = _settings()
    state = ChatHubState(
        settings=settings,
        user=UserSnapshot(telegram_user_id=77, username="viewer", first_name="View", last_name="Er", is_bot=False),
        activity_groups=[_overview(-1001, "Selara Hub")],
        alias_mode_by_chat={-1001: "both"},
    )
    state.chat_settings_by_chat[-1001] = default_chat_settings(settings)

    async with _web_client(monkeypatch, state) as client:
        response = await client.post(
            "/app/chat/-1001/settings",
            headers={"Accept": "application/json", "X-Requested-With": "fetch"},
            data={"key": "alias_mode", "value": "standard_only"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["setting"]["key"] == "alias_mode"
    assert payload["setting"]["current_value"] == "standard_only"
    assert state.alias_mode_by_chat[-1001] == "standard_only"
    assert state.audit_log_calls[-1]["action_code"] == "web_setting_updated"
    assert "both -> standard_only" in str(state.audit_log_calls[-1]["description"])
