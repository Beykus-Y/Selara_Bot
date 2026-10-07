"""CI smoke check inside the actual hardened image (not run on the host)."""

import asyncio
import errno
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from gacha_service.config import Settings
from gacha_service.infrastructure.backup import create_database_backup, cleanup_backup_artifact


async def main():
    assert os.getuid() == 10001
    status = Path("/proc/self/status").read_text()
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    assert fields["NoNewPrivs"].strip() == "1"
    assert int(fields["CapEff"].strip(), 16) == 0
    try:
        Path("/app/forbidden-runtime-write").write_text("must fail")
    except OSError as error:
        assert error.errno in (errno.EROFS, errno.EACCES)
    else:
        raise AssertionError("application filesystem is writable")

    # Production PostgreSQL backup path invokes pg_dump under the same UID.
    artifact = await create_database_backup(settings=Settings())
    try:
        assert artifact.path.read_bytes().startswith(b"PGDMP")
        subprocess.run(["pg_restore", "--list", str(artifact.path)], check=True, stdout=subprocess.DEVNULL)
    finally:
        await cleanup_backup_artifact(artifact)
    assert not artifact.cleanup_dir.exists()

    # Exercise the real sqlite backup and writable tempfile paths as well.
    with tempfile.TemporaryDirectory(dir="/data") as folder:
        source = Path(folder) / "source.sqlite3"
        with sqlite3.connect(source) as database:
            database.execute("CREATE TABLE marker (value TEXT)")
            database.execute("INSERT INTO marker VALUES ('preserved')")
        artifact = await create_database_backup(settings=Settings(database_url=f"sqlite:///{source}"))
        try:
            with sqlite3.connect(artifact.path) as database:
                assert database.execute("PRAGMA integrity_check").fetchone() == ("ok",)
                assert database.execute("SELECT value FROM marker").fetchone() == ("preserved",)
        finally:
            await cleanup_backup_artifact(artifact)
    print("Non-root gacha migrations, PostgreSQL/SQLite backups and tempfiles: OK")


if __name__ == "__main__":
    asyncio.run(main())
