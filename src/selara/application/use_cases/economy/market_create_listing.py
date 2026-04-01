from __future__ import annotations

from datetime import datetime, timedelta, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.catalog import CONSUMABLE_CATALOG
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error, to_meta_json
from selara.application.use_cases.economy.results import MarketCreateResult

MAX_OPEN_LISTINGS_PER_SELLER = 8


def _is_tradable(item_code: str) -> bool:
    if item_code.startswith("crop:"):
        return True
    if item_code.startswith("item:"):
        short = item_code.removeprefix("item:")
        return short in CONSUMABLE_CATALOG
    return False


async def execute(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
    item_code: str,
    quantity: int,
    unit_price: int,
    market_fee_percent: int,
    event_at: datetime | None = None,
) -> MarketCreateResult:
    now = event_at or datetime.now(timezone.utc)
    normalized_item = item_code.strip().lower()

    if quantity <= 0:
        return MarketCreateResult(accepted=False, reason="Количество должно быть > 0.", listing=None)
    if unit_price <= 0:
        return MarketCreateResult(accepted=False, reason="Цена за единицу должна быть > 0.", listing=None)

    if not _is_tradable(normalized_item):
        return MarketCreateResult(
            accepted=False,
            reason="В v1 на рынке можно продавать только культуры и расходники.",
            listing=None,
        )

    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=user_id)
    if scope is None:
        return MarketCreateResult(accepted=False, reason=error or "Не удалось определить режим экономики", listing=None)

    open_count = await repo.count_open_market_listings_for_seller(scope=scope, seller_user_id=user_id)
    if open_count >= MAX_OPEN_LISTINGS_PER_SELLER:
        return MarketCreateResult(
            accepted=False,
            reason=f"Достигнут лимит открытых лотов ({MAX_OPEN_LISTINGS_PER_SELLER}).",
            listing=None,
        )

    account, _ = await get_account_or_error(repo, scope=scope, user_id=user_id)
    inventory = {item.item_code: item for item in await repo.list_inventory(account_id=account.id)}
    stock = inventory.get(normalized_item)
    if stock is None or stock.quantity < quantity:
        return MarketCreateResult(accepted=False, reason="Недостаточно предметов в инвентаре.", listing=None)

    total = quantity * unit_price
    fee = int(total * max(0, market_fee_percent) / 100)
    coupon = inventory.get("item:market_fee_coupon")
    if coupon is not None and coupon.quantity > 0:
        fee = 0
        await repo.add_inventory_item(account_id=account.id, item_code="item:market_fee_coupon", delta=-1)

    if fee > 0 and account.balance < fee:
        return MarketCreateResult(accepted=False, reason=f"Недостаточно монет для комиссии ({fee}).", listing=None)

    if fee > 0:
        await repo.add_balance(account_id=account.id, delta=-fee)
        await repo.add_ledger(
            account_id=account.id,
            direction="out",
            amount=fee,
            reason="market_listing_fee",
            meta_json=to_meta_json({"item": normalized_item}),
        )

    await repo.add_inventory_item(account_id=account.id, item_code=normalized_item, delta=-quantity)

    listing = await repo.create_market_listing(
        scope=scope,
        chat_id=scope.chat_id,
        seller_user_id=user_id,
        item_code=normalized_item,
        qty_total=quantity,
        unit_price=unit_price,
        fee_paid=fee,
        expires_at=now + timedelta(hours=24),
    )

    return MarketCreateResult(accepted=True, reason=None, listing=listing)
