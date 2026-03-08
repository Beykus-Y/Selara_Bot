from selara.core.web_auth import (
    digest_login_code,
    digest_session_token,
    generate_login_code,
    generate_session_token,
    normalize_base_url,
    normalize_login_code,
)


def test_normalize_login_code_keeps_only_digits() -> None:
    assert normalize_login_code(" 12-34 56 ") == "123456"


def test_generate_login_code_returns_six_digits() -> None:
    code = generate_login_code(length=6)
    assert len(code) == 6
    assert code.isdigit()


def test_login_code_digest_is_stable() -> None:
    first = digest_login_code(secret="secret", code="123456")
    second = digest_login_code(secret="secret", code="123 456")
    assert first == second


def test_session_token_digest_changes_with_token() -> None:
    first = digest_session_token(secret="secret", token=generate_session_token())
    second = digest_session_token(secret="secret", token=generate_session_token())
    assert first != second


def test_normalize_base_url_trims_trailing_slash() -> None:
    assert normalize_base_url("http://localhost:8080/") == "http://localhost:8080"
