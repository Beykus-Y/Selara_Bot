from selara.core.config import Settings


def test_resolved_web_base_url_prefers_web_domain_without_scheme() -> None:
    settings = Settings(
        BOT_TOKEN="token",
        DATABASE_URL="sqlite+aiosqlite:///tmp/test.db",
        WEB_DOMAIN="selara.example.com",
        WEB_BASE_URL="http://127.0.0.1:8080",
    )

    assert settings.resolved_web_base_url == "https://selara.example.com"


def test_resolved_web_base_url_accepts_full_web_domain_url() -> None:
    settings = Settings(
        BOT_TOKEN="token",
        DATABASE_URL="sqlite+aiosqlite:///tmp/test.db",
        WEB_DOMAIN="https://panel.selara.example.com/",
    )

    assert settings.resolved_web_base_url == "https://panel.selara.example.com"
