import warnings

import pytest
from pydantic import ValidationError

from selara.core.config import Settings, WEB_AUTH_FALLBACK_APP_ENVS


def _kwargs(**extra) -> dict:
    return {
        "BOT_TOKEN": "123456:TESTTOKEN",
        "DATABASE_URL": "sqlite+aiosqlite:///tmp/test.db",
        **extra,
    }


def test_production_requires_explicit_web_auth_secret() -> None:
    with pytest.raises(ValidationError, match="WEB_AUTH_SECRET"):
        Settings(**_kwargs(APP_ENV="prod"))


def test_production_spelling_also_requires_explicit_secret() -> None:
    with pytest.raises(ValidationError, match="WEB_AUTH_SECRET"):
        Settings(**_kwargs(APP_ENV="production"))


def test_app_env_matching_ignores_case() -> None:
    with pytest.raises(ValidationError, match="WEB_AUTH_SECRET"):
        Settings(**_kwargs(APP_ENV="Prod"))


def test_unknown_app_env_is_treated_as_production() -> None:
    # Fail closed: anything outside the dev/test allowlist (staging, typos,
    # future values) must not silently reuse BOT_TOKEN.
    with pytest.raises(ValidationError, match="WEB_AUTH_SECRET"):
        Settings(**_kwargs(APP_ENV="staging"))


def test_blank_secret_is_treated_as_missing_in_production() -> None:
    with pytest.raises(ValidationError, match="WEB_AUTH_SECRET"):
        Settings(**_kwargs(APP_ENV="prod", WEB_AUTH_SECRET="   "))


def test_explicit_secret_satisfies_production() -> None:
    settings = Settings(**_kwargs(APP_ENV="prod", WEB_AUTH_SECRET="separate-web-secret"))

    assert settings.resolved_web_auth_secret == "separate-web-secret"


def test_production_rejects_secret_equal_to_bot_token() -> None:
    with pytest.raises(ValidationError, match="must differ from BOT_TOKEN"):
        Settings(**_kwargs(APP_ENV="prod", WEB_AUTH_SECRET="123456:TESTTOKEN"))


def test_web_disabled_production_starts_without_secret() -> None:
    settings = Settings(**_kwargs(APP_ENV="prod", WEB_ENABLED="false"))

    # The panel is off, so startup is allowed, but the auth property still
    # refuses to hand out BOT_TOKEN as an HMAC key (defense in depth).
    with pytest.raises(RuntimeError, match="WEB_AUTH_SECRET"):
        _ = settings.resolved_web_auth_secret


def test_dev_falls_back_to_bot_token_with_warning() -> None:
    with pytest.warns(UserWarning, match="WEB_AUTH_SECRET"):
        settings = Settings(**_kwargs(APP_ENV="dev"))

    assert settings.resolved_web_auth_secret == "123456:TESTTOKEN"


def test_explicit_dev_secret_silences_fallback_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        settings = Settings(**_kwargs(APP_ENV="dev", WEB_AUTH_SECRET="separate-web-secret"))

    assert settings.resolved_web_auth_secret == "separate-web-secret"


def test_dev_warns_when_secret_equals_bot_token() -> None:
    with pytest.warns(UserWarning, match="BOT_TOKEN"):
        Settings(**_kwargs(APP_ENV="dev", WEB_AUTH_SECRET="123456:TESTTOKEN"))


def test_bot_token_rotation_keeps_web_hmac_domain() -> None:
    secret = "separate-web-secret"
    before = Settings(**_kwargs(APP_ENV="prod", WEB_AUTH_SECRET=secret))
    after = Settings(
        **_kwargs(BOT_TOKEN="999999:ROTATEDTOKEN", APP_ENV="prod", WEB_AUTH_SECRET=secret)
    )

    assert before.resolved_web_auth_secret == after.resolved_web_auth_secret == secret


def test_fallback_allowlist_is_explicit() -> None:
    assert WEB_AUTH_FALLBACK_APP_ENVS == frozenset({"dev", "development", "local", "test", "testing"})
