import pytest


@pytest.fixture(autouse=True)
def _allow_web_auth_bot_token_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt every test into the dev-only WEB_AUTH_SECRET fallback (#71).

    Since #71 the fallback to BOT_TOKEN is an explicit opt-in that defaults to
    false so production deployments fail closed. Test suites construct Settings
    without WEB_AUTH_SECRET all over the place, so the opt-in is enabled once,
    centrally, instead of per test file.
    """
    monkeypatch.setenv("WEB_AUTH_ALLOW_BOT_TOKEN_FALLBACK", "true")
