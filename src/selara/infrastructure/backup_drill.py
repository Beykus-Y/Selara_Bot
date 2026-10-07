"""Restore drill for backup dumps: a dump counts as a backup only once it restores.

A PostgreSQL dump is restored into a scratch database that exists only for the
drill. The restored alembic head, the critical tables and their row counts are
checked, then the scratch database is dropped. SQLite snapshots are opened
read-only and checked the same way. The drill never needs the backup private
key: the bot runs it on each plaintext dump before encryption, and the operator
runs it on a decrypted backup set through `scripts/backup_restore.py drill`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sqlite3
import tempfile
import uuid
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import monotonic

from alembic.script import ScriptDirectory
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from selara.infrastructure.backup_encryption import restore_backup_set

logger = logging.getLogger(__name__)

SCRATCH_DATABASE_PREFIX = "selara_restore_drill_"

# Every SQLite database file starts with this 16-byte header.
_SQLITE_HEADER_MAGIC = b"SQLite format 3\x00"
# The SQLite progress handler is consulted after this many VM instructions.
_SQLITE_PROGRESS_HANDLER_OPS = 1000
# A restore child that ignores SIGKILL must not hang the drill; see backup.py.
_PROCESS_REAP_TIMEOUT_SECONDS = 10.0
_TABLE_NAMES_SQL = "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"


class BackupDrillError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DrillTarget:
    """What a restored database must contain to pass the drill."""

    label: str
    required_tables: tuple[str, ...]
    # Tables that must hold at least one row: a dump of an empty database is no usable backup.
    non_empty_tables: tuple[str, ...] = ()
    # Alembic head the restored schema must carry; None accepts any single recorded head.
    expected_head: str | None = None


@dataclass(frozen=True, slots=True)
class DrillResult:
    label: str
    schema_head: str
    row_counts: dict[str, int] = field(default_factory=dict)


MAIN_DATABASE_TARGET = DrillTarget(
    label="main bot database",
    required_tables=("users", "chats", "chat_settings", "economy_accounts"),
    non_empty_tables=("users", "chats"),
)

GACHA_DATABASE_TARGET = DrillTarget(
    label="gacha database",
    required_tables=("gacha_players", "gacha_player_cards", "gacha_pull_history"),
)


def repository_alembic_head(script_location: Path) -> str:
    """Return the single alembic head of a migration tree in this repository checkout."""
    head = ScriptDirectory(str(script_location)).get_current_head()
    if head is None:
        raise BackupDrillError(f"No alembic revisions found in {script_location}.")
    return head


def find_restore_problems(
    *,
    target: DrillTarget,
    schema_head: str,
    tables: Iterable[str],
    row_counts: dict[str, int],
) -> list[str]:
    """List every failed check of a restored database; an empty list means the restore passed."""
    present = set(tables)
    problems: list[str] = []
    if target.expected_head is not None and schema_head != target.expected_head:
        problems.append(f"schema head {schema_head} does not match expected {target.expected_head}")
    for table in target.required_tables:
        if table not in present:
            problems.append(f"table {table} is missing")
        elif table in target.non_empty_tables and row_counts.get(table, 0) == 0:
            problems.append(f"table {table} is empty")
    return problems


def _raise_for_problems(target: DrillTarget, problems: list[str]) -> None:
    if problems:
        raise BackupDrillError(f"Backup restore drill failed for {target.label}: {'; '.join(problems)}.")


def _create_engine(url: URL, *, autocommit: bool = False) -> AsyncEngine:
    # NullPool closes each connection with its step, so DROP DATABASE never waits on a pooled session.
    options = {"isolation_level": "AUTOCOMMIT"} if autocommit else {}
    return create_async_engine(url, poolclass=NullPool, **options)


def _single_schema_head(versions: list[object], label: str) -> str:
    if len(versions) != 1:
        raise BackupDrillError(
            f"Backup restore drill failed for {label}: alembic_version holds {len(versions)} rows, expected one."
        )
    return str(versions[0])


async def _read_alembic_head(connection: AsyncConnection, label: str) -> str:
    versions = (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
    return _single_schema_head(list(versions), label)


async def read_live_schema_head(database_url: URL, *, label: str) -> str:
    """Read the alembic head of a running database; it is the reference the restored copy must match."""
    engine = _create_engine(database_url)
    try:
        async with engine.connect() as connection:
            return await _read_alembic_head(connection, label)
    except SQLAlchemyError as exc:
        raise BackupDrillError(f"Backup restore drill could not read the schema head of the {label}: {exc}") from exc
    finally:
        await engine.dispose()


async def check_restored_database(database_url: URL, target: DrillTarget) -> DrillResult:
    """Check a restored PostgreSQL database: schema head, required tables and their row counts."""
    engine = _create_engine(database_url)
    try:
        async with engine.connect() as connection:
            schema_head = await _read_alembic_head(connection, target.label)
            tables = set((await connection.execute(text(_TABLE_NAMES_SQL))).scalars().all())
            row_counts: dict[str, int] = {}
            for table in target.required_tables:
                if table in tables:
                    count = (await connection.execute(text(f'SELECT count(*) FROM "{table}"'))).scalar_one()
                    row_counts[table] = int(count)
    except SQLAlchemyError as exc:
        raise BackupDrillError(f"Backup restore drill could not query the restored {target.label}: {exc}") from exc
    finally:
        await engine.dispose()

    problems = find_restore_problems(target=target, schema_head=schema_head, tables=tables, row_counts=row_counts)
    _raise_for_problems(target, problems)
    return DrillResult(label=target.label, schema_head=schema_head, row_counts=row_counts)


async def _create_scratch_database(engine: AsyncEngine, name: str) -> None:
    # template0 keeps anything someone added to template1 out of the restored copy.
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    except SQLAlchemyError as exc:
        raise BackupDrillError(f"Backup restore drill could not create a scratch database: {exc}") from exc


async def _drop_scratch_database(engine: AsyncEngine, name: str) -> None:
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    except SQLAlchemyError:
        # A leftover copy of the data must not disappear silently; the log names it so it can be dropped by hand.
        logger.exception("Could not drop backup restore drill database", extra={"database": name})


def _libpq_url(admin_url: URL, database: str) -> str:
    # pg_restore receives this URL in argv, so the password is left out and travels in PGPASSWORD instead.
    return URL.create(
        "postgresql",
        username=admin_url.username,
        host=admin_url.host,
        port=admin_url.port,
        database=database,
        query=admin_url.query,
    ).render_as_string(hide_password=True)


async def _run_pg_restore(
    *,
    admin_url: URL,
    scratch_name: str,
    dump_path: Path,
    label: str,
    pg_restore_path: str,
    timeout_seconds: float,
) -> None:
    command = [
        pg_restore_path,
        "--no-owner",
        "--no-privileges",
        "--exit-on-error",
        f"--dbname={_libpq_url(admin_url, scratch_name)}",
        str(dump_path),
    ]
    env = os.environ.copy()
    if admin_url.password is not None:
        env["PGPASSWORD"] = admin_url.password
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError as exc:
        raise BackupDrillError(f"Backup restore drill command '{pg_restore_path}' is not available for {label}.") from exc

    # A stalled restore must not pin the backup job, so it is killed on expiry.
    try:
        async with asyncio.timeout(timeout_seconds):
            _stdout, stderr = await process.communicate()
    except TimeoutError as exc:
        await _kill_process(process)
        raise BackupDrillError(f"Backup restore drill timed out after {timeout_seconds:g}s for {label}.") from exc
    except asyncio.CancelledError:
        # Shutdown must not leave a restore writing into the scratch database.
        await _kill_process(process)
        raise

    if process.returncode == 0:
        return
    detail = _last_line(stderr)
    if detail:
        raise BackupDrillError(f"Backup restore drill could not restore {label}: {detail}")
    raise BackupDrillError(f"Backup restore drill could not restore {label}.")


async def _kill_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    try:
        await asyncio.wait_for(process.wait(), _PROCESS_REAP_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning("Backup restore drill process did not exit after being killed")


def _last_line(raw: bytes) -> str:
    decoded = raw.decode("utf-8", errors="ignore").strip()
    if not decoded:
        return ""
    return decoded.splitlines()[-1]


async def drill_postgres_dump(
    *,
    admin_url: URL,
    dump_path: Path,
    target: DrillTarget,
    pg_restore_path: str,
    timeout_seconds: float,
) -> DrillResult:
    """Restore a custom-format dump into a new scratch database, check it, and drop the database again.

    Only the scratch database is written. `admin_url` must be allowed to create databases on its server.
    """
    scratch_name = f"{SCRATCH_DATABASE_PREFIX}{uuid.uuid4().hex}"
    admin_engine = _create_engine(admin_url, autocommit=True)
    created = False
    try:
        await _create_scratch_database(admin_engine, scratch_name)
        created = True
        await _run_pg_restore(
            admin_url=admin_url,
            scratch_name=scratch_name,
            dump_path=dump_path,
            label=target.label,
            pg_restore_path=pg_restore_path,
            timeout_seconds=timeout_seconds,
        )
        result = await check_restored_database(admin_url.set(database=scratch_name), target)
    finally:
        if created:
            await _drop_scratch_database(admin_engine, scratch_name)
        await admin_engine.dispose()
    logger.info(
        "Backup restore drill passed",
        extra={"label": target.label, "schema_head": result.schema_head, "row_counts": result.row_counts},
    )
    return result


def drill_sqlite_snapshot(*, snapshot_path: Path, target: DrillTarget, timeout_seconds: float) -> DrillResult:
    """Open a SQLite snapshot read-only and check it like the PostgreSQL drill: integrity, head, tables, rows."""
    label = target.label
    if snapshot_path.stat().st_size == 0:
        raise BackupDrillError(f"Backup restore drill failed for {label}: snapshot file is empty.")
    with snapshot_path.open("rb") as handle:
        header = handle.read(len(_SQLITE_HEADER_MAGIC))
    if header != _SQLITE_HEADER_MAGIC:
        raise BackupDrillError(f"Backup restore drill failed for {label}: file is not a SQLite database.")
    try:
        connection = sqlite3.connect(f"{snapshot_path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise BackupDrillError(f"Backup restore drill could not open {label}: {exc}") from exc

    # integrity_check on a large snapshot can run for a long time; the progress handler bounds it by the same timeout.
    deadline = monotonic() + max(timeout_seconds, 0.0)

    def _enforce_deadline() -> int:
        return int(monotonic() > deadline)

    connection.set_progress_handler(_enforce_deadline, _SQLITE_PROGRESS_HANDLER_OPS)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "alembic_version" not in tables:
            raise BackupDrillError(f"Backup restore drill failed for {label}: table alembic_version is missing.")
        versions = [row[0] for row in connection.execute("SELECT version_num FROM alembic_version")]
        row_counts = {
            table: int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
            for table in target.required_tables
            if table in tables
        }
    except sqlite3.Error as exc:
        if _enforce_deadline():
            raise BackupDrillError(f"Backup restore drill timed out after {timeout_seconds:g}s for {label}.") from exc
        raise BackupDrillError(f"Backup restore drill failed for {label}: {exc}") from exc
    finally:
        connection.close()

    schema_head = _single_schema_head(versions, label)
    problems = [] if integrity == "ok" else [f"integrity_check reported {integrity!r}"]
    problems += find_restore_problems(target=target, schema_head=schema_head, tables=tables, row_counts=row_counts)
    _raise_for_problems(target, problems)
    logger.info(
        "Backup restore drill passed",
        extra={"label": label, "schema_head": schema_head, "row_counts": row_counts},
    )
    return DrillResult(label=label, schema_head=schema_head, row_counts=row_counts)


def target_for_archive(archive_name: str, *, main_head: str, gacha_head: str) -> DrillTarget:
    """Map an archive of a backup set to the database it holds; the prefixes are set by `send_daily_backup`."""
    if archive_name.startswith("bot_"):
        return replace(MAIN_DATABASE_TARGET, expected_head=main_head)
    if archive_name.startswith("gacha_"):
        return replace(GACHA_DATABASE_TARGET, expected_head=gacha_head)
    raise BackupDrillError(f"Backup set holds an unknown archive: {archive_name}.")


async def drill_backup_set(
    *,
    manifest_path: Path,
    parts_dir: Path,
    identity: X25519PrivateKey,
    admin_url: URL,
    pg_restore_path: str,
    timeout_seconds: float,
    main_head: str,
    gacha_head: str,
) -> list[DrillResult]:
    """Decrypt a backup set and drill every archive in it; the main bot and gacha archives must both be present.

    The decrypted archives are plaintext, so they go to a fresh directory that is removed when the drill ends.
    """
    work_dir = Path(tempfile.mkdtemp(prefix="selara-restore-drill-"))
    try:
        archives = await asyncio.to_thread(
            restore_backup_set,
            manifest_path=manifest_path,
            parts_dir=parts_dir,
            identity=identity,
            output_dir=work_dir,
        )
        prefixes = {archive.name.split("_", 1)[0] for archive in archives}
        if not {"bot", "gacha"} <= prefixes:
            raise BackupDrillError("Backup set must hold both the main bot archive and the gacha archive.")
        results: list[DrillResult] = []
        for archive in archives:
            target = target_for_archive(archive.name, main_head=main_head, gacha_head=gacha_head)
            if archive.suffix.lower() == ".sqlite3":
                result = await asyncio.to_thread(
                    drill_sqlite_snapshot,
                    snapshot_path=archive,
                    target=target,
                    timeout_seconds=timeout_seconds,
                )
            else:
                result = await drill_postgres_dump(
                    admin_url=admin_url,
                    dump_path=archive,
                    target=target,
                    pg_restore_path=pg_restore_path,
                    timeout_seconds=timeout_seconds,
                )
            results.append(result)
        return results
    finally:
        await asyncio.to_thread(shutil.rmtree, work_dir, True)
