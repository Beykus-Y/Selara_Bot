"""Browser regression for the Mini App «Моя Selara» page (profile, memory, subscription).

The backend is stubbed at the network layer with a small in-memory model; the real frontend build is served by
``vite preview``. Run after ``npm run build`` from ``frontend/``.
"""

from __future__ import annotations

import asyncio
import copy
import json

from playwright.async_api import async_playwright

from test_miniapp_selara_ai_browser import (
    PREVIEW_URL,
    SESSION,
    WIDTHS,
    _new_page,
    _overflow_free,
    _start_preview,
)

PROFILE = {
    "display_name": "Selara", "character_preset": "assistant", "character_title": "Спокойный помощник",
    "character_custom": None, "address_form": None, "formality": "ty", "reply_length": "medium",
    "emoji_enabled": True, "mode": "assistant", "memory_enabled": True, "auto_memory_enabled": False, "revision": 0,
}
OPTIONS = {
    "presets": [
        {"key": "assistant", "title": "Спокойный помощник"}, {"key": "sarcastic", "title": "Саркастичный"},
        {"key": "custom", "title": "Свой вариант"},
    ],
    "max_display_name": 32, "max_custom_character": 500, "max_address": 64,
}
LONG_FACT = "Очень длинный факт без пробелов " + "слово" * 40


class Backend:
    def __init__(self, *, tier="free", limit=3, facts=None, conflict_once=False, auto_available=False):
        self.tier = tier
        self.limit = limit
        self.profile = copy.deepcopy(PROFILE)
        self.facts = facts if facts is not None else [
            {"id": 1, "content": "Я веган", "source": "explicit", "pinned": False, "created_at": None, "last_used_at": None},
            {"id": 2, "content": LONG_FACT, "source": "extracted", "pinned": True, "created_at": None, "last_used_at": None},
        ]
        self.next_id = 10
        self.conflict_once = conflict_once
        self.auto_available = auto_available
        self.requests: list[tuple[str, str, object]] = []

    def overview(self):
        paid = self.tier == "paid"
        return {
            "ok": True, "profile": self.profile, "options": OPTIONS,
            "subscription": {
                "available": True, "tier": self.tier,
                "valid_until": "2026-11-04T12:00:00+00:00" if paid else None,
                "quota": {"limit": 150 if paid else 5, "used": 10 if paid else 5, "remaining": 140 if paid else 0,
                          "reset_at": None, "unlimited": False},
                "price_stars": 69, "duration_days": 30, "offer_available": True, "ai_available": True,
                "purchase_command": "/premium", "bot_url": "https://t.me/selara_test_bot",
            },
            "memory": {"count": len(self.facts), "limit": self.limit, "max_length": 300,
                       "auto_extract_enabled_by_admin": self.auto_available, "auto_extract_available": self.auto_available},
        }

    def memories(self):
        return {"ok": True, "items": self.facts, "count": len(self.facts), "limit": self.limit, "max_length": 300,
                "memory_enabled": self.profile["memory_enabled"]}

    async def handle(self, route):
        request = route.request
        path = request.url.split("?", 1)[0]
        method = request.method
        # The session call posts a urlencoded form; only the personal API sends JSON.
        body = json.loads(request.post_data) if request.post_data and "/personal" in path else None
        status, payload = 200, None
        if path.endswith("/miniapp/session"):
            payload = SESSION
        elif path.endswith("/landing/context"):
            payload = {"ok": True, "page": {"hero_ctas": []}}
        elif path.endswith("/miniapp/personal") and method == "GET":
            payload = self.overview()
        elif path.endswith("/personal/memories") and method == "GET":
            payload = self.memories()
        elif path.endswith("/personal/memories") and method == "POST":
            self.requests.append((method, "memories", body))
            if len(self.facts) >= self.limit:
                status, payload = 409, {"ok": False, "code": "limit_reached", "message": "Достигнут лимит памяти: 3 факта."}
            elif any(f["content"].casefold() == body["content"].casefold() for f in self.facts):
                status, payload = 409, {"ok": False, "code": "duplicate", "message": "Это я уже помню."}
            else:
                item = {"id": self.next_id, "content": body["content"], "source": "explicit", "pinned": False,
                        "created_at": None, "last_used_at": None}
                self.next_id += 1
                self.facts.append(item)
                status, payload = 201, {"ok": True, "item": item, "count": len(self.facts)}
        elif "/personal/memories/" in path and method == "PATCH":
            self.requests.append((method, path.rsplit("/", 1)[1], body))
            fact = next(f for f in self.facts if str(f["id"]) == path.rsplit("/", 1)[1])
            fact["pinned"] = body["pinned"]
            payload = {"ok": True, "id": fact["id"], "pinned": fact["pinned"]}
        elif "/personal/memories/" in path and method == "DELETE":
            self.requests.append((method, path.rsplit("/", 1)[1], None))
            self.facts = [f for f in self.facts if str(f["id"]) != path.rsplit("/", 1)[1]]
            payload = {"ok": True, "count": len(self.facts)}
        elif path.endswith("/personal/profile") and method == "PATCH":
            self.requests.append((method, "profile", body))
            if self.conflict_once:
                self.conflict_once = False
                self.profile = {**self.profile, "display_name": "Из другого окна", "revision": self.profile["revision"] + 1}
                status, payload = 409, {"ok": False, "code": "revision_conflict", "message": "Настройки уже изменились.",
                                        "profile": self.profile}
            elif body["revision"] != self.profile["revision"]:
                status, payload = 409, {"ok": False, "code": "revision_conflict", "message": "stale", "profile": self.profile}
            else:
                changes = {k: v for k, v in body.items() if k != "revision"}
                if "address_form" in changes and changes["address_form"] == "":
                    changes["address_form"] = None
                self.profile = {**self.profile, **changes, "revision": self.profile["revision"] + 1}
                payload = {"ok": True, "profile": self.profile}
        else:
            status, payload = 404, {"ok": False, "message": "Unexpected API call."}
        await route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


async def _open(browser, width, backend):
    context, page, errors = await _new_page(browser, width)
    await page.route("**/api/**", backend.handle)
    await page.goto(f"{PREVIEW_URL}/personal", wait_until="domcontentloaded")
    await page.get_by_role("heading", name="Моя Selara").wait_for(timeout=10000)
    await page.get_by_text("Я веган").first.wait_for(timeout=10000)
    return context, page, errors


async def _run_layouts(browser) -> None:
    for width in WIDTHS:
        for tier, expected in (
            ("free", ["Бесплатный доступ", "Лимит на сегодня исчерпан: 5 из 5", "Оформить Selara Personal"]),
            ("paid", ["Selara Personal до 04.11.2026", "Сегодня осталось 140 из 150", "Продлить подписку"]),
        ):
            backend = Backend(tier=tier)
            context, page, errors = await _open(browser, width, backend)
            text = await page.locator("body").inner_text()
            for fragment in expected:
                assert fragment in text, f"{tier}@{width}: missing {fragment!r}"
            # Section titles are upper-cased by the theme, so compare the counter case-insensitively.
            assert "2 из 3" in text.lower(), f"{tier}@{width}: missing memory counter"
            if tier == "paid":
                assert "Оформить Selara Personal" not in text
            # Auto memory needs a paid plan and the admin switch: disabled here in both scenarios.
            assert await page.get_by_label("Авто-запоминание (только Selara Personal)").is_disabled()
            await _overflow_free(page, f"personal {tier}@{width}")
            assert not errors, errors
            await context.close()


async def _run_memory(browser) -> None:
    backend = Backend(limit=3)
    context, page, errors = await _open(browser, 360, backend)

    await page.get_by_label("Новый факт", exact=False).fill("Живу в Казани")
    await page.get_by_role("button", name="Запомнить").click()
    await page.get_by_text("Живу в Казани").first.wait_for()
    assert backend.requests[-1] == ("POST", "memories", {"content": "Живу в Казани"})
    # The list is full now: the form is locked and says why.
    await page.get_by_text("Лимит памяти достигнут").wait_for()
    assert await page.get_by_role("button", name="Запомнить").is_disabled()

    pin_button = page.locator("li", has_text="Я веган").get_by_role("button", name="Закрепить")
    await pin_button.click()
    await page.locator("li.is-pinned", has_text="Я веган").wait_for()
    assert backend.requests[-1] == ("PATCH", "1", {"pinned": True})

    # Deleting asks for confirmation first.
    await page.locator("li", has_text="Я веган").get_by_role("button", name="Удалить").click()
    assert not any(request[0] == "DELETE" for request in backend.requests)
    await page.locator("li", has_text="Я веган").get_by_role("button", name="Да, забыть").click()
    await page.get_by_text("Забыла.").wait_for()
    assert [f["content"] for f in backend.facts if f["content"] == "Я веган"] == []
    assert await page.locator("li", has_text="Я веган").count() == 0

    await _overflow_free(page, "personal memory")
    assert not errors, errors
    await context.close()


async def _run_duplicate_error(browser) -> None:
    backend = Backend(limit=10)
    context, page, errors = await _open(browser, 360, backend)
    await page.get_by_label("Новый факт", exact=False).fill("я ВЕГАН")
    await page.get_by_role("button", name="Запомнить").click()
    await page.get_by_role("alert").filter(has_text="Это я уже помню.").wait_for()
    assert len(backend.facts) == 2
    assert not errors, errors
    await context.close()


async def _run_profile(browser) -> None:
    backend = Backend()
    context, page, errors = await _open(browser, 393, backend)

    save = page.get_by_role("button", name="Сохранить")
    assert await save.is_disabled()
    await page.get_by_label("Имя собеседника").fill("Мира")
    await page.get_by_label("Характер", exact=True).select_option("custom")
    await page.get_by_label("Свой характер", exact=False).fill("Говорит как пират")
    await page.get_by_role("button", name="На «вы»").click()
    await save.click()
    await page.get_by_text("Настройки сохранены.").wait_for()
    method, target, body = backend.requests[-1]
    assert (method, target) == ("PATCH", "profile")
    assert body == {"revision": 0, "display_name": "Мира", "character_preset": "custom",
                    "character_custom": "Говорит как пират", "formality": "vy"}, body
    assert backend.profile["revision"] == 1
    assert await page.get_by_role("button", name="Сохранить").is_disabled()
    assert await page.get_by_label("Имя собеседника").input_value() == "Мира"
    await _overflow_free(page, "personal profile")
    assert not errors, errors
    await context.close()


async def _run_conflict(browser) -> None:
    backend = Backend(conflict_once=True)
    context, page, errors = await _open(browser, 360, backend)
    await page.get_by_label("Имя собеседника").fill("Моё имя")
    await page.get_by_role("button", name="Сохранить").click()
    await page.get_by_text("Настройки уже изменились в другом месте").wait_for()
    # The form now shows the server copy, not the discarded draft.
    assert await page.get_by_label("Имя собеседника").input_value() == "Из другого окна"
    assert not errors, errors
    await context.close()


async def _run_auto_memory(browser) -> None:
    backend = Backend(tier="paid", auto_available=True)
    context, page, errors = await _open(browser, 360, backend)
    checkbox = page.get_by_label("Авто-запоминание (только Selara Personal)")
    assert await checkbox.is_enabled()
    await checkbox.check()
    await page.get_by_role("button", name="Сохранить").click()
    await page.get_by_text("Настройки сохранены.").wait_for()
    assert backend.requests[-1][2] == {"revision": 0, "auto_memory_enabled": True}
    assert not errors, errors
    await context.close()


async def _run_more_link(browser) -> None:
    backend = Backend()
    context, page, errors = await _new_page(browser, 360)
    await page.route("**/api/**", backend.handle)
    await page.goto(f"{PREVIEW_URL}/more", wait_until="domcontentloaded")
    await page.get_by_role("link", name="Моя Selara").click()
    await page.get_by_role("heading", name="Моя Selara").wait_for(timeout=10000)
    assert page.url.endswith("/personal")
    assert not errors, errors
    await context.close()


async def _run() -> None:
    preview = _start_preview()
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            await _run_layouts(browser)
            await _run_memory(browser)
            await _run_duplicate_error(browser)
            await _run_profile(browser)
            await _run_conflict(browser)
            await _run_auto_memory(browser)
            await _run_more_link(browser)
            await browser.close()
    finally:
        preview.terminate()
        preview.wait(timeout=10)


if __name__ == "__main__":
    asyncio.run(_run())
