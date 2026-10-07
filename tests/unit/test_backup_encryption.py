from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from selara.infrastructure import backup, backup_encryption
from selara.infrastructure.backup import BackupFile
from selara.infrastructure.backup_encryption import BackupCryptoError

# Tiny frames make even short plaintexts span several authenticated frames.
_SMALL_FRAME_BYTES = 7
# Header (magic, ephemeral key, nonce prefix) plus one frame of _SMALL_FRAME_BYTES.
_HEADER_BYTES = len(b"SELARA-BACKUP-ENC-V1\n") + 32 + 8
_FRAME_BYTES = 5 + _SMALL_FRAME_BYTES + 16


@pytest.fixture
def small_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backup_encryption, "_FRAME_PLAINTEXT_BYTES", _SMALL_FRAME_BYTES)


def _key_pair() -> tuple[object, object]:
    private_b64, public_b64 = backup_encryption.generate_keypair()
    return backup_encryption.load_private_key(private_b64), backup_encryption.parse_public_key(public_b64)


def _encrypt(tmp_path: Path, plaintext: bytes, recipient: object) -> Path:
    source = tmp_path / "bot_pg_dump.dump"
    source.write_bytes(plaintext)
    encrypted = tmp_path / "bot_pg_dump.dump.enc"
    backup_encryption.encrypt_file(source=source, destination=encrypted, recipient=recipient)
    return encrypted


def test_encrypt_split_reassemble_decrypt_keeps_checksum(
    small_frames: None,
    tmp_path: Path,
) -> None:
    plaintext = bytes(range(256)) * 9
    identity, recipient = _key_pair()
    encrypted = _encrypt(tmp_path, plaintext, recipient)

    # The ciphertext must not expose any plaintext run.
    assert plaintext[:64] not in encrypted.read_bytes()

    parts, entry = backup._split_backup_file(
        BackupFile(path=encrypted, archive_name=encrypted.name),
        tmp_path,
        100,
    )
    assert len(parts) > 1
    manifest_path = backup._write_backup_manifest(
        temp_dir=tmp_path,
        created_at="20260315T000000Z",
        chunk_size_bytes=100,
        files=[entry],
        encryption={
            "format": backup_encryption.ENCRYPTION_FORMAT,
            "recipient_sha256": backup_encryption.public_key_fingerprint(recipient),
        },
    )

    restored = backup_encryption.restore_backup_set(
        manifest_path=manifest_path,
        parts_dir=tmp_path,
        identity=identity,
        output_dir=tmp_path / "restored",
    )

    assert [path.name for path in restored] == ["bot_pg_dump.dump"]
    assert hashlib.sha256(restored[0].read_bytes()).hexdigest() == hashlib.sha256(plaintext).hexdigest()


@pytest.mark.parametrize("size", [0, 1, 6, 7, 8, 14, 15, 100])
def test_round_trip_handles_frame_boundary_sizes(
    small_frames: None,
    tmp_path: Path,
    size: int,
) -> None:
    plaintext = bytes((index * 37) % 256 for index in range(size))
    identity, recipient = _key_pair()
    encrypted = _encrypt(tmp_path, plaintext, recipient)
    decrypted = tmp_path / "decrypted.dump"

    backup_encryption.decrypt_file(source=encrypted, destination=decrypted, private_key=identity)

    assert decrypted.read_bytes() == plaintext


def test_decrypt_with_another_identity_fails_and_leaves_no_output(
    small_frames: None,
    tmp_path: Path,
) -> None:
    _identity, recipient = _key_pair()
    other_identity, _other_recipient = _key_pair()
    encrypted = _encrypt(tmp_path, b"secret-dump-bytes", recipient)
    decrypted = tmp_path / "decrypted.dump"

    with pytest.raises(BackupCryptoError, match="authentication failed"):
        backup_encryption.decrypt_file(source=encrypted, destination=decrypted, private_key=other_identity)

    assert not decrypted.exists()


def test_tampered_frame_is_rejected(small_frames: None, tmp_path: Path) -> None:
    identity, recipient = _key_pair()
    encrypted = _encrypt(tmp_path, b"0123456789abcdef", recipient)
    data = bytearray(encrypted.read_bytes())
    data[_HEADER_BYTES + 5] ^= 0x01
    encrypted.write_bytes(bytes(data))

    with pytest.raises(BackupCryptoError, match="authentication failed"):
        backup_encryption.decrypt_file(
            source=encrypted,
            destination=tmp_path / "decrypted.dump",
            private_key=identity,
        )


def test_reordered_frames_are_rejected(small_frames: None, tmp_path: Path) -> None:
    identity, recipient = _key_pair()
    encrypted = _encrypt(tmp_path, b"0123456789abcdefghijk", recipient)
    data = encrypted.read_bytes()
    first = data[_HEADER_BYTES : _HEADER_BYTES + _FRAME_BYTES]
    second = data[_HEADER_BYTES + _FRAME_BYTES : _HEADER_BYTES + 2 * _FRAME_BYTES]
    rest = data[_HEADER_BYTES + 2 * _FRAME_BYTES :]
    encrypted.write_bytes(data[:_HEADER_BYTES] + second + first + rest)

    with pytest.raises(BackupCryptoError, match="authentication failed"):
        backup_encryption.decrypt_file(
            source=encrypted,
            destination=tmp_path / "decrypted.dump",
            private_key=identity,
        )


def test_truncation_at_frame_boundary_is_rejected(small_frames: None, tmp_path: Path) -> None:
    identity, recipient = _key_pair()
    encrypted = _encrypt(tmp_path, b"0123456789abcdefghijk", recipient)
    encrypted.write_bytes(encrypted.read_bytes()[: _HEADER_BYTES + 2 * _FRAME_BYTES])

    with pytest.raises(BackupCryptoError, match="truncated"):
        backup_encryption.decrypt_file(
            source=encrypted,
            destination=tmp_path / "decrypted.dump",
            private_key=identity,
        )


def test_trailing_data_after_final_frame_is_rejected(small_frames: None, tmp_path: Path) -> None:
    identity, recipient = _key_pair()
    encrypted = _encrypt(tmp_path, b"short", recipient)
    encrypted.write_bytes(encrypted.read_bytes() + b"x")

    with pytest.raises(BackupCryptoError, match="after its final frame"):
        backup_encryption.decrypt_file(
            source=encrypted,
            destination=tmp_path / "decrypted.dump",
            private_key=identity,
        )


def test_restore_rejects_part_that_fails_manifest_checksum(
    small_frames: None,
    tmp_path: Path,
) -> None:
    identity, recipient = _key_pair()
    encrypted = _encrypt(tmp_path, b"0123456789abcdef", recipient)
    parts, entry = backup._split_backup_file(
        BackupFile(path=encrypted, archive_name=encrypted.name),
        tmp_path,
        40,
    )
    # Same size, different bytes: only the checksum can catch this.
    first_part = parts[0].path
    corrupted = bytearray(first_part.read_bytes())
    corrupted[0] ^= 0xFF
    first_part.write_bytes(bytes(corrupted))
    manifest_path = backup._write_backup_manifest(
        temp_dir=tmp_path,
        created_at="20260315T000000Z",
        chunk_size_bytes=40,
        files=[entry],
        encryption={
            "format": backup_encryption.ENCRYPTION_FORMAT,
            "recipient_sha256": backup_encryption.public_key_fingerprint(recipient),
        },
    )

    with pytest.raises(BackupCryptoError, match="checksum mismatch"):
        backup_encryption.restore_backup_set(
            manifest_path=manifest_path,
            parts_dir=tmp_path,
            identity=identity,
            output_dir=tmp_path / "restored",
        )


def test_restore_rejects_backup_set_for_another_key(
    small_frames: None,
    tmp_path: Path,
) -> None:
    _identity, recipient = _key_pair()
    other_identity, _other_recipient = _key_pair()
    encrypted = _encrypt(tmp_path, b"0123456789abcdef", recipient)
    _parts, entry = backup._split_backup_file(
        BackupFile(path=encrypted, archive_name=encrypted.name),
        tmp_path,
        40,
    )
    manifest_path = backup._write_backup_manifest(
        temp_dir=tmp_path,
        created_at="20260315T000000Z",
        chunk_size_bytes=40,
        files=[entry],
        encryption={
            "format": backup_encryption.ENCRYPTION_FORMAT,
            "recipient_sha256": backup_encryption.public_key_fingerprint(recipient),
        },
    )

    with pytest.raises(BackupCryptoError, match="different key"):
        backup_encryption.restore_backup_set(
            manifest_path=manifest_path,
            parts_dir=tmp_path,
            identity=other_identity,
            output_dir=tmp_path / "restored",
        )


@pytest.mark.parametrize("encoded", ["not base64!!", "c2hvcnQ="])
def test_parse_public_key_rejects_malformed_values(encoded: str) -> None:
    with pytest.raises(BackupCryptoError):
        backup_encryption.parse_public_key(encoded)


def test_generated_public_key_parses_back_to_same_fingerprint() -> None:
    private_b64, public_b64 = backup_encryption.generate_keypair()
    identity = backup_encryption.load_private_key(private_b64)

    recipient = backup_encryption.parse_public_key(public_b64 + "\n")

    assert backup_encryption.public_key_fingerprint(recipient) == backup_encryption.public_key_fingerprint(
        identity.public_key()
    )


def test_failed_restore_removes_archives_it_already_wrote(
    small_frames: None,
    tmp_path: Path,
) -> None:
    identity, recipient = _key_pair()
    entries = []
    for name in ("bot_pg_dump.dump", "gacha_pg_dump.dump"):
        source = tmp_path / name
        source.write_bytes(b"0123456789abcdef")
        encrypted = tmp_path / f"{name}.enc"
        backup_encryption.encrypt_file(source=source, destination=encrypted, recipient=recipient)
        parts, entry = backup._split_backup_file(
            BackupFile(path=encrypted, archive_name=encrypted.name),
            tmp_path,
            40,
        )
        entries.append(entry)
        if name == "gacha_pg_dump.dump":
            # The first archive restores fine; the second one has a corrupt part.
            corrupted = bytearray(parts[0].path.read_bytes())
            corrupted[0] ^= 0xFF
            parts[0].path.write_bytes(bytes(corrupted))
    manifest_path = backup._write_backup_manifest(
        temp_dir=tmp_path,
        created_at="20260315T000000Z",
        chunk_size_bytes=40,
        files=entries,
        encryption={
            "format": backup_encryption.ENCRYPTION_FORMAT,
            "recipient_sha256": backup_encryption.public_key_fingerprint(recipient),
        },
    )
    output_dir = tmp_path / "restored"

    with pytest.raises(BackupCryptoError, match="checksum mismatch"):
        backup_encryption.restore_backup_set(
            manifest_path=manifest_path,
            parts_dir=tmp_path,
            identity=identity,
            output_dir=output_dir,
        )

    assert list(output_dir.iterdir()) == []
