import warnings

import pytest
from pydantic import ValidationError

from selara.core.config import Settings

OPT_IN_ENV = "WEB_AUTH_ALLOW_BOT_TOKEN_FALLBACK"


def _kwargs(**extra) -> dict:
    return {
        "BOT_TOKEN": "123456:TESTTOKEN",
        "DATABASE_URL": "sqlite+aiosqlite:///tmp/test.db",
        **extra,
    }


@pytest.fixture
def no_fallback_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    # tests/conftest.py opts the whole suite into the fallback via the OS
    # environment; these tests need the strict, no-opt-in baseline instead.
    monkeypatch.delenv(OPT_IN_ENV, raising=False)


def test_missing_secret_fails_closed_by_default(no_fallback_opt_in) -> None:
    # The shipped .env.example default (APP_ENV=dev, no WEB_AUTH_SECRET) must
    # not silently turn BOT_TOKEN into the web auth key (#71, Codex review).
    with pytest.raises(ValidationError, match="WEB_AUTH_SECRET"):
        Settings(**_kwargs())


def test_dev_app_env_alone_does_not_enable_fallback(no_fallback_opt_in) -> None:
    with pytest.raises(ValidationError, match="WEB_AUTH_ALLOW_BOT_TOKEN_FALLBACK"):
        Settings(**_kwargs(APP_ENV="dev"))


def test_fallback_requires_explicit_opt_in_flag() -> None:
    assert Settings.model_fields["web_auth_allow_bot_token_fallback"].default is False

    with pytest.raises(ValidationError, match="WEB_AUTH_ALLOW_BOT_TOKEN_FALLBACK"):
        Settings(**_kwargs(**{OPT_IN_ENV: "false"}))


def test_opt_in_enables_fallback_with_warning() -> None:
    with pytest.warns(UserWarning, match="WEB_AUTH_ALLOW_BOT_TOKEN_FALLBACK"):
        settings = Settings(**_kwargs(**{OPT_IN_ENV: "true"}))

    assert settings.resolved_web_auth_secret == "123456:TESTTOKEN"


def test_opt_in_works_regardless_of_app_env() -> None:
    # APP_ENV plays no role anymore: the explicit flag is the only opt-in, so
    # a production deploy that really wants the fallback must say so itself.
    with pytest.warns(UserWarning, match="WEB_AUTH_SECRET"):
        settings = Settings(**_kwargs(APP_ENV="prod", **{OPT_IN_ENV: "true"}))

    assert settings.resolved_web_auth_secret == "123456:TESTTOKEN"


def test_blank_secret_is_treated_as_missing(no_fallback_opt_in) -> None:
    with pytest.raises(ValidationError, match="WEB_AUTH_SECRET"):
        Settings(**_kwargs(WEB_AUTH_SECRET="   "))


def test_explicit_secret_needs_no_opt_in_and_silences_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        settings = Settings(**_kwargs(WEB_AUTH_SECRET="separate-web-secret", **{OPT_IN_ENV: "false"}))

    assert settings.resolved_web_auth_secret == "separate-web-secret"


def test_secret_equal_to_bot_token_fails_without_opt_in(no_fallback_opt_in) -> None:
    with pytest.raises(ValidationError, match="must differ from BOT_TOKEN"):
        Settings(**_kwargs(WEB_AUTH_SECRET="123456:TESTTOKEN"))


def test_secret_equal_to_bot_token_warns_with_opt_in() -> None:
    with pytest.warns(UserWarning, match="BOT_TOKEN"):
        Settings(**_kwargs(WEB_AUTH_SECRET="123456:TESTTOKEN", **{OPT_IN_ENV: "true"}))


def test_web_disabled_starts_without_secret(no_fallback_opt_in) -> None:
    settings = Settings(**_kwargs(WEB_ENABLED="false"))

    # The panel is off, so startup is allowed, but the auth property still
    # refuses to hand out BOT_TOKEN as an HMAC key (defense in depth).
    with pytest.raises(RuntimeError, match="WEB_AUTH_SECRET"):
        _ = settings.resolved_web_auth_secret


def test_web_disabled_property_fails_closed_without_opt_in() -> None:
    settings = Settings(**_kwargs(WEB_ENABLED="false", **{OPT_IN_ENV: "false"}))

    with pytest.raises(RuntimeError, match="WEB_AUTH_ALLOW_BOT_TOKEN_FALLBACK"):
        _ = settings.resolved_web_auth_secret


def test_bot_token_rotation_keeps_web_hmac_domain() -> None:
    secret = "separate-web-secret"
    before = Settings(**_kwargs(WEB_AUTH_SECRET=secret))
    after = Settings(**_kwargs(BOT_TOKEN="999999:ROTATEDTOKEN", WEB_AUTH_SECRET=secret))

    assert before.resolved_web_auth_secret == after.resolved_web_auth_secret == secret
