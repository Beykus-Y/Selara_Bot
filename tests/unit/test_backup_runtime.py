from __future__ import annotations

import hashlib
import json
import os
import sqlite3
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


def _install_dump_verifier(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    verified: list[str] = []

    async def fake_verify_dump_restorable(
        *,
        dump_path: Path,
        label: str,
        settings: SimpleNamespace,
        temp_dir: Path,
    ) -> None:
        _ = dump_path, settings, temp_dir
        verified.append(label)

    monkeypatch.setattr(backup, "_verify_dump_restorable", fake_verify_dump_restorable)
    return verified


@pytest.mark.asyncio
async def test_send_daily_backup_downloads_gacha_and_sends_both_dumps_in_chunks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    calls: list[str] = []
    sent: list[dict[str, object]] = []
    verified = _install_dump_verifier(monkeypatch)

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
    assert verified == ["main bot database dump", "gacha dump"]
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
    _install_dump_verifier(monkeypatch)

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


@pytest.mark.asyncio
async def test_send_daily_backup_sends_nothing_when_dump_is_not_restorable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    sent: list[object] = []

    async def fake_create_bot_database_dump(*, settings, temp_dir: Path) -> BackupFile:
        _ = settings
        path = temp_dir / "bot_pg_dump.dump"
        path.write_bytes(b"corrupt-bytes")
        return BackupFile(path=path, archive_name=path.name)

    async def fake_download_gacha_backup(*, settings, temp_dir: Path) -> BackupFile:
        _ = settings
        path = temp_dir / "gacha_pg_dump.dump"
        path.write_bytes(b"gacha-dump")
        return BackupFile(path=path, archive_name=path.name)

    async def fake_verify_dump_restorable(
        *,
        dump_path: Path,
        label: str,
        settings: SimpleNamespace,
        temp_dir: Path,
    ) -> None:
        _ = dump_path, settings, temp_dir
        raise backup.BackupJobError(f"Backup restore verification failed for {label}: corrupt archive")

    async def fake_send_document(**kwargs) -> None:
        sent.append(kwargs)

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(backup.tempfile, "mkdtemp", lambda prefix: str(job_dir))
    monkeypatch.setattr(backup, "_create_bot_database_dump", fake_create_bot_database_dump)
    monkeypatch.setattr(backup, "_download_gacha_backup", fake_download_gacha_backup)
    monkeypatch.setattr(backup, "_verify_dump_restorable", fake_verify_dump_restorable)
    monkeypatch.setattr(backup.asyncio, "to_thread", fake_to_thread)

    settings = SimpleNamespace(admin_user_id=42)
    bot_client = SimpleNamespace(send_document=fake_send_document)

    with pytest.raises(backup.BackupJobError, match="corrupt archive"):
        await backup.send_daily_backup(bot=bot_client, settings=settings)

    assert sent == []
    assert not job_dir.exists()


class _FakePgRestoreProcess:
    def __init__(self, *, returncode: int, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr


def _make_settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "backup_pg_restore_path": "pg_restore",
        "backup_restore_database_url": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _install_fake_pg_restore(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, object],
    *,
    returncode: int = 0,
    stderr: bytes = b"",
) -> None:
    stderr_bytes = stderr

    async def fake_exec(
        *command: str, stdout: object, stderr: object, env: dict[str, str]
    ) -> _FakePgRestoreProcess:
        _ = stdout, stderr
        captured["command"] = command
        captured["env"] = env
        return _FakePgRestoreProcess(returncode=returncode, stderr=stderr_bytes)

    monkeypatch.setattr(backup.asyncio, "create_subprocess_exec", fake_exec)


@pytest.mark.asyncio
async def test_verify_dump_restorable_runs_pg_restore_archive_list_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dump_path = tmp_path / "bot_pg_dump.dump"
    dump_path.write_bytes(b"PGDMP-fake")
    captured: dict[str, object] = {}
    _install_fake_pg_restore(monkeypatch, captured)

    await backup._verify_dump_restorable(
        dump_path=dump_path,
        label="main bot database dump",
        settings=_make_settings(),
        temp_dir=tmp_path,
    )

    assert captured["command"] == (
        "pg_restore",
        "--list",
        f"--file={tmp_path / 'bot_pg_dump.dump.restore-check'}",
        str(dump_path),
    )
    assert captured["env"].get("PGPASSWORD") == os.environ.get("PGPASSWORD")


@pytest.mark.asyncio
async def test_verify_dump_restorable_runs_full_restore_drill_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dump_path = tmp_path / "bot_pg_dump.dump"
    dump_path.write_bytes(b"PGDMP-fake")
    captured: dict[str, object] = {}
    _install_fake_pg_restore(monkeypatch, captured)

    settings = _make_settings(
        backup_restore_database_url="postgresql://restore_user:restore_pass@localhost:5433/selara_restore",
    )

    await backup._verify_dump_restorable(
        dump_path=dump_path,
        label="main bot database dump",
        settings=settings,
        temp_dir=tmp_path,
    )

    assert captured["command"] == (
        "pg_restore",
        "--no-owner",
        "--no-privileges",
        "--exit-on-error",
        "--clean",
        "--if-exists",
        "--dbname=postgresql://restore_user@localhost:5433/selara_restore",
        str(dump_path),
    )
    assert captured["env"]["PGPASSWORD"] == "restore_pass"


@pytest.mark.asyncio
async def test_verify_dump_restorable_fails_job_when_pg_restore_rejects_archive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dump_path = tmp_path / "bot_pg_dump.dump"
    dump_path.write_bytes(b"not-a-pgdump")
    captured: dict[str, object] = {}
    _install_fake_pg_restore(
        monkeypatch,
        captured,
        returncode=1,
        stderr=b"pg_restore: error: processing archive\ngarbage at end of archive",
    )

    with pytest.raises(
        backup.BackupJobError,
        match="Backup restore verification failed for main bot database dump: garbage at end of archive",
    ):
        await backup._verify_dump_restorable(
            dump_path=dump_path,
            label="main bot database dump",
            settings=_make_settings(),
            temp_dir=tmp_path,
        )


@pytest.mark.asyncio
async def test_verify_dump_restorable_fails_job_when_pg_restore_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dump_path = tmp_path / "bot_pg_dump.dump"
    dump_path.write_bytes(b"PGDMP-fake")

    async def fake_exec(*command: str, **kwargs: object) -> _FakePgRestoreProcess:
        raise FileNotFoundError()

    monkeypatch.setattr(backup.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(
        backup.BackupJobError,
        match="Backup restore verification command 'pg_restore' is not available",
    ):
        await backup._verify_dump_restorable(
            dump_path=dump_path,
            label="main bot database dump",
            settings=_make_settings(),
            temp_dir=tmp_path,
        )


def test_resolve_backup_restore_target_validates_configuration() -> None:
    assert backup._resolve_backup_restore_target(_make_settings()) is None
    assert backup._resolve_backup_restore_target(_make_settings(backup_restore_database_url="   ")) is None

    with pytest.raises(backup.BackupJobError, match="BACKUP_RESTORE_DATABASE_URL is invalid"):
        backup._resolve_backup_restore_target(_make_settings(backup_restore_database_url="not-a-url"))

    with pytest.raises(backup.BackupJobError, match="supports only PostgreSQL"):
        backup._resolve_backup_restore_target(
            _make_settings(backup_restore_database_url="mysql://user@localhost:3306/selara")
        )


@pytest.mark.asyncio
async def test_verify_dump_restorable_routes_sqlite_dumps_to_integrity_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    routed: list[str] = []

    def fake_verify_sqlite(dump_path: Path, label: str) -> None:
        routed.append(f"sqlite:{label}")

    async def fake_verify_pg(**kwargs: object) -> None:
        raise AssertionError("pg_restore verification must not run for SQLite dumps")

    monkeypatch.setattr(backup, "_verify_sqlite_dump_restorable", fake_verify_sqlite)
    monkeypatch.setattr(backup, "_verify_pg_dump_restorable", fake_verify_pg)

    dump_path = tmp_path / "gacha_pg_dump.sqlite3"
    dump_path.write_bytes(b"SQLite format 3 fake")

    await backup._verify_dump_restorable(
        dump_path=dump_path,
        label="gacha dump",
        settings=SimpleNamespace(),
        temp_dir=tmp_path,
    )

    assert routed == ["sqlite:gacha dump"]


def test_verify_sqlite_dump_restorable_passes_on_healthy_sqlite_file(tmp_path: Path) -> None:
    dump_path = tmp_path / "gacha_pg_dump.sqlite3"
    connection = sqlite3.connect(dump_path)
    connection.execute("CREATE TABLE gacha_items (id INTEGER PRIMARY KEY, name TEXT)")
    connection.commit()
    connection.close()

    backup._verify_sqlite_dump_restorable(dump_path, "gacha dump")


def test_verify_sqlite_dump_restorable_fails_on_corrupt_sqlite_file(tmp_path: Path) -> None:
    dump_path = tmp_path / "gacha_pg_dump.sqlite3"
    dump_path.write_bytes(b"definitely not a sqlite database")

    with pytest.raises(
        backup.BackupJobError,
        match="Backup restore verification failed for gacha dump",
    ):
        backup._verify_sqlite_dump_restorable(dump_path, "gacha dump")
