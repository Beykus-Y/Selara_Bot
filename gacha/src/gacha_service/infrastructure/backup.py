from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from gacha_service.config import Settings


class BackupError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(slots=True)
class BackupArtifact:
    path: Path
    filename: str
    media_type: str
    cleanup_dir: Path


async def create_database_backup(*, settings: Settings) -> BackupArtifact:
    try:
        database_url = make_url(settings.database_url)
    except ArgumentError as exc:
        raise BackupError("Gacha database URL is invalid, backup could not be created.") from exc

    cleanup_dir = Path(tempfile.mkdtemp(prefix="gacha-backup-"))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backend_name = database_url.get_backend_name()

    if backend_name == "postgresql":
        filename = f"selara-gacha-{timestamp}.dump"
        path = cleanup_dir / filename
        await _dump_postgresql_database(database_url=database_url, output_path=path, settings=settings)
        media_type = "application/octet-stream"
    elif backend_name == "sqlite":
        filename = f"selara-gacha-{timestamp}.sqlite3"
        path = cleanup_dir / filename
        await _dump_sqlite_database(database_url=database_url, output_path=path, settings=settings)
        media_type = "application/vnd.sqlite3"
    else:
        raise BackupError(f"Backup is not supported for database backend '{backend_name}'.")

    return BackupArtifact(
        path=path,
        filename=filename,
        media_type=media_type,
        cleanup_dir=cleanup_dir,
    )


async def cleanup_backup_artifact(artifact: BackupArtifact) -> None:
    await asyncio.to_thread(shutil.rmtree, artifact.cleanup_dir, True)


async def _dump_postgresql_database(*, database_url: URL, output_path: Path, settings: Settings) -> None:
    command = [
        settings.pg_dump_path,
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
        raise BackupError(
            f"Backup command '{settings.pg_dump_path}' is not available in the gacha runtime."
        ) from exc

    _stdout, stderr = await process.communicate()
    if process.returncode == 0:
        return

    detail = _last_line(stderr)
    if detail:
        raise BackupError(f"pg_dump failed while creating gacha backup: {detail}", status_code=502)
    raise BackupError("pg_dump failed while creating gacha backup.", status_code=502)


async def _dump_sqlite_database(*, database_url: URL, output_path: Path, settings: Settings) -> None:
    source_path = _resolve_sqlite_path(database_url=database_url, settings=settings)
    if not source_path.exists():
        raise BackupError(f"SQLite database file was not found: {source_path}")

    await asyncio.to_thread(_backup_sqlite_file, source_path, output_path)


def _resolve_sqlite_path(*, database_url: URL, settings: Settings) -> Path:
    raw_path = (database_url.database or "").strip()
    if not raw_path:
        raise BackupError("SQLite database path is empty, backup could not be created.")
    if raw_path == ":memory:":
        raise BackupError("In-memory SQLite database cannot be backed up through this endpoint.")

    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = settings.app_path / path
    return path.resolve()


def _backup_sqlite_file(source_path: Path, output_path: Path) -> None:
    with sqlite3.connect(source_path) as source:
        with sqlite3.connect(output_path) as target:
            source.backup(target)


def _last_line(raw: bytes) -> str:
    decoded = raw.decode("utf-8", errors="ignore").strip()
    if not decoded:
        return ""
    return decoded.splitlines()[-1]
