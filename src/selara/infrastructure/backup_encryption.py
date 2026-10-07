"""Application-level encryption for backup archives.

Every archive is sealed to the operator's X25519 public key with a fresh
ephemeral key. The bot host therefore needs only the public key; the private
key stays with the operator and is used only to restore a backup set.

Stream format, version 1:

    magic (21 bytes) | ephemeral public key (32) | nonce prefix (8) | frame*
    frame = final flag (1 byte, 0 or 1) | ciphertext length (4, big-endian) | ciphertext

Frames are AES-256-GCM sealed with nonce = nonce prefix || frame counter
(4 bytes, big-endian) and the final flag as associated data. Reordering,
truncation, and trailing data therefore fail authentication. The AES key is
HKDF-SHA256 over the X25519 shared secret, bound to both public keys.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
from pathlib import Path
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ENCRYPTION_FORMAT = "selara-x25519-aes256gcm-v1"
ENCRYPTED_SUFFIX = ".enc"

_MAGIC = b"SELARA-BACKUP-ENC-V1\n"
_KEY_BYTES = 32
_NONCE_PREFIX_BYTES = 8
_TAG_BYTES = 16
_HKDF_INFO = b"selara-backup-encryption-v1"
_FRAME_HEADER = struct.Struct(">BI")
_COUNTER_LIMIT = 2**32
_FRAME_PLAINTEXT_BYTES = 1024 * 1024


class BackupCryptoError(ValueError):
    pass


def parse_public_key(encoded: str) -> X25519PublicKey:
    """Parse a base64-encoded raw X25519 public key, as printed by `keygen`."""
    try:
        raw = base64.b64decode(encoded.strip(), validate=True)
    except ValueError as exc:
        raise BackupCryptoError("Backup encryption public key is not valid base64.") from exc
    if len(raw) != _KEY_BYTES:
        raise BackupCryptoError("Backup encryption public key must be a 32-byte X25519 key.")
    return X25519PublicKey.from_public_bytes(raw)


def public_key_fingerprint(public_key: X25519PublicKey) -> str:
    """Return a non-secret identifier of the recipient key, safe to publish in the manifest."""
    return hashlib.sha256(_raw_public(public_key)).hexdigest()


def generate_keypair() -> tuple[str, str]:
    """Return (private, public) base64 encodings of a fresh X25519 key pair."""
    private_key = X25519PrivateKey.generate()
    private_raw = private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_raw = _raw_public(private_key.public_key())
    return base64.b64encode(private_raw).decode("ascii"), base64.b64encode(public_raw).decode("ascii")


def load_private_key(encoded: str) -> X25519PrivateKey:
    try:
        raw = base64.b64decode(encoded.strip(), validate=True)
    except ValueError as exc:
        raise BackupCryptoError("Backup identity is not valid base64.") from exc
    if len(raw) != _KEY_BYTES:
        raise BackupCryptoError("Backup identity must be a 32-byte X25519 private key.")
    return X25519PrivateKey.from_private_bytes(raw)


def encrypt_file(*, source: Path, destination: Path, recipient: X25519PublicKey) -> None:
    """Stream-encrypt `source` into a new `destination` file; never overwrites an existing file."""
    ephemeral = X25519PrivateKey.generate()
    ephemeral_public = _raw_public(ephemeral.public_key())
    aead = AESGCM(_derive_key(ephemeral.exchange(recipient), ephemeral_public, _raw_public(recipient)))
    nonce_prefix = os.urandom(_NONCE_PREFIX_BYTES)

    with source.open("rb") as reader, destination.open("xb") as writer:
        writer.write(_MAGIC + ephemeral_public + nonce_prefix)
        current = reader.read(_FRAME_PLAINTEXT_BYTES)
        counter = 0
        while True:
            # Read one chunk ahead so the final frame is known before it is sealed.
            upcoming = reader.read(_FRAME_PLAINTEXT_BYTES)
            final = not upcoming
            if counter >= _COUNTER_LIMIT:
                raise BackupCryptoError("Backup is too large to encrypt with one key stream.")
            flag = 1 if final else 0
            sealed = aead.encrypt(_nonce(nonce_prefix, counter), current, bytes([flag]))
            writer.write(_FRAME_HEADER.pack(flag, len(sealed)))
            writer.write(sealed)
            if final:
                break
            current = upcoming
            counter += 1


def decrypt_file(*, source: Path, destination: Path, private_key: X25519PrivateKey) -> None:
    """Decrypt `source` into a new `destination`. A failed decryption leaves no partial output."""
    with source.open("rb") as reader, destination.open("xb") as writer:
        try:
            _decrypt_stream(reader, writer, private_key)
        except BaseException:
            writer.close()
            destination.unlink(missing_ok=True)
            raise


def _decrypt_stream(reader: BinaryIO, writer: BinaryIO, private_key: X25519PrivateKey) -> None:
    header = _read_exact(reader, len(_MAGIC) + _KEY_BYTES + _NONCE_PREFIX_BYTES, "header")
    if not hmac.compare_digest(header[: len(_MAGIC)], _MAGIC):
        raise BackupCryptoError("File is not an encrypted Selara backup.")
    ephemeral_public = header[len(_MAGIC) : len(_MAGIC) + _KEY_BYTES]
    nonce_prefix = header[len(_MAGIC) + _KEY_BYTES :]

    recipient_public = _raw_public(private_key.public_key())
    try:
        shared = private_key.exchange(X25519PublicKey.from_public_bytes(ephemeral_public))
    except ValueError as exc:
        raise BackupCryptoError("Backup carries an invalid ephemeral key.") from exc
    aead = AESGCM(_derive_key(shared, ephemeral_public, recipient_public))

    counter = 0
    while True:
        flag, length = _FRAME_HEADER.unpack(_read_exact(reader, _FRAME_HEADER.size, "frame header"))
        if flag not in (0, 1) or length > _FRAME_PLAINTEXT_BYTES + _TAG_BYTES:
            raise BackupCryptoError("Backup frame header is malformed.")
        sealed = _read_exact(reader, length, "frame")
        try:
            plaintext = aead.decrypt(_nonce(nonce_prefix, counter), sealed, bytes([flag]))
        except InvalidTag as exc:
            raise BackupCryptoError(
                "Backup authentication failed: wrong identity or corrupted archive."
            ) from exc
        writer.write(plaintext)
        if flag:
            if reader.read(1):
                raise BackupCryptoError("Backup has data after its final frame.")
            return
        counter += 1


def _read_exact(reader: BinaryIO, size: int, what: str) -> bytes:
    data = reader.read(size)
    if len(data) != size:
        raise BackupCryptoError(f"Backup is truncated: {what} is incomplete.")
    return data


def _derive_key(shared_secret: bytes, ephemeral_public: bytes, recipient_public: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_BYTES,
        salt=None,
        info=_HKDF_INFO + ephemeral_public + recipient_public,
    ).derive(shared_secret)


def _nonce(nonce_prefix: bytes, counter: int) -> bytes:
    return nonce_prefix + counter.to_bytes(4, "big")


def restore_backup_set(
    *,
    manifest_path: Path,
    parts_dir: Path,
    identity: X25519PrivateKey,
    output_dir: Path,
) -> list[Path]:
    """Reassemble and decrypt every archive listed in a backup manifest.

    Each reassembled encrypted file is checked against the manifest's size and
    SHA-256 before decryption. Returns the decrypted archive paths.
    """
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    encryption = manifest.get("encryption")
    if not isinstance(encryption, dict) or encryption.get("format") != ENCRYPTION_FORMAT:
        raise BackupCryptoError("Manifest does not describe an encrypted backup set.")
    if not hmac.compare_digest(
        str(encryption.get("recipient_sha256", "")),
        public_key_fingerprint(identity.public_key()),
    ):
        raise BackupCryptoError("Backup set was encrypted for a different key.")

    output_dir.mkdir(parents=True, exist_ok=True)
    restored: list[Path] = []
    for entry in manifest["files"]:
        encrypted_name = _safe_name(entry["filename"])
        if not encrypted_name.endswith(ENCRYPTED_SUFFIX):
            raise BackupCryptoError(f"Unexpected archive name in manifest: {encrypted_name}")
        encrypted_path = output_dir / encrypted_name
        _reassemble_parts(entry=entry, parts_dir=parts_dir, destination=encrypted_path)
        plain_path = output_dir / encrypted_name[: -len(ENCRYPTED_SUFFIX)]
        decrypt_file(source=encrypted_path, destination=plain_path, private_key=identity)
        encrypted_path.unlink()
        restored.append(plain_path)
    return restored


def _reassemble_parts(*, entry: dict, parts_dir: Path, destination: Path) -> None:
    digest = hashlib.sha256()
    total = 0
    with destination.open("xb") as writer:
        for part in entry["parts"]:
            content = (parts_dir / _safe_name(part["filename"])).read_bytes()
            if len(content) != part["size_bytes"]:
                destination.unlink(missing_ok=True)
                raise BackupCryptoError(f"Backup part has an unexpected size: {part['filename']}")
            digest.update(content)
            total += len(content)
            writer.write(content)
    if total != entry["size_bytes"] or not hmac.compare_digest(digest.hexdigest(), entry["sha256"]):
        destination.unlink(missing_ok=True)
        raise BackupCryptoError(f"Backup checksum mismatch for {entry['filename']}.")


def _safe_name(name: str) -> str:
    if not isinstance(name, str) or not name or Path(name).name != name or name in (".", ".."):
        raise BackupCryptoError(f"Unsafe file name in manifest: {name!r}")
    return name


def _raw_public(public_key: X25519PublicKey) -> bytes:
    return public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
