from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import zipfile
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


class BackupJobError(RuntimeError):
    pass


@dataclass(slots=True)
class BackupFile:
    path: Path
    archive_name: str


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
        archive_path = await asyncio.to_thread(
            _build_backup_archive,
            temp_dir / _archive_filename(),
            [bot_dump, gacha_dump],
        )
        await bot.send_document(
            chat_id=admin_user_id,
            document=FSInputFile(archive_path, filename=archive_path.name),
            caption="Selara daily backup",
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


def _build_backup_archive(archive_path: Path, files: list[BackupFile]) -> Path:
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in files:
            archive.write(item.path, arcname=item.archive_name)
    return archive_path


def _resolve_gacha_backup_base_url(settings: Settings) -> str | None:
    for banner in ("", "genshin", "hsr"):
        resolved = settings.resolve_gacha_base_url(banner)
        if resolved:
            return resolved
    return None


def _archive_filename(now: datetime | None = None) -> str:
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"selara-daily-backup-{timestamp}.zip"


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
