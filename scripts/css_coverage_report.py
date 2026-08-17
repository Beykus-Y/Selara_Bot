"""Report-only CSS selector coverage across a representative set of rendered
pages.

Playwright Python has no page.coverage API (that convenience wrapper is
Puppeteer/Node-only). Rather than reimplement Chrome's CSS rule-usage
tracking with a hand-rolled offset parser -- the same class of mistake
already made once this session with a naive text-grep approach, see
docs/WEB_UI_MODERNIZATION_TODO.md's Stage 1 journal -- this asks the
browser itself: for every CSSOM rule in our stylesheets, does
`document.querySelectorAll(selectorText)` match anything in this page's
DOM? That's simpler to get right than manual byte-offset bookkeeping and
gives the same answer for our purpose (is this selector reachable from any
element we rendered).

This is NOT proof a selector is dead: a miss here means "never matched in
the states this script visited", not "confirmed unused everywhere" --
dynamic/JS-toggled classes and unvisited states can still hit it. Treat
findings as leads for manual review, same as the djLint baseline.

Usage: .venv/bin/python scripts/css_coverage_report.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "unit"))

from selara.web.rendering import create_template_environment  # noqa: E402

STATIC_DIR = ROOT / "src" / "selara" / "web" / "static"
TEMPLATE_DIR = ROOT / "src" / "selara" / "web" / "templates"

ALL_CSS_FILES = [
    "server-ui-foundation.css",
    "panel.css",
    "games.css",
    "landing.css",
    "login-error.css",
    "admin-overview.css",
    "admin-feedback.css",
    "admin-broadcast.css",
    "admin-broadcast-detail.css",
    "admin-archive.css",
    "audit.css",
    "admin-table.css",
    "admin-table-search.css",
    "docs-item-actions.css",
    "docs-search.css",
    "docs-responsive.css",
]


def _render(template_name: str, **context) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    return environment.get_template(template_name).render(**context)


async def _app_rendered_pages() -> list[tuple[str, str]]:
    """Pages that need the real FastAPI app (games has too much render
    logic in app.py to safely hand-build a context, same reasoning as the
    rest of this session's games test harnesses)."""
    import httpx
    from unittest import mock
    from selara.core.config import Settings
    from selara.presentation.game_state import GameStore
    from selara.web import app as web_app_module
    from selara.domain.entities import UserSnapshot, UserChatOverview

    class FakeActivityRepo:
        async def list_user_admin_chats(self, *, user_id): return []
        async def list_user_activity_chats(self, *, user_id, limit=50): return []
        async def list_user_manageable_game_chats(self, *, user_id):
            return [UserChatOverview(chat_id=-1001, chat_type="group", chat_title="Клуб настолок", bot_role="game_master", message_count=None, last_seen_at=None)]
        async def get_chat_settings(self, *, chat_id): return None
        async def get_chat_display_name(self, *, chat_id, user_id): return None
        async def get_effective_role_definition(self, *, chat_id, user_id): return None

    class FakeWebAuthRepo:
        def __init__(self, user): self.user = user
        async def get_user_by_session(self, *, session_digest, now, touch): return self.user

    class DummySession:
        async def commit(self): return None

    class DummySessionFactory:
        def __call__(self):
            session = DummySession()

            class M:
                async def __aenter__(self_inner): return session
                async def __aexit__(self_inner, *a): return False

            return M()

    settings = Settings.model_validate({
        "BOT_TOKEN": "123456:TEST", "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
        "BOT_USERNAME": "selara_test_bot", "WEB_AUTH_SECRET": "secret", "WEB_BASE_URL": "http://127.0.0.1:8080",
    })
    user = UserSnapshot(telegram_user_id=77, username="gm", first_name="Game", last_name="Master", is_bot=False)
    anon_user = UserSnapshot(telegram_user_id=78, username="viewer", first_name="View", last_name="Er", is_bot=False)
    store = GameStore()

    with mock.patch.object(web_app_module, "SqlAlchemyActivityRepository", lambda session: FakeActivityRepo()), \
         mock.patch.object(web_app_module, "SqlAlchemyWebAuthRepository", lambda session: FakeWebAuthRepo(user)), \
         mock.patch.object(web_app_module, "GAME_STORE", store), \
         mock.patch.object(web_app_module.game_router_module, "GAME_STORE", store):
        app = web_app_module.create_web_app(settings=settings, session_factory=DummySessionFactory())
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
        client.cookies.set(settings.web_session_cookie_name, "session-token")

        games_create = (await client.get("/app/games")).text

        game, error = await store.create_lobby(
            kind="whoami", chat_id=-1001, chat_title="Клуб настолок",
            owner_user_id=77, owner_label="Хозяйка вечера", reveal_eliminated_role=True,
        )
        assert error is None
        for uid, label in [(303, "Чайник"), (404, "Ложка")]:
            await store.join(game_id=game.game_id, user_id=uid, user_label=label)
        await store.start(game_id=game.game_id)
        games_active = (await client.get("/app/games")).text

        landing = (await client.get("/")).text

        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()

    return [
        ("games_create_grid", games_create),
        ("games_active_whoami", games_active),
        ("landing", landing),
    ]


async def _family_page() -> str:
    """family.html needs its own repo mocking (has_permission + a distinct
    activity-repo shape) -- reuse the fixtures already built for
    test_web_family_routes.py instead of re-deriving them."""
    import httpx
    from unittest import mock

    import test_web_family_routes as family_test

    state = family_test.FamilyState(
        settings=family_test._settings(),
        user=family_test.UserSnapshot(
            telegram_user_id=77, username="viewer", first_name="View", last_name="Er", is_bot=False
        ),
        activity_groups=[family_test._overview(family_test.CHAT_ID, "Клуб настолок")],
        bundle=family_test.FamilyBundle(
            subject_user_id=77, spouse_user_id=None,
            parents=(), grandparents=(), step_parents=(), siblings=(), children=(), pets=(), owners=(),
        ),
        graph=family_test.FamilyGraph(focus_user_id=77, node_user_ids=(), edges=()),
    )

    with mock.patch.object(
        family_test.web_app_module, "SqlAlchemyActivityRepository", lambda session: family_test.FakeFamilyActivityRepo(state)
    ), mock.patch.object(
        family_test.web_app_module, "SqlAlchemyWebAuthRepository", lambda session: family_test.FakeWebAuthRepo(state)
    ), mock.patch.object(family_test.web_app_module, "has_permission", family_test._has_permission):
        app = family_test.web_app_module.create_web_app(
            settings=state.settings, session_factory=family_test.DummySessionFactory()
        )
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
        client.cookies.set(state.settings.web_session_cookie_name, "session-token")
        html = (await client.get(f"/app/family/{family_test.CHAT_ID}")).text
        await client.aclose()
        await getattr(app.router, "shutdown", app.router._shutdown)()
    return html


def _direct_render_pages() -> list[tuple[str, str]]:
    """Pages with an existing standalone _render()-style fixture in a test
    module -- imported and reused rather than re-deriving contexts."""
    import test_web_chat_audit_browser as audit_test
    import test_web_chat_overview_browser as chat_test
    import test_web_economy_browser as economy_test

    return [
        ("economy", economy_test._render()),
        ("chat_overview", chat_test._render(active_tab="overview")),
        ("chat_achievements", chat_test._render(active_tab="achievements")),
        ("chat_settings", chat_test._render(active_tab="settings")),
        ("audit", audit_test._render_audit()),
    ]


def _capture_stdout_render(module_name: str) -> str:
    """render_server_ui_fixture.py and render_chat_audit_fixture.py only
    expose main() (writes to stdout), no importable render() -- reuse them
    as-is via stdout capture instead of duplicating their large fixture
    contexts here."""
    import importlib
    import io
    from contextlib import redirect_stdout

    module = importlib.import_module(module_name)
    buf = io.StringIO()
    with redirect_stdout(buf):
        module.main()
    return buf.getvalue()


def _fixture_pages() -> list[tuple[str, str]]:
    import render_admin_archive_fixture as archive_fx
    import render_admin_broadcast_detail_fixture as broadcast_fx
    import render_admin_docs_fixture as admin_docs_fx
    import render_admin_login_fixture as admin_login_fx
    import render_admin_table_fixture as table_fx
    import render_login_fixture as login_fx
    import render_user_docs_fixture as user_docs_fx

    pages = [
        ("admin_messages_compact", archive_fx.render()),
        ("admin_broadcast_detail", broadcast_fx.render()),
        ("admin_docs", admin_docs_fx.render()),
        ("admin_login", admin_login_fx.render()),
        ("admin_table_list", table_fx.render_table()),
        ("admin_table_list_empty", table_fx.render_table(empty=True)),
        ("admin_table_edit", table_fx.render_edit()),
        ("login", login_fx.render()),
        ("login_error", login_fx.render(error="Код истёк")),
        ("user_docs", user_docs_fx.render()),
        ("admin_dashboard", _capture_stdout_render("render_server_ui_fixture")),
        ("chat_audit", _capture_stdout_render("render_chat_audit_fixture")),
    ]

    pages.append((
        "error_404",
        _render(
            "error.html",
            page_title="Selara • 404",
            page_name="error",
            extra_styles=["login-error.css"],
            top_links=[{"href": "/", "label": "На главную", "variant": "ghost"}],
            show_logout=False,
            flash=None,
            error=None,
            home_href="/",
            brand_subtitle="бот для Telegram-групп",
            status_code="404",
            status_label="не найдено",
            headline="Страница не найдена",
            message="Такого адреса нет.",
            action_links=[{"href": "/", "label": "На главную", "variant": "ghost"}],
            request_id=None,
        ),
    ))

    return pages


INJECT_TAGGED_STYLE_JS = """
({ fname, cssText }) => {
  const style = document.createElement('style');
  style.setAttribute('data-coverage-file', fname);
  style.textContent = cssText;
  document.head.appendChild(style);
}
"""

COLLECT_SELECTORS_JS = """
() => {
  // Match stylesheets by an explicit data-coverage-file marker on their
  // owner <style> node, set at injection time -- positional/insertion-order
  // matching turned out unreliable (pages can differ in how many other
  // stylesheets/style tags precede ours), so this is deliberately explicit
  // instead of inferred.
  const out = {};
  for (const sheet of document.styleSheets) {
    const owner = sheet.ownerNode;
    const fname = owner && owner.getAttribute && owner.getAttribute('data-coverage-file');
    if (!fname) continue;
    const selectors = out[fname] || (out[fname] = []);
    const walk = (rules) => {
      for (const rule of rules) {
        if (rule.selectorText) selectors.push(rule.selectorText);
        else if (rule.cssRules) walk(rule.cssRules);
      }
    };
    try { walk(sheet.cssRules); } catch { /* skip */ }
  }
  return out;
}
"""

MATCH_SELECTORS_JS = """
(selectorsByFile) => {
  // :hover/:focus/:active/::before/etc can never match via static
  // querySelectorAll (they need real interaction state or aren't real
  // DOM nodes at all) -- strip them so we test the underlying structural
  // selector instead of guaranteeing a false "never matched".
  const stripPseudo = (selector) => selector
    .split(',')
    .map((part) => part
      .replace(/::?(hover|focus(-visible|-within)?|active|before|after|placeholder|marker|selection)\\b/g, '')
      .trim())
    .filter((part) => part.length > 0)
    .join(', ');
  const matched = {};
  for (const [fname, selectors] of Object.entries(selectorsByFile)) {
    const hits = matched[fname] || (matched[fname] = []);
    for (const selector of selectors) {
      const cleaned = stripPseudo(selector) || selector;
      let isMatch = false;
      try { isMatch = document.querySelectorAll(cleaned).length > 0; }
      catch { isMatch = null; }  // invalid/unsupported selector text
      hits.push(isMatch);
    }
  }
  return matched;
}
"""


async def main() -> None:
    from playwright.async_api import async_playwright

    pages = (
        _fixture_pages()
        + await _app_rendered_pages()
        + _direct_render_pages()
        + [("family", await _family_page())]
    )
    # Keyed by selector text, not index -- some pages can legitimately
    # observe a slightly different rule count (e.g. a stylesheet with a
    # duplicate selector counted once vs twice depending on parser state),
    # so string keys are robust where positional lists are not.
    all_selectors: dict[str, set[str]] = {}
    ever_matched: dict[str, set[str]] = {}

    css_texts = {fname: (STATIC_DIR / fname).read_text(encoding="utf-8") for fname in ALL_CSS_FILES}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        for label, html in pages:
            page = await browser.new_page()
            await page.set_content(html)
            for css_file in ALL_CSS_FILES:
                await page.evaluate(
                    INJECT_TAGGED_STYLE_JS, {"fname": css_file, "cssText": css_texts[css_file]}
                )
            await page.wait_for_timeout(50)

            selectors_by_file = await page.evaluate(COLLECT_SELECTORS_JS)
            for fname, selectors in selectors_by_file.items():
                all_selectors.setdefault(fname, set()).update(selectors)

            matched = await page.evaluate(MATCH_SELECTORS_JS, selectors_by_file)
            for fname, hits in matched.items():
                selectors = selectors_by_file[fname]
                matched_set = ever_matched.setdefault(fname, set())
                for selector, hit in zip(selectors, hits):
                    if hit:
                        matched_set.add(selector)

            await page.close()
            print(f"visited: {label}", file=sys.stderr)

        await browser.close()

    print(f"\n=== CSS selector coverage across {len(pages)} rendered fixture pages ===\n")
    for fname in ALL_CSS_FILES:
        selectors = all_selectors.get(fname)
        if not selectors:
            print(f"{fname}: not observed (0 rules loaded)")
            continue
        hits = ever_matched.get(fname, set())
        never = sorted(sel for sel in selectors if sel not in hits)
        print(f"{fname}: {len(selectors)} selectors, {len(never)} never matched an element in visited states")
        for sel in never[:400]:
            print(f"    {sel}")


if __name__ == "__main__":
    asyncio.run(main())
