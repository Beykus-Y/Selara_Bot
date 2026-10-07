from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from aiogram.types import FSInputFile
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from selara.core.config import Settings
from selara.infrastructure.http.gacha_client import GachaClientError, HttpGachaClient

logger = logging.getLogger(__name__)

# The hosted Telegram Bot API accepts documents smaller than 50 MB. Keep a
# margin for differences between decimal MB and MiB and for future API changes.
BACKUP_CHUNK_SIZE_BYTES = 45 * 1024 * 1024

# Full restore drills write into one shared scratch database; serialize them
# so a scheduled backup and an admin-triggered request cannot interleave their
# `--clean` drop/create phases against the same database.
_RESTORE_DRILL_LOCK = asyncio.Lock()

# SQLite database files always start with this 16-byte header string.
_SQLITE_HEADER_MAGIC = b"SQLite format 3\x00"

# A verification child that ignores SIGKILL would defeat the timeout, so after
# killing it wait only this long for the event loop to reap it.
_PROCESS_REAP_TIMEOUT_SECONDS = 10.0

# libpq connection URI query parameters that can override where a connection
# actually goes (host, port, database or even a pg_service.conf entry). The
# restore drill must not accept them, or a query string could redirect the
# --clean restore onto the production database behind the guard's back.
_LIBPQ_CONNECTION_TARGET_KEYS = frozenset({"host", "hostaddr", "port", "dbname", "service"})

# Telegram messages are bounded; keep the failure reason useful but short so a
# verbose pg_restore error cannot turn the notification into a wall of text.
_BACKUP_FAILURE_REASON_MAX_CHARS = 500


class BackupJobError(RuntimeError):
    pass


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


def seconds_until_next_backup(*, timezone_name: str, now: datetime | None = None) -> float:
    try:
        local_tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise BackupJobError(f"Unknown backup timezone: {timezone_name}") from exc

    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)

    local_now = now_utc.astimezone(local_tz)
    next_local_date = local_now.date() + timedelta(days=1)
    next_local_midnight = datetime.combine(next_local_date, time.min, tzinfo=local_tz)
    return max(1.0, (next_local_midnight.astimezone(timezone.utc) - now_utc).total_seconds())


async def run_daily_backup_scheduler(*, bot: Bot, settings: Settings) -> None:
    while True:
        delay = seconds_until_next_backup(timezone_name=settings.bot_timezone)
        await asyncio.sleep(delay)
        try:
            await send_daily_backup(bot=bot, settings=settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Daily Selara backup job failed")
            try:
                await _notify_backup_failure(bot=bot, settings=settings, reason=str(exc))
            except Exception:
                logger.exception("Could not notify admin about backup failure")


async def send_daily_backup(*, bot: Bot, settings: Settings) -> None:
    admin_user_id = settings.admin_user_id
    if admin_user_id is None:
        raise BackupJobError("ADMIN_USER_ID is not configured, backup archive cannot be delivered.")

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
            temp_dir=temp_dir,
        )
        await _verify_dump_restorable(
            dump_path=gacha_dump.path,
            label="gacha dump",
            settings=settings,
            temp_dir=temp_dir,
        )

        created_at = _backup_timestamp()
        manifest_files: list[dict[str, object]] = []
        for backup_file in (bot_dump, gacha_dump):
            parts, manifest_entry = await asyncio.to_thread(
                _split_backup_file,
                backup_file,
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
        )
        await bot.send_document(
            chat_id=admin_user_id,
            document=FSInputFile(manifest_path, filename=manifest_path.name),
            caption="Selara daily backup manifest",
        )
    finally:
        await asyncio.to_thread(shutil.rmtree, temp_dir, True)


async def _create_bot_database_dump(*, settings: Settings, temp_dir: Path) -> BackupFile:
    try:
        database_url = make_url(settings.database_url)
    except ArgumentError as exc:
        raise BackupJobError("DATABASE_URL is invalid, bot backup could not be created.") from exc

    if database_url.get_backend_name() != "postgresql":
        raise BackupJobError("Daily backup currently supports only PostgreSQL for the main bot.")

    output_path = temp_dir / "bot_pg_dump.dump"
    command = [
        settings.backup_pg_dump_path,
        "--format=custom",
        "--compress=9",
        "--no-owner",
        "--no-privileges",
        f"--file={output_path}",
        f"--dbname={database_url.set(drivername='postgresql', password=None).render_as_string(hide_password=False)}",
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
    temp_dir: Path,
) -> None:
    """Fail the backup job when the produced dump cannot be restored.

    PostgreSQL custom-format dumps are validated with pg_restore: either by
    emitting the whole SQL script offline (pg_restore parses and decompresses
    every archive data block; no database server required) or, when
    BACKUP_RESTORE_DATABASE_URL points at a disposable database, by a full
    restore drill into it. SQLite dumps are validated with an equivalent
    integrity check.
    """
    if dump_path.suffix.lower() == ".sqlite3":
        # The gacha service names its SQLite snapshot `*.sqlite3`; this suffix
        # is what routes the dump to the integrity check instead of pg_restore.
        await asyncio.to_thread(_verify_sqlite_dump_restorable, dump_path, label)
        return
    await _verify_pg_dump_restorable(
        dump_path=dump_path,
        label=label,
        settings=settings,
        temp_dir=temp_dir,
    )


async def _verify_pg_dump_restorable(
    *,
    dump_path: Path,
    label: str,
    settings: Settings,
    temp_dir: Path,
) -> None:
    # temp_dir stays in the signature because the shared verification call
    # contract passes it; the offline check deliberately writes nothing into it.
    restore_target = _resolve_backup_restore_target(settings)
    if restore_target is not None:
        database_url, password = restore_target
        args = [
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            "--clean",
            "--if-exists",
            f"--dbname={database_url}",
            str(dump_path),
        ]
        async with _RESTORE_DRILL_LOCK:
            await _run_pg_restore_verification(
                label=label,
                settings=settings,
                args=args,
                password=password,
            )
        return

    # Offline check without a database server: emitting the SQL script forces
    # pg_restore to parse and decompress every archive data block, unlike
    # `--list`, which only reads the table of contents. The script itself is
    # discarded into the null device instead of a staging file: /tmp is a
    # small tmpfs shared with gacha rendering, and a dump-sized SQL file next
    # to the archive can exhaust it.
    await _run_pg_restore_verification(
        label=label,
        settings=settings,
        args=[f"--file={os.devnull}", str(dump_path)],
        password=None,
    )


def _resolve_backup_restore_target(settings: Settings) -> tuple[str, str | None] | None:
    raw_url = (settings.backup_restore_database_url or "").strip()
    if not raw_url:
        return None

    try:
        database_url = make_url(raw_url)
    except ArgumentError as exc:
        raise BackupJobError(
            "BACKUP_RESTORE_DATABASE_URL is invalid, backup restore drill cannot run."
        ) from exc
    if database_url.get_backend_name() != "postgresql":
        raise BackupJobError(
            "Backup restore drill supports only PostgreSQL for BACKUP_RESTORE_DATABASE_URL."
        )

    _reject_restore_target_connection_overrides(database_url)
    _reject_production_restore_target(settings, database_url)

    # Keep libpq options such as sslmode so the drill connects the same way
    # the configured URL does; only the password moves into PGPASSWORD.
    query = {key: value for key, value in database_url.query.items() if key != "password"}
    rendered_url = URL.create(
        drivername="postgresql",
        username=database_url.username,
        password=None,
        host=database_url.host,
        port=database_url.port,
        database=database_url.database,
        query=query,
    ).render_as_string(hide_password=False)
    return rendered_url, database_url.password


def _reject_restore_target_connection_overrides(restore_url: URL) -> None:
    """Refuse query parameters that redirect the drill to another connection.

    libpq applies query parameters of a connection URI on top of its addressing
    components: ``postgresql://scratch.internal/db?host=db.internal`` connects
    to ``db.internal``, not to the host in the URI. A drill URL that smuggles
    such an override in would restore with ``--clean`` against the database the
    production guard just compared against, so connection addressing must only
    ever come from the URI itself.
    """
    query_keys = {key.lower() for key in restore_url.query}
    overrides = sorted(_LIBPQ_CONNECTION_TARGET_KEYS & query_keys)
    if overrides:
        raise BackupJobError(
            "BACKUP_RESTORE_DATABASE_URL must not override connection addressing via query "
            f"parameters ({', '.join(overrides)}): libpq applies them over the URI host, port "
            "and database, which defeats the production database guard."
        )


def _reject_production_restore_target(settings: Settings, restore_url: URL) -> None:
    raw_production_url = getattr(settings, "database_url", None) or ""
    if not raw_production_url:
        return
    try:
        production_url = make_url(raw_production_url)
    except ArgumentError:
        return
    if production_url.get_backend_name() != "postgresql":
        return

    # The drill drops and recreates every object in its target database, so a
    # misconfigured alias for the live database would destroy production data.
    if _postgres_endpoint(restore_url) == _postgres_endpoint(production_url):
        raise BackupJobError(
            "BACKUP_RESTORE_DATABASE_URL must not point at the production DATABASE_URL: "
            "the restore drill drops and recreates all objects in its target."
        )


def _postgres_endpoint(url: URL) -> tuple[str, int, str]:
    # Compare on the connection parameters libpq will actually use: a URI
    # query such as ``?host=...`` or ``?port=...`` overrides the addressing
    # components, so a plain host/port/database comparison would miss an
    # aliased production target (see the drill URL rejection above).
    query = {key.lower(): value for key, value in url.query.items()}
    host = query.get("host") or query.get("hostaddr") or url.host or ""
    port = _parse_query_port(query.get("port")) or url.port or 5432
    database = query.get("dbname") or url.database or ""
    return (host.lower(), port, database)


def _parse_query_port(raw: object) -> int | None:
    if raw is None:
        return None
    try:
        return int(str(raw))
    except ValueError:
        return None


def _verify_sqlite_dump_restorable(dump_path: Path, label: str) -> None:
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
    try:
        status = connection.execute("PRAGMA integrity_check").fetchone()[0]
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
    password: str | None,
) -> None:
    command = [settings.backup_pg_restore_path, *args]
    env = os.environ.copy()
    if password is not None:
        env["PGPASSWORD"] = password

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError as exc:
        raise BackupJobError(
            f"Backup restore verification command '{settings.backup_pg_restore_path}' "
            "is not available in the main bot runtime."
        ) from exc

    # A stalled pg_restore (unresponsive scratch database, lock waiter, DNS
    # blackhole) must not pin the nightly scheduler task or the admin HTTP
    # request forever, so bound the child and kill it on expiry.
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
        # survive as an orphan that keeps restoring while the drill lock is
        # already released. Kill and reap it, then propagate the cancellation.
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
    an orphan holding the archive -- or the scratch database -- open.
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


def _write_backup_manifest(
    *,
    temp_dir: Path,
    created_at: str,
    chunk_size_bytes: int,
    files: list[dict[str, object]],
) -> Path:
    manifest_path = temp_dir / f"selara-daily-backup-{created_at}.manifest.json"
    payload = {
        "created_at": created_at,
        "chunk_size_bytes": chunk_size_bytes,
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
    # Surface the concrete failure so a missing pg_restore, a misconfigured
    # BACKUP_RESTORE_DATABASE_URL and real corruption are distinguishable
    # without log access. The dump is still withheld on failure.
    detail = _bounded_failure_reason(reason)
    if detail:
        text = f"Суточный backup Selara завершился ошибкой.\nПричина: {detail}"
    else:
        text = "Суточный backup Selara завершился ошибкой. Подробности есть в логах."
    await bot.send_message(chat_id=admin_user_id, text=text)
