import pytest


@pytest.fixture(autouse=True)
def _test_web_auth_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test a dedicated WEB_AUTH_SECRET (#71).

    Since #71 a missing WEB_AUTH_SECRET fails closed unless the dev-only
    BOT_TOKEN fallback is explicitly opted into. Test suites construct
    Settings without WEB_AUTH_SECRET all over the place, so a dedicated
    throwaway secret is injected once, centrally, instead of opting the whole
    suite into the weakened BOT_TOKEN fallback configuration.
    """
    monkeypatch.setenv("WEB_AUTH_SECRET", "test-suite-web-auth-secret")
