"""Every internal link the server UI emits must land on a real route.

Closes the last Этап 4 item ("Проверить все cross-page links и возврат назад
после действий"). Written as a systematic scan rather than a handful of spot
checks, because the bug it first caught was invisible to page-level tests:
fifteen JSON error responses in the admin panel told the client to navigate
to `/admin/login`, while the admin login page is actually registered at
`/app/admin/login`. Every HTML redirect used the correct path, so only the
fetch/XHR paths dead-ended — on a 404.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.routing import Mount

from selara.core.config import Settings
from selara.web import app as web_app_module

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src/selara/web"

# `/miniapp/*` is owned by the React Mini App SPA, which is served outside
# FastAPI; the server only ever hands these paths back to the Telegram WebApp
# client for its own client-side routing.
EXTERNAL_PREFIXES = ("/miniapp/",)

SOURCES = [
    WEB / "app.py",
    WEB / "presenters.py",
    WEB / "user_docs.py",
    WEB / "admin_docs.py",
    *sorted((WEB / "templates").glob("*.html")),
]

PY_LINK = re.compile(r'["\'](/(?:app|login|logout|admin|api|miniapp)[^"\'\s]*)["\']')
PY_FSTRING_LINK = re.compile(r'f["\'](/(?:app|login|logout|admin|api|miniapp)[^"\']*)["\']')
TPL_LINK = re.compile(r'href="(/[^"\s]*)"')


class _DummySession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _DummySessionFactory:
    def __call__(self):
        session = _DummySession()

        class _Manager:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Manager()


def _app():
    return web_app_module.create_web_app(
        settings=Settings.model_validate(
            {
                "BOT_TOKEN": "123456:TEST",
                "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
                "BOT_USERNAME": "selara_test_bot",
                "WEB_AUTH_SECRET": "secret",
                "WEB_BASE_URL": "http://127.0.0.1:8080",
            }
        ),
        session_factory=_DummySessionFactory(),
    )


def _route_matchers(app) -> list[re.Pattern[str]]:
    matchers: list[re.Pattern[str]] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        if isinstance(route, Mount):
            matchers.append(re.compile("^" + re.escape(path) + "(/.*)?$"))
            continue
        # `/app/chat/{chat_id}/audit` -> `^/app/chat/[^/]+/audit$`
        pattern = "".join(
            "[^/]+" if part.startswith("{") else re.escape(part)
            for part in re.split(r"(\{[^}]+\})", path)
        )
        matchers.append(re.compile("^" + pattern + "$"))
    return matchers


def _collect_links() -> dict[str, set[str]]:
    links: dict[str, set[str]] = {}
    for source in SOURCES:
        text = source.read_text(encoding="utf-8")
        if source.suffix == ".py":
            found = set(PY_LINK.findall(text)) | set(PY_FSTRING_LINK.findall(text))
        else:
            found = set(TPL_LINK.findall(text))
        for link in found:
            links.setdefault(link, set()).add(source.name)
    return links


def _normalise(link: str) -> str:
    link = link.split("?", 1)[0].split("#", 1)[0]
    link = re.sub(r"\{\{[^}]*\}\}", "1", link)  # Jinja interpolation
    link = re.sub(r"\{[^}]*\}", "1", link)  # f-string placeholder
    return link or "/"


def test_every_internal_server_link_resolves_to_a_registered_route() -> None:
    app = _app()
    matchers = _route_matchers(app)
    links = _collect_links()
    assert len(links) > 50, "link extraction found suspiciously little — check the regexes"

    broken: list[str] = []
    for link, sources in sorted(links.items()):
        if link.startswith(EXTERNAL_PREFIXES):
            continue
        path = _normalise(link)
        if not any(matcher.match(path) for matcher in matchers):
            broken.append(f"{link!r} (as {path!r}) referenced by {sorted(sources)}")

    assert not broken, "internal links point at routes that do not exist:\n" + "\n".join(broken)


def test_admin_json_session_expiry_points_at_the_real_admin_login() -> None:
    """The HTML branch redirected to /app/admin/login while the JSON branch
    next to it used /admin/login, which 404s."""
    source = (WEB / "app.py").read_text(encoding="utf-8")
    assert '"/admin/login"' not in source
    assert "/app/admin/login" in source


@pytest.mark.asyncio
async def test_admin_login_redirect_target_actually_serves_a_page() -> None:
    import httpx

    app = _app()
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        ok = await client.get("/app/admin/login")
        stale = await client.get("/admin/login")
    finally:
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    assert ok.status_code == 200
    # The old target must stay a 404 — that is precisely why it was wrong.
    assert stale.status_code == 404
