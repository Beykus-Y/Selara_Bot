from __future__ import annotations

import base64
import hmac
import json
import secrets
from hashlib import sha256
from time import time
from urllib.parse import parse_qsl


def normalize_login_code(raw_value: str | None) -> str:
    if raw_value is None:
        return ""
    return "".join(ch for ch in str(raw_value) if ch.isdigit())


def generate_login_code(*, length: int = 6) -> str:
    normalized_length = max(4, int(length))
    return "".join(secrets.choice("0123456789") for _ in range(normalized_length))


def digest_login_code(*, secret: str, code: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        f"login:{normalize_login_code(code)}".encode("utf-8"),
        sha256,
    ).hexdigest()


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def digest_session_token(*, secret: str, token: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        f"session:{token}".encode("utf-8"),
        sha256,
    ).hexdigest()


def normalize_base_url(raw_value: str) -> str:
    value = (raw_value or "").strip()
    if not value:
        return "http://127.0.0.1:8080"
    return value.rstrip("/")


def validate_telegram_webapp_init_data(
    *,
    init_data: str,
    bot_token: str,
    max_age_seconds: int = 3600,
    now_timestamp: int | None = None,
) -> dict[str, object] | None:
    normalized_init_data = (init_data or "").strip()
    normalized_bot_token = (bot_token or "").strip()
    if not normalized_init_data or not normalized_bot_token:
        return None

    raw_values = dict(parse_qsl(normalized_init_data, keep_blank_values=True))
    expected_hash = raw_values.pop("hash", "")
    signature_b64 = raw_values.pop("signature", None)
    if not expected_hash and not signature_b64:
        return None

    bot_id = normalized_bot_token.split(":")[0]
    sorted_fields = "\n".join(
        f"{key}={value}"
        for key, value in sorted(raw_values.items(), key=lambda item: item[0])
    )

    if signature_b64:
        # New Ed25519 format — verify using Telegram's production public key
        _TELEGRAM_PUBLIC_KEY = bytes.fromhex(
            "e7bf03a2fa4602af4580703d88dda5bb59f32ed8b02a56c187fe7d34caed242d"
        )
        data_check_string = f"{bot_id}:WebAppData\n{sorted_fields}"
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            signature_bytes = base64.urlsafe_b64decode(signature_b64 + "==")
            pub_key = Ed25519PublicKey.from_public_bytes(_TELEGRAM_PUBLIC_KEY)
            pub_key.verify(signature_bytes, data_check_string.encode("utf-8"))
        except Exception:
            return None
    else:
        # Legacy HMAC-SHA256 format
        secret_key = hmac.new(b"WebAppData", normalized_bot_token.encode("utf-8"), sha256).digest()
        actual_hash = hmac.new(secret_key, sorted_fields.encode("utf-8"), sha256).hexdigest()
        if not hmac.compare_digest(actual_hash, expected_hash):
            return None

    auth_date_raw = raw_values.get("auth_date", "")
    if not auth_date_raw.isdigit():
        return None
    auth_date = int(auth_date_raw)
    current_timestamp = int(time()) if now_timestamp is None else int(now_timestamp)
    if auth_date > current_timestamp + 60:
        return None
    if current_timestamp - auth_date > max(60, int(max_age_seconds)):
        return None

    parsed_values: dict[str, object] = dict(raw_values)
    for key in ("user", "receiver", "chat"):
        raw_json = raw_values.get(key)
        if not raw_json:
            continue
        try:
            parsed_values[key] = json.loads(raw_json)
        except json.JSONDecodeError:
            return None

    parsed_values["auth_date"] = auth_date
    parsed_values["raw_init_data"] = normalized_init_data
    return parsed_values
