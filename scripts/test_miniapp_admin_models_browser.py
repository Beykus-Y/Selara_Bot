"""Real frontend CRUD, conflict and mobile layout; API is stubbed at the network."""
import asyncio
import copy
import json

from playwright.async_api import async_playwright

from test_miniapp_selara_ai_browser import (
    PREVIEW_URL, SESSION, READINESS, WIDTHS, _new_page, _overflow_free, _start_preview, _ai_summary, _monetization,
)

MODEL = {
    "key": "one", "model_id": "provider/" + "very-long-model-id/" * 12, "display_name": "One",
    "enabled": True, "prompt_price_usd_per_million": None, "completion_price_usd_per_million": "0",
    "capabilities": {"supports_tools": True, "supports_structured_output": True, "supports_vision": False},
    "aliases": ["snapshot"], "revision": 1, "used_by_profiles": ["analytics"],
}


def profile(model=None, revision=1, multiplier="2"):
    return {
        "profile_key": "analytics", "display_name": "Аналитик", "enabled": True,
        "model_key": model["key"] if model else None, "ail_multiplier": multiplier, "revision": revision,
        "assigned_model": model, "effective_pricing": model,
        "effective": {"model_id": model["model_id"] if model and model["enabled"] else "legacy",
                      "is_fallback": not model or not model["enabled"], "capabilities": model["capabilities"] if model else None},
    }


async def scenario(browser, width):
    context, page, errors = await _new_page(browser, width)
    state = {"models": [copy.deepcopy(MODEL)], "profile": profile(copy.deepcopy(MODEL)), "error": None,
             "writes": [], "slow": False}
    gate = asyncio.Event()

    async def handle(route):
        path = route.request.url.split("?", 1)[0]
        status = 200
        if path.endswith("/miniapp/session"):
            body = SESSION
        elif path.endswith("/landing/context"):
            body = {"ok": True, "page": {"hero_ctas": []}}
        elif "/admin/ai/models" in path:
            if state["slow"]:
                await gate.wait()
            if route.request.method == "GET":
                if state.get("read_error"):
                    body, status = {"message": "Catalog unavailable"}, 503
                else:
                    body = {"ok": True, "items": state["models"]}
            else:
                payload = route.request.post_data_json
                state["writes"].append(payload)
                if str(payload.get("prompt_price_usd_per_million", "")).startswith("-"):
                    body, status = {"ok": False, "status_code": 422, "message": "Цена должна быть числом ≥ 0."}, 422
                elif state["error"]:
                    body, status = {"ok": False, "status_code": 409, "message": state["error"]}, 409
                elif route.request.method == "POST":
                    new = {**payload, "revision": 1, "used_by_profiles": []}
                    state["models"].append(new)
                    body, status = {"ok": True, "item": new}, 201
                else:
                    current = next(m for m in state["models"] if m["key"] == path.rsplit("/", 1)[1])
                    current.update({**payload, "revision": current["revision"] + 1})
                    state["profile"] = profile(state["models"][0], revision=state["profile"]["revision"],
                                               multiplier=state["profile"]["ail_multiplier"])
                    body = {"ok": True, "item": current}
        elif "/admin/ai/model-profiles" in path:
            if route.request.method == "PUT":
                payload = route.request.post_data_json
                state["writes"].append(payload)
                selected = next((m for m in state["models"] if m["key"] == payload["model_key"]), None)
                state["profile"] = profile(selected, state["profile"]["revision"] + 1, payload["ail_multiplier"])
                body = {"ok": True, "item": state["profile"]}
            else:
                body = {"ok": True, "items": [state["profile"]], "fallback_note": "Fallback зависит от операции."}
        elif path.endswith("/admin/ai/readiness"):
            body = READINESS
        elif path.endswith("/admin/ai/summary"):
            body = _ai_summary(0, cost="0")
        elif path.endswith("/admin/ai/breakdown"):
            body = {"ok": True, "features": [], "models": [], "stages": [], "unattributed_provider_calls": 0}
        elif path.endswith("/admin/monetization/summary"):
            body = _monetization(empty=True)
        elif path.endswith("/admin/monetization/entitlements"):
            body = {"ok": True, "items": [], "active_paid_chats": 0}
        elif path.endswith("/admin/monetization/payments"):
            body = {"ok": True, "items": [], "next_cursor": None}
        else:
            body, status = {"message": "unexpected"}, 404
        await route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    await page.route("**/api/**", handle)
    await page.goto(PREVIEW_URL + "/admin/ai")
    section = page.locator(".admin-models")
    await section.get_by_role("button", name="Изменить модель One", exact=True).wait_for()
    assert "5/150" in await section.inner_text()
    assert "цена неизвестна" in await section.inner_text()
    await _overflow_free(page, f"models loaded {width}")

    await section.get_by_role("button", name="Добавить модель", exact=True).click()
    form = section.locator("form")
    await form.get_by_label("Название модели", exact=True).fill("Two")
    await form.get_by_label("Catalog key", exact=True).fill("two")
    await form.get_by_label("Provider model ID", exact=True).fill("provider/two")
    await form.get_by_label("Input USD / 1M tokens", exact=True).fill("0.123456789")
    await form.get_by_label("Aliases — по одному на строку", exact=True).fill("snapshot-two")
    await _overflow_free(page, f"models form {width}")
    await form.get_by_role("button", name="Сохранить модель", exact=True).click()
    await section.get_by_role("button", name="Изменить модель Two", exact=True).wait_for()
    assert state["writes"][-1]["prompt_price_usd_per_million"] == "0.123456789"
    assert state["writes"][-1]["completion_price_usd_per_million"] is None

    await section.get_by_role("button", name="Изменить профиль Аналитик", exact=True).click()
    form = section.locator("form")
    await form.get_by_label("Модель", exact=True).select_option("two")
    await form.get_by_label("AIL multiplier", exact=True).fill("5")
    await form.get_by_role("button", name="Сохранить профиль", exact=True).click()
    await section.get_by_role("button", name="Изменить профиль Аналитик", exact=True).wait_for()
    assert state["writes"][-1]["model_key"] == "two"
    assert state["writes"][-1]["ail_multiplier"] == "5"

    await section.get_by_role("button", name="Изменить модель One", exact=True).click()
    form = section.locator("form")
    await form.get_by_label("Input USD / 1M tokens", exact=True).fill("-1")
    await form.get_by_role("button", name="Сохранить модель", exact=True).click()
    await form.get_by_text("Цена должна быть числом ≥ 0.").wait_for()
    await form.get_by_label("Input USD / 1M tokens", exact=True).fill("")
    state["error"] = "Настройка уже была изменена. Обновите данные и повторите."
    await form.get_by_role("button", name="Сохранить модель", exact=True).click()
    await form.get_by_role("alert").wait_for()
    assert "Обновите данные и повторите." in await form.inner_text()
    state["error"] = None
    await form.get_by_label("Input USD / 1M tokens", exact=True).fill("0")
    await form.get_by_label("Модель включена", exact=True).uncheck()
    dialogs = []
    async def confirm(dialog):
        dialogs.append(dialog.message)
        await dialog.accept()
    page.on("dialog", confirm)
    await form.get_by_role("button", name="Сохранить модель", exact=True).click()
    await section.get_by_role("button", name="Изменить модель One", exact=True).wait_for()
    assert dialogs
    assert state["writes"][-1]["confirm_disable"] is True
    assert state["writes"][-1]["prompt_price_usd_per_million"] == "0"
    await _overflow_free(page, f"models disabled {width}")

    # Reload with an empty catalog, then a delayed catalog read.
    state["models"] = []
    state["profile"] = profile()
    await page.reload()
    await section.get_by_text("Каталог пуст. Профили используют legacy fallback.").wait_for()
    state["slow"] = True
    await page.reload(wait_until="domcontentloaded")
    await section.locator(".admin-skeleton-list").first.wait_for(timeout=5000)
    gate.set()
    await section.get_by_text("Каталог пуст. Профили используют legacy fallback.").wait_for()
    state["slow"] = False
    state["read_error"] = True
    await page.reload()
    await section.get_by_text("Catalog unavailable").wait_for(timeout=15000)
    state["read_error"] = False
    await section.get_by_role("alert").get_by_role("button", name="Повторить", exact=True).click()
    await section.get_by_text("Каталог пуст. Профили используют legacy fallback.").wait_for()
    assert not errors, errors
    await context.close()


async def run():
    preview = _start_preview()
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            for width in WIDTHS:
                await scenario(browser, width)
            await browser.close()
    finally:
        preview.terminate()
        preview.wait(timeout=10)


if __name__ == "__main__":
    asyncio.run(run())
