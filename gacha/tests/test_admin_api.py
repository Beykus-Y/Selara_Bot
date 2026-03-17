from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.routing import APIRoute

from gacha_service.domain.models import CardRarity
from gacha_service.infrastructure.backup import BackupArtifact
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


@pytest.mark.asyncio
async def test_admin_backup_requires_token_and_returns_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backup_file = tmp_path / "selara-gacha-test.dump"
    backup_file.write_bytes(b"backup-bytes")
    cleanup_calls: list[Path] = []

    async def fake_create_database_backup(*, settings) -> BackupArtifact:
        _ = settings
        return BackupArtifact(
            path=backup_file,
            filename=backup_file.name,
            media_type="application/octet-stream",
            cleanup_dir=tmp_path,
        )

    async def fake_cleanup_backup_artifact(artifact: BackupArtifact) -> None:
        cleanup_calls.append(artifact.path)

    monkeypatch.setattr(api.settings, "admin_token", "secret")
    monkeypatch.setattr(api, "create_database_backup", fake_create_database_backup)
    monkeypatch.setattr(api, "cleanup_backup_artifact", fake_cleanup_backup_artifact)

    app = FastAPI()
    app.include_router(api.build_router(object()))
    backup_route = next(
        route
        for route in app.router.routes
        if isinstance(route, APIRoute) and route.path == "/v1/gacha/admin/backup"
    )

    with pytest.raises(HTTPException) as denied:
        await backup_route.endpoint(x_gacha_admin_token=None)

    allowed = await backup_route.endpoint(x_gacha_admin_token="secret")
    await allowed.background()

    assert denied.value.status_code == 403
    assert allowed.path == backup_file
    assert allowed.media_type == "application/octet-stream"
    assert "filename=\"selara-gacha-test.dump\"" in allowed.headers["content-disposition"]
    assert allowed.headers["x-gacha-backup-format"] == "dump"
    assert allowed.headers["cache-control"] == "no-store"
    assert cleanup_calls == [backup_file]


@pytest.mark.asyncio
async def test_admin_give_card_requires_token_and_returns_pull_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRepo:
        def __init__(self, session) -> None:
            _ = session

    class FakeService:
        def __init__(self, repo) -> None:
            self.repo = repo

        async def grant_card(self, *, user_id: int, username: str | None, banner: str, card_code: str):
            _ = self.repo
            return SimpleNamespace(
                status="ok",
                message="card granted",
                card=SimpleNamespace(
                    code=card_code,
                    name="Тарталья",
                    rarity=CardRarity.legendary,
                    points=5,
                    primogems=10,
                    image_url="/images/genshin/tartalia.jpg",
                ),
                player=SimpleNamespace(
                    user_id=user_id,
                    adventure_xp=0,
                    total_points=5,
                    total_primogems=10,
                ),
                cooldown_until=datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc),
                is_new=True,
                copies_owned=1,
                adventure_xp_gained=100,
            )

    async def fake_session_dependency(_session_factory):
        yield SimpleNamespace()

    monkeypatch.setattr(api, "GachaRepository", FakeRepo)
    monkeypatch.setattr(api, "GachaService", FakeService)
    monkeypatch.setattr(api, "session_dependency", fake_session_dependency)
    monkeypatch.setattr(api.settings, "admin_token", "secret")

    app = FastAPI()
    app.include_router(api.build_router(object()))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        denied = await client.post(
            "/v1/gacha/admin/give",
            json={"user_id": 123, "code": "tartalia"},
        )
        allowed = await client.post(
            "/v1/gacha/admin/give",
            headers={"X-Gacha-Admin-Token": "secret"},
            json={"user_id": 123, "code": "tartalia"},
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "ok"
    assert allowed.json()["card"]["code"] == "tartalia"
