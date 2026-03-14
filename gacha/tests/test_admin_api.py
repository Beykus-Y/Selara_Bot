from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from gacha_service.web import api


@pytest.mark.asyncio
async def test_admin_reset_cooldown_requires_token_and_resets_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, str]] = []

    class FakeRepo:
        def __init__(self, session) -> None:
            _ = session

        async def reset_banner_cooldown(self, *, user_id: int, banner: str) -> bool:
            calls.append((user_id, banner))
            return True

    async def fake_session_dependency(_session_factory):
        yield SimpleNamespace()

    monkeypatch.setattr(api, "GachaRepository", FakeRepo)
    monkeypatch.setattr(api, "session_dependency", fake_session_dependency)
    monkeypatch.setattr(api.settings, "admin_token", "secret")

    app = FastAPI()
    app.include_router(api.build_router(object()))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        denied = await client.post(
            "/v1/gacha/admin/cooldowns/reset",
            json={"user_id": 123, "banner": "genshin"},
        )
        allowed = await client.post(
            "/v1/gacha/admin/cooldowns/reset",
            headers={"X-Gacha-Admin-Token": "secret"},
            json={"user_id": 123, "banner": "genshin"},
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "ok"
    assert calls == [(123, "genshin")]
