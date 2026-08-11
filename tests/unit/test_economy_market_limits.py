from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from selara.application.use_cases.economy.market_buy_listing import (
    execute as buy_listing,
)
from selara.application.use_cases.economy.market_create_listing import (
    execute as create_listing,
)
from selara.application.use_cases.economy.market_limits import (
    MAX_MARKET_QUANTITY,
    MAX_MARKET_UNIT_PRICE,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("quantity", "unit_price", "expected_fragment"),
    [
        (MAX_MARKET_QUANTITY + 1, 1, "Количество"),
        (1, MAX_MARKET_UNIT_PRICE + 1, "Цена"),
    ],
)
async def test_market_create_rejects_values_above_limits(
    quantity: int,
    unit_price: int,
    expected_fragment: str,
) -> None:
    repo = AsyncMock()

    result = await create_listing(
        repo,
        economy_mode="global",
        chat_id=-100,
        user_id=10,
        item_code="crop:radish",
        quantity=quantity,
        unit_price=unit_price,
        market_fee_percent=2,
    )

    assert not result.accepted
    assert expected_fragment in (result.reason or "")
    repo.resolve_scope.assert_not_awaited()


@pytest.mark.asyncio
async def test_market_buy_rejects_huge_quantity_before_database_access() -> None:
    repo = AsyncMock()

    result = await buy_listing(
        repo,
        economy_mode="global",
        chat_id=-100,
        buyer_user_id=10,
        listing_id=1,
        quantity=MAX_MARKET_QUANTITY + 1,
        seller_tax_percent=5,
    )

    assert not result.accepted
    assert "Количество" in (result.reason or "")
    repo.resolve_scope.assert_not_awaited()
