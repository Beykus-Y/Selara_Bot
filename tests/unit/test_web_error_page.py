"""Coverage for the shared status/error page (Этап 4: `error.html`).

Checks three things the roadmap item names explicitly — distinguishable
4xx/5xx states, working recovery actions, and a request ID for server
errors — plus a regression guard for the class of bug found while opening
this slice: `error.html` referenced eight CSS classes (`.error-hero`,
`.error-copy`, `.error-meta`, `.error-code`, `.error-actions`,
`.error-status-stack`, `.error-status-card`, `.status-chip`) that were
defined in no stylesheet at all, while `panel.css` still carried four
`.error-panel` rules for markup no template renders any more.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
from playwright.async_api import async_playwright

from selara.core.config import Settings
from selara.web import app as web_app_module
from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"


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


class DummySession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class DummySessionFactory:
    def __call__(self):
        session = DummySession()

        class _Manager:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Manager()


def _client(*, raise_app_exceptions: bool = True) -> tuple[httpx.AsyncClient, object]:
    app = web_app_module.create_web_app(settings=_settings(), session_factory=DummySessionFactory())
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver"), app


async def _shutdown(app) -> None:
    await getattr(app.router, "shutdown", app.router._shutdown)()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "method", "status_code", "status_label", "headline_fragment"),
    [
        ("/definitely-not-a-real-page", "GET", 404, "не найдено", "не найдена"),
        ("/login", "DELETE", 405, "метод не поддерживается", "Метод не поддерживается"),
    ],
)
async def test_error_page_states_are_distinguishable_per_status_code(
    path: str, method: str, status_code: int, status_label: str, headline_fragment: str
) -> None:
    client, app = _client()
    try:
        response = await client.request(method, path)
    finally:
        await client.aclose()
        await _shutdown(app)

    assert response.status_code == status_code
    body = response.text
    # The status must be carried by text, not by colour alone (WEB_UI rule).
    assert str(status_code) in body
    assert status_label in body
    assert headline_fragment in body


@pytest.mark.asyncio
async def test_error_page_offers_recovery_actions_to_an_anonymous_visitor() -> None:
    client, app = _client()
    try:
        response = await client.get("/definitely-not-a-real-page")
    finally:
        await client.aclose()
        await _shutdown(app)

    body = response.text
    assert 'href="/"' in body
    assert 'href="/login"' in body


@pytest.mark.asyncio
async def test_server_error_page_exposes_a_request_id_that_is_also_logged(monkeypatch, caplog) -> None:
    def _boom(**kwargs):
        raise RuntimeError("synthetic landing failure")

    monkeypatch.setattr(web_app_module, "build_landing_context", _boom)

    client, app = _client(raise_app_exceptions=False)
    try:
        with caplog.at_level("ERROR"):
            response = await client.get("/")
    finally:
        await client.aclose()
        await _shutdown(app)

    assert response.status_code == 500
    body = response.text

    match = re.search(r"data-request-id=\"([0-9a-f]{8,})\"", body)
    assert match is not None, "5xx page must expose a request id the user can quote to an admin"
    request_id = match.group(1)

    # The id is worthless unless the very same value reaches the log.
    assert any(request_id in record.getMessage() for record in caplog.records), (
        "request id shown to the user must appear in the server log"
    )
    assert request_id in body


@pytest.mark.asyncio
async def test_client_error_page_does_not_invent_a_request_id() -> None:
    client, app = _client()
    try:
        response = await client.get("/definitely-not-a-real-page")
    finally:
        await client.aclose()
        await _shutdown(app)

    assert "data-request-id" not in response.text


def _render(*, status_code: str, status_label: str, request_id: str | None = None) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    return environment.get_template("error.html").render(
        page_title=f"Selara • {status_code}",
        page_name="error",
        top_links=[{"href": "/", "label": "На главную", "variant": "ghost"}],
        show_logout=False,
        flash=None,
        error=None,
        home_href="/",
        brand_subtitle="бот для Telegram-групп",
        status_code=status_code,
        status_label=status_label,
        headline="Страница не найдена",
        message=(
            "Такого адреса нет или страница уже была перемещена. "
            "Проверьте URL или вернитесь на главную."
        ),
        action_links=[
            {"href": "/", "label": "На главную", "variant": "ghost"},
            {"href": "/login", "label": "Войти через Telegram", "variant": "primary"},
        ],
        request_id=request_id,
    )


async def _mount(page, html: str) -> None:
    await page.set_content(html)
    for stylesheet in ("panel.css", "server-ui-foundation.css", "login-error.css"):
        await page.add_style_tag(path=str(STATIC_DIR / stylesheet))


@pytest.mark.asyncio
async def test_error_page_status_elements_are_actually_styled() -> None:
    """Regression guard: these classes previously had no CSS whatsoever."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page, _render(status_code="404", status_label="не найдено"))

            # A status chip must read as a chip, not as bare inline text.
            chip = await page.locator(".status-chip").evaluate(
                "el => { const s = getComputedStyle(el); return {"
                " bg: s.backgroundColor, radius: parseFloat(s.borderRadius),"
                " padX: parseFloat(s.paddingLeft) }; }"
            )
            assert chip["bg"] != "rgba(0, 0, 0, 0)", ".status-chip has no background"
            assert chip["radius"] >= 4, ".status-chip is not rounded like a chip"
            assert chip["padX"] > 0, ".status-chip has no horizontal padding"

            # The numeric code is the primary signal and must be visually large.
            code_size = await page.locator(".error-code").evaluate(
                "el => parseFloat(getComputedStyle(el).fontSize)"
            )
            body_size = await page.locator("body").evaluate(
                "el => parseFloat(getComputedStyle(el).fontSize)"
            )
            assert code_size > body_size, ".error-code is not visually emphasised"

            # Each explanation card must be a real surface, not naked text.
            cards = page.locator(".error-status-card")
            assert await cards.count() == 2
            card = await cards.first.evaluate(
                "el => { const s = getComputedStyle(el); return {"
                " bg: s.backgroundColor, border: s.borderTopWidth,"
                " pad: parseFloat(s.paddingTop) }; }"
            )
            assert card["bg"] != "rgba(0, 0, 0, 0)" or card["border"] != "0px", (
                ".error-status-card renders as unstyled text"
            )
            assert card["pad"] > 0, ".error-status-card has no padding"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_error_page_desktop_layout_is_balanced_and_actions_stay_on_one_row() -> None:
    """`.hero`'s content-sized right column squeezed the headline and forced
    the two recovery buttons to stack; `.error-hero` overrides the grid."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page, _render(status_code="404", status_label="не найдено"))

            copy_box = await page.locator(".error-copy").bounding_box()
            stack_box = await page.locator(".error-status-stack").bounding_box()
            assert copy_box is not None and stack_box is not None
            assert copy_box["width"] >= 480, (
                f"headline column is cramped at desktop width ({copy_box['width']}px)"
            )
            # Neither side may collapse into a sliver next to the other.
            ratio = copy_box["width"] / stack_box["width"]
            assert 0.6 <= ratio <= 2.5, f"error hero columns are unbalanced (ratio {ratio:.2f})"

            buttons = page.locator(".error-actions .button")
            assert await buttons.count() == 2
            first = await buttons.nth(0).bounding_box()
            second = await buttons.nth(1).bounding_box()
            assert first is not None and second is not None
            assert abs(first["y"] - second["y"]) < 4, (
                "recovery actions wrapped onto separate rows on desktop"
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_error_page_does_not_repeat_the_status_three_times() -> None:
    """The status was printed as chip, numeric code and again inside the
    first explanation card; the card now carries only the explanation."""
    html = _render(status_code="404", status_label="не найдено")
    assert html.count("не найдено") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "viewport",
    [
        {"width": 1440, "height": 900},
        {"width": 820, "height": 1180},
        {"width": 390, "height": 844},
    ],
)
async def test_error_page_has_no_horizontal_overflow(viewport: dict[str, int]) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await _mount(page, _render(status_code="500", status_label="ошибка сервера", request_id="a1b2c3d4"))
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
            copy_box = await page.locator(".error-copy").bounding_box()
            stack_box = await page.locator(".error-status-stack").bounding_box()
            assert copy_box is not None and stack_box is not None
            overlap = (
                copy_box["x"] < stack_box["x"] + stack_box["width"]
                and copy_box["x"] + copy_box["width"] > stack_box["x"]
                and copy_box["y"] < stack_box["y"] + stack_box["height"]
                and copy_box["y"] + copy_box["height"] > stack_box["y"]
            )
            assert not overlap, f"error hero copy overlaps the status cards at {viewport}"
        finally:
            await browser.close()


def test_stale_error_panel_css_is_not_left_behind() -> None:
    """`.error-panel` markup no longer exists; its CSS must not linger."""
    css = (STATIC_DIR / "panel.css").read_text(encoding="utf-8")
    templates = "\n".join(
        path.read_text(encoding="utf-8") for path in TEMPLATE_DIR.glob("*.html")
    )
    assert "error-panel" not in templates
    assert "error-panel" not in css, "dead .error-panel rules should have been removed"
