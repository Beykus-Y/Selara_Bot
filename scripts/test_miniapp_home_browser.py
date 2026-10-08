"""Browser regression for the Mini App home and chats screens (v2 layout).

Covers the three home states (new user, member, admin), the add-bot link taken from the API, and the default
chats tab. The backend is stubbed at the network layer; the real frontend build is served by ``vite preview``.
Run after ``npm run build`` from ``frontend/``.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
PREVIEW_URL = "http://127.0.0.1:4175"
BOT_ADD_URL = "https://t.me/selara_test_bot?startgroup=true"

SESSION = {
    "ok": True,
    "viewer": {
        "telegram_user_id": 77, "display_name": "User", "username": "user", "first_name": "User",
        "last_name": "", "initials": "U", "avatar_url": "",
    },
    "permissions": {"admin": False},
    "miniapp_url": "/",
}


def _group(chat_id: int, title: str, *, is_admin: bool) -> dict:
    return {
        "chat_id": chat_id, "title": title, "meta": f"ID {chat_id}", "badge": "admin" if is_admin else "user",
        "is_admin": is_admin, "last_seen_at": "10 мин назад", "message_count": 42,
    }


def _home(*, recent=(), admins=()) -> dict:
    return {
        "ok": True,
        "page": {
            "hero_title": "User", "hero_subtitle": "", "metrics": [], "recent_groups": list(recent),
            "admin_groups": list(admins), "recent_games": [], "global_dashboard": None,
            "bot_add_url": BOT_ADD_URL, "desktop_url": "/app",
        },
    }


def _groups(*, admins=(), activity=()) -> dict:
    return {
        "ok": True,
        "page": {
            "hero_title": "Группы", "hero_subtitle": "", "admin_groups": list(admins),
            "activity_groups": list(activity), "bot_add_url": BOT_ADD_URL, "desktop_url": "/app",
        },
    }


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


class FakeBackend:
    def __init__(self, home: dict, groups: dict) -> None:
        self.home = home
        self.groups = groups

    async def handle(self, route) -> None:
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/miniapp/session"):
            return await self._json(route, SESSION)
        if path.endswith("/landing/context"):
            return await self._json(route, {"ok": True, "page": {"hero_ctas": []}})
        if path.endswith("/miniapp/home"):
            return await self._json(route, self.home)
        if path.endswith("/miniapp/groups"):
            return await self._json(route, self.groups)
        return await self._json(route, {"ok": False, "message": "Unexpected API call."}, 404)

    @staticmethod
    async def _json(route, body, status=200) -> None:
        await route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


async def _open(browser, width: int, backend: FakeBackend, path: str):
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
    await page.route("**/api/**", backend.handle)
    await page.goto(f"{PREVIEW_URL}{path}", wait_until="domcontentloaded")
    return context, page, errors


async def _run_new_user(browser) -> None:
    backend = FakeBackend(_home(), _groups())
    for width in (320, 393):
        context, page, errors = await _open(browser, width, backend, "/")
        await expect(page.get_by_role("heading", name="Selara в вашем чате")).to_be_visible(timeout=10000)
        await expect(page.get_by_role("link", name="Добавить бота в группу")).to_have_attribute("href", BOT_ADD_URL)
        await _overflow_free(page, f"home new user {width}")
        assert not errors, errors
        await context.close()


async def _run_member(browser) -> None:
    group = _group(-1001, "Selara Hub", is_admin=False)
    backend = FakeBackend(_home(recent=[group]), _groups(activity=[group]))
    context, page, errors = await _open(browser, 393, backend, "/")
    await expect(page.get_by_role("heading", name="User, с возвращением")).to_be_visible(timeout=10000)
    text = await page.locator("body").inner_text()
    assert "Ваши чаты" in text and "Selara Hub" in text, text
    assert "Управление" not in text and "Аудит действий" not in text, "member must not see admin links"
    assert "Ваш прогресс" not in text, "progress block must not render without real metrics"
    await _overflow_free(page, "home member")
    assert not errors, errors
    await context.close()


async def _run_admin(browser) -> None:
    group = _group(-1001, "Selara Hub", is_admin=True)
    backend = FakeBackend(_home(recent=[group], admins=[group]), _groups(admins=[group], activity=[group]))
    context, page, errors = await _open(browser, 393, backend, "/")
    await expect(page.get_by_role("heading", name="User, с возвращением")).to_be_visible(timeout=10000)
    await expect(page.get_by_role("link", name="Аудит действий")).to_be_visible()
    await expect(page.get_by_role("link", name="Лидерборд")).to_be_visible()
    text = await page.locator("body").inner_text()
    assert "Управление" in text, text
    assert "Настройки группы" not in text, "settings CTA must stay removed until a settings screen exists"
    await _overflow_free(page, "home admin")
    assert not errors, errors
    await context.close()


async def _run_chats_default_tab(browser) -> None:
    member_group = _group(-1002, "Участник чат", is_admin=False)
    backend = FakeBackend(_home(), _groups(activity=[member_group]))
    context, page, errors = await _open(browser, 393, backend, "/groups")
    await expect(page.get_by_role("tab", name="Участвую")).to_have_attribute("aria-selected", "true", timeout=10000)
    await expect(page.get_by_role("tab", name="Управляю")).to_have_attribute("aria-selected", "false")
    await expect(page.get_by_role("link", name="Участник чат")).to_be_visible()
    await _overflow_free(page, "chats member")
    assert not errors, errors
    await context.close()

    admin_group = _group(-1001, "Selara Hub", is_admin=True)
    backend = FakeBackend(_home(), _groups(admins=[admin_group], activity=[admin_group]))
    context, page, errors = await _open(browser, 393, backend, "/groups")
    await expect(page.get_by_role("tab", name="Управляю")).to_have_attribute("aria-selected", "true", timeout=10000)
    await expect(page.get_by_role("link", name="Selara Hub")).to_be_visible()
    assert not errors, errors
    await context.close()


async def _run() -> None:
    preview = _start_preview()
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            await _run_new_user(browser)
            await _run_member(browser)
            await _run_admin(browser)
            await _run_chats_default_tab(browser)
            await browser.close()
    finally:
        preview.terminate()
        preview.wait(timeout=10)


if __name__ == "__main__":
    asyncio.run(_run())
