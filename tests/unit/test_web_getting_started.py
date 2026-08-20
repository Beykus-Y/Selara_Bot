"""Regression guard for the "P1 -- Discoverability / Onboarding" block of
docs/DOCUMENTATION_AUDIT_TODO.md: a public "Как начать" page that /start
links to. It must be reachable without a session (same guarantee as
/app/docs/user), and every link it sends a first-time user into the real
docs pages must land on a real anchor -- not just a string that happens to
match on both sides.
"""

from __future__ import annotations

import httpx
import pytest

from selara.core.config import Settings
from selara.web import app as web_app_module
from selara.web.admin_docs import build_admin_docs_context
from selara.web.getting_started import build_getting_started_context
from tests.unit.test_web_docs_anchors import (
    _AnchorCollector,
    _assert_anchors_are_sound,
    _render,
)
from selara.web.user_docs import build_user_docs_context


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
            "BOT_USERNAME": "selara_test_bot",
            "WEB_AUTH_SECRET": "secret",
            "WEB_BASE_URL": "http://127.0.0.1:8080",
        }
    )


class _NoCommitSession:
    async def commit(self) -> None:
        return None


class _FailingSessionFactory:
    """A session factory that raises if it is ever entered -- proves the
    getting-started route touches no DB at all, not even the anonymous-safe
    lookup /app/docs/user performs."""

    def __call__(self):
        raise AssertionError("getting-started page must not open a DB session")


@pytest.mark.asyncio
async def test_getting_started_page_is_reachable_without_touching_the_database() -> None:
    app = web_app_module.create_web_app(settings=_settings(), session_factory=_FailingSessionFactory())
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        response = await client.get("/app/docs/getting-started")
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert "Как начать" in response.text
    assert "set-cookie" not in {key.lower() for key in response.headers.keys()}


def test_getting_started_page_has_a_link_into_full_user_docs() -> None:
    context = build_getting_started_context()
    html = _render("getting_started.html", context)
    assert 'href="/app/docs/user"' in html


def test_getting_started_page_own_anchors_are_sound() -> None:
    context = build_getting_started_context()
    html = _render("getting_started.html", context)
    _assert_anchors_are_sound(html, page_name="getting_started.html")


def test_getting_started_nav_links_resolve_on_the_real_docs_pages() -> None:
    context = build_getting_started_context()
    nav_links = context["nav_links"]
    assert nav_links, "getting-started page must link somewhere into the docs"

    user_docs_html = _render("user_docs.html", build_user_docs_context(chat=None))
    admin_docs_html = _render("admin_docs.html", build_admin_docs_context(chat=None))

    user_collector = _AnchorCollector()
    user_collector.feed(user_docs_html)
    user_ids = set(user_collector.ids)

    admin_collector = _AnchorCollector()
    admin_collector.feed(admin_docs_html)
    admin_ids = set(admin_collector.ids)

    for link in nav_links:
        href = link["href"]
        path, _, fragment = href.partition("#")
        if path == "/app/docs/user":
            target_ids = user_ids
        elif path == "/app/docs/admin":
            target_ids = admin_ids
        else:
            raise AssertionError(f"unexpected nav link target: {href!r}")
        if fragment:
            assert fragment in target_ids, f"{href!r} points to a missing anchor on {path}"
