"""Report-only axe-core accessibility scan across the same representative
set of rendered pages used by css_coverage_report.py -- reuses that
script's page-building helpers instead of re-deriving fixture contexts.

axe-core (npm, MIT) is injected directly via Playwright's
add_script_tag(path=...) from frontend/node_modules/axe-core/axe.min.js,
then run in-page via window.axe.run(). This is check-only: it prints
violations grouped by page and rule, nothing is auto-fixed and nothing
blocks CI (same status as djLint's check-only integration this session).

Usage: .venv/bin/python scripts/axe_scan_report.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from css_coverage_report import (  # noqa: E402
    ALL_CSS_FILES,
    STATIC_DIR,
    _app_rendered_pages,
    _direct_render_pages,
    _family_page,
    _fixture_pages,
)

AXE_SCRIPT_PATH = ROOT / "frontend" / "node_modules" / "axe-core" / "axe.min.js"

RUN_AXE_JS = """
async () => {
  const result = await window.axe.run(document, {
    // color-contrast needs real layout/paint, which is fine here since we
    // render into a real headless page -- left enabled deliberately, not
    // one of the checks report-only tooling usually has to skip.
    resultTypes: ['violations'],
  });
  return result.violations.map((v) => ({
    id: v.id,
    impact: v.impact,
    help: v.help,
    nodeCount: v.nodes.length,
    targets: v.nodes.slice(0, 3).map((n) => n.target.join(' ')),
  }));
}
"""


async def main() -> None:
    from playwright.async_api import async_playwright

    if not AXE_SCRIPT_PATH.exists():
        raise SystemExit(f"axe-core not found at {AXE_SCRIPT_PATH} -- run `npm install` in frontend/")

    pages = (
        _fixture_pages()
        + await _app_rendered_pages()
        + _direct_render_pages()
        + [("family", await _family_page())]
    )

    findings: dict[str, list[dict[str, object]]] = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        for label, html in pages:
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.set_content(html)
            for css_file in ALL_CSS_FILES:
                await page.add_style_tag(path=str(STATIC_DIR / css_file))
            await page.add_script_tag(path=str(AXE_SCRIPT_PATH))
            violations = await page.evaluate(RUN_AXE_JS)
            if violations:
                findings[label] = violations
            await page.close()
            print(f"scanned: {label}", file=sys.stderr)
        await browser.close()

    print(f"\n=== axe-core violations across {len(pages)} rendered fixture pages ===\n")
    if not findings:
        print("No violations found in any visited state.")
        return

    by_rule: dict[str, list[str]] = {}
    for label, violations in findings.items():
        print(f"{label}: {len(violations)} rule(s) with violations")
        for v in violations:
            print(f"    [{v['impact']}] {v['id']} — {v['help']} ({v['nodeCount']} node(s))")
            for target in v["targets"]:
                print(f"        {target}")
            by_rule.setdefault(v["id"], []).append(label)

    print("\n=== summary by rule ===\n")
    for rule_id, pages_hit in sorted(by_rule.items()):
        print(f"{rule_id}: seen on {len(pages_hit)} page(s) — {', '.join(sorted(set(pages_hit)))}")


if __name__ == "__main__":
    asyncio.run(main())
