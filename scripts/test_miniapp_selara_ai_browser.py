"""Mobile/desktop browser regression for the Selara AI chat panel and admin AI/Stars page.

The backend is stubbed at the network layer; the real frontend build is served by
``vite preview``. Run after ``npm run build`` from ``frontend/``.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
PREVIEW_URL = "http://127.0.0.1:4174"
WIDTHS = (320, 360, 393, 1024)


def _start_preview() -> subprocess.Popen:
    process = subprocess.Popen(
        ["npm", "run", "preview", "--", "--host", "127.0.0.1", "--port", "4174", "--strictPort"],
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


def _quota(used, limit, period_end="2026-10-06T21:00:00+00:00"):
    return {
        "status": "ok", "used": used, "limit": limit, "remaining": limit - used,
        "reset_at": period_end, "exhausted": used >= limit,
    }


UNLIMITED = {"status": "unlimited", "used": None, "limit": None, "remaining": None, "reset_at": None, "exhausted": False}
UNAVAILABLE = {"status": "unavailable", "used": None, "limit": None, "remaining": None, "reset_at": None}


def _ai_access(tier="free", *, auto_state="requires_access", enabled=False, llm=None, manual=None, **extra):
    base = {
        "ok": True, "chat_id": -1001, "state": "available", "tier": tier, "checked_at": "2026-10-05T12:00:00+00:00",
        "timezone": "Europe/Moscow", "can_manage_purchase": True, "checkout_configured": True,
        "purchase": {"command": "/premium", "bot_dm_url": "https://t.me/selara_test_bot"},
        "entitlement": {
            "active": tier == "paid", "valid_until": "2026-11-04T12:00:00+00:00" if tier == "paid" else None,
            "expired_at": None, "expiring_soon": False, "days_left": 30 if tier == "paid" else None, "source": None,
        },
        "llm": llm or _quota(3, 10),
        "manual_summary": manual or _quota(2, 10, "2026-10-31T21:00:00+00:00"),
        "automatic_summary": {"enabled": enabled, "access_allowed": tier != "free", "state": auto_state},
    }
    base.update(extra)
    return base


SESSION = {
    "ok": True,
    "viewer": {
        "telegram_user_id": 77, "display_name": "Admin", "username": "owner", "first_name": "Admin",
        "last_name": "", "initials": "A", "avatar_url": "",
    },
    "permissions": {"admin": True},
    "miniapp_url": "/",
}

CHAT_PAGE = {
    "ok": True,
    "page": {
        "chat_id": -1001,
        "chat_title": "A very long chat title that keeps going to check that nothing overflows on narrow phones",
        "hero_subtitle": "subtitle",
        "metrics": [],
        "summary": {"participants_count": 3, "total_messages": 10, "last_activity_at": "now"},
        "daily_activity": [],
        "hero_of_day": None,
        "richest_of_day": None,
        "dashboard_panels": [],
        "leaderboards": [],
        "desktop_url": "/app/chat/-1001",
    },
}

LEADERBOARD = {
    "ok": True, "mode": "mix", "query": "", "page": 1, "page_size": 10, "total_rows": 0, "total_pages": 1,
    "my_rank": None, "truncated": False, "rows": [],
}


def _payment(payment_id, *, state="applied", refund=None, amount=100, reason=None):
    return {
        "id": payment_id, "payment_at": "2026-10-04T10:00:00+00:00", "buyer_user_id": 555,
        "source_chat_id": -1001, "target_chat_id": -1001, "chat_id": -1001,
        "chat_title": "Очень длинное название чата для проверки переполнения " * 2,
        "amount_stars": amount, "currency": "XTR", "state": state, "reason": reason,
        "product_key": "selara_ai_monthly", "refund": refund,
    }


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


def _chat_handler(payload):
    async def handle(route):
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/miniapp/session"):
            body = SESSION
        elif path.endswith("/landing/context"):
            body = {"ok": True, "page": {"hero_ctas": []}}
        elif path.endswith("/ai-access"):
            body = payload
        elif path.endswith("/leaderboard"):
            body = LEADERBOARD
        elif path.endswith("/miniapp/chat/-1001"):
            body = CHAT_PAGE
        else:
            await route.fulfill(status=404, content_type="application/json", body='{"ok": false, "message": "x"}')
            return
        await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    return handle


async def _run_chat_scenarios(browser) -> None:
    for width in WIDTHS:
        scenarios = [
            ("free", _ai_access("free", enabled=True, auto_state="requires_access_enabled"),
             ["Бесплатный доступ", "Осталось 7 из 10", "Включены в настройках, но требуют Selara AI", "Получить Selara AI"]),
            ("paid", _ai_access("paid", auto_state="available_disabled"),
             ["Активна до 04.11.2026", "Доступны, но выключены в настройках", "Продлить"]),
            ("paid-active", _ai_access("paid", enabled=True, auto_state="active"), ["Активны"]),
            ("owner", _ai_access("owner_internal", auto_state="active", llm=UNLIMITED, manual=UNLIMITED),
             ["Внутренний доступ", "Без коммерческого лимита"]),
            ("exhausted", _ai_access("free", llm=_quota(10, 10)), ["Лимит исчерпан: 10 из 10 сегодня"]),
            ("expired", _ai_access(
                "free",
                entitlement={"active": False, "valid_until": None, "expired_at": "2026-10-04T12:00:00+00:00",
                             "expiring_soon": False, "days_left": None, "source": None},
             ), ["Selara AI закончилась 04.10.2026", "Продлить"]),
            ("unavailable", {**_ai_access("free"), "state": "unavailable", "tier": None, "entitlement": None,
                             "llm": UNAVAILABLE, "manual_summary": UNAVAILABLE,
                             "automatic_summary": {"enabled": False, "access_allowed": None, "state": "unknown"}},
             ["Статус временно не удалось проверить"]),
        ]
        for name, payload, expected in scenarios:
            context, page, errors = await _new_page(browser, width)

            await page.route("**/api/**", _chat_handler(payload))
            await page.goto(f"{PREVIEW_URL}/chat/-1001", wait_until="domcontentloaded")
            panel = page.locator(".selara-ai")
            await panel.wait_for(timeout=8000)
            text = await panel.inner_text()
            for fragment in expected:
                assert fragment in text, f"{name}@{width}: missing {fragment!r} in {text!r}"
            if name == "owner":
                assert "Получить Selara AI" not in text and "Продлить" not in text
            if name == "unavailable":
                assert "0 из 10" not in text and "Осталось" not in text
            assert await page.locator(".selara-ai__bar").count() == (0 if name in {"owner", "unavailable"} else 2)
            await _overflow_free(page, f"chat {name}@{width}")
            assert not errors, errors
            await context.close()


async def _run_chat_retry(browser) -> None:
    context, page, errors = await _new_page(browser, 390)
    calls = {"ai": 0}

    async def handle(route):
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/miniapp/session"):
            body, status = SESSION, 200
        elif path.endswith("/landing/context"):
            body, status = {"ok": True, "page": {"hero_ctas": []}}, 200
        elif path.endswith("/ai-access"):
            calls["ai"] += 1
            body, status = (
                ({"ok": False, "message": "Не удалось загрузить статус Selara AI."}, 503)
                if calls["ai"] <= 2 else (_ai_access("free"), 200)
            )
        elif path.endswith("/leaderboard"):
            body, status = LEADERBOARD, 200
        elif path.endswith("/miniapp/chat/-1001"):
            body, status = CHAT_PAGE, 200
        else:
            body, status = {"ok": False, "message": "x"}, 404
        await route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    await page.route("**/api/**", handle)
    await page.goto(f"{PREVIEW_URL}/chat/-1001", wait_until="domcontentloaded")
    # The rest of the page renders even though the AI status failed (retry:1 delays the error).
    await page.get_by_role("alert").wait_for(timeout=10000)
    assert await page.get_by_role("button", name="Повторить").count() == 1
    await page.get_by_role("button", name="Повторить").click()
    await page.get_by_text("Бесплатный доступ").wait_for()
    assert not errors, errors
    await context.close()


def _ai_summary(invocations, *, unknown=0, cost="0.0184", calls=None):
    return {
        "ok": True, "period_days": 30, "window_from": "x", "window_to": "y", "invocations": invocations,
        "provider_calls": calls if calls is not None else invocations, "unsuccessful_invocations": 1 if invocations else 0,
        "unknown_cost_calls": unknown, "known_cost_usd": cost,
        "average_known_cost_per_invocation_usd": "0.0001" if invocations else None,
        "daily": [
            {"date": "2026-10-03", "invocations": 1, "provider_calls": 1, "known_cost_usd": "0.001"},
            {"date": "2026-10-04", "invocations": 2, "provider_calls": 2, "known_cost_usd": "0.002"},
        ] if invocations else [],
    }


def _monetization(*, empty=False):
    return {
        "ok": True, "period_days": 30, "currency": "XTR",
        "successful_payments": 0 if empty else 3, "stars_revenue": 0 if empty else 1240,
        "rejected_payments": 0 if empty else 1, "refunds": {"pending": 0 if empty else 1, "refunded": 0, "failed": 0},
        "all_time": {"successful_payments": 0 if empty else 3, "stars_revenue": 0 if empty else 1240},
        "active_paid_chats": 0 if empty else 2, "expiring_within_7_days": 0 if empty else 1,
        "daily": [] if empty else [
            {"date": "2026-10-03", "payments": 1, "stars": 400}, {"date": "2026-10-04", "payments": 2, "stars": 840},
        ],
        "checkout": {"configured": True, "price_stars": 400},
    }


BREAKDOWN = {
    "ok": True, "period_days": 30, "unattributed_provider_calls": 1,
    "features": [
        {"feature": "llm_admin", "invocations": 5, "unsuccessful_invocations": 1, "provider_calls": 6,
         "known_cost_usd": "0.0150", "unknown_cost_calls": 1},
        {"feature": "brand_new_feature", "invocations": 1, "unsuccessful_invocations": 0, "provider_calls": 1,
         "known_cost_usd": "0.0034", "unknown_cost_calls": 0},
    ],
    "models": [{"model": "a-very-long-model-name-" * 3, "provider_calls": 7, "prompt_tokens": 12000,
                "completion_tokens": 900, "known_cost_usd": "0.0184", "unknown_cost_calls": 1}],
    "stages": [{"feature": "daily_summary", "stage": "writer", "provider_calls": 2, "known_cost_usd": "0.01"}],
}

READINESS = {
    "ok": True, "checkout": {"configured": True, "price_stars": 400, "product_key": "selara_ai_monthly"},
    "checks": [
        {"key": "llm_provider", "label": "AI-провайдер", "status": "ok", "detail": "Настроен."},
        {"key": "stars_price", "label": "Цена Stars", "status": "missing",
         "detail": "Checkout выключен: SELARA_AI_PRICE_STARS не настроен."},
    ],
}

ENTITLEMENTS = {
    "ok": True, "active_paid_chats": 2, "expiring_within_7_days": 1,
    "items": [
        {"chat_id": -1002, "chat_title": "Скоро закончится", "product_key": "selara_ai_monthly",
         "valid_until": "2026-10-08T12:00:00+00:00", "days_left": 3, "expiring_soon": True,
         "last_purchase_at": "2026-09-08T12:00:00+00:00"},
        {"chat_id": -1001, "chat_title": "Надолго", "product_key": "selara_ai_monthly",
         "valid_until": "2026-11-08T12:00:00+00:00", "days_left": 30, "expiring_soon": False,
         "last_purchase_at": None},
    ],
}


async def _run_admin_scenarios(browser) -> None:
    for width in WIDTHS:
        context, page, errors = await _new_page(browser, width)
        state = {"ai_fail": False, "ai_calls": 0, "periods": [], "payment_calls": []}

        async def handle(route):
            request = route.request
            path = request.url.split("?", 1)[0]
            query = request.url.split("?", 1)[1] if "?" in request.url else ""
            if path.endswith("/miniapp/session"):
                body, status = SESSION, 200
            elif path.endswith("/landing/context"):
                body, status = {"ok": True, "page": {"hero_ctas": []}}, 200
            elif path.endswith("/admin/ai/summary"):
                state["ai_calls"] += 1
                state["periods"].append(query)
                if state["ai_fail"]:
                    body, status = {"ok": False, "message": "AI backend down"}, 503
                elif "period_days=1&" in query + "&":
                    await asyncio.sleep(1.2)  # a slow, stale response must not overwrite the newest one
                    body, status = _ai_summary(111), 200
                elif "period_days=7&" in query + "&":
                    body, status = _ai_summary(777, unknown=2), 200
                else:
                    body, status = _ai_summary(333, unknown=2), 200
            elif path.endswith("/admin/ai/breakdown"):
                body, status = BREAKDOWN, 200
            elif path.endswith("/admin/ai/readiness"):
                body, status = READINESS, 200
            elif path.endswith("/admin/monetization/summary"):
                body, status = _monetization(), 200
            elif path.endswith("/admin/monetization/entitlements"):
                body, status = ENTITLEMENTS, 200
            elif path.endswith("/admin/monetization/payments"):
                state["payment_calls"].append(query)
                if "cursor=c1" in query:
                    body, status = {"ok": True, "items": [_payment(1)], "next_cursor": None}, 200
                else:
                    body, status = {"ok": True, "items": [
                        _payment(3, state="rejected", amount=40, reason="chat_mismatch",
                                 refund={"status": "pending", "requested_at": None, "completed_at": None, "result_code": None}),
                        _payment(2),
                    ], "next_cursor": "c1"}, 200
            elif "/admin/monetization/payments/" in path:
                body, status = {"ok": True, **_payment(3, state="rejected", amount=40, reason="chat_mismatch"),
                                "telegram_payment_charge_id": "charge-xyz",
                                "refund_command": "/stars_refund 3", "intent": None, "entitlement": None}, 200
            else:
                body, status = {"ok": False, "message": "Unexpected API call."}, 404
            await route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

        await page.route("**/api/**", handle)
        await page.goto(f"{PREVIEW_URL}/admin/ai", wait_until="domcontentloaded")
        await page.get_by_text("Известная стоимость", exact=True).first.wait_for(timeout=10000)
        await page.get_by_text("Надолго").wait_for(timeout=10000)
        await page.get_by_role("button", name="Платёж 2, применён, 100 Stars").wait_for(timeout=10000)
        await page.get_by_text("Checkout выключен: SELARA_AI_PRICE_STARS не настроен.").wait_for(timeout=10000)
        body_text = await page.locator("body").inner_text()
        assert "Есть 2 вызовов с неизвестной стоимостью; фактический расход выше или равен указанному." in body_text
        assert "Средняя известная часть стоимости" in body_text
        assert "$0.0184" in body_text
        assert "brand_new_feature" in body_text and "? / ??" in body_text  # unknown features are not dropped
        assert "Выручка в Stars и расходы AI в USD" in body_text
        assert "Исход возврата требует ручной проверки." in body_text
        assert "истекают в ближайшие 7 дней" in body_text.lower()
        assert "Checkout выключен: SELARA_AI_PRICE_STARS не настроен." in body_text
        assert "charge-xyz" not in body_text  # charge id only in the detail view
        await _overflow_free(page, f"admin ai loaded@{width}")

        # Payment history: expand detail, then paginate.
        await page.get_by_role("button", name="Платёж 3, отклонён, 40 Stars").click()
        try:
            await page.get_by_text("charge-xyz").wait_for(timeout=5000)
        except Exception:
            print(await page.locator(".admin-payment").first.inner_text())
            raise
        assert "/stars_refund 3" in await page.locator("body").inner_text()
        await page.get_by_role("button", name="Показать ещё").click()
        await page.get_by_role("button", name="Платёж 1, применён, 100 Stars").wait_for()
        assert any("cursor=c1" in call for call in state["payment_calls"])
        assert await page.get_by_role("button", name="Показать ещё").count() == 0
        await _overflow_free(page, f"admin payments@{width}")

        # Rapid 1 -> 7 -> 30: the slow 24h response arrives last and must not win.
        period = page.get_by_role("group", name="Период аналитики")
        await period.get_by_role("button", name="24 ч").click()
        await period.get_by_role("button", name="7 дн").click()
        await period.get_by_role("button", name="30 дн").click()
        await page.wait_for_timeout(1800)
        text = await page.locator(".admin-ai").inner_text()
        assert "333" in text and "111" not in text

        # A failed refresh keeps the previous data and offers retry; payments keep rendering.
        state["ai_fail"] = True
        await period.get_by_role("button", name="90 дн").click()
        await page.get_by_text("AI backend down").wait_for(timeout=10000)
        assert "Платёж 1" in await page.locator(".admin-ai").inner_text() or await page.get_by_role("button", name="Платёж 1, применён, 100 Stars").count() == 1
        assert "1 240 ⭐" in (await page.locator("body").inner_text()).replace("\xa0", " ").replace("\u202f", " ")  # Stars analytics unaffected
        await page.get_by_role("button", name="Повторить").first.wait_for()
        assert not errors, errors
        await context.close()


async def _run_admin_empty_and_dashboard(browser) -> None:
    context, page, errors = await _new_page(browser, 360)

    async def handle(route):
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/miniapp/session"):
            body, status = SESSION, 200
        elif path.endswith("/landing/context"):
            body, status = {"ok": True, "page": {"hero_ctas": []}}, 200
        elif path.endswith("/admin/ai/summary"):
            body, status = _ai_summary(0, cost="0"), 200
        elif path.endswith("/admin/ai/breakdown"):
            body, status = {"ok": True, "period_days": 30, "features": [], "models": [], "stages": [],
                            "unattributed_provider_calls": 0}, 200
        elif path.endswith("/admin/ai/readiness"):
            body, status = READINESS, 200
        elif path.endswith("/admin/monetization/summary"):
            body, status = _monetization(empty=True), 200
        elif path.endswith("/admin/monetization/entitlements"):
            body, status = {"ok": True, "active_paid_chats": 0, "expiring_within_7_days": 0, "items": []}, 200
        elif path.endswith("/admin/monetization/payments"):
            body, status = {"ok": True, "items": [], "next_cursor": None}, 200
        elif path.endswith("/admin/health") or path.endswith("/admin/audience") or path.endswith("/admin/summary"):
            body, status = {"ok": False, "message": "not needed"}, 500
        else:
            body, status = {"ok": False, "message": "Unexpected API call."}, 404
        await route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    await page.route("**/api/**", handle)
    await page.goto(f"{PREVIEW_URL}/admin/ai", wait_until="domcontentloaded")
    await page.get_by_text("Пока нет AI-запросов за этот период.").first.wait_for(timeout=10000)
    text = await page.locator("body").inner_text()
    assert "Платежей Selara AI пока нет." in text
    assert "Активных подписок Selara AI нет." in text
    assert "неизвестной стоимостью" not in text
    await _overflow_free(page, "admin empty")

    # Dashboard preview links to the new page even when its other sections fail.
    await page.goto(f"{PREVIEW_URL}/admin", wait_until="domcontentloaded")
    link = page.get_by_role("link", name="Открыть аналитику")
    await link.wait_for(timeout=10000)
    await link.click()
    await page.get_by_role("heading", name="AI и монетизация").wait_for()
    assert not errors, errors
    await context.close()


async def _run() -> None:
    preview = _start_preview()
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            await _run_chat_scenarios(browser)
            await _run_chat_retry(browser)
            await _run_admin_scenarios(browser)
            await _run_admin_empty_and_dashboard(browser)
            await browser.close()
    finally:
        preview.terminate()
        preview.wait(timeout=10)


if __name__ == "__main__":
    asyncio.run(_run())
