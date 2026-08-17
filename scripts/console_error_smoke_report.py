"""Report-only smoke check: load every representative rendered page state
and fail loudly if any static JS module throws an uncaught error while
running against real browser DOM/CSSOM.

Reuses the exact same 22 page states already vetted by
scripts/css_coverage_report.py and scripts/axe_scan_report.py (same
fixture-building functions, imported directly -- no duplicated contexts).

Pages are served from a fake http://selara.test origin via page.route()
interception rather than page.set_content() -- the same pattern already
used in tests/unit/test_web_games_action_flow_browser.py. This matters for
more than style: set_content() pages load at about:blank, which has no real
origin, so relative-URL browser APIs like `new EventSource("/api/...")`
throw immediately (confirmed by reading games.js/chat-overview.js -- they
use plain relative URLs, which are correct for a real page and only fail
here for lack of an origin). A real (fake) origin makes those resolve
normally; the /api/** requests they trigger are then aborted, which these
scripts already handle via .onerror rather than an uncaught throw.

Usage: .venv/bin/python scripts/console_error_smoke_report.py
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "unit"))

from css_coverage_report import (  # noqa: E402
    STATIC_DIR,
    _app_rendered_pages,
    _direct_render_pages,
    _family_page,
    _fixture_pages,
)

BASE_URL = "http://selara.test"


async def main() -> None:
    from playwright.async_api import async_playwright

    pages = (
        _fixture_pages()
        + await _app_rendered_pages()
        + _direct_render_pages()
        + [("family", await _family_page())]
    )

    findings: dict[str, list[str]] = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        for label, html in pages:
            page = await browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))

            async def serve_page(route, *, _html=html):
                await route.fulfill(body=_html, content_type="text/html")

            async def serve_static(route):
                path = re.sub(r"^.*?/static/", "", route.request.url).split("?", 1)[0]
                local_path = STATIC_DIR / path
                if local_path.is_file():
                    await route.fulfill(path=str(local_path))
                else:
                    await route.fulfill(status=404, body="")

            await page.route(f"{BASE_URL}/", serve_page)
            await page.route(f"{BASE_URL}/static/**", serve_static)
            await page.route(f"{BASE_URL}/api/**", lambda route: route.abort())
            await page.route(f"{BASE_URL}/app/**", lambda route: route.abort())

            await page.goto(f"{BASE_URL}/")
            await page.wait_for_timeout(300)
            if errors:
                findings[label] = errors
            await page.close()
            print(f"visited: {label}", file=sys.stderr)
        await browser.close()

    print(f"\n=== JS console-error smoke check across {len(pages)} rendered page states ===\n")
    if not findings:
        print("No uncaught JS errors found in any visited state.")
        return
    for label, errors in findings.items():
        print(f"{label}: {len(errors)} uncaught error(s)")
        for error in errors:
            print(f"    {error}")


if __name__ == "__main__":
    asyncio.run(main())
