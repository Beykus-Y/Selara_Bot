import pytest
from pydantic import ValidationError

from selara.core.config import Settings


def settings(**extra):
    return Settings(_env_file=None, BOT_TOKEN="token", DATABASE_URL="sqlite+aiosqlite:///test.db",
                    WEB_AUTH_SECRET="separate-secret", **extra)


@pytest.mark.parametrize("url", ["https://panel.example.com", "https://localhost:8080", "http://panel.example.com"])
def test_public_or_https_cookies_default_to_secure(url):
    configured = settings(WEB_BASE_URL=url)
    assert configured.web_session_cookie_secure is True
    assert configured.admin_session_cookie_secure is True


@pytest.mark.parametrize("url", ["http://localhost:8080", "http://127.0.0.1:8080", "http://[::1]:8080", "http://app.localhost:8080"])
def test_http_loopback_keeps_local_development_working(url):
    configured = settings(WEB_BASE_URL=url)
    assert configured.web_session_cookie_secure is False
    assert configured.admin_session_cookie_secure is False


def test_web_domain_takes_precedence_over_http_loopback_base_url():
    configured = settings(WEB_DOMAIN="panel.example.com", WEB_BASE_URL="http://127.0.0.1:8080")
    assert configured.web_session_cookie_secure is True
    assert configured.admin_session_cookie_secure is True


@pytest.mark.parametrize("flag", ["WEB_SESSION_COOKIE_SECURE", "ADMIN_SESSION_COOKIE_SECURE"])
@pytest.mark.parametrize("source", [{"WEB_BASE_URL": "https://panel.example.com"}, {"WEB_DOMAIN": "panel.example.com"}])
def test_https_rejects_explicit_insecure_cookie_override(flag, source):
    with pytest.raises(ValidationError, match=flag):
        settings(**source, **{flag: "false"})


@pytest.mark.parametrize("flag", ["WEB_SESSION_COOKIE_SECURE", "ADMIN_SESSION_COOKIE_SECURE"])
def test_https_rejects_insecure_environment_override(monkeypatch, flag):
    monkeypatch.setenv(flag, "false")
    with pytest.raises(ValidationError, match=flag):
        settings(WEB_BASE_URL="https://panel.example.com")


def test_explicit_http_development_and_secure_overrides_are_preserved():
    configured = settings(WEB_BASE_URL="http://testserver", WEB_SESSION_COOKIE_SECURE=False,
                          ADMIN_SESSION_COOKIE_SECURE=False)
    assert not configured.web_session_cookie_secure
    assert not configured.admin_session_cookie_secure
    configured = settings(WEB_BASE_URL="http://localhost:8080", WEB_SESSION_COOKIE_SECURE=True,
                          ADMIN_SESSION_COOKIE_SECURE=True)
    assert configured.web_session_cookie_secure
    assert configured.admin_session_cookie_secure


def test_disabled_web_panel_does_not_reject_unused_cookie_flags():
    settings(WEB_ENABLED=False, WEB_BASE_URL="https://panel.example.com",
             WEB_SESSION_COOKIE_SECURE=False, ADMIN_SESSION_COOKIE_SECURE=False)
