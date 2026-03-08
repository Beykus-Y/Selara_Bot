from __future__ import annotations

import hmac
import secrets
from hashlib import sha256


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
