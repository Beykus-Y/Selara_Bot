from __future__ import annotations

import httpx
import pytest

from selara.infrastructure.http.gacha_client import HttpGachaClient


@pytest.mark.asyncio
async def test_gacha_client_download_backup_returns_filename_and_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAsyncClient:
        def __init__(self, *, base_url: str, timeout: float) -> None:
            assert base_url == "http://gacha.local"
            assert timeout == 120.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, path: str, headers: dict[str, str]) -> httpx.Response:
            assert path == "/v1/gacha/admin/backup"
            assert headers == {"X-Gacha-Admin-Token": "secret"}
            request = httpx.Request("POST", "http://gacha.local/v1/gacha/admin/backup")
            return httpx.Response(
                200,
                headers={"content-disposition": 'attachment; filename="gacha-export.dump"'},
                content=b"gacha-backup",
                request=request,
            )

    monkeypatch.setattr("selara.infrastructure.http.gacha_client.httpx.AsyncClient", FakeAsyncClient)

    client = HttpGachaClient(base_url="http://gacha.local", timeout_seconds=120.0)
    backup_file = await client.download_backup(admin_token="secret")

    assert backup_file.filename == "gacha-export.dump"
    assert backup_file.content == b"gacha-backup"
