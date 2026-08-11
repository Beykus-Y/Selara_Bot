from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from aiogram.types import FSInputFile
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from selara.core.config import Settings
from selara.infrastructure.http.gacha_client import GachaClientError, HttpGachaClient

logger = logging.getLogger(__name__)

# The hosted Telegram Bot API accepts documents smaller than 50 MB. Keep a
# margin for differences between decimal MB and MiB and for future API changes.
BACKUP_CHUNK_SIZE_BYTES = 45 * 1024 * 1024


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
        except Exception:
            logger.exception("Daily Selara backup job failed")
            try:
                await _notify_backup_failure(bot=bot, settings=settings)
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


async def _notify_backup_failure(*, bot: Bot, settings: Settings) -> None:
    admin_user_id = settings.admin_user_id
    if admin_user_id is None:
        return
    await bot.send_message(
        chat_id=admin_user_id,
        text="Суточный backup Selara завершился ошибкой. Подробности есть в логах.",
    )
