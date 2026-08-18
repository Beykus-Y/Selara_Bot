"""Adversarial security/correctness tests against the LIVE local gacha-service
(http://127.0.0.1:8001, backed by the local Postgres in docker: selara_local_pg).

These are deliberately NOT unit tests against a shared in-process engine —
they open real TCP connections via httpx against the running FastAPI server,
so any race-condition finding here reflects genuine concurrent-request
behaviour, not an artifact of sharing one asyncio event loop / DB session
factory.

Do not run against a production gacha-service URL.
"""

from __future__ import annotations

import asyncio
import os
import random

import httpx
import pytest

BASE_URL = os.environ.get("GACHA_LIVE_BASE_URL", "http://127.0.0.1:8001")
SERVICE_TOKEN = os.environ.get("GACHA_SERVICE_TOKEN", "BWe7gcqddO51VLFsahhjAYup9ftqZQXha9GkLrEJtcU")
ADMIN_TOKEN = os.environ.get("GACHA_ADMIN_TOKEN", "n0ttSCPqNvGDHq4_flD9IfzZiZAJUujCQ9CB7FjoxAI")


def _service_headers() -> dict[str, str]:
    return {"X-Gacha-Service-Token": SERVICE_TOKEN}


def _admin_headers() -> dict[str, str]:
    return {"X-Gacha-Admin-Token": ADMIN_TOKEN}


def _fresh_user_id() -> int:
    # High range, astronomically unlikely to collide with a real Telegram
    # user id (Telegram user ids are currently well under 1e10) or with
    # another test's id.
    return random.randint(9_000_000_000, 9_999_999_999)


async def _server_reachable() -> bool:
    try:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=2.0) as client:
            r = await client.get("/v1/gacha/health")
            return r.status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _require_live_server():
    if not await _server_reachable():
        pytest.skip(f"live gacha-service not reachable at {BASE_URL}")


async def _pull(client: httpx.AsyncClient, user_id: int, banner: str = "genshin") -> httpx.Response:
    return await client.post(
        "/v1/gacha/pull",
        json={"user_id": user_id, "username": "adversarial-test", "banner": banner},
        headers=_service_headers(),
    )


async def _purchase_pull(client: httpx.AsyncClient, user_id: int, banner: str = "genshin") -> httpx.Response:
    return await client.post(
        "/v1/gacha/pull/purchase",
        json={"user_id": user_id, "username": "adversarial-test", "banner": banner},
        headers=_service_headers(),
    )


async def _grant_currency(
    client: httpx.AsyncClient, user_id: int, amount: int, *, banner: str = "genshin", idem: str | None = None
) -> httpx.Response:
    return await client.post(
        "/v1/gacha/admin/currency/grant",
        json={"user_id": user_id, "username": "adversarial-test", "banner": banner, "amount": amount, "idempotency_key": idem},
        headers=_admin_headers(),
    )


async def _sell(client: httpx.AsyncClient, pull_id: int, user_id: int) -> httpx.Response:
    return await client.post(
        f"/v1/gacha/pulls/{pull_id}/sell",
        json={"user_id": user_id},
        headers=_service_headers(),
    )


# ---------------------------------------------------------------------------
# 1. Double free-pull under REAL network concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_concurrent_free_pulls_yield_single_winner() -> None:
    """Fire N concurrent /pull requests for a brand-new user over real HTTP
    connections. Only one may succeed (status=ok); the rest must be
    'cooldown'. A failure here means a user can script N simultaneous /pull
    clicks (e.g. via multiple devices or a script hitting the bot's webhook
    concurrently) to get free currency/cards N times instead of once."""
    user_id = _fresh_user_id()
    concurrency = 8

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        responses = await asyncio.gather(*(_pull(client, user_id) for _ in range(concurrency)))

    statuses = [r.json()["status"] for r in responses if r.status_code == 200]
    assert len(statuses) == concurrency, [r.status_code for r in responses]
    ok_count = statuses.count("ok")
    cooldown_count = statuses.count("cooldown")

    assert ok_count == 1, f"expected exactly 1 successful free pull, got {ok_count} (statuses={statuses})"
    assert cooldown_count == concurrency - 1

    # Corroborate via history: exactly one PullHistory row must exist.
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        history = await client.get(f"/v1/gacha/users/{user_id}/history", params={"banner": "genshin", "limit": 20})
    assert history.status_code == 200
    assert len(history.json()["entries"]) == 1


# ---------------------------------------------------------------------------
# 2. Double-spend on paid pulls under REAL network concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_concurrent_paid_pulls_cannot_double_spend() -> None:
    """Grant exactly one paid-pull's worth of currency (160), then fire two
    concurrent /pull/purchase requests. Both succeeding would mean the user
    got two pulls for the price of one (currency duplication bug)."""
    user_id = _fresh_user_id()
    PAID_PULL_PRICE = 160

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        grant = await _grant_currency(client, user_id, PAID_PULL_PRICE, idem=f"adversarial-grant-{user_id}")
        assert grant.status_code == 200, grant.text
        assert grant.json()["player"]["total_primogems"] == PAID_PULL_PRICE

        responses = await asyncio.gather(
            _purchase_pull(client, user_id), _purchase_pull(client, user_id)
        )

    ok_responses = [r for r in responses if r.status_code == 200]
    failed_responses = [r for r in responses if r.status_code != 200]

    assert len(ok_responses) == 1, f"expected exactly 1 successful paid pull, got {len(ok_responses)}: {[r.text for r in responses]}"
    assert len(failed_responses) == 1
    assert failed_responses[0].status_code == 400
    assert "Недостаточно" in failed_responses[0].json()["detail"]

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        profile = await client.get(f"/v1/gacha/users/{user_id}/profile", params={"banner": "genshin"})
    assert profile.status_code == 200
    assert profile.json()["player"]["total_primogems"] >= 0


# ---------------------------------------------------------------------------
# 3. Double-sell under REAL network concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_concurrent_sell_of_same_pull_has_single_winner() -> None:
    """Pull until a sellable (duplicate) card is produced, then fire two
    concurrent /sell requests for that pull_id. Only one may succeed —
    otherwise the user duplicates currency by selling the same card twice."""
    user_id = _fresh_user_id()

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        # Grant a big pile of currency and pull repeatedly (paid, no
        # cooldown gate) until we get a sellable duplicate. Genshin cards
        # become sellable once a character hits its max constellation
        # duplicate stage, which can take a while — cap attempts.
        await _grant_currency(client, user_id, 100_000, idem=f"adversarial-sell-setup-{user_id}")

        sellable_pull_id = None
        for _ in range(400):
            resp = await _purchase_pull(client, user_id)
            if resp.status_code != 200:
                break
            body = resp.json()
            if body.get("sell_offer") is not None:
                sellable_pull_id = body["pull_id"]
                break

        if sellable_pull_id is None:
            pytest.skip("could not produce a sellable duplicate within attempt budget")

        responses = await asyncio.gather(
            _sell(client, sellable_pull_id, user_id), _sell(client, sellable_pull_id, user_id)
        )

    ok_responses = [r for r in responses if r.status_code == 200]
    failed_responses = [r for r in responses if r.status_code != 200]
    assert len(ok_responses) == 1, f"expected exactly 1 successful sell, got {len(ok_responses)}: {[r.text for r in responses]}"
    assert len(failed_responses) == 1
    assert "уже продана" in failed_responses[0].json()["detail"]


# ---------------------------------------------------------------------------
# 4. IDOR: sell someone else's pull
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_cannot_sell_another_users_pull_idor() -> None:
    """Victim gets a sellable duplicate card; attacker (different user_id,
    but reusing the *same* service token — the only credential the
    endpoint checks) tries to sell the victim's pull_id under their own
    user_id. Must be rejected — proves the repository's WHERE user_id=...
    ownership check on sell_pull actually holds server-side."""
    victim_id = _fresh_user_id()
    attacker_id = _fresh_user_id()

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        await _grant_currency(client, victim_id, 100_000, idem=f"adversarial-idor-setup-{victim_id}")

        sellable_pull_id = None
        for _ in range(400):
            resp = await _purchase_pull(client, victim_id)
            if resp.status_code != 200:
                break
            body = resp.json()
            if body.get("sell_offer") is not None:
                sellable_pull_id = body["pull_id"]
                break

        if sellable_pull_id is None:
            pytest.skip("could not produce a sellable duplicate within attempt budget")

        # Attacker attempts to sell the victim's pull under their own user_id.
        attack_response = await _sell(client, sellable_pull_id, attacker_id)

    assert attack_response.status_code == 404, attack_response.text
    assert "не найдена" in attack_response.json()["detail"]


# ---------------------------------------------------------------------------
# 5. Unauthenticated read of ANY user's balance / history / collection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_profile_history_collection_have_no_authentication() -> None:
    """The GET /users/{user_id}/profile|history|collection endpoints require
    neither the service token nor any per-caller identity check — the
    user_id is taken straight from the URL. Anyone who can reach the
    gacha-service's network port can read ANY player's currency balance,
    full pull history and card collection, including the operator's own
    account (GACHA_ADMIN_USER_ID). This is an information-disclosure /
    IDOR finding on the read side, distinct from (and less severe than)
    a money-moving bug, but still a real gap: the mutating endpoints are
    gated by X-Gacha-Service-Token while these are not gated at all."""
    victim_id = _fresh_user_id()

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        await _grant_currency(client, victim_id, 4242, idem=f"adversarial-read-setup-{victim_id}")

        # No auth headers of any kind — plain unauthenticated GET.
        profile = await client.get(f"/v1/gacha/users/{victim_id}/profile", params={"banner": "genshin"})
        history = await client.get(f"/v1/gacha/users/{victim_id}/history", params={"banner": "genshin"})
        collection = await client.get(f"/v1/gacha/users/{victim_id}/collection", params={"banner": "genshin"})

    assert profile.status_code == 200
    assert profile.json()["player"]["total_primogems"] == 4242
    assert history.status_code == 200
    assert collection.status_code == 200


# ---------------------------------------------------------------------------
# 6. Mutating endpoints reject missing/wrong service or admin token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_pull_rejects_missing_and_wrong_service_token() -> None:
    user_id = _fresh_user_id()
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        no_token = await client.post(
            "/v1/gacha/pull", json={"user_id": user_id, "banner": "genshin"}
        )
        wrong_token = await client.post(
            "/v1/gacha/pull",
            json={"user_id": user_id, "banner": "genshin"},
            headers={"X-Gacha-Service-Token": "totally-wrong-token"},
        )
        empty_token = await client.post(
            "/v1/gacha/pull",
            json={"user_id": user_id, "banner": "genshin"},
            headers={"X-Gacha-Service-Token": ""},
        )

    assert no_token.status_code == 403
    assert wrong_token.status_code == 403
    assert empty_token.status_code == 403


@pytest.mark.asyncio
async def test_live_admin_endpoints_reject_missing_and_wrong_admin_token() -> None:
    user_id = _fresh_user_id()
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        no_token = await client.post(
            "/v1/gacha/admin/currency/grant",
            json={"user_id": user_id, "banner": "genshin", "amount": 999999},
        )
        wrong_token = await client.post(
            "/v1/gacha/admin/currency/grant",
            json={"user_id": user_id, "banner": "genshin", "amount": 999999},
            headers={"X-Gacha-Admin-Token": "not-the-real-token"},
        )
        # Reusing the *service* token (a real, valid-shaped secret that is
        # just for the wrong purpose) against the admin endpoint must also
        # fail — proves the two token checks are not accidentally merged.
        service_token_on_admin_route = await client.post(
            "/v1/gacha/admin/currency/grant",
            json={"user_id": user_id, "banner": "genshin", "amount": 999999},
            headers={"X-Gacha-Admin-Token": SERVICE_TOKEN},
        )
        cooldown_reset_no_token = await client.post(
            "/v1/gacha/admin/cooldowns/reset",
            json={"user_id": user_id, "banner": "genshin"},
        )

    assert no_token.status_code == 403
    assert wrong_token.status_code == 403
    assert service_token_on_admin_route.status_code == 403
    assert cooldown_reset_no_token.status_code == 403

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        profile = await client.get(f"/v1/gacha/users/{user_id}/profile", params={"banner": "genshin"})
    assert profile.json()["player"]["total_primogems"] == 0, "no grant should have gone through"


# ---------------------------------------------------------------------------
# 7. Currency drain via negative-balance admin grant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_admin_currency_grant_cannot_drive_balance_negative() -> None:
    user_id = _fresh_user_id()
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        grant = await _grant_currency(client, user_id, 100, idem=f"adversarial-neg-setup-{user_id}")
        assert grant.status_code == 200

        over_debit = await _grant_currency(client, user_id, -1000, idem=f"adversarial-neg-attempt-{user_id}")

    assert over_debit.status_code == 400
    assert "Недостаточно" in over_debit.json()["detail"]

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        profile = await client.get(f"/v1/gacha/users/{user_id}/profile", params={"banner": "genshin"})
    assert profile.json()["player"]["total_primogems"] == 100


# ---------------------------------------------------------------------------
# 8. Idempotency key: two DIFFERENT keys for admin currency grant are
#    legitimately two separate grants (not a bug, but the boundary must be
#    understood/documented — same key dedupes, different key does not).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_currency_grant_idempotency_is_scoped_to_the_key_not_the_operation() -> None:
    """Confirms the dedup is purely keyed on idempotency_key: reusing the
    same key collapses two grants into one (safety net for the bot's
    retry-on-timeout logic), while two different keys for what a human
    would call "the same logical top-up" both apply. This is safe *only*
    because idempotency_key is generated server-side by the trusted bot
    process per purchase attempt (uuid4 in buy_currency_with_coins) and
    the endpoint itself is admin-token-gated — an end user has no path to
    choose or replay idempotency keys directly. Documented here as the
    load-bearing assumption, since if the admin token ever became
    reachable from untrusted input this would turn into an unlimited
    currency-duplication primitive."""
    user_id = _fresh_user_id()
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        same_key = f"adversarial-dup-key-{user_id}"
        first = await _grant_currency(client, user_id, 50, idem=same_key)
        second = await _grant_currency(client, user_id, 50, idem=same_key)
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["player"]["total_primogems"] == 50, "same key must be deduped, not applied twice"

        third = await _grant_currency(client, user_id, 50, idem=f"adversarial-dup-key-{user_id}-b")
        assert third.status_code == 200
        assert third.json()["player"]["total_primogems"] == 100, "a genuinely different key legitimately applies again"

        # Sending NO idempotency key at all bypasses dedup entirely (by
        # design — dedup is opt-in). Confirm this is real: two consecutive
        # grants with idem=None both land.
        no_key_first = await _grant_currency(client, user_id, 10, idem=None)
        no_key_second = await _grant_currency(client, user_id, 10, idem=None)
        assert no_key_first.status_code == 200
        assert no_key_second.status_code == 200
        assert no_key_second.json()["player"]["total_primogems"] == 120


# ---------------------------------------------------------------------------
# 9. Integer boundary: absurdly large admin currency grant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_admin_currency_grant_rejects_int32_overflow_instead_of_wrapping() -> None:
    """total_primogems / currency_balance are Postgres Integer columns
    (32-bit signed: max 2147483647). A malicious/compromised admin-token
    holder sending an amount that overflows this must not silently wrap
    into a negative balance (which would then let normal spend/pull logic
    treat the account as broke, or worse, wrap positive again) — it must
    fail loudly.

    Uses two separate connections (one for the overflow attempt, one for
    the follow-up read): the overflow attempt observably tears down its
    own HTTP/1.1 keep-alive connection server-side — the endpoint returns
    a bare 500 with no JSON body (asyncpg's NumericValueOutOfRange
    bubbling up uncaught) instead of a clean 400, and reusing that
    connection for the next request surfaces an unrelated ReadError in
    *this test's* transport layer rather than the DB-correctness question
    actually being asserted."""
    user_id = _fresh_user_id()
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        overflow = await _grant_currency(client, user_id, 3_000_000_000, idem=f"adversarial-overflow-{user_id}")

    # Whatever happens, it must not silently succeed with a negative or
    # wrapped balance. In practice this currently surfaces as a bare 500
    # rather than a clean 400 validation response — noted as a minor
    # robustness gap, not a security hole, since the transaction rolls
    # back cleanly (verified below).
    if overflow.status_code == 200:
        balance = overflow.json()["player"]["total_primogems"]
        assert balance == 3_000_000_000, f"grant succeeded but balance was mangled: {balance}"
    else:
        assert overflow.status_code == 500, overflow.text

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        profile = await client.get(f"/v1/gacha/users/{user_id}/profile", params={"banner": "genshin"})
    assert profile.status_code == 200
    assert profile.json()["player"]["total_primogems"] == 0, "overflowing grant must roll back cleanly, not partially apply"
