from __future__ import annotations

import asyncio
import json
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen
from pathlib import Path

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
PREVIEW_URL = "http://127.0.0.1:4173"


def _start_preview() -> subprocess.Popen:
    process = subprocess.Popen(
        ["npm", "run", "preview", "--", "--host", "127.0.0.1", "--port", "4173", "--strictPort"],
        cwd=FRONTEND,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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


async def _run_browser_regression() -> None:
    preview_process = _start_preview()
    preview_requests: list[str] = []
    send_requests: list[str] = []
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 390, "height": 844})
            await context.add_init_script(
                """window.Telegram = {WebApp: {
                  initData: 'browser-test-init-data', colorScheme: 'dark', themeParams: {},
                  ready() {}, expand() {}, onEvent() {}, offEvent() {},
                  BackButton: {show() {}, hide() {}, onClick() {}, offClick() {}}
                }};"""
            )
            page = await context.new_page()

            async def handle_api(route):
                request = route.request
                path = request.url.split("?", 1)[0]
                if path.endswith("/landing/context"):
                    payload = {"ok": True, "page": {"hero_ctas": []}}
                elif path.endswith("/miniapp/session"):
                    payload = {
                        "ok": True,
                        "viewer": {
                            "telegram_user_id": 77,
                            "display_name": "Admin",
                            "username": "owner",
                            "first_name": "Admin",
                            "last_name": "",
                            "initials": "A",
                            "avatar_url": "",
                        },
                        "permissions": {"admin": True},
                        "miniapp_url": "/",
                    }
                elif path.endswith("/miniapp/admin/broadcasts"):
                    payload = {"ok": True, "items": [], "next_cursor": None}
                elif path.endswith("/miniapp/admin/broadcast/preview"):
                    preview_requests.append(request.post_data or "")
                    payload = {
                        "ok": True,
                        "body": "Test announcement",
                        "rendered_text": "Test announcement",
                        "reaction_options": [],
                        "active_since_days": 3,
                        "target_count": 1,
                        "targets": [{"chat_id": -1001, "title": "Test group", "last_activity_at": None}],
                        "targets_truncated": False,
                        "media": None,
                        "preview_token": "browser-preview-token",
                    }
                elif path.endswith("/miniapp/admin/broadcast"):
                    send_requests.append(request.post_data or "")
                    payload = {"ok": True, "broadcast_id": 1, "status": "sending", "target_count": 1}
                else:
                    await route.fulfill(
                        status=404,
                        content_type="application/json",
                        body=json.dumps({"ok": False, "message": "Unexpected API call."}),
                    )
                    return
                await route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

            await page.route("**/api/**", handle_api)
            await page.goto(f"{PREVIEW_URL}/admin/broadcast", wait_until="domcontentloaded")
            await page.get_by_label("Текст Telegram-сообщения").fill("Test announcement")
            await page.get_by_role("button", name="Далее: аудитория").click()
            await page.get_by_role("button", name="Выбрать группы").click()
            await page.get_by_role("button", name="Загрузить список групп").click()
            checkbox = page.locator(".admin-broadcast-targets input[type=checkbox]")
            await checkbox.wait_for(state="visible")
            await checkbox.check()
            await checkbox.uncheck()

            continue_button = page.get_by_role("button", name="Выберите хотя бы одну группу")
            assert await continue_button.is_disabled()
            assert len(preview_requests) == 1
            assert send_requests == []
            assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")

            await context.close()
            await browser.close()
    finally:
        preview_process.terminate()
        preview_process.wait(timeout=10)


if __name__ == "__main__":
    asyncio.run(_run_browser_regression())
