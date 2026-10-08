from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from selara.core.config import Settings
from selara.infrastructure import backup, backup_encryption
from selara.infrastructure.backup import BackupFile


_PRIVATE_KEY_B64, _PUBLIC_KEY = backup_encryption.generate_keypair()
_PRIVATE_KEY = backup_encryption.load_private_key(_PRIVATE_KEY_B64)
_RECIPIENT = backup_encryption.parse_public_key(_PUBLIC_KEY)


def test_seconds_until_next_backup_targets_next_local_midnight() -> None:
    now = datetime(2026, 3, 15, 16, 30, tzinfo=timezone.utc)

    delay = backup.seconds_until_next_backup(timezone_name="Asia/Barnaul", now=now)

    assert delay == pytest.approx(30 * 60)


def test_backup_chunk_size_stays_below_hosted_telegram_document_limit() -> None:
    assert backup.BACKUP_CHUNK_SIZE_BYTES == 45 * 1024 * 1024
    assert backup.BACKUP_CHUNK_SIZE_BYTES < 50_000_000


def test_pg_dump_timeout_is_its_own_setting_with_a_long_default() -> None:
    field = Settings.model_fields["backup_pg_dump_timeout_seconds"]

    assert field.default == 1800.0
    assert field.validation_alias == "BACKUP_PG_DUMP_TIMEOUT_SECONDS"


def _install_dump_verifier(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    verified: list[str] = []

    async def fake_verify_dump_restorable(
        *,
        dump_path: Path,
        label: str,
        settings: SimpleNamespace,
    ) -> None:
        _ = dump_path, settings
        verified.append(label)

    monkeypatch.setattr(backup, "_verify_dump_restorable", fake_verify_dump_restorable)
    return verified


def _install_restore_drill(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    drilled: list[str] = []

    async def fake_drill_bot_database_dump(*, dump_path: Path, settings: SimpleNamespace) -> None:
        _ = settings
        drilled.append(dump_path.name)

    async def fake_drill_gacha_dump(*, dump_path: Path, settings: SimpleNamespace) -> None:
        _ = settings
        drilled.append(dump_path.name)

    monkeypatch.setattr(backup, "_drill_bot_database_dump", fake_drill_bot_database_dump)
    monkeypatch.setattr(backup, "_drill_gacha_dump", fake_drill_gacha_dump)
    return drilled


@pytest.mark.asyncio
async def test_send_daily_backup_uploads_only_ciphertext_and_restores_to_originals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    calls: list[str] = []
    sent: list[dict[str, object]] = []
    verified = _install_dump_verifier(monkeypatch)
    drilled = _install_restore_drill(monkeypatch)
    bot_plaintext =b"BOT-PLAINTEXT-ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    gacha_plaintext = b"GACHA-PLAINTEXT-0123"

    async def fake_create_bot_database_dump(*, settings, temp_dir: Path) -> BackupFile:
        _ = settings
        calls.append("bot")
        path = temp_dir / "bot_pg_dump.dump"
        path.write_bytes(bot_plaintext)
        return BackupFile(path=path, archive_name="bot_pg_dump.dump")

    async def fake_download_gacha_backup(*, settings, temp_dir: Path) -> BackupFile:
        _ = settings
        calls.append("gacha")
        path = temp_dir / "gacha_pg_dump.dump"
        path.write_bytes(gacha_plaintext)
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
    monkeypatch.setattr(backup, "BACKUP_CHUNK_SIZE_BYTES", 64)
    monkeypatch.setattr(backup, "_backup_timestamp", lambda now=None: "20260315T000000Z")
    monkeypatch.setattr(backup, "FSInputFile", lambda path, filename=None: SimpleNamespace(path=path, filename=filename))

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(backup.asyncio, "to_thread", fake_to_thread)

    settings = SimpleNamespace(
        admin_user_id=42,
        backup_encryption_public_key=_PUBLIC_KEY,
        backup_restore_drill_enabled=True,
    )
    bot_client = SimpleNamespace(send_document=fake_send_document)

    await backup.send_daily_backup(bot=bot_client, settings=settings)

    assert calls == ["bot", "gacha"]
    assert verified == ["main bot database dump", "gacha dump"]
    assert drilled == ["bot_pg_dump.dump", "gacha_pg_dump.dump"]
    assert [item["chat_id"] for item in sent] == [42] * len(sent)
    assert all(item["caption"].startswith("Selara daily backup") for item in sent[:-1])

    # Nothing plaintext may reach Telegram, neither in a part nor in a manifest.
    for item in sent:
        assert bot_plaintext not in item["content"]
        assert gacha_plaintext not in item["content"]

    manifest = json.loads(sent[-1]["content"])
    assert manifest["encryption"] == {
        "format": backup_encryption.ENCRYPTION_FORMAT,
        "recipient_sha256": backup_encryption.public_key_fingerprint(_RECIPIENT),
    }
    assert [entry["filename"] for entry in manifest["files"]] == [
        "bot_pg_dump.dump.enc",
        "gacha_pg_dump.dump.enc",
    ]
    uploaded_parts = [(item["filename"], item["content"]) for item in sent[:-1]]
    assert [name for name, _content in uploaded_parts] == [
        part["filename"] for entry in manifest["files"] for part in entry["parts"]
    ]
    assert [entry["sha256"] for entry in manifest["files"]] == [
        hashlib.sha256(
            b"".join(content for name, content in uploaded_parts if name.startswith(entry["filename"] + ".part-"))
        ).hexdigest()
        for entry in manifest["files"]
    ]

    # Reassemble, decrypt and checksum exactly as the admin's restore procedure does.
    restore_input = tmp_path / "restore-input"
    restore_input.mkdir()
    for name, content in uploaded_parts:
        (restore_input / name).write_bytes(content)
    manifest_path = restore_input / "manifest.json"
    manifest_path.write_bytes(sent[-1]["content"])
    restored = backup_encryption.restore_backup_set(
        manifest_path=manifest_path,
        parts_dir=restore_input,
        identity=_PRIVATE_KEY,
        output_dir=tmp_path / "restored",
    )
    restored_by_name = {path.name: path.read_bytes() for path in restored}
    assert restored_by_name == {
        "bot_pg_dump.dump": bot_plaintext,
        "gacha_pg_dump.dump": gacha_plaintext,
    }
    assert not job_dir.exists()


@pytest.mark.asyncio
async def test_send_daily_backup_sends_nothing_without_encryption_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    sent: list[object] = []

    async def fake_create_bot_database_dump(*, settings, temp_dir: Path) -> BackupFile:
        calls.append("bot")
        raise AssertionError("dump must not be created without an encryption key")

    async def fake_send_document(**kwargs) -> None:
        sent.append(kwargs)

    monkeypatch.setattr(backup, "_create_bot_database_dump", fake_create_bot_database_dump)

    for key in (None, "   "):
        settings = SimpleNamespace(admin_user_id=42, backup_encryption_public_key=key)
        bot_client = SimpleNamespace(send_document=fake_send_document)
        with pytest.raises(backup.BackupJobError, match="BACKUP_ENCRYPTION_PUBLIC_KEY is not configured"):
            await backup.send_daily_backup(bot=bot_client, settings=settings)

    assert calls == []
    assert sent == []


@pytest.mark.asyncio
async def test_send_daily_backup_rejects_malformed_encryption_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[object] = []

    async def fake_send_document(**kwargs) -> None:
        sent.append(kwargs)

    settings = SimpleNamespace(admin_user_id=42, backup_encryption_public_key="not-a-key")
    bot_client = SimpleNamespace(send_document=fake_send_document)

    with pytest.raises(backup.BackupJobError, match="BACKUP_ENCRYPTION_PUBLIC_KEY is invalid"):
        await backup.send_daily_backup(bot=bot_client, settings=settings)

    assert sent == []


@pytest.mark.asyncio
async def test_send_daily_backup_sends_nothing_when_encryption_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    sent: list[object] = []
    _install_dump_verifier(monkeypatch)
    _install_restore_drill(monkeypatch)

    async def fake_create_bot_database_dump(*, settings, temp_dir: Path) -> BackupFile:
        _ = settings
        path = temp_dir / "bot_pg_dump.dump"
        path.write_bytes(b"BOT-PLAINTEXT")
        return BackupFile(path=path, archive_name=path.name)

    async def fake_download_gacha_backup(*, settings, temp_dir: Path) -> BackupFile:
        _ = settings
        path = temp_dir / "gacha_pg_dump.dump"
        path.write_bytes(b"gacha-dump")
        return BackupFile(path=path, archive_name=path.name)

    def failing_encrypt_file(**kwargs) -> None:
        raise backup_encryption.BackupCryptoError("disk full")

    async def fake_send_document(**kwargs) -> None:
        sent.append(kwargs)

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(backup.tempfile, "mkdtemp", lambda prefix: str(job_dir))
    monkeypatch.setattr(backup, "_create_bot_database_dump", fake_create_bot_database_dump)
    monkeypatch.setattr(backup, "_download_gacha_backup", fake_download_gacha_backup)
    monkeypatch.setattr(backup, "encrypt_file", failing_encrypt_file)
    monkeypatch.setattr(backup.asyncio, "to_thread", fake_to_thread)

    settings = SimpleNamespace(
        admin_user_id=42,
        backup_encryption_public_key=_PUBLIC_KEY,
        backup_restore_drill_enabled=True,
    )
    bot_client = SimpleNamespace(send_document=fake_send_document)

    with pytest.raises(backup.BackupJobError, match="Backup encryption failed for bot_pg_dump.dump: disk full"):
        await backup.send_daily_backup(bot=bot_client, settings=settings)

    assert sent == []
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

    settings = SimpleNamespace(admin_user_id=42, backup_encryption_public_key=_PUBLIC_KEY)
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
    ) -> None:
        _ = dump_path, settings
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

    settings = SimpleNamespace(admin_user_id=42, backup_encryption_public_key=_PUBLIC_KEY)
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
        "backup_timeout_seconds": 30.0,
        "backup_pg_dump_timeout_seconds": 1800.0,
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
        *command: str, stdout: object, stderr: object
    ) -> _FakePgRestoreProcess:
        _ = stdout, stderr
        captured["command"] = command
        return _FakePgRestoreProcess(returncode=returncode, stderr=stderr_bytes)

    monkeypatch.setattr(backup.asyncio, "create_subprocess_exec", fake_exec)


@pytest.mark.asyncio
async def test_pg_dump_keeps_the_database_password_out_of_argv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_exec(*command: str, **kwargs: Any) -> _FakePgRestoreProcess:
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return _FakePgRestoreProcess(returncode=0)

    monkeypatch.setattr(backup.asyncio, "create_subprocess_exec", fake_exec)
    settings = _make_settings(
        database_url="postgresql+asyncpg://selara:s3cret@db.internal:5432/selara",
        backup_pg_dump_path="pg_dump",
    )

    dump = await backup._create_bot_database_dump(settings=settings, temp_dir=tmp_path)

    # argv is readable by every local user through /proc, so the password travels only in PGPASSWORD.
    assert not any("s3cret" in argument for argument in captured["command"])
    assert "--dbname=postgresql://selara@db.internal:5432/selara" in captured["command"]
    assert captured["env"]["PGPASSWORD"] == "s3cret"
    assert dump.path == tmp_path / "bot_pg_dump.dump"


class _PgDumpChild:
    """A pg_dump stand-in that runs until it is stopped, and records how it was stopped.

    `ignores_terminate` models a child that only exits when it is killed.
    """

    def __init__(self, *, temp_dir: Path, ignores_terminate: bool = False) -> None:
        self._temp_dir = temp_dir
        self._ignores_terminate = ignores_terminate
        self._exited = asyncio.Event()
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.reaped = False
        self.temp_dir_present_at_reap: bool | None = None

    async def communicate(self) -> tuple[bytes, bytes]:
        await self._exited.wait()
        return b"", b""

    def terminate(self) -> None:
        self.terminated = True
        if not self._ignores_terminate:
            self._exit(-15)

    def kill(self) -> None:
        self.killed = True
        self._exit(-9)

    def _exit(self, returncode: int) -> None:
        self.returncode = returncode
        self._exited.set()

    async def wait(self) -> int:
        await self._exited.wait()
        self.reaped = True
        self.temp_dir_present_at_reap = self._temp_dir.exists()
        assert self.returncode is not None
        return self.returncode


async def _cancel_daily_backup_during_pg_dump(
    monkeypatch: pytest.MonkeyPatch,
    job_dir: Path,
    child: _PgDumpChild,
) -> None:
    started = asyncio.Event()

    async def fake_exec(*command: str, **kwargs: Any) -> _PgDumpChild:
        _ = command, kwargs
        started.set()
        return child

    monkeypatch.setattr(backup.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(backup.tempfile, "mkdtemp", lambda prefix: str(job_dir))
    settings = SimpleNamespace(
        admin_user_id=42,
        backup_encryption_public_key=_PUBLIC_KEY,
        backup_pg_dump_path="pg_dump",
        backup_restore_drill_enabled=False,
        backup_pg_dump_timeout_seconds=30.0,
        database_url="postgresql+asyncpg://selara:s3cret@db.internal:5432/selara",
    )
    job = asyncio.create_task(backup.send_daily_backup(bot=SimpleNamespace(), settings=settings))
    await asyncio.wait_for(started.wait(), 5)

    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job


@pytest.mark.asyncio
async def test_cancelled_pg_dump_is_terminated_and_reaped_before_the_temp_dir_is_removed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    child = _PgDumpChild(temp_dir=job_dir)

    await _cancel_daily_backup_during_pg_dump(monkeypatch, job_dir, child)

    # A cancelled backup must not leave pg_dump reading the database, and the temp directory
    # may go only once the child has been reaped.
    assert child.terminated
    assert not child.killed
    assert child.reaped
    assert child.temp_dir_present_at_reap
    assert not job_dir.exists()


@pytest.mark.asyncio
async def test_pg_dump_that_ignores_terminate_is_killed_after_a_bounded_wait(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    child = _PgDumpChild(temp_dir=job_dir, ignores_terminate=True)
    monkeypatch.setattr(backup, "_PROCESS_REAP_TIMEOUT_SECONDS", 0.05)

    await _cancel_daily_backup_during_pg_dump(monkeypatch, job_dir, child)

    assert child.terminated
    assert child.killed
    assert child.reaped
    assert child.temp_dir_present_at_reap
    assert not job_dir.exists()


@pytest.mark.asyncio
async def test_pg_dump_that_outlives_its_timeout_is_terminated_and_reaped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    child = _PgDumpChild(temp_dir=tmp_path)

    async def fake_exec(*command: str, **kwargs: Any) -> _PgDumpChild:
        _ = command, kwargs
        return child

    monkeypatch.setattr(backup.asyncio, "create_subprocess_exec", fake_exec)
    settings = _make_settings(
        database_url="postgresql+asyncpg://selara:s3cret@db.internal:5432/selara",
        backup_pg_dump_path="pg_dump",
        backup_pg_dump_timeout_seconds=0.05,
    )

    # A dump that never finishes must fail the job instead of holding it forever.
    with pytest.raises(backup.BackupJobError, match="timed out after 0.05s"):
        await asyncio.wait_for(
            backup._create_bot_database_dump(settings=settings, temp_dir=tmp_path),
            timeout=5,
        )

    assert child.terminated
    assert not child.killed
    assert child.reaped


@pytest.mark.asyncio
async def test_verify_dump_restorable_emits_full_sql_script_offline(
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
    )

    # Emitting the SQL script makes pg_restore decompress every archive data
    # block, unlike `--list`, which only reads the table of contents. The
    # script goes to the null device so no dump-sized file lands in /tmp, and
    # the archive is read from its filename argument, so no database connection
    # (and no credentials in argv) is needed at all.
    assert captured["command"] == (
        "pg_restore",
        f"--file={os.devnull}",
        str(dump_path),
    )
    # The offline check must not leave any scratch artefact next to the dump.
    assert [item.name for item in tmp_path.iterdir()] == [dump_path.name]


@pytest.mark.asyncio
async def test_concurrent_verifications_run_serialized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    verification_state = {"active": 0, "max_active": 0}

    class SlowRestoreProcess:
        def __init__(self) -> None:
            self.returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            verification_state["active"] += 1
            verification_state["max_active"] = max(
                verification_state["max_active"], verification_state["active"]
            )
            await asyncio.sleep(0.01)
            verification_state["active"] -= 1
            return b"", b""

    async def fake_exec(*command: str, **kwargs: object) -> SlowRestoreProcess:
        _ = command, kwargs
        return SlowRestoreProcess()

    monkeypatch.setattr(backup.asyncio, "create_subprocess_exec", fake_exec)

    async def verify(dump_name: str) -> None:
        dump_path = tmp_path / dump_name
        dump_path.write_bytes(b"PGDMP-fake")
        await backup._verify_pg_dump_restorable(
            dump_path=dump_path,
            label=f"dump {dump_name}",
            settings=_make_settings(),
        )

    await asyncio.gather(verify("bot_pg_dump.dump"), verify("gacha_pg_dump.dump"))

    # The nightly scheduler and an admin-triggered backup may overlap; their
    # pg_restore children must not.
    assert verification_state["max_active"] == 1


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
        )


@pytest.mark.asyncio
async def test_verify_dump_restorable_times_out_and_kills_pg_restore(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dump_path = tmp_path / "bot_pg_dump.dump"
    dump_path.write_bytes(b"PGDMP-fake")
    state = {"killed": 0, "reaped": 0}

    class HangingRestoreProcess:
        def __init__(self) -> None:
            self.returncode: int | None = None

        async def communicate(self) -> tuple[bytes, bytes]:
            # Simulate a pg_restore that hangs on a pathological archive.
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        def kill(self) -> None:
            state["killed"] += 1
            self.returncode = -9

        async def wait(self) -> int:
            state["reaped"] += 1
            return self.returncode if self.returncode is not None else -9

    async def fake_exec(*command: str, **kwargs: object) -> HangingRestoreProcess:
        _ = command, kwargs
        return HangingRestoreProcess()

    monkeypatch.setattr(backup.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(
        backup.BackupJobError,
        match="timed out after 0.05s for main bot database dump",
    ):
        await backup._verify_dump_restorable(
            dump_path=dump_path,
            label="main bot database dump",
            settings=_make_settings(backup_timeout_seconds=0.05),
        )

    assert state == {"killed": 1, "reaped": 1}


@pytest.mark.asyncio
async def test_verify_dump_restorable_cancellation_kills_pg_restore(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dump_path = tmp_path / "bot_pg_dump.dump"
    dump_path.write_bytes(b"PGDMP-fake")
    state = {"killed": 0, "reaped": 0}
    restore_started = asyncio.Event()

    class HangingRestoreProcess:
        def __init__(self) -> None:
            self.returncode: int | None = None

        async def communicate(self) -> tuple[bytes, bytes]:
            # Simulate a pg_restore that is still running when the task that
            # spawned it is cancelled (bot shutdown in the middle of a backup).
            restore_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        def kill(self) -> None:
            state["killed"] += 1
            self.returncode = -9

        async def wait(self) -> int:
            state["reaped"] += 1
            return self.returncode if self.returncode is not None else -9

    async def fake_exec(*command: str, **kwargs: object) -> HangingRestoreProcess:
        _ = command, kwargs
        return HangingRestoreProcess()

    monkeypatch.setattr(backup.asyncio, "create_subprocess_exec", fake_exec)

    task = asyncio.create_task(
        backup._verify_dump_restorable(
            dump_path=dump_path,
            label="main bot database dump",
            settings=_make_settings(),
        )
    )
    await asyncio.wait_for(restore_started.wait(), 1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The child was killed and reaped instead of surviving as an orphan, the
    # cancellation still propagates, and the verification lock is free again.
    assert state == {"killed": 1, "reaped": 1}
    assert task.cancelled()
    assert not backup._BACKUP_VERIFICATION_LOCK.locked()


@pytest.mark.asyncio
async def test_notify_backup_failure_includes_bounded_reason() -> None:
    messages: list[str] = []

    async def fake_send_message(*, chat_id: int, text: str) -> None:
        messages.append(text)

    bot_client = SimpleNamespace(send_message=fake_send_message)
    await backup._notify_backup_failure(
        bot=bot_client,
        settings=SimpleNamespace(admin_user_id=42),
        reason="pg_restore: error: " + "very-long-detail " * 100,
    )

    assert len(messages) == 1
    assert "Суточный backup Selara завершился ошибкой." in messages[0]
    assert "pg_restore: error:" in messages[0]
    assert len(messages[0]) < backup._BACKUP_FAILURE_REASON_MAX_CHARS + 100
    assert "very-long-detail " * 100 not in messages[0]


@pytest.mark.asyncio
async def test_verify_dump_restorable_routes_sqlite_dumps_to_integrity_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    routed: list[str] = []

    def fake_verify_sqlite(dump_path: Path, label: str, timeout_seconds: float) -> None:
        _ = timeout_seconds
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
        settings=SimpleNamespace(backup_timeout_seconds=30.0),
    )

    assert routed == ["sqlite:gacha dump"]


def test_verify_sqlite_dump_restorable_passes_on_healthy_sqlite_file(tmp_path: Path) -> None:
    dump_path = tmp_path / "gacha_pg_dump.sqlite3"
    connection = sqlite3.connect(dump_path)
    connection.execute("CREATE TABLE gacha_items (id INTEGER PRIMARY KEY, name TEXT)")
    connection.commit()
    connection.close()

    backup._verify_sqlite_dump_restorable(dump_path, "gacha dump", 30.0)


def test_verify_sqlite_dump_restorable_rejects_empty_sqlite_file(tmp_path: Path) -> None:
    dump_path = tmp_path / "gacha_pg_dump.sqlite3"
    dump_path.write_bytes(b"")

    with pytest.raises(
        backup.BackupJobError,
        match="Backup restore verification failed for gacha dump: dump file is empty",
    ):
        backup._verify_sqlite_dump_restorable(dump_path, "gacha dump", 30.0)


def test_verify_sqlite_dump_restorable_fails_on_corrupt_sqlite_file(tmp_path: Path) -> None:
    dump_path = tmp_path / "gacha_pg_dump.sqlite3"
    dump_path.write_bytes(b"definitely not a sqlite database")

    with pytest.raises(
        backup.BackupJobError,
        match="Backup restore verification failed for gacha dump: file is not a SQLite database",
    ):
        backup._verify_sqlite_dump_restorable(dump_path, "gacha dump", 30.0)


def test_verify_sqlite_dump_restorable_fails_on_truncated_sqlite_header(tmp_path: Path) -> None:
    dump_path = tmp_path / "gacha_pg_dump.sqlite3"
    dump_path.write_bytes(b"SQLite format 3")

    with pytest.raises(
        backup.BackupJobError,
        match="Backup restore verification failed for gacha dump: file is not a SQLite database",
    ):
        backup._verify_sqlite_dump_restorable(dump_path, "gacha dump", 30.0)


def test_verify_sqlite_dump_restorable_interruption_is_bounded_by_timeout(
    tmp_path: Path,
) -> None:
    # integrity_check on a large snapshot can outlive the job that waits for
    # it; a zero timeout must interrupt the check instead of running it to
    # completion in the executor thread.
    dump_path = tmp_path / "gacha_pg_dump.sqlite3"
    connection = sqlite3.connect(dump_path)
    connection.execute("CREATE TABLE gacha_items (id INTEGER PRIMARY KEY, name TEXT)")
    connection.executemany(
        "INSERT INTO gacha_items (name) VALUES (?)",
        [(f"item {number}",) for number in range(1000)],
    )
    connection.commit()
    connection.close()

    with pytest.raises(
        backup.BackupJobError,
        match="timed out after 0s for gacha dump.*integrity_check did not finish",
    ):
        backup._verify_sqlite_dump_restorable(dump_path, "gacha dump", 0.0)
