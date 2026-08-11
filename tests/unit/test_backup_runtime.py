from __future__ import annotations

import hashlib
import json
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


def test_backup_chunk_size_stays_below_hosted_telegram_document_limit() -> None:
    assert backup.BACKUP_CHUNK_SIZE_BYTES == 45 * 1024 * 1024
    assert backup.BACKUP_CHUNK_SIZE_BYTES < 50_000_000


@pytest.mark.asyncio
async def test_send_daily_backup_downloads_gacha_and_sends_both_dumps_in_chunks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    calls: list[str] = []
    sent: list[dict[str, object]] = []

    async def fake_create_bot_database_dump(*, settings, temp_dir: Path) -> BackupFile:
        _ = settings
        calls.append("bot")
        path = temp_dir / "bot_pg_dump.dump"
        path.write_bytes(b"ABCDEFGHIJKL")
        return BackupFile(path=path, archive_name="bot_pg_dump.dump")

    async def fake_download_gacha_backup(*, settings, temp_dir: Path) -> BackupFile:
        _ = settings
        calls.append("gacha")
        path = temp_dir / "gacha_pg_dump.dump"
        path.write_bytes(b"gacha!!")
        return BackupFile(path=path, archive_name="gacha_pg_dump.dump")

    async def fake_send_document(*, chat_id: int, document, caption: str) -> None:
        path = Path(document.path)
        sent.append(
            {
                "chat_id": chat_id,
                "caption": caption,
                "filename": document.filename,
                "content": path.read_bytes(),
            }
        )

    monkeypatch.setattr(backup.tempfile, "mkdtemp", lambda prefix: str(job_dir))
    monkeypatch.setattr(backup, "_create_bot_database_dump", fake_create_bot_database_dump)
    monkeypatch.setattr(backup, "_download_gacha_backup", fake_download_gacha_backup)
    monkeypatch.setattr(backup, "BACKUP_CHUNK_SIZE_BYTES", 5)
    monkeypatch.setattr(backup, "_backup_timestamp", lambda now=None: "20260315T000000Z")
    monkeypatch.setattr(backup, "FSInputFile", lambda path, filename=None: SimpleNamespace(path=path, filename=filename))

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(backup.asyncio, "to_thread", fake_to_thread)

    settings = SimpleNamespace(admin_user_id=42)
    bot_client = SimpleNamespace(send_document=fake_send_document)

    await backup.send_daily_backup(bot=bot_client, settings=settings)

    assert calls == ["bot", "gacha"]
    assert [item["chat_id"] for item in sent] == [42] * 6
    assert [item["filename"] for item in sent] == [
        "bot_pg_dump.dump.part-001-of-003",
        "bot_pg_dump.dump.part-002-of-003",
        "bot_pg_dump.dump.part-003-of-003",
        "gacha_pg_dump.dump.part-001-of-002",
        "gacha_pg_dump.dump.part-002-of-002",
        "selara-daily-backup-20260315T000000Z.manifest.json",
    ]
    assert [item["content"] for item in sent[:-1]] == [
        b"ABCDE",
        b"FGHIJ",
        b"KL",
        b"gacha",
        b"!!",
    ]
    assert [item["caption"] for item in sent[:-1]] == [
        "Selara daily backup: bot_pg_dump.dump (part 1/3)",
        "Selara daily backup: bot_pg_dump.dump (part 2/3)",
        "Selara daily backup: bot_pg_dump.dump (part 3/3)",
        "Selara daily backup: gacha_pg_dump.dump (part 1/2)",
        "Selara daily backup: gacha_pg_dump.dump (part 2/2)",
    ]

    manifest = json.loads(sent[-1]["content"])
    assert manifest == {
        "created_at": "20260315T000000Z",
        "chunk_size_bytes": 5,
        "files": [
            {
                "filename": "bot_pg_dump.dump",
                "size_bytes": 12,
                "sha256": hashlib.sha256(b"ABCDEFGHIJKL").hexdigest(),
                "parts": [
                    {"filename": "bot_pg_dump.dump.part-001-of-003", "size_bytes": 5},
                    {"filename": "bot_pg_dump.dump.part-002-of-003", "size_bytes": 5},
                    {"filename": "bot_pg_dump.dump.part-003-of-003", "size_bytes": 2},
                ],
            },
            {
                "filename": "gacha_pg_dump.dump",
                "size_bytes": 7,
                "sha256": hashlib.sha256(b"gacha!!").hexdigest(),
                "parts": [
                    {"filename": "gacha_pg_dump.dump.part-001-of-002", "size_bytes": 5},
                    {"filename": "gacha_pg_dump.dump.part-002-of-002", "size_bytes": 2},
                ],
            },
        ],
    }
    assert sent[-1]["caption"] == "Selara daily backup manifest"
    assert not job_dir.exists()


@pytest.mark.asyncio
async def test_send_daily_backup_sends_nothing_when_gacha_download_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    sent: list[object] = []

    async def fake_create_bot_database_dump(*, settings, temp_dir: Path) -> BackupFile:
        _ = settings
        path = temp_dir / "bot_pg_dump.dump"
        path.write_bytes(b"main-dump")
        return BackupFile(path=path, archive_name=path.name)

    async def fake_download_gacha_backup(*, settings, temp_dir: Path) -> BackupFile:
        _ = settings, temp_dir
        raise backup.BackupJobError("gacha unavailable")

    async def fake_send_document(**kwargs) -> None:
        sent.append(kwargs)

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(backup.tempfile, "mkdtemp", lambda prefix: str(job_dir))
    monkeypatch.setattr(backup, "_create_bot_database_dump", fake_create_bot_database_dump)
    monkeypatch.setattr(backup, "_download_gacha_backup", fake_download_gacha_backup)
    monkeypatch.setattr(backup.asyncio, "to_thread", fake_to_thread)

    settings = SimpleNamespace(admin_user_id=42)
    bot_client = SimpleNamespace(send_document=fake_send_document)

    with pytest.raises(backup.BackupJobError, match="gacha unavailable"):
        await backup.send_daily_backup(bot=bot_client, settings=settings)

    assert sent == []
    assert not job_dir.exists()
