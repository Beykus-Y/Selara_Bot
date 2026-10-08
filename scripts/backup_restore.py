#!/usr/bin/env python3
"""Generate the backup key pair, restore encrypted Selara backup sets and drill them.

Run this on the operator's own machine, never on the bot host: the private key
must not exist there. Requires the `cryptography` package and PYTHONPATH=src;
`drill` also needs the project's SQLAlchemy and alembic dependencies.

  python3 scripts/backup_restore.py keygen --private-key backup.key --public-key backup.pub
  python3 scripts/backup_restore.py restore --manifest DIR/selara-daily-backup-<ts>.manifest.json \
      --parts-dir DIR --identity backup.key --output-dir restored
  BACKUP_RESTORE_IDENTITY="$(cat backup.key)" BACKUP_DRILL_DATABASE_URL=postgresql+asyncpg://... \
      python3 scripts/backup_restore.py drill --manifest DIR/selara-daily-backup-<ts>.manifest.json \
      --parts-dir DIR

`drill` decrypts the set into a temporary directory, restores each archive into a
scratch database on the server named by BACKUP_DRILL_DATABASE_URL, checks it against
the schema heads of this checkout, and drops the scratch databases again. Point it at
a PostgreSQL server that is not production.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from selara.infrastructure.backup_encryption import (
    BackupCryptoError,
    generate_keypair,
    load_private_key,
    restore_backup_set,
)

_IDENTITY_ENV = "BACKUP_RESTORE_IDENTITY"
_DRILL_DATABASE_ENV = "BACKUP_DRILL_DATABASE_URL"
_REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def _write_new_file(path: Path, content: str, mode: int) -> None:
    # O_EXCL: never overwrite an existing key, which could silently orphan backups.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)


def _keygen(args: argparse.Namespace) -> int:
    private_b64, public_b64 = generate_keypair()
    _write_new_file(Path(args.private_key), private_b64 + "\n", mode=0o600)
    _write_new_file(Path(args.public_key), public_b64 + "\n", mode=0o644)
    print(f"Set on the bot host: BACKUP_ENCRYPTION_PUBLIC_KEY={public_b64}")
    print(f"Keep {args.private_key} offline. The bot host must never receive it.")
    return 0


def _restore(args: argparse.Namespace) -> int:
    identity = load_private_key(Path(args.identity).read_text(encoding="utf-8"))
    restored = restore_backup_set(
        manifest_path=Path(args.manifest),
        parts_dir=Path(args.parts_dir),
        identity=identity,
        output_dir=Path(args.output_dir),
    )
    for path in restored:
        print(path)
    print(
        "Restore the PostgreSQL archive into an empty database with: "
        "pg_restore --clean --if-exists --no-owner --no-privileges -d <target-url> <archive>. "
        "The gacha *.sqlite3 file is a plain SQLite database."
    )
    return 0


def _drill(args: argparse.Namespace) -> int:
    # Loaded here, not at import: keygen and restore must keep working with only `cryptography` installed.
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    from selara.infrastructure.backup_drill import (
        BackupDrillError,
        drill_backup_set,
        repository_alembic_head,
    )

    # The private key arrives through the environment only, so it is never written to a file here.
    identity_text = os.environ.get(_IDENTITY_ENV, "")
    if not identity_text.strip():
        raise ValueError(f"{_IDENTITY_ENV} is not set; export the backup private key for this drill only.")
    database_text = os.environ.get(_DRILL_DATABASE_ENV, "").strip()
    if not database_text:
        raise ValueError(f"{_DRILL_DATABASE_ENV} is not set; point it at a PostgreSQL server for scratch databases.")
    try:
        admin_url = make_url(database_text)
    except ArgumentError as exc:
        raise ValueError(f"{_DRILL_DATABASE_ENV} is not a valid database URL.") from exc
    if admin_url.get_backend_name() != "postgresql":
        raise ValueError(f"{_DRILL_DATABASE_ENV} must point at a PostgreSQL server.")

    try:
        results = asyncio.run(
            drill_backup_set(
                manifest_path=Path(args.manifest),
                parts_dir=Path(args.parts_dir),
                identity=load_private_key(identity_text),
                admin_url=admin_url,
                pg_restore_path=args.pg_restore,
                timeout_seconds=args.timeout,
                main_head=repository_alembic_head(_REPOSITORY_ROOT / "alembic"),
                gacha_head=repository_alembic_head(_REPOSITORY_ROOT / "gacha" / "alembic"),
            )
        )
    except BackupDrillError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for result in results:
        counts = ", ".join(f"{table}={count}" for table, count in result.row_counts.items())
        print(f"PASS {result.label}: schema head {result.schema_head}; {counts}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subcommands = parser.add_subparsers(dest="command", required=True)

    keygen = subcommands.add_parser("keygen", help="create a new key pair")
    keygen.add_argument("--private-key", required=True, help="new file for the private key (keep offline)")
    keygen.add_argument("--public-key", required=True, help="new file for the public key (goes into .env)")
    keygen.set_defaults(handler=_keygen)

    restore = subcommands.add_parser("restore", help="reassemble, verify and decrypt a backup set")
    restore.add_argument("--manifest", required=True, help="selara-daily-backup-<ts>.manifest.json")
    restore.add_argument("--parts-dir", required=True, help="directory holding every received part file")
    restore.add_argument("--identity", required=True, help="file with the private key from keygen")
    restore.add_argument("--output-dir", required=True, help="directory for the decrypted archives")
    restore.set_defaults(handler=_restore)

    drill = subcommands.add_parser(
        "drill",
        help=f"decrypt a backup set and restore it into scratch databases (key in {_IDENTITY_ENV})",
    )
    drill.add_argument("--manifest", required=True, help="selara-daily-backup-<ts>.manifest.json")
    drill.add_argument("--parts-dir", required=True, help="directory holding every received part file")
    drill.add_argument(
        "--pg-restore",
        default="pg_restore",
        help="pg_restore binary; it must be at least as new as the client that made the dump",
    )
    drill.add_argument("--timeout", type=float, default=1800.0, help="seconds allowed for each restore")
    drill.set_defaults(handler=_drill)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (BackupCryptoError, OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
