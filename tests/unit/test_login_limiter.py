from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError
from redis.exceptions import ConnectionError
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from selara.core.config import Settings
from selara.web import app as module
from selara.web.login_limiter import LoginLimiterUnavailable, RedisLoginAttemptLimiter


def settings(**extra):
    return Settings(_env_file=None, BOT_TOKEN="token", DATABASE_URL="sqlite+aiosqlite:///test.db",
                    WEB_AUTH_SECRET="separate-secret", **extra)


@pytest.mark.asyncio
@pytest.mark.parametrize("peer,expected", [("127.0.0.1", "198.51.100.8"), ("10.0.0.3", "10.0.0.3")])
async def test_forwarded_client_identity_is_used_only_from_trusted_proxy(monkeypatch, peer, expected):
    limiter = SimpleNamespace(reserve=AsyncMock(return_value="token"))
    monkeypatch.setattr(module, "RedisLoginAttemptLimiter", lambda **kwargs: limiter)
    app = module.create_web_app(settings=settings(), session_factory=None)
    trusted_app = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1")
    transport = httpx.ASGITransport(app=trusted_app, client=(peer, 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        response = await client.post("/login", data={"code": "invalid"}, headers={
            "accept": "application/json", "x-forwarded-for": "203.0.113.5, 198.51.100.8",
            "x-real-ip": "192.0.2.3",
        })
    assert response.status_code == 400
    limiter.reserve.assert_awaited_once_with(f"web:{expected}")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/login", "/app/admin/login", "/api/admin/login"])
async def test_limiter_outage_refuses_login_before_credential_check(monkeypatch, path):
    limiter = SimpleNamespace(reserve=AsyncMock(side_effect=LoginLimiterUnavailable("Вход временно недоступен")))
    monkeypatch.setattr(module, "RedisLoginAttemptLimiter", lambda **kwargs: limiter)
    app = module.create_web_app(settings=settings(), session_factory=None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.post(path, data={"password": "anything", "code": "123456"},
                                     headers={"accept": "application/json"})
    assert response.status_code == 503
    assert response.json()["ok"] is False


@pytest.mark.asyncio
async def test_redis_error_does_not_expose_credentials(monkeypatch, caplog):
    limiter = RedisLoginAttemptLimiter(redis_url="redis://secret:password@invalid", limit=2, window_seconds=60)
    def unavailable():
        raise ConnectionError("redis://secret:password@invalid")
    monkeypatch.setattr(limiter, "_client", unavailable)
    with pytest.raises(LoginLimiterUnavailable) as caught:
        await limiter.reserve("web:127.0.0.1")
    await limiter.release("web:127.0.0.1", "token")
    assert "secret" not in str(caught.value)
    assert "password" not in caplog.text


@pytest.mark.parametrize("trusted", ["*", "0.0.0.0/0", "::/0", "arbitrary-host", "172.20.1.1/16"])
def test_proxy_configuration_rejects_wildcard_or_invalid_trust(trusted):
    with pytest.raises(ValidationError, match="WEB_FORWARDED_ALLOW_IPS"):
        settings(WEB_FORWARDED_ALLOW_IPS=trusted)


@pytest.mark.parametrize("trusted", ["", "127.0.0.1,::1", "172.20.0.0/16", "10.0.0.2"])
def test_proxy_configuration_accepts_explicit_ips_and_networks(trusted):
    assert settings(WEB_FORWARDED_ALLOW_IPS=trusted).web_forwarded_allow_ips == trusted
