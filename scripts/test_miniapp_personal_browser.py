"""Browser regression for the "Моя Selara" Mini App page (subscription, quota, memory, privacy).

The backend is stubbed at the network layer with a small stateful fake; the real frontend build is served by
``vite preview``. Run after ``npm run build`` from ``frontend/``.
"""

from __future__ import annotations

import asyncio
import copy
import json
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
PREVIEW_URL = "http://127.0.0.1:4175"
WIDTHS = (320, 393, 1024)

SESSION = {
    "ok": True,
    "viewer": {
        "telegram_user_id": 77, "display_name": "User", "username": "user", "first_name": "User",
        "last_name": "", "initials": "U", "avatar_url": "",
    },
    "permissions": {"admin": False},
    "miniapp_url": "/",
}


def _overview(*, tier="free", facts=None, limit=20, memory_enabled=True, available=True, valid_days=None):
    facts = facts if facts is not None else []
    paid = tier == "paid"
    return {
        "ok": True,
        "checked_at": "2026-10-06T12:00:00+00:00",
        "subscription": {
            "state": "available" if available else "unavailable",
            "tier": tier if available else None,
            "active": paid and available,
            "owner_exempt": tier == "owner_internal",
            "valid_until": "2026-11-04T12:00:00+00:00" if paid else None,
            "days_left": valid_days, "expiring_soon": False,
            "offer_available": True, "price_stars": 69, "duration_days": 30,
            "purchase": {"command": "/premium", "bot_dm_url": "https://t.me/selara_test_bot"},
        },
        "quota": (
            {"status": "ok", "used": 3, "limit": 150 if paid else 5, "remaining": 147 if paid else 2,
             "reset_at": "2026-10-06T21:00:00+00:00", "exhausted": False}
            if available else {"status": "unavailable", "used": None, "limit": None, "remaining": None, "reset_at": None}
        ),
        "profile": {
            "memory_enabled": memory_enabled, "auto_memory_enabled": False, "auto_memory_available": paid,
            "display_name": None, "mode": "assistant",
        },
        "memory": {"count": len(facts), "limit": limit if available else None, "items": facts},
    }


def _fact(fact_id, text, pinned=False):
    return {"id": fact_id, "content": text, "pinned": pinned, "source": "explicit", "created_at": "2026-10-05T10:00:00+00:00"}


def _start_preview() -> subprocess.Popen:
    process = subprocess.Popen(
        ["npm", "run", "preview", "--", "--host", "127.0.0.1", "--port", "4175", "--strictPort"],
        cwd=FRONTEND, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("Vite preview exited before it became ready.")
        try:
            with urlopen(PREVIEW_URL, timeout=1):
                return process
        except URLError:
            time.sleep(0.1)
    process.terminate()
    raise TimeoutError("Vite preview did not start within 10 seconds.")


async def _overflow_free(page, label: str) -> None:
    fits = await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    assert fits, f"horizontal overflow: {label}"


async def _new_page(browser, width: int):
    context = await browser.new_context(viewport={"width": width, "height": 844})
    await context.add_init_script(
        """window.Telegram = {WebApp: {
          initData: 'browser-test-init-data', colorScheme: 'dark', themeParams: {},
          ready() {}, expand() {}, onEvent() {}, offEvent() {},
          BackButton: {show() {}, hide() {}, onClick() {}, offClick() {}}
        }};"""
    )
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    async def stub_sdk(route):
        await route.fulfill(status=200, content_type="application/javascript", body="")

    await page.route("https://telegram.org/**", stub_sdk)
    return context, page, errors


class FakeBackend:
    """Keeps the user's state so the page can be driven through add / pin / delete / toggle / forget."""

    def __init__(self, overview):
        self.state = overview
        self.calls: list[tuple[str, str, dict | None]] = []
        self.add_error: str | None = None

    def _sync(self):
        self.state["memory"]["count"] = len(self.state["memory"]["items"])

    async def handle(self, route):
        request = route.request
        path = request.url.split("?", 1)[0]
        method = request.method
        is_json = request.headers.get("content-type", "").startswith("application/json")
        body = json.loads(request.post_data) if request.post_data and is_json else None
        if path.endswith("/miniapp/session"):
            return await self._json(route, SESSION)
        if path.endswith("/landing/context"):
            return await self._json(route, {"ok": True, "page": {"hero_ctas": []}})
        if "/miniapp/personal" not in path:
            return await self._json(route, {"ok": False, "message": "x"}, 404)
        tail = path.split("/miniapp/personal", 1)[1]
        self.calls.append((method, tail, body))
        items = self.state["memory"]["items"]
        if method == "GET" and tail == "":
            return await self._json(route, copy.deepcopy(self.state))
        if method == "POST" and tail == "/memory":
            if self.add_error:
                return await self._json(route, {"ok": False, "message": self.add_error}, 409)
            fact = _fact(100 + len(items), body["content"])
            items.append(fact)
            self._sync()
            return await self._json(route, {"ok": True, "status": "added", "item": fact})
        if tail.startswith("/memory/") and method == "DELETE":
            fact_id = int(tail.rsplit("/", 1)[1])
            items[:] = [item for item in items if item["id"] != fact_id]
            self._sync()
            return await self._json(route, {"ok": True})
        if tail.endswith("/pin") and method == "POST":
            fact_id = int(tail.split("/")[2])
            for item in items:
                if item["id"] == fact_id:
                    item["pinned"] = body["pinned"]
            return await self._json(route, {"ok": True, "pinned": body["pinned"]})
        if tail == "/settings" and method == "PUT":
            self.state["profile"].update(body)
            return await self._json(route, {"ok": True, "profile": self.state["profile"]})
        if tail == "/forget-all" and method == "POST":
            removed = {"memories": len(items), "messages": 4, "summaries": 0, "profile": True}
            items.clear()
            self._sync()
            return await self._json(route, {"ok": True, "removed": removed})
        return await self._json(route, {"ok": False, "message": "Unexpected API call."}, 404)

    @staticmethod
    async def _json(route, body, status=200):
        await route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


async def _open(browser, width, backend):
    context, page, errors = await _new_page(browser, width)
    await page.route("**/api/**", backend.handle)
    await page.goto(f"{PREVIEW_URL}/personal", wait_until="domcontentloaded")
    await page.get_by_role("heading", name="Личный AI").wait_for(timeout=10000)
    return context, page, errors


async def _run_memory_flow(browser) -> None:
    for width in WIDTHS:
        backend = FakeBackend(_overview(facts=[_fact(1, "я веган"), _fact(2, "живу в Казани", pinned=True)]))
        context, page, errors = await _open(browser, width, backend)
        text = await page.locator("body").inner_text()
        for fragment in ("Бесплатный доступ", "Осталось 2 из 5", "Память (2 из 20)", "я веган", "живу в Казани", "Оформить в Telegram", "69 ⭐"):
            assert fragment in text, f"{width}: missing {fragment!r}"
        assert await page.locator(".selara-ai__bar").count() == 1

        await page.get_by_label("Новый факт о себе").fill("не ем орехи")
        await page.get_by_role("button", name="Запомнить").click()
        await page.get_by_text("не ем орехи").wait_for()
        await page.get_by_text("Память (3 из 20)").wait_for()
        assert await page.get_by_label("Новый факт о себе").input_value() == ""

        await page.get_by_role("button", name="Закрепить: я веган").click()
        await page.get_by_role("button", name="Открепить: я веган").wait_for()

        await page.get_by_role("button", name="Забыть: я веган").click()
        await page.get_by_text("Память (2 из 20)").wait_for()
        assert await page.get_by_text("я веган").count() == 0

        await page.get_by_label("Использовать память в разговоре").uncheck()
        await page.get_by_text("Память выключена: включите её выше").wait_for()
        assert await page.get_by_label("Новый факт о себе").is_disabled()
        await page.get_by_label("Использовать память в разговоре").check()
        await page.get_by_label("Новый факт о себе").wait_for()
        assert not await page.get_by_label("Новый факт о себе").is_disabled()
        assert any(call[0] == "PUT" and call[2] == {"memory_enabled": False} for call in backend.calls)
        await _overflow_free(page, f"memory flow@{width}")
        assert not errors, errors
        await context.close()


async def _run_limits_and_errors(browser) -> None:
    full = [_fact(i, f"факт {i}") for i in range(1, 4)]
    backend = FakeBackend(_overview(facts=full, limit=3))
    context, page, errors = await _open(browser, 393, backend)
    await page.get_by_text("Лимит исчерпан. Удалите лишний факт").wait_for()
    assert await page.get_by_label("Новый факт о себе").is_disabled()
    await context.close()

    backend = FakeBackend(_overview())
    backend.add_error = "Достигнут лимит памяти: 20 фактов."
    context, page, errors = await _open(browser, 393, backend)
    await page.get_by_label("Новый факт о себе").fill("что-то")
    await page.get_by_role("button", name="Запомнить").click()
    await page.get_by_role("alert").filter(has_text="Достигнут лимит памяти").wait_for()
    assert await page.get_by_label("Новый факт о себе").input_value() == "что-то"  # the draft is not lost
    await context.close()

    backend = FakeBackend(_overview(available=False, facts=[_fact(1, "я веган")]))
    context, page, errors = await _open(browser, 393, backend)
    text = await page.locator("body").inner_text()
    assert "Не удалось проверить" in text and "Осталось" not in text and "Бесплатный доступ" not in text
    assert "Оформить в Telegram" not in text
    assert await page.get_by_label("Новый факт о себе").is_disabled()
    assert "я веган" in text
    assert not errors, errors
    await context.close()


async def _run_paid_and_owner(browser) -> None:
    backend = FakeBackend(_overview(tier="paid", limit=200, valid_days=28))
    context, page, errors = await _open(browser, 393, backend)
    text = await page.locator("body").inner_text()
    for fragment in ("Активна до 04.11.2026", "Осталось 147 из 150", "Память (0 из 200)", "Продлить в Telegram"):
        assert fragment in text, f"paid: missing {fragment!r}"
    assert "Автоматическое запоминание работает только" not in text
    await page.get_by_label("Предлагать запоминать факты автоматически").check()
    for _ in range(50):
        if ("PUT", "/settings", {"auto_memory_enabled": True}) in backend.calls:
            break
        await asyncio.sleep(0.1)
    assert ("PUT", "/settings", {"auto_memory_enabled": True}) in backend.calls
    await context.close()

    backend = FakeBackend(_overview(tier="owner_internal", limit=200))
    context, page, errors = await _open(browser, 393, backend)
    text = await page.locator("body").inner_text()
    assert "Внутренний доступ" in text and "Оформить в Telegram" not in text and "Продлить" not in text
    await context.close()


async def _run_forget_all(browser) -> None:
    backend = FakeBackend(_overview(facts=[_fact(1, "я веган"), _fact(2, "живу в Казани")]))
    context, page, errors = await _open(browser, 393, backend)
    await page.get_by_role("button", name="Удалить все мои данные").click()
    await page.get_by_text("Удалить все личные данные Selara AI?").wait_for()
    assert not any(call[1] == "/forget-all" for call in backend.calls), "must not delete before confirmation"

    await page.get_by_role("button", name="Отмена").click()
    assert await page.get_by_text("Удалить все личные данные Selara AI?").count() == 0
    assert not any(call[1] == "/forget-all" for call in backend.calls)

    await page.get_by_role("button", name="Удалить все мои данные").click()
    await page.get_by_role("button", name="Да, удалить всё").click()
    await page.get_by_text("Всё удалено: 4 сообщ., 2 фактов.").wait_for()
    await page.get_by_text("Память (0 из 20)").wait_for()
    assert [call for call in backend.calls if call[1] == "/forget-all"] == [("POST", "/forget-all", {"confirm": True})]
    await _overflow_free(page, "forget all")
    assert not errors, errors
    await context.close()


async def _run_more_link(browser) -> None:
    backend = FakeBackend(_overview())
    context, page, errors = await _new_page(browser, 393)
    await page.route("**/api/**", backend.handle)
    await page.goto(f"{PREVIEW_URL}/more", wait_until="domcontentloaded")
    await page.get_by_role("link", name="Моя Selara").click()
    await page.get_by_role("heading", name="Личный AI").wait_for(timeout=10000)
    assert not errors, errors
    await context.close()


async def _run() -> None:
    preview = _start_preview()
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            await _run_memory_flow(browser)
            await _run_limits_and_errors(browser)
            await _run_paid_and_owner(browser)
            await _run_forget_all(browser)
            await _run_more_link(browser)
            await browser.close()
    finally:
        preview.terminate()
        preview.wait(timeout=10)


if __name__ == "__main__":
    asyncio.run(_run())
