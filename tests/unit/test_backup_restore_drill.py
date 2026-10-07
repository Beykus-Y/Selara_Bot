"""Restore drill for backup dumps (issue #69), checked without a PostgreSQL server.

SQL against a real server is covered by tests/integration/test_backup_restore_drill_postgres.py.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.engine import make_url

from selara.infrastructure import backup, backup_drill, backup_encryption
from selara.infrastructure.backup import BackupFile
from selara.infrastructure.backup_drill import BackupDrillError, DrillTarget


_PRIVATE_KEY_B64, _PUBLIC_KEY = backup_encryption.generate_keypair()
_GACHA_TABLES = backup_drill.GACHA_DATABASE_TARGET.required_tables
_GACHA_HEAD = "20260818_0005"


def test_find_restore_problems_lists_every_failed_check() -> None:
    target = DrillTarget(
        label="main bot database",
        required_tables=("users", "chats", "economy_accounts"),
        non_empty_tables=("users", "chats"),
        expected_head="0103_ai_turn_leases",
    )

    problems = backup_drill.find_restore_problems(
        target=target,
        schema_head="0102_backup_job_claims",
        tables={"users", "chats"},
        row_counts={"users": 4, "chats": 0},
    )

    assert problems == [
        "schema head 0102_backup_job_claims does not match expected 0103_ai_turn_leases",
        "table chats is empty",
        "table economy_accounts is missing",
    ]


def test_find_restore_problems_accepts_a_complete_restore() -> None:
    target = DrillTarget(
        label="main bot database",
        required_tables=("users", "chats"),
        non_empty_tables=("users", "chats"),
        expected_head="0103_ai_turn_leases",
    )

    problems = backup_drill.find_restore_problems(
        target=target,
        schema_head="0103_ai_turn_leases",
        tables={"users", "chats", "unrelated"},
        row_counts={"users": 1, "chats": 2},
    )

    assert problems == []


def test_target_for_archive_maps_each_archive_to_its_database() -> None:
    main = backup_drill.target_for_archive("bot_pg_dump.dump", main_head="0103", gacha_head=_GACHA_HEAD)
    gacha = backup_drill.target_for_archive("gacha_pg_dump.dump", main_head="0103", gacha_head=_GACHA_HEAD)

    assert (main.label, main.expected_head) == ("main bot database", "0103")
    assert (gacha.label, gacha.expected_head) == ("gacha database", _GACHA_HEAD)
    with pytest.raises(BackupDrillError, match="unknown archive"):
        backup_drill.target_for_archive("notes.txt", main_head="0103", gacha_head=_GACHA_HEAD)


def _write_gacha_snapshot(path: Path, *, tables: tuple[str, ...], version: str | None = _GACHA_HEAD) -> Path:
    with closing(sqlite3.connect(path)) as connection:
        for table in tables:
            connection.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)')
            connection.execute(f'INSERT INTO "{table}" (id) VALUES (1)')
        if version is not None:
            connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            connection.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (version,))
        connection.commit()
    return path


def test_drill_sqlite_snapshot_passes_for_a_complete_gacha_snapshot(tmp_path: Path) -> None:
    snapshot = _write_gacha_snapshot(tmp_path / "gacha_pg_dump.sqlite3", tables=_GACHA_TABLES)

    result = backup_drill.drill_sqlite_snapshot(
        snapshot_path=snapshot,
        target=backup_drill.GACHA_DATABASE_TARGET,
        timeout_seconds=30.0,
    )

    assert result.schema_head == _GACHA_HEAD
    assert result.row_counts == {table: 1 for table in _GACHA_TABLES}


def test_drill_sqlite_snapshot_fails_when_a_required_table_is_missing(tmp_path: Path) -> None:
    snapshot = _write_gacha_snapshot(tmp_path / "snapshot.sqlite3", tables=_GACHA_TABLES[:-1])

    with pytest.raises(BackupDrillError, match="table gacha_pull_history is missing"):
        backup_drill.drill_sqlite_snapshot(
            snapshot_path=snapshot,
            target=backup_drill.GACHA_DATABASE_TARGET,
            timeout_seconds=30.0,
        )


def test_drill_sqlite_snapshot_requires_an_alembic_version_record(tmp_path: Path) -> None:
    snapshot = _write_gacha_snapshot(tmp_path / "snapshot.sqlite3", tables=_GACHA_TABLES, version=None)

    with pytest.raises(BackupDrillError, match="table alembic_version is missing"):
        backup_drill.drill_sqlite_snapshot(
            snapshot_path=snapshot,
            target=backup_drill.GACHA_DATABASE_TARGET,
            timeout_seconds=30.0,
        )


def test_drill_sqlite_snapshot_rejects_bytes_that_are_not_sqlite(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.sqlite3"
    snapshot.write_bytes(b"gacha-dump-bytes-that-are-not-sqlite")

    with pytest.raises(BackupDrillError, match="not a SQLite database"):
        backup_drill.drill_sqlite_snapshot(
            snapshot_path=snapshot,
            target=backup_drill.GACHA_DATABASE_TARGET,
            timeout_seconds=30.0,
        )


class _FakeRestoreProcess:
    def __init__(self, *, returncode: int, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr


def _install_fake_restore(monkeypatch: pytest.MonkeyPatch, process: _FakeRestoreProcess) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def fake_exec(*command: str, **kwargs: Any) -> _FakeRestoreProcess:
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return process

    monkeypatch.setattr(backup_drill.asyncio, "create_subprocess_exec", fake_exec)
    return captured


@pytest.mark.asyncio
async def test_pg_restore_targets_the_scratch_database_and_keeps_the_password_out_of_argv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dump_path = tmp_path / "bot_pg_dump.dump"
    dump_path.write_bytes(b"PGDMP-fake")
    captured = _install_fake_restore(monkeypatch, _FakeRestoreProcess(returncode=0))

    await backup_drill._run_pg_restore(
        admin_url=make_url("postgresql+asyncpg://selara:s3cret@db.internal:5432/selara"),
        scratch_name="selara_restore_drill_abc",
        dump_path=dump_path,
        label="main bot database",
        pg_restore_path="pg_restore",
        timeout_seconds=60.0,
    )

    # --exit-on-error stops at the first failing statement, and the password travels only in PGPASSWORD.
    assert captured["command"] == (
        "pg_restore",
        "--no-owner",
        "--no-privileges",
        "--exit-on-error",
        "--dbname=postgresql://selara@db.internal:5432/selara_restore_drill_abc",
        str(dump_path),
    )
    assert captured["env"]["PGPASSWORD"] == "s3cret"


@pytest.mark.asyncio
async def test_pg_restore_failure_reports_the_last_stderr_line(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dump_path = tmp_path / "bot_pg_dump.dump"
    dump_path.write_bytes(b"PGDMP-fake")
    _install_fake_restore(
        monkeypatch,
        _FakeRestoreProcess(
            returncode=1,
            stderr=b"pg_restore: connecting to database\npg_restore: error: permission denied to create database\n",
        ),
    )

    with pytest.raises(BackupDrillError, match="permission denied to create database"):
        await backup_drill._run_pg_restore(
            admin_url=make_url("postgresql+asyncpg://selara:pw@db:5432/selara"),
            scratch_name="selara_restore_drill_abc",
            dump_path=dump_path,
            label="main bot database",
            pg_restore_path="pg_restore",
            timeout_seconds=60.0,
        )


@pytest.mark.asyncio
async def test_postgres_drill_drops_the_scratch_database_when_the_check_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dump_path = tmp_path / "bot_pg_dump.dump"
    dump_path.write_bytes(b"PGDMP-fake")
    events: list[str] = []

    async def fake_create(engine: object, name: str) -> None:
        _ = engine
        events.append(f"create {name}")

    async def fake_restore(**kwargs: Any) -> None:
        events.append(f"restore {kwargs['scratch_name']}")

    async def fake_check(database_url: Any, target: DrillTarget) -> None:
        _ = target
        events.append(f"check {database_url.database}")
        raise BackupDrillError("Backup restore drill failed for main bot database: table users is missing.")

    async def fake_drop(engine: object, name: str) -> None:
        _ = engine
        events.append(f"drop {name}")

    monkeypatch.setattr(backup_drill, "_create_scratch_database", fake_create)
    monkeypatch.setattr(backup_drill, "_run_pg_restore", fake_restore)
    monkeypatch.setattr(backup_drill, "check_restored_database", fake_check)
    monkeypatch.setattr(backup_drill, "_drop_scratch_database", fake_drop)

    with pytest.raises(BackupDrillError, match="table users is missing"):
        await backup_drill.drill_postgres_dump(
            admin_url=make_url("postgresql+asyncpg://selara:pw@db:5432/selara"),
            dump_path=dump_path,
            target=backup_drill.MAIN_DATABASE_TARGET,
            pg_restore_path="pg_restore",
            timeout_seconds=60.0,
        )

    scratch_name = events[0].removeprefix("create ")
    assert scratch_name.startswith(backup_drill.SCRATCH_DATABASE_PREFIX)
    assert events == [
        f"create {scratch_name}",
        f"restore {scratch_name}",
        f"check {scratch_name}",
        f"drop {scratch_name}",
    ]


@pytest.mark.asyncio
async def test_drill_backup_set_refuses_a_set_without_the_gacha_archive(tmp_path: Path) -> None:
    recipient = backup_encryption.parse_public_key(_PUBLIC_KEY)
    plain = tmp_path / "bot_pg_dump.dump"
    plain.write_bytes(b"PGDMP-fake-bot-archive")
    encrypted = backup._encrypt_backup_file(BackupFile(path=plain, archive_name=plain.name), recipient)
    parts_dir = tmp_path / "parts"
    parts_dir.mkdir()
    _parts, manifest_entry = backup._split_backup_file(encrypted, parts_dir, 64)
    manifest_path = backup._write_backup_manifest(
        temp_dir=parts_dir,
        created_at="20260818T000000Z",
        chunk_size_bytes=64,
        files=[manifest_entry],
        encryption={
            "format": backup_encryption.ENCRYPTION_FORMAT,
            "recipient_sha256": backup_encryption.public_key_fingerprint(recipient),
        },
    )

    with pytest.raises(BackupDrillError, match="must hold both the main bot archive and the gacha archive"):
        await backup_drill.drill_backup_set(
            manifest_path=manifest_path,
            parts_dir=parts_dir,
            identity=backup_encryption.load_private_key(_PRIVATE_KEY_B64),
            admin_url=make_url("postgresql+asyncpg://selara:pw@127.0.0.1:5432/postgres"),
            pg_restore_path="pg_restore",
            timeout_seconds=60.0,
            main_head="0103_ai_turn_leases",
            gacha_head=_GACHA_HEAD,
        )


@pytest.mark.asyncio
async def test_send_daily_backup_sends_nothing_when_restore_drill_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # The dump parses offline, yet it does not restore into the schema the bot runs: nothing may be sent.
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    sent: list[object] = []

    async def fake_create_bot_database_dump(*, settings: object, temp_dir: Path) -> BackupFile:
        _ = settings
        path = temp_dir / "bot_pg_dump.dump"
        path.write_bytes(b"bot-dump-that-parses-but-does-not-restore")
        return BackupFile(path=path, archive_name=path.name)

    async def fake_download_gacha_backup(*, settings: object, temp_dir: Path) -> BackupFile:
        _ = settings
        path = temp_dir / "gacha_pg_dump.dump"
        path.write_bytes(b"gacha-dump")
        return BackupFile(path=path, archive_name=path.name)

    async def fake_verify_dump_restorable(*, dump_path: Path, label: str, settings: object) -> None:
        _ = dump_path, label, settings

    async def fake_drill_bot_database_dump(*, dump_path: Path, settings: object) -> None:
        _ = dump_path, settings
        raise BackupDrillError(
            "Backup restore drill failed for main bot database: schema head 0102_backup_job_claims "
            "does not match expected 0103_ai_turn_leases."
        )

    async def fake_send_document(**kwargs: object) -> None:
        sent.append(kwargs)

    monkeypatch.setattr(backup.tempfile, "mkdtemp", lambda prefix: str(job_dir))
    monkeypatch.setattr(backup, "_create_bot_database_dump", fake_create_bot_database_dump)
    monkeypatch.setattr(backup, "_download_gacha_backup", fake_download_gacha_backup)
    monkeypatch.setattr(backup, "_verify_dump_restorable", fake_verify_dump_restorable)
    monkeypatch.setattr(backup, "_drill_bot_database_dump", fake_drill_bot_database_dump)

    settings = SimpleNamespace(
        admin_user_id=42,
        backup_encryption_public_key=_PUBLIC_KEY,
        backup_restore_drill_enabled=True,
    )
    bot_client = SimpleNamespace(send_document=fake_send_document)

    with pytest.raises(BackupDrillError, match="does not match expected 0103_ai_turn_leases"):
        await backup.send_daily_backup(bot=bot_client, settings=settings)

    assert sent == []
    assert not job_dir.exists()
