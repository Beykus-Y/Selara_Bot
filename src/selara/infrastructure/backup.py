from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from aiogram.types import FSInputFile
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.core.config import Settings
from selara.infrastructure.backup_encryption import (
    ENCRYPTED_SUFFIX,
    ENCRYPTION_FORMAT,
    BackupCryptoError,
    encrypt_file,
    parse_public_key,
    public_key_fingerprint,
)
from selara.infrastructure.backup_drill import (
    GACHA_DATABASE_TARGET,
    MAIN_DATABASE_TARGET,
    drill_postgres_dump,
    drill_sqlite_snapshot,
    libpq_url,
    read_live_schema_head,
)
from selara.infrastructure.db.backup_claims import (
    BACKUP_SLOT_COMPLETED,
    BACKUP_SLOT_FAILED,
    BACKUP_SLOT_RUNNING,
    MANUAL_BACKUP_SLOT_KEY,
    finish_backup_slot,
    read_backup_slot,
    read_backup_slot_status,
    renew_backup_slot_lease,
    try_claim_backup_slot,
    try_claim_manual_backup,
)
from selara.infrastructure.http.gacha_client import GachaClientError, HttpGachaClient

logger = logging.getLogger(__name__)

# The hosted Telegram Bot API accepts documents smaller than 50 MB. Keep a
# margin for differences between decimal MB and MiB and for future API changes.
BACKUP_CHUNK_SIZE_BYTES = 45 * 1024 * 1024

# A scheduled slot is owned through a lease that the running job renews. If the
# owner dies, its lease expires and a later claim may take the slot over.
_BACKUP_LEASE_SECONDS = 15 * 60
_BACKUP_LEASE_RENEW_SECONDS = _BACKUP_LEASE_SECONDS / 3

# Serialize dump verification: overlapping backup jobs (the nightly scheduler
# and an admin-triggered request) must not run parallel pg_restore children.
_BACKUP_VERIFICATION_LOCK = asyncio.Lock()

# SQLite database files always start with this 16-byte header string.
_SQLITE_HEADER_MAGIC = b"SQLite format 3\x00"

# A verification child that ignores SIGKILL would defeat the timeout, so after
# killing it wait only this long for the event loop to reap it.
_PROCESS_REAP_TIMEOUT_SECONDS = 10.0

# The SQLite progress handler is consulted after this many VM instructions; a
# non-zero return aborts the running integrity_check, which bounds it in time.
_SQLITE_PROGRESS_HANDLER_OPS = 1000

# Telegram messages are bounded; keep the failure reason useful but short so a
# verbose pg_restore error cannot turn the notification into a wall of text.
_BACKUP_FAILURE_REASON_MAX_CHARS = 500


class BackupJobError(RuntimeError):
    pass


class BackupAlreadyRunningError(BackupJobError):
    pass


# Manual backup jobs are not owned by any request, so keep them (by job id) until they finish.
_manual_backup_tasks: dict[str, asyncio.Task[None]] = {}


@dataclass(slots=True)
class BackupFile:
    path: Path
    archive_name: str


@dataclass(slots=True)
class BackupPart:
    path: Path
    filename: str
    number: int
    total: int
    size_bytes: int


def next_backup_slot(*, timezone_name: str, now: datetime | None = None) -> datetime:
    """Return the next local midnight; every bot instance computes the same slot for a given day."""
    try:
        local_tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise BackupJobError(f"Unknown backup timezone: {timezone_name}") from exc

    local_now = _as_utc(now).astimezone(local_tz)
    next_local_date = local_now.date() + timedelta(days=1)
    return datetime.combine(next_local_date, time.min, tzinfo=local_tz)


def seconds_until_next_backup(*, timezone_name: str, now: datetime | None = None) -> float:
    now_utc = _as_utc(now)
    next_local_midnight = next_backup_slot(timezone_name=timezone_name, now=now_utc)
    return max(1.0, (next_local_midnight.astimezone(timezone.utc) - now_utc).total_seconds())


def _as_utc(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _scheduled_slot_key(slot: datetime) -> str:
    return f"daily:{slot.date().isoformat()}"


async def run_daily_backup_scheduler(
    *,
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    while True:
        slot = next_backup_slot(timezone_name=settings.bot_timezone)
        delay = max(1.0, (slot.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
        await asyncio.sleep(delay)
        try:
            await run_scheduled_daily_backup(
                bot=bot,
                settings=settings,
                session_factory=session_factory,
                slot_key=_scheduled_slot_key(slot),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Daily Selara backup job failed")
            try:
                await _notify_backup_failure(bot=bot, settings=settings, reason=str(exc))
            except Exception:
                logger.exception("Could not notify admin about backup failure")


async def run_scheduled_daily_backup(
    *,
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    slot_key: str,
) -> None:
    """Run the scheduled backup for one slot, taking it over if its owner crashes.

    A losing instance waits while the slot is running elsewhere. It returns once the
    slot is completed or failed, or claims it after the owner's lease has expired.
    """
    owner_token = uuid4().hex
    while True:
        claimed = await try_claim_backup_slot(
            session_factory=session_factory,
            slot_key=slot_key,
            owner_token=owner_token,
            lease_seconds=_BACKUP_LEASE_SECONDS,
        )
        if claimed:
            break
        status = await read_backup_slot_status(session_factory=session_factory, slot_key=slot_key)
        if status in (BACKUP_SLOT_COMPLETED, BACKUP_SLOT_FAILED):
            logger.info("Daily backup slot already finished on another instance; skipping", extra={"slot_key": slot_key})
            return
        # Still running on another instance: look again once its lease could have expired.
        await asyncio.sleep(_BACKUP_LEASE_SECONDS + 1)

    lease_lost = asyncio.Event()
    job = asyncio.create_task(send_daily_backup(bot=bot, settings=settings), name="daily-backup-job")
    lease = asyncio.create_task(
        _keep_backup_lease_alive(
            session_factory=session_factory,
            slot_key=slot_key,
            owner_token=owner_token,
            on_lost=lambda: _abort_lost_backup(job, lease_lost),
        ),
        name="daily-backup-lease",
    )
    try:
        await job
    except asyncio.CancelledError:
        await _stop_task(lease)
        if lease_lost.is_set():
            # Another instance now owns the slot; this run must not record its outcome.
            logger.error("Daily backup stopped after losing its lease", extra={"slot_key": slot_key})
            return
        raise
    except Exception as exc:
        await _stop_task(lease)
        await finish_backup_slot(
            session_factory=session_factory,
            slot_key=slot_key,
            owner_token=owner_token,
            status=BACKUP_SLOT_FAILED,
            error=str(exc),
        )
        raise

    await _stop_task(lease)
    await finish_backup_slot(
        session_factory=session_factory,
        slot_key=slot_key,
        owner_token=owner_token,
        status=BACKUP_SLOT_COMPLETED,
    )


def _abort_lost_backup(job: asyncio.Task[None], lease_lost: asyncio.Event) -> None:
    lease_lost.set()
    job.cancel()


async def _keep_backup_lease_alive(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    slot_key: str,
    owner_token: str,
    on_lost: Callable[[], None],
) -> None:
    while True:
        await asyncio.sleep(_BACKUP_LEASE_RENEW_SECONDS)
        try:
            renewed = await renew_backup_slot_lease(
                session_factory=session_factory,
                slot_key=slot_key,
                owner_token=owner_token,
                lease_seconds=_BACKUP_LEASE_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Could not renew daily backup lease", extra={"slot_key": slot_key})
            continue
        if not renewed:
            logger.error("Daily backup lease was lost; stopping this run", extra={"slot_key": slot_key})
            on_lost()
            return


async def _stop_task(task: asyncio.Task[None]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def start_manual_backup(
    *,
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> str:
    """Start an admin-requested backup in the background and return its job id.

    The request only claims the single manual slot. The dump runs in its own task, so
    a dropped browser connection cannot cancel it. Only one manual backup may run at a
    time across bot instances; a second request raises BackupAlreadyRunningError.
    """
    if settings.admin_user_id is None:
        raise BackupJobError("ADMIN_USER_ID is not configured, backup archive cannot be delivered.")

    # Fail before claiming the slot if the archives could not be encrypted.
    _resolve_backup_recipient(settings)

    job_id = uuid4().hex
    claimed = await try_claim_manual_backup(
        session_factory=session_factory,
        owner_token=job_id,
        lease_seconds=_BACKUP_LEASE_SECONDS,
    )
    if not claimed:
        raise BackupAlreadyRunningError("Backup уже выполняется.")

    task = asyncio.create_task(
        _run_manual_backup(
            bot=bot,
            settings=settings,
            session_factory=session_factory,
            job_id=job_id,
        ),
        name="manual-backup-job",
    )
    _manual_backup_tasks[job_id] = task
    task.add_done_callback(lambda _done, jid=job_id: _manual_backup_tasks.pop(jid, None))
    return job_id


async def _run_manual_backup(
    *,
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    job_id: str,
) -> None:
    lease_lost = asyncio.Event()
    job = asyncio.create_task(send_daily_backup(bot=bot, settings=settings), name="manual-backup-dump")
    lease = asyncio.create_task(
        _keep_backup_lease_alive(
            session_factory=session_factory,
            slot_key=MANUAL_BACKUP_SLOT_KEY,
            owner_token=job_id,
            on_lost=lambda: _abort_lost_backup(job, lease_lost),
        ),
        name="manual-backup-lease",
    )
    try:
        await job
    except asyncio.CancelledError:
        await _stop_task(lease)
        if lease_lost.is_set():
            logger.error("Manual backup stopped after losing its lease", extra={"job_id": job_id})
            return
        await _record_manual_backup_result(
            session_factory=session_factory,
            job_id=job_id,
            status=BACKUP_SLOT_FAILED,
            error="Backup прерван остановкой сервиса.",
        )
        raise
    except Exception as exc:
        await _stop_task(lease)
        logger.exception("Manual Selara backup failed", extra={"job_id": job_id})
        await _record_manual_backup_result(
            session_factory=session_factory,
            job_id=job_id,
            status=BACKUP_SLOT_FAILED,
            error=str(exc),
        )
        try:
            await _notify_backup_failure(bot=bot, settings=settings, reason=str(exc))
        except Exception:
            logger.exception("Could not notify admin about manual backup failure")
        return

    await _stop_task(lease)
    await _record_manual_backup_result(
        session_factory=session_factory,
        job_id=job_id,
        status=BACKUP_SLOT_COMPLETED,
    )


async def _record_manual_backup_result(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    job_id: str,
    status: str,
    error: str | None = None,
) -> None:
    # A failed status write must not hide the outcome from the admin: the caller still notifies.
    try:
        await finish_backup_slot(
            session_factory=session_factory,
            slot_key=MANUAL_BACKUP_SLOT_KEY,
            owner_token=job_id,
            status=status,
            error=error,
        )
    except Exception:
        logger.exception("Could not record manual backup result", extra={"job_id": job_id, "status": status})


async def stop_manual_backups(*, session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Cancel manual backups still running and record each as failed; call before their bot session closes.

    A job cancelled before it first runs never reaches its own failure path, so the
    record is written here. finish_backup_slot only updates a row that is still running,
    so a job that already finished keeps its result.
    """
    jobs = tuple(_manual_backup_tasks.items())
    for _job_id, task in jobs:
        task.cancel()
    if jobs:
        await asyncio.gather(*(task for _job_id, task in jobs), return_exceptions=True)
    for job_id, _task in jobs:
        await _record_manual_backup_result(
            session_factory=session_factory,
            job_id=job_id,
            status=BACKUP_SLOT_FAILED,
            error="Backup прерван остановкой сервиса.",
        )


async def read_manual_backup_status(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    now: datetime | None = None,
) -> dict[str, str | None]:
    """Describe the latest manual backup: idle, running, completed, failed or interrupted.

    A running row whose lease has expired means its process died mid-job; the next
    request may take the slot over, so it is reported as interrupted rather than running.
    """
    snapshot = await read_backup_slot(session_factory=session_factory, slot_key=MANUAL_BACKUP_SLOT_KEY)
    if snapshot is None:
        return {"status": "idle", "started_at": None, "finished_at": None, "error": None}

    status = snapshot.status
    error = snapshot.last_error
    if status == BACKUP_SLOT_RUNNING and _as_utc(now) > _as_utc(snapshot.lease_expires_at):
        status = "interrupted"
        error = "Backup прервался до завершения. Запросите его снова."
    finished_at = snapshot.finished_at
    return {
        "status": status,
        "started_at": _as_utc(snapshot.claimed_at).isoformat(),
        "finished_at": _as_utc(finished_at).isoformat() if finished_at is not None else None,
        "error": error,
    }


async def send_daily_backup(*, bot: Bot, settings: Settings) -> None:
    """Send an encrypted backup now. Admin requests reach it through start_manual_backup; it claims no scheduled slot.

    Every archive is encrypted to BACKUP_ENCRYPTION_PUBLIC_KEY before it is split or
    sent. If encryption cannot be set up or fails, nothing is sent at all.
    """
    admin_user_id = settings.admin_user_id
    if admin_user_id is None:
        raise BackupJobError("ADMIN_USER_ID is not configured, backup archive cannot be delivered.")
    recipient = _resolve_backup_recipient(settings)

    temp_dir = Path(tempfile.mkdtemp(prefix="selara-daily-backup-"))
    try:
        bot_dump = await _create_bot_database_dump(settings=settings, temp_dir=temp_dir)
        gacha_dump = await _download_gacha_backup(settings=settings, temp_dir=temp_dir)

        # Verify that every produced dump is actually restorable before it is
        # sent out; a backup that cannot be restored is worse than no backup.
        await _verify_dump_restorable(
            dump_path=bot_dump.path,
            label="main bot database dump",
            settings=settings,
        )
        await _verify_dump_restorable(
            dump_path=gacha_dump.path,
            label="gacha dump",
            settings=settings,
        )

        # Restore both dumps into scratch databases before anything is encrypted or
        # sent: a dump that parses offline can still fail to restore.
        if settings.backup_restore_drill_enabled:
            await _drill_bot_database_dump(dump_path=bot_dump.path, settings=settings)
            await _drill_gacha_dump(dump_path=gacha_dump.path, settings=settings)
        else:
            logger.warning("Backup restore drill is disabled; dumps are sent without a restore check")

        # Encrypt both archives before the first upload, so a failure here cannot
        # leave a partial backup set in Telegram.
        encrypted_files = [
            await asyncio.to_thread(_encrypt_backup_file, backup_file, recipient)
            for backup_file in (bot_dump, gacha_dump)
        ]

        created_at = _backup_timestamp()
        manifest_files: list[dict[str, object]] = []
        for encrypted_file in encrypted_files:
            parts, manifest_entry = await asyncio.to_thread(
                _split_backup_file,
                encrypted_file,
                temp_dir,
                BACKUP_CHUNK_SIZE_BYTES,
            )
            manifest_files.append(manifest_entry)
            for part in parts:
                await bot.send_document(
                    chat_id=admin_user_id,
                    document=FSInputFile(part.path, filename=part.filename),
                    caption=(
                        f"Selara daily backup: {manifest_entry['filename']} "
                        f"(part {part.number}/{part.total})"
                    ),
                )

        manifest_path = await asyncio.to_thread(
            _write_backup_manifest,
            temp_dir=temp_dir,
            created_at=created_at,
            chunk_size_bytes=BACKUP_CHUNK_SIZE_BYTES,
            files=manifest_files,
            encryption={
                "format": ENCRYPTION_FORMAT,
                "recipient_sha256": public_key_fingerprint(recipient),
            },
        )
        await bot.send_document(
            chat_id=admin_user_id,
            document=FSInputFile(manifest_path, filename=manifest_path.name),
            caption="Selara daily backup manifest",
        )
    finally:
        await asyncio.to_thread(shutil.rmtree, temp_dir, True)


def _bot_database_url(settings: Settings) -> URL:
    try:
        database_url = make_url(settings.database_url)
    except ArgumentError as exc:
        raise BackupJobError("DATABASE_URL is invalid, bot backup could not be created.") from exc

    if database_url.get_backend_name() != "postgresql":
        raise BackupJobError("Daily backup currently supports only PostgreSQL for the main bot.")
    return database_url


async def _create_bot_database_dump(*, settings: Settings, temp_dir: Path) -> BackupFile:
    database_url = _bot_database_url(settings)

    output_path = temp_dir / "bot_pg_dump.dump"
    command = [
        settings.backup_pg_dump_path,
        "--format=custom",
        "--compress=9",
        "--no-owner",
        "--no-privileges",
        f"--file={output_path}",
        # The password travels in PGPASSWORD below: argv is readable by every local user through /proc.
        f"--dbname={libpq_url(database_url, database_url.database)}",
    ]
    env = os.environ.copy()
    if database_url.password is not None:
        env["PGPASSWORD"] = database_url.password

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError as exc:
        raise BackupJobError(
            f"Backup command '{settings.backup_pg_dump_path}' is not available in the main bot runtime."
        ) from exc

    _stdout, stderr = await process.communicate()
    if process.returncode != 0:
        detail = _last_line(stderr)
        if detail:
            raise BackupJobError(f"pg_dump failed for main bot database: {detail}")
        raise BackupJobError("pg_dump failed for main bot database.")

    return BackupFile(path=output_path, archive_name=output_path.name)


async def _download_gacha_backup(*, settings: Settings, temp_dir: Path) -> BackupFile:
    base_url = _resolve_gacha_backup_base_url(settings)
    if base_url is None:
        raise BackupJobError("Gacha backup is not configured: missing GACHA_BASE_URL.")
    admin_token = settings.gacha_admin_token.strip()
    if not admin_token:
        raise BackupJobError("Gacha backup is not configured: missing GACHA_ADMIN_TOKEN.")

    client = HttpGachaClient(base_url=base_url, timeout_seconds=settings.backup_timeout_seconds)
    try:
        gacha_backup = await client.download_backup(admin_token=admin_token)
    except GachaClientError as exc:
        raise BackupJobError(f"Gacha backup download failed: {exc.message}") from exc

    suffix = Path(gacha_backup.filename).suffix or ".dump"
    output_path = temp_dir / f"gacha_pg_dump{suffix}"
    await asyncio.to_thread(output_path.write_bytes, gacha_backup.content)
    return BackupFile(path=output_path, archive_name=output_path.name)


async def _verify_dump_restorable(
    *,
    dump_path: Path,
    label: str,
    settings: Settings,
) -> None:
    """Fail the backup job when the produced dump cannot be restored.

    PostgreSQL custom-format dumps are validated offline with pg_restore:
    emitting the whole SQL script parses and decompresses every archive data
    block without touching any database server. SQLite dumps are validated
    with an equivalent integrity check. Both checks are bounded by
    BACKUP_TIMEOUT_SECONDS.
    """
    if dump_path.suffix.lower() == ".sqlite3":
        # The gacha service names its SQLite snapshot `*.sqlite3`; this suffix
        # is what routes the dump to the integrity check instead of pg_restore.
        await asyncio.to_thread(
            _verify_sqlite_dump_restorable,
            dump_path,
            label,
            settings.backup_timeout_seconds,
        )
        return
    await _verify_pg_dump_restorable(
        dump_path=dump_path,
        label=label,
        settings=settings,
    )


async def _verify_pg_dump_restorable(
    *,
    dump_path: Path,
    label: str,
    settings: Settings,
) -> None:
    # Overlapping backup jobs verify their dumps one after another instead of
    # spawning parallel pg_restore children.
    async with _BACKUP_VERIFICATION_LOCK:
        # Offline check without a database server: emitting the SQL script
        # forces pg_restore to parse and decompress every archive data block,
        # unlike `--list`, which only reads the table of contents. The archive
        # is read from its filename argument, so pg_restore never opens a
        # database connection and needs no credentials. The script itself is
        # discarded into the null device instead of a staging file: /tmp is a
        # small tmpfs shared with gacha rendering, and a dump-sized SQL file
        # next to the archive can exhaust it.
        await _run_pg_restore_verification(
            label=label,
            settings=settings,
            args=[f"--file={os.devnull}", str(dump_path)],
        )


def _verify_sqlite_dump_restorable(
    dump_path: Path,
    label: str,
    timeout_seconds: float,
) -> None:
    if dump_path.stat().st_size == 0:
        raise BackupJobError(
            f"Backup restore verification failed for {label}: dump file is empty."
        )
    with dump_path.open("rb") as handle:
        header = handle.read(len(_SQLITE_HEADER_MAGIC))
    if header != _SQLITE_HEADER_MAGIC:
        raise BackupJobError(
            f"Backup restore verification failed for {label}: file is not a SQLite database."
        )
    try:
        connection = sqlite3.connect(dump_path)
    except sqlite3.Error as exc:
        raise BackupJobError(f"Backup restore verification failed for {label}: {exc}") from exc

    # integrity_check on a large snapshot can run for a long time; a progress
    # handler makes it interruptible, so the check honours the same timeout as
    # the pg_restore child instead of outliving the backup job.
    deadline = monotonic() + max(timeout_seconds, 0.0)

    def _enforce_deadline() -> int:
        return int(monotonic() > deadline)

    connection.set_progress_handler(_enforce_deadline, _SQLITE_PROGRESS_HANDLER_OPS)
    try:
        status = connection.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.OperationalError as exc:
        if _enforce_deadline():
            raise BackupJobError(
                f"Backup restore verification timed out after {timeout_seconds:g}s for {label}: "
                "SQLite integrity_check did not finish and was interrupted."
            ) from exc
        raise BackupJobError(f"Backup restore verification failed for {label}: {exc}") from exc
    except sqlite3.Error as exc:
        raise BackupJobError(f"Backup restore verification failed for {label}: {exc}") from exc
    finally:
        connection.close()
    if status != "ok":
        raise BackupJobError(
            f"Backup restore verification failed for {label}: integrity_check reported {status!r}."
        )


async def _run_pg_restore_verification(
    *,
    label: str,
    settings: Settings,
    args: list[str],
) -> None:
    command = [settings.backup_pg_restore_path, *args]

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise BackupJobError(
            f"Backup restore verification command '{settings.backup_pg_restore_path}' "
            "is not available in the main bot runtime."
        ) from exc

    # A stalled pg_restore (a pathological archive, an I/O hang on a huge dump)
    # must not pin the nightly scheduler task or the admin HTTP request
    # forever, so bound the child and kill it on expiry.
    timeout_seconds = settings.backup_timeout_seconds
    try:
        async with asyncio.timeout(timeout_seconds):
            _stdout, stderr = await process.communicate()
    except TimeoutError as exc:
        await _kill_verification_process(process)
        raise BackupJobError(
            f"Backup restore verification timed out after {timeout_seconds:g}s for {label}: "
            f"'{settings.backup_pg_restore_path}' did not finish and was terminated."
        ) from exc
    except asyncio.CancelledError:
        # Shutdown cancels the scheduler task (backup_task.cancel() in
        # main.py) without any timeout having expired: the child must not
        # survive as an orphan while the verification lock is already
        # released. Kill and reap it, then propagate the cancellation.
        await _kill_verification_process(process)
        raise

    if process.returncode == 0:
        return

    detail = _last_line(stderr)
    if detail:
        raise BackupJobError(f"Backup restore verification failed for {label}: {detail}")
    raise BackupJobError(f"Backup restore verification failed for {label}.")


async def _kill_verification_process(process: asyncio.subprocess.Process) -> None:
    """Terminate and reap a verification child that outlived its coroutine.

    Whether its ``communicate()`` was cut short by the verification timeout or
    by the surrounding task being cancelled (bot shutdown), the child is killed
    and then reaped explicitly; otherwise a stalled pg_restore would survive as
    an orphan holding the archive open.
    """
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:  # pragma: no cover - child already exited
            pass
    try:
        await asyncio.wait_for(process.wait(), _PROCESS_REAP_TIMEOUT_SECONDS)
    except TimeoutError:  # pragma: no cover - a killed child must exit
        logger.warning("Backup restore verification process did not exit after being killed")


async def _drill_bot_database_dump(*, dump_path: Path, settings: Settings) -> None:
    """Restore the main bot dump into a scratch database and require the live schema head.

    The live head is read first, so a dump of a schema other than the one the running bot
    uses fails the drill instead of being sent.
    """
    database_url = _bot_database_url(settings)
    async with _BACKUP_VERIFICATION_LOCK:
        live_head = await read_live_schema_head(database_url, label=MAIN_DATABASE_TARGET.label)
        await drill_postgres_dump(
            admin_url=database_url,
            dump_path=dump_path,
            target=replace(MAIN_DATABASE_TARGET, expected_head=live_head),
            pg_restore_path=settings.backup_pg_restore_path,
            timeout_seconds=settings.backup_restore_drill_timeout_seconds,
        )


async def _drill_gacha_dump(*, dump_path: Path, settings: Settings) -> None:
    """Restore the gacha dump into a scratch database on the bot's own PostgreSQL server.

    The bot holds no gacha database credentials, but a logical dump restores anywhere,
    so the copy made here tests the same archive that is about to be sent.
    """
    if dump_path.suffix.lower() == ".sqlite3":
        await asyncio.to_thread(
            drill_sqlite_snapshot,
            snapshot_path=dump_path,
            target=GACHA_DATABASE_TARGET,
            timeout_seconds=settings.backup_restore_drill_timeout_seconds,
        )
        return
    async with _BACKUP_VERIFICATION_LOCK:
        await drill_postgres_dump(
            admin_url=_bot_database_url(settings),
            dump_path=dump_path,
            target=GACHA_DATABASE_TARGET,
            pg_restore_path=settings.backup_pg_restore_path,
            timeout_seconds=settings.backup_restore_drill_timeout_seconds,
        )


def _split_backup_file(
    backup_file: BackupFile,
    temp_dir: Path,
    chunk_size_bytes: int,
) -> tuple[list[BackupPart], dict[str, object]]:
    if chunk_size_bytes <= 0:
        raise BackupJobError("Backup chunk size must be greater than zero.")

    filename = Path(backup_file.archive_name).name
    if not filename:
        raise BackupJobError("Backup filename is empty.")

    size_bytes = backup_file.path.stat().st_size
    total_parts = max(1, (size_bytes + chunk_size_bytes - 1) // chunk_size_bytes)
    number_width = max(3, len(str(total_parts)))
    digest = hashlib.sha256()
    parts: list[BackupPart] = []

    with backup_file.path.open("rb") as source:
        for number in range(1, total_parts + 1):
            content = source.read(chunk_size_bytes)
            digest.update(content)
            part_filename = (
                f"{filename}.part-{number:0{number_width}d}-of-"
                f"{total_parts:0{number_width}d}"
            )
            part_path = temp_dir / part_filename
            part_path.write_bytes(content)
            parts.append(
                BackupPart(
                    path=part_path,
                    filename=part_filename,
                    number=number,
                    total=total_parts,
                    size_bytes=len(content),
                )
            )

    manifest_entry: dict[str, object] = {
        "filename": filename,
        "size_bytes": size_bytes,
        "sha256": digest.hexdigest(),
        "parts": [
            {"filename": part.filename, "size_bytes": part.size_bytes}
            for part in parts
        ],
    }
    return parts, manifest_entry


def _resolve_backup_recipient(settings: Settings) -> X25519PublicKey:
    encoded = (settings.backup_encryption_public_key or "").strip()
    if not encoded:
        raise BackupJobError("BACKUP_ENCRYPTION_PUBLIC_KEY is not configured, backup archive cannot be encrypted.")
    try:
        return parse_public_key(encoded)
    except BackupCryptoError as exc:
        raise BackupJobError(f"BACKUP_ENCRYPTION_PUBLIC_KEY is invalid: {exc}") from exc


def _encrypt_backup_file(backup_file: BackupFile, recipient: X25519PublicKey) -> BackupFile:
    """Replace a plaintext archive with its encrypted copy; the plaintext never leaves the host."""
    encrypted_path = backup_file.path.with_name(backup_file.path.name + ENCRYPTED_SUFFIX)
    try:
        encrypt_file(source=backup_file.path, destination=encrypted_path, recipient=recipient)
    except (BackupCryptoError, OSError) as exc:
        raise BackupJobError(f"Backup encryption failed for {backup_file.archive_name}: {exc}") from exc
    backup_file.path.unlink()
    return BackupFile(path=encrypted_path, archive_name=encrypted_path.name)


def _write_backup_manifest(
    *,
    temp_dir: Path,
    created_at: str,
    chunk_size_bytes: int,
    files: list[dict[str, object]],
    encryption: dict[str, object],
) -> Path:
    manifest_path = temp_dir / f"selara-daily-backup-{created_at}.manifest.json"
    payload = {
        "created_at": created_at,
        "chunk_size_bytes": chunk_size_bytes,
        "encryption": encryption,
        "files": files,
    }
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _resolve_gacha_backup_base_url(settings: Settings) -> str | None:
    for banner in ("", "genshin", "hsr"):
        resolved = settings.resolve_gacha_base_url(banner)
        if resolved:
            return resolved
    return None


def _backup_timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _last_line(raw: bytes) -> str:
    decoded = raw.decode("utf-8", errors="ignore").strip()
    if not decoded:
        return ""
    return decoded.splitlines()[-1]


def _bounded_failure_reason(reason: str) -> str:
    collapsed = " ".join(reason.split())
    if len(collapsed) <= _BACKUP_FAILURE_REASON_MAX_CHARS:
        return collapsed
    return collapsed[: _BACKUP_FAILURE_REASON_MAX_CHARS - 1] + "…"


async def _notify_backup_failure(*, bot: Bot, settings: Settings, reason: str = "") -> None:
    admin_user_id = settings.admin_user_id
    if admin_user_id is None:
        return
    # Surface the concrete failure so a missing pg_restore, a corrupt archive
    # and real corruption are distinguishable without log access. The dump is
    # still withheld on failure.
    detail = _bounded_failure_reason(reason)
    if detail:
        text = f"Суточный backup Selara завершился ошибкой.\nПричина: {detail}"
    else:
        text = "Суточный backup Selara завершился ошибкой. Подробности есть в логах."
    await bot.send_message(chat_id=admin_user_id, text=text)
