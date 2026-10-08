import pytest


@pytest.fixture
def login_limiter_stub(monkeypatch):
    """HTTP route tests inject admission; shared Redis behavior is tested in CI."""
    from uuid import uuid4
    from selara.web import app

    class Limiter:
        def __init__(self, *, limit, **kwargs):
            self.limit = limit
            self.attempts = {}

        async def reserve(self, key):
            attempts = self.attempts.setdefault(key, set())
            if len(attempts) >= self.limit:
                return None
            token = uuid4().hex
            attempts.add(token)
            return token

        async def release(self, key, token):
            self.attempts[key].discard(token)

    monkeypatch.setattr(app, "RedisLoginAttemptLimiter", Limiter)


@pytest.fixture
def stt_cooldown_stub(monkeypatch):
    """Handler tests inject admission; the real Redis contract is tested separately."""
    from selara.presentation.handlers import voice

    claimed = set()

    async def claim(*, chat_id, user_id, cooldown_seconds, **kwargs):
        if cooldown_seconds <= 0:
            return True
        key = (chat_id, user_id)
        if key in claimed:
            return False
        claimed.add(key)
        return True

    monkeypatch.setattr(voice, "claim_stt_cooldown", claim)


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
