"""Report-only Lighthouse baseline for the public landing/login/user-docs
pages plus one representative authenticated page (home.html).

Never sent anywhere -- runs `lighthouse` locally against a throwaway local
HTTP server serving the already-rendered fixture HTML plus a copy of
static/, and prints category scores to stdout. No public report upload
(the CLI's own `--output=json` writes to a local temp path only).

Usage: .venv/bin/python scripts/lighthouse_report.py
"""

from __future__ import annotations

import asyncio
import http.server
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "unit"))

STATIC_DIR = ROOT / "src" / "selara" / "web" / "static"
FRONTEND_DIR = ROOT / "frontend"

CATEGORIES = "performance,accessibility,best-practices,seo"


def _fixture_pages() -> dict[str, str]:
    import render_login_fixture
    import render_user_docs_fixture
    import test_web_home_browser as home_test

    return {
        "login": render_login_fixture.render(),
        "user_docs": render_user_docs_fixture.render(),
        "home": home_test._render_home(),
    }


async def _landing_page() -> str:
    from css_coverage_report import _app_rendered_pages

    pages = dict(await _app_rendered_pages())
    return pages["landing"]


def _chrome_path() -> str:
    """Lighthouse's own Chrome launcher needs CHROME_PATH -- there is no
    system Chrome in this environment, only Playwright's bundled Chromium
    (already installed for the browser test suite), so point at that
    instead of installing a second copy."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        return pw.chromium.executable_path


def _run_lighthouse(url: str, out_json: Path, *, chrome_path: str) -> dict | None:
    import os

    env = {**os.environ, "CHROME_PATH": chrome_path}
    result = subprocess.run(
        [
            "npx", "--yes", "lighthouse", url,
            "--output=json", f"--output-path={out_json}",
            "--chrome-flags=--headless --no-sandbox --disable-gpu",
            f"--only-categories={CATEGORIES}",
            "--quiet",
        ],
        cwd=FRONTEND_DIR,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    if result.returncode != 0 or not out_json.exists():
        print(f"    lighthouse run failed (exit {result.returncode})", file=sys.stderr)
        print(result.stderr[-2000:], file=sys.stderr)
        return None
    return json.loads(out_json.read_text(encoding="utf-8"))


def main() -> None:
    pages = _fixture_pages()
    pages["landing"] = asyncio.run(_landing_page())

    with tempfile.TemporaryDirectory() as tmpdir_name:
        tmpdir = Path(tmpdir_name)
        shutil.copytree(STATIC_DIR, tmpdir / "static")
        for name, html in pages.items():
            (tmpdir / f"{name}.html").write_text(html, encoding="utf-8")

        handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(
            *a, directory=str(tmpdir), **kw
        )
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        chrome_path = _chrome_path()
        print(f"\n=== Lighthouse report-only baseline ({CATEGORIES}) ===\n")
        try:
            for name in pages:
                url = f"http://127.0.0.1:{port}/{name}.html"
                out_json = tmpdir / f"{name}-lh.json"
                print(f"running: {name} ({url})", file=sys.stderr)
                report = _run_lighthouse(url, out_json, chrome_path=chrome_path)
                if report is None:
                    print(f"{name}: FAILED TO RUN")
                    continue
                scores = {
                    cat: round(data["score"] * 100)
                    for cat, data in report["categories"].items()
                }
                print(f"{name}: " + ", ".join(f"{k}={v}" for k, v in scores.items()))
        finally:
            server.shutdown()


if __name__ == "__main__":
    main()
