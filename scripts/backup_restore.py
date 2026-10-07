#!/usr/bin/env python3
"""Generate the backup key pair and restore encrypted Selara backup sets.

Run this on the operator's own machine, never on the bot host: the private key
must not exist there. Requires the `cryptography` package and PYTHONPATH=src.

  python3 scripts/backup_restore.py keygen --private-key backup.key --public-key backup.pub
  python3 scripts/backup_restore.py restore --manifest DIR/selara-daily-backup-<ts>.manifest.json \
      --parts-dir DIR --identity backup.key --output-dir restored
"""

import argparse
import os
import sys
from pathlib import Path

from selara.infrastructure.backup_encryption import (
    BackupCryptoError,
    generate_keypair,
    load_private_key,
    restore_backup_set,
)


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

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (BackupCryptoError, OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
