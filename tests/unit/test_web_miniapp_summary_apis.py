"""Read-only MVP summary APIs for the Mini App economy/family/audit tabs
(see docs/WEB_UI_MODERNIZATION_TODO.md Stage 6, "Mini App views" item).

Scope decided by the user (2026-08-17, via Telegram): minimal read-only
summaries, not full parity with the Jinja economy.html/family.html/
audit.html pages -- these tests only cover that each endpoint returns the
expected summary shape and enforces the same auth/permission checks the
Jinja pages already have, reusing existing fake-repo fixtures rather than
re-deriving them.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from selara.core.chat_settings import default_chat_settings
from selara.domain.entities import ChatAuditLogEntry, UserSnapshot
from selara.web import app as web_app_module

import test_web_chat_hub_routes as hub_test
import test_web_family_routes as family_test


@asynccontextmanager
async def _hub_client(monkeypatch, state: hub_test.ChatHubState):
    monkeypatch.setattr(web_app_module, "SqlAlchemyActivityRepository", lambda session: hub_test.FakeActivityRepo(state))
    monkeypatch.setattr(web_app_module, "SqlAlchemyEconomyRepository", lambda session: hub_test.FakeEconomyRepo(state))
    monkeypatch.setattr(web_app_module, "SqlAlchemyWebAuthRepository", lambda session: hub_test.FakeWebAuthRepo(state))
    monkeypatch.setattr(web_app_module, "has_permission", hub_test._has_permission)

    app = web_app_module.create_web_app(settings=state.settings, session_factory=hub_test.DummySessionFactory())
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    client.cookies.set(state.settings.web_session_cookie_name, "session-token")
    try:
        yield client
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()


@pytest.mark.asyncio
async def test_miniapp_economy_api_returns_a_balance_and_plot_summary(monkeypatch) -> None:
    settings = hub_test._settings()
    state = hub_test.ChatHubState(
        settings=settings,
        user=UserSnapshot(telegram_user_id=77, username="viewer", first_name="View", last_name="Er", is_bot=False),
        activity_groups=[hub_test._overview(-1001, "Selara Hub")],
        chat_settings_by_chat={-1001: replace(default_chat_settings(settings), economy_enabled=True, economy_mode="local")},
        economy_account=SimpleNamespace(id=1, balance=420, growth_size_mm=15, growth_actions=2),
        economy_farm=SimpleNamespace(
            account_id=1, farm_level=2, size_tier="small", negative_event_streak=0, last_planted_crop_code="radish",
        ),
        economy_plots=[
            SimpleNamespace(plot_no=1, crop_code=None, ready_at=None),
            SimpleNamespace(plot_no=2, crop_code="radish", ready_at=None),
        ],
        economy_inventory=[SimpleNamespace(item_code="item:energy_drink", quantity=2)],
    )

    async with _hub_client(monkeypatch, state) as client:
        response = await client.get("/api/miniapp/chat/-1001/economy")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["economy"]["balance"] == 420
    assert payload["economy"]["farm_level"] == 2
    assert payload["economy"]["plots_growing"] == 1
    assert payload["economy"]["plots_empty"] == 1
    assert payload["economy"]["inventory_item_count"] == 1


@pytest.mark.asyncio
async def test_miniapp_economy_api_rejects_when_economy_disabled(monkeypatch) -> None:
    settings = hub_test._settings()
    state = hub_test.ChatHubState(
        settings=settings,
        user=UserSnapshot(telegram_user_id=77, username="viewer", first_name="View", last_name="Er", is_bot=False),
        activity_groups=[hub_test._overview(-1001, "Selara Hub")],
        chat_settings_by_chat={-1001: replace(default_chat_settings(settings), economy_enabled=False)},
    )

    async with _hub_client(monkeypatch, state) as client:
        response = await client.get("/api/miniapp/chat/-1001/economy")

    assert response.status_code == 403
    assert response.json()["ok"] is False


@pytest.mark.asyncio
async def test_miniapp_audit_api_returns_recent_rows(monkeypatch) -> None:
    settings = hub_test._settings()
    state = hub_test.ChatHubState(
        settings=settings,
        user=UserSnapshot(telegram_user_id=77, username="viewer", first_name="View", last_name="Er", is_bot=False),
        activity_groups=[hub_test._overview(-1001, "Selara Hub")],
        chat_settings_by_chat={-1001: default_chat_settings(settings)},
        audit_entries=[
            ChatAuditLogEntry(
                id=1, chat_id=-1001, action_code="ban_applied", actor_user_id=77, target_user_id=88,
                description="Забанен за спам", meta_json=None,
                created_at=datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc),
            ),
        ],
    )

    async with _hub_client(monkeypatch, state) as client:
        response = await client.get("/api/miniapp/chat/-1001/audit")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["audit"]["total_rows"] == 1
    row = payload["audit"]["rows"][0]
    assert row["description"] == "Забанен за спам"
    assert row["target_label"] is not None


@asynccontextmanager
async def _family_client(monkeypatch, state: family_test.FamilyState):
    monkeypatch.setattr(family_test.web_app_module, "SqlAlchemyActivityRepository", lambda session: family_test.FakeFamilyActivityRepo(state))
    monkeypatch.setattr(family_test.web_app_module, "SqlAlchemyWebAuthRepository", lambda session: family_test.FakeWebAuthRepo(state))
    monkeypatch.setattr(family_test.web_app_module, "has_permission", family_test._has_permission)

    app = family_test.web_app_module.create_web_app(settings=state.settings, session_factory=family_test.DummySessionFactory())
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    client.cookies.set(state.settings.web_session_cookie_name, "session-token")
    try:
        yield client
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()


@pytest.mark.asyncio
async def test_miniapp_family_api_returns_a_bundle_summary(monkeypatch) -> None:
    state = family_test.FamilyState(
        settings=family_test._settings(),
        user=UserSnapshot(telegram_user_id=77, username="viewer", first_name="View", last_name="Er", is_bot=False),
        activity_groups=[family_test._overview(family_test.CHAT_ID, "Клуб настолок")],
        bundle=family_test.FamilyBundle(
            subject_user_id=77, spouse_user_id=None,
            parents=(), grandparents=(), step_parents=(), siblings=(), children=(88,), pets=(), owners=(),
        ),
        graph=family_test.FamilyGraph(focus_user_id=77, node_user_ids=(77, 88), edges=()),
        display_names={88: "Малыш"},
        snapshots={},
    )

    async with _family_client(monkeypatch, state) as client:
        response = await client.get(f"/api/miniapp/family/{family_test.CHAT_ID}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["family"]["summary"][2] == {"label": "Дети", "value": "1"}
    member_ids = {member["id"] for member in payload["family"]["members"]}
    assert member_ids == {77, 88}
