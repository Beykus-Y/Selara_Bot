"""Bounded static HTML rendering, used only by the isolated renderer worker."""
from __future__ import annotations

import asyncio
import base64
import re
from html.parser import HTMLParser

from playwright.async_api import async_playwright

MAX_HTML = 48000
MAX_CSS = 12000
MAX_PAGES = 3
MAX_IMAGE_BYTES = 2_000_000
_ALLOWED_TAGS = frozenset("div span p h1 h2 h3 h4 section article header footer main aside table thead tbody tfoot tr th td ul ol li strong b em i small br hr pre code style svg g path rect circle ellipse line polyline polygon text tspan defs lineargradient radialgradient stop title desc".split())
_ALLOWED_ATTRS = frozenset("class id style colspan rowspan viewbox width height x y x1 x2 y1 y2 cx cy r rx ry d points fill stroke stroke-width stroke-linecap stroke-linejoin opacity transform text-anchor dominant-baseline offset stop-color stop-opacity preserveaspectratio role aria-label".split())
_VOID_TAGS = frozenset({"br", "hr"})


def validate_css(css: str) -> None:
    # Reject escape/comment tricks before inspecting network/function syntax.
    if any(value in css for value in ("\\", "/*", "*/", "<", ">")):
        raise ValueError("CSS: экранирование, комментарии и HTML запрещены.")
    if re.search(r"url\s*\(|@|expression\s*\(|behavior\s*:|-moz-binding|image-set\s*\(", css, re.I):
        raise ValueError("CSS: внешние ресурсы и активное содержимое запрещены.")


class _StaticHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes = 0
        self.stack: list[str] = []
        self.style = False
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag not in _ALLOWED_TAGS:
            raise ValueError(f"HTML: тег {tag} не разрешён.")
        self.nodes += 1
        if self.nodes > 1500 or len(self.stack) > 40:
            raise ValueError("HTML: слишком сложная композиция.")
        for key, value in attrs:
            if key not in _ALLOWED_ATTRS:
                raise ValueError(f"HTML: атрибут {key} не разрешён.")
            if key == "style":
                validate_css(value or "")
            if key in {"fill", "stroke"} and "url" in (value or "").lower():
                raise ValueError("SVG: URL-ресурсы запрещены.")
        if tag not in _VOID_TAGS:
            self.stack.append(tag)
        if tag == "style":
            self.style = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS:
            return
        if not self.stack or self.stack.pop() != tag:
            raise ValueError("HTML: несбалансированные теги.")
        if tag == "style":
            self.style = False

    def handle_data(self, data):
        if self.style:
            validate_css(data)

    def handle_decl(self, decl):
        raise ValueError("HTML: декларации запрещены.")

    def handle_pi(self, data):
        raise ValueError("HTML: инструкции обработки запрещены.")


def validate_source(pages: list[str], css: str) -> None:
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES or any(not isinstance(p, str) for p in pages):
        raise ValueError("Нужны 1–3 HTML-страницы.")
    if not isinstance(css, str) or len(css) > MAX_CSS or sum(map(len, pages)) > MAX_HTML:
        raise ValueError("Превышен лимит HTML/CSS.")
    validate_css(css)
    for page in pages:
        if not page.strip():
            raise ValueError("Пустая страница.")
        parser = _StaticHTML()
        parser.feed(page)
        parser.close()
        if parser.stack:
            raise ValueError("HTML: незакрытые теги.")


_BASE_CSS = """
* {box-sizing:border-box} html,body{margin:0;width:800px;background:#fff;color:#18202a}
body{font-family:Inter,'Noto Sans',sans-serif;font-size:24px;line-height:1.4;padding:32px;overflow-wrap:anywhere}
h1,h2,h3{line-height:1.2} h1{font-size:36px} h2{font-size:30px} h3{font-size:26px}
table{width:100%;table-layout:fixed;border-collapse:collapse} th,td{padding:12px;text-align:left;border-bottom:1px solid #dce1e7}
svg{max-width:100%;height:auto} pre{white-space:pre-wrap} p{margin:0 0 16px}
"""

# Locally controlled JavaScript measures the static DOM; page-authored JS is disabled.
_DIAGNOSTICS = """() => {
 const bad=[]; const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
 while(walker.nextNode()) { const n=walker.currentNode; const e=n.parentElement;
  if(!n.textContent.trim() || e.tagName==='STYLE' || e.closest('defs')) continue;
  const s=getComputedStyle(e); const r=e.getBoundingClientRect();
  if(s.display==='none' || s.visibility==='hidden' || r.width===0 || r.height===0) {bad.push('Скрытый текст');continue}
  if(parseFloat(s.fontSize)<18) bad.push('Текст меньше 18 px');
  if(r.left<0 || r.right>801) bad.push('Текст выходит за ширину страницы');
  const range=document.createRange();range.selectNodeContents(n);
  for(const t of range.getClientRects()) if(t.left<0 || t.right>801) bad.push('Текст выходит за ширину страницы');
 }
 for(const e of document.body.querySelectorAll('*')) {const s=getComputedStyle(e);
  if(['hidden','clip','auto','scroll'].includes(s.overflowY) && e.scrollHeight>e.clientHeight+2) bad.push('Обрезанное содержимое');
  if(e.scrollWidth>e.clientWidth+2 && e.tagName!=='svg' && !e.closest('svg')) bad.push('Горизонтальное переполнение');
 }
 return {height:Math.max(document.body.scrollHeight,document.documentElement.scrollHeight),
 width:Math.max(document.body.scrollWidth,document.documentElement.scrollWidth),
 text:document.body.innerText.trim(),svg:!!document.querySelector('svg'),errors:[...new Set(bad)]};
}"""


async def render_static_pages(pages: list[str], css: str) -> dict:
    validate_source(pages, css)
    async with async_playwright() as pw:
        # Container isolation is enforced by Compose; the worker has no bot secrets,
        # no DB volumes, no external network, read-only rootfs, and bounded resources.
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            context = await browser.new_context(viewport={"width": 800, "height": 1},
                device_scale_factor=2, java_script_enabled=False, service_workers="block")
            await context.route("**/*", lambda route: route.abort())
            images, dimensions = [], []
            for index, html in enumerate(pages):
                page = await context.new_page()
                try:
                    document = ('<!doctype html><html><head><meta http-equiv="Content-Security-Policy" '
                        'content="default-src \'none\'; style-src \'unsafe-inline\'; font-src \'none\'; '
                        'img-src \'none\'; script-src \'none\'; connect-src \'none\'; base-uri \'none\'">'
                        f'<style>{_BASE_CSS}\n{css}</style></head><body>{html}</body></html>')
                    await page.set_content(document, wait_until="domcontentloaded", timeout=5000)
                    await page.evaluate("document.fonts.ready")
                    info = await page.evaluate(_DIAGNOSTICS)
                    if info["width"] > 800 or not 40 <= info["height"] <= 1200:
                        raise ValueError(f"Страница {index+1}: размер превышает 800×1200 CSS px; раздели содержимое.")
                    if info["errors"]:
                        raise ValueError(f"Страница {index+1}: " + "; ".join(info["errors"]))
                    if not info["text"] and not info["svg"]:
                        raise ValueError("Нет видимого содержимого.")
                    png = await page.screenshot(type="png", full_page=True, animations="disabled", timeout=5000)
                    if len(png) > MAX_IMAGE_BYTES:
                        raise ValueError("PNG больше 2 МБ; упрости композицию.")
                    images.append(base64.b64encode(png).decode("ascii"))
                    dimensions.append({"width": 1600, "height": info["height"] * 2, "bytes": len(png)})
                finally:
                    await page.close()
            await context.close()
            return {"pages": images, "dimensions": dimensions}
        finally:
            await browser.close()
