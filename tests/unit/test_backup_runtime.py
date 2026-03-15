from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from selara.infrastructure import backup
from selara.infrastructure.backup import BackupFile


def test_seconds_until_next_backup_targets_next_local_midnight() -> None:
    now = datetime(2026, 3, 15, 16, 30, tzinfo=timezone.utc)

    delay = backup.seconds_until_next_backup(timezone_name="Asia/Barnaul", now=now)

    assert delay == pytest.approx(30 * 60)


@pytest.mark.asyncio
async def test_send_daily_backup_builds_archive_with_bot_and_gacha_dumps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    sent = {}

    async def fake_create_bot_database_dump(*, settings, temp_dir: Path) -> BackupFile:
        _ = settings
        path = temp_dir / "bot_pg_dump.dump"
        path.write_bytes(b"bot-backup")
        return BackupFile(path=path, archive_name="bot_pg_dump.dump")

    async def fake_download_gacha_backup(*, settings, temp_dir: Path) -> BackupFile:
        _ = settings
        path = temp_dir / "gacha_pg_dump.dump"
        path.write_bytes(b"gacha-backup")
        return BackupFile(path=path, archive_name="gacha_pg_dump.dump")

    async def fake_send_document(*, chat_id: int, document, caption: str) -> None:
        sent["chat_id"] = chat_id
        sent["caption"] = caption
        archive_path = Path(document.path)
        sent["archive_name"] = archive_path.name
        with zipfile.ZipFile(archive_path) as archive_file:
            sent["entries"] = {name: archive_file.read(name) for name in archive_file.namelist()}

    monkeypatch.setattr(backup.tempfile, "mkdtemp", lambda prefix: str(job_dir))
    monkeypatch.setattr(backup, "_create_bot_database_dump", fake_create_bot_database_dump)
    monkeypatch.setattr(backup, "_download_gacha_backup", fake_download_gacha_backup)
    monkeypatch.setattr(backup, "_archive_filename", lambda now=None: "daily.zip")
    monkeypatch.setattr(backup, "FSInputFile", lambda path, filename=None: SimpleNamespace(path=path, filename=filename))

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(backup.asyncio, "to_thread", fake_to_thread)

    settings = SimpleNamespace(admin_user_id=42)
    bot_client = SimpleNamespace(send_document=fake_send_document)

    await backup.send_daily_backup(bot=bot_client, settings=settings)

    assert sent["chat_id"] == 42
    assert sent["caption"] == "Selara daily backup"
    assert sent["archive_name"] == "daily.zip"
    assert sent["entries"] == {
        "bot_pg_dump.dump": b"bot-backup",
        "gacha_pg_dump.dump": b"gacha-backup",
    }
    assert not job_dir.exists()
