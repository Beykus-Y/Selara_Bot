from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.routing import APIRoute

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
