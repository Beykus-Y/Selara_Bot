"""Stateful HTTP-boundary Gacha contract for Genshin and HSR, no real charges.

Exercises the actual HttpGachaClient request, validation and error mapping
against an in-process mock HTTP service. This is NOT a live Gacha-provider smoke.
"""
from __future__ import annotations

import httpx
import pytest

from selara.infrastructure.http.gacha_client import GachaClientError, HttpGachaClient


@pytest.mark.asyncio
@pytest.mark.parametrize("banner", ["genshin", "hsr"])
async def test_gacha_banner_currency_purchase_collection_and_sale_http_contract(
    monkeypatch: pytest.MonkeyPatch, banner: str,
) -> None:
    balances = {"genshin": 0, "hsr": 0}
    pulls: dict[int, dict[str, object]] = {}
    grant_keys: set[str] = set()
    requests: list[tuple[str, str, str]] = []
    next_pull_id = 900
    alternate = "hsr" if banner == "genshin" else "genshin"

    def player() -> dict[str, int]:
        return {
            "user_id": 42,
            "adventure_rank": 1,
            "adventure_xp": 0,
            "xp_into_rank": 0,
            "xp_for_next_rank": 100,
            "total_points": 0,
            "total_primogems": balances[banner],
        }

    def on_request(request: httpx.Request) -> httpx.Response:
        nonlocal next_pull_id
        path = request.url.path
        body = __import__("json").loads(request.content) if request.content else {}
        req_banner = body.get("banner", request.url.params.get("banner", banner))
        requests.append((request.method, path, str(req_banner)))
        is_admin = path.startswith("/v1/gacha/admin/")
        expected_header = "X-Gacha-Admin-Token" if is_admin else "X-Gacha-Service-Token"
        expected_token = "admin-secret" if is_admin else "service-secret"
        if request.headers.get(expected_header) != expected_token:
            return httpx.Response(403, json={"detail": "forbidden"})

        if path == "/v1/gacha/admin/currency/grant":
            assert body["user_id"] == 42 and body["amount"] == 160
            key = body["idempotency_key"]
            assert key
            if key not in grant_keys:
                balances[req_banner] += 160
                grant_keys.add(key)
            return httpx.Response(200, json={
                "status": "ok", "message": "Granted", "banner": req_banner,
                "user_id": 42, "amount": 160,
                "player": {**player(), "total_primogems": balances[req_banner]},
            })

        if path == "/v1/gacha/pull/purchase":
            assert body["user_id"] == 42
            if balances[req_banner] < 160:
                return httpx.Response(400, json={"detail": "Недостаточно валюты"})
            balances[req_banner] -= 160
            next_pull_id += 1
            pull_id = next_pull_id
            pulls[pull_id] = {"user_id": 42, "banner": req_banner, "sold": False}
            return httpx.Response(200, json={
                "status": "ok", "message": "Крутка выполнена",
                "pull_id": pull_id, "is_new": True, "copies_owned": 1,
                "card": {
                    "code": "amber", "name": "Эмбер", "rarity": "common",
                    "rarity_label": "Обычная", "points": 0, "primogems": 0,
                    "image_url": "https://example.test/amber.png",
                },
                "player": {**player(), "total_primogems": balances[req_banner]},
                "sell_offer": {"sale_price": 20},
            })

        if path == "/v1/gacha/users/42/profile":
            assert req_banner in balances
            own = [p for p in pulls.values() if p["banner"] == req_banner and not p["sold"]]
            return httpx.Response(200, json={
                "status": "ok", "banner": req_banner, "message": "Профиль",
                "player": {**player(), "total_primogems": balances[req_banner]},
                "unique_cards": len(own), "total_copies": len(own),
                "rarity_counts": [], "recent_pulls": [],
            })

        if path == "/v1/gacha/users/42/collection":
            own = [p for p in pulls.values() if p["banner"] == req_banner and not p["sold"]]
            cards = [{
                "code": "amber", "name": "Эмбер", "rarity": "common",
                "rarity_label": "Обычная", "copies_owned": 1,
                "image_url": "https://example.test/amber.png",
            }] if own else []
            return httpx.Response(200, json={
                "status": "ok", "banner": req_banner, "user_id": 42,
                "cards": cards, "total_unique": len(cards), "total_copies": len(own),
            })

        if path.startswith("/v1/gacha/pulls/") and path.endswith("/sell"):
            pull_id = int(path.split("/")[4])
            record = pulls.get(pull_id)
            if not record or record["sold"] or record["user_id"] != body.get("user_id"):
                return httpx.Response(409, json={"detail": "Карточка недоступна"})
            record["sold"] = True
            balances[record["banner"]] += 20
            return httpx.Response(200, json={
                "status": "ok", "message": "Продано",
                "pull_id": pull_id, "banner": record["banner"],
                "sale_price": 20, "sold_at": "2026-10-10T12:00:00+00:00",
                "player": {**player(), "total_primogems": balances[record["banner"]]},
            })

        return httpx.Response(404, json={"detail": "Unknown endpoint"})

    transport = httpx.MockTransport(on_request)
    original_async_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        return original_async_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(
        "selara.infrastructure.http.gacha_client.httpx.AsyncClient", client_factory,
    )
    client = HttpGachaClient(
        base_url="https://gacha.example.test",
        timeout_seconds=5.0, service_token="service-secret",
    )

    before = await client.get_profile(user_id=42, banner=banner)
    assert before.player.total_primogems == 0
    assert before.unique_cards == 0

    grant = await client.grant_currency(
        user_id=42, username="tester", banner=banner, amount=160,
        admin_token="admin-secret", idempotency_key=f"gux15-{banner}-grant-1",
    )
    assert grant.banner == banner and grant.player.total_primogems == 160
    # Retrying one idempotency key cannot create an extra 160.
    await client.grant_currency(
        user_id=42, username="tester", banner=banner, amount=160,
        admin_token="admin-secret", idempotency_key=f"gux15-{banner}-grant-1",
    )
    assert balances[banner] == 160 and balances[alternate] == 0

    paid = await client.purchase_pull(user_id=42, username="tester", banner=banner)
    assert paid.pull_id is not None and paid.sell_offer is not None
    assert paid.player.total_primogems == 0
    collection = await client.get_collection(user_id=42, banner=banner)
    assert collection.total_copies == 1 and collection.cards[0].code == "amber"
    assert (await client.get_profile(user_id=42, banner=banner)).total_copies == 1

    with pytest.raises(GachaClientError) as forbidden:
        await client.sell_pull(user_id=777, pull_id=paid.pull_id)
    assert not forbidden.value.is_operational
    assert (await client.get_collection(user_id=42, banner=banner)).total_copies == 1

    sale = await client.sell_pull(user_id=42, pull_id=paid.pull_id)
    assert sale.banner == banner and sale.sale_price == 20
    assert (await client.get_collection(user_id=42, banner=banner)).total_copies == 0
    assert (await client.get_profile(user_id=42, banner=banner)).player.total_primogems == 20
    assert balances[alternate] == 0

    with pytest.raises(GachaClientError) as already_sold:
        await client.sell_pull(user_id=42, pull_id=paid.pull_id)
    assert not already_sold.value.is_operational
    assert balances[banner] == 20
    assert any(path == "/v1/gacha/pull/purchase" for _, path, _ in requests)
