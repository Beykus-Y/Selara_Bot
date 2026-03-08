from __future__ import annotations

from datetime import datetime, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.catalog import inventory_stack_limit
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error, to_meta_json
from selara.application.use_cases.economy.results import MarketBuyResult


async def execute(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    buyer_user_id: int,
    listing_id: int,
    quantity: int,
    seller_tax_percent: int,
    event_at: datetime | None = None,
) -> MarketBuyResult:
    now = event_at or datetime.now(timezone.utc)

    if quantity <= 0:
        return MarketBuyResult(
            accepted=False,
            reason="Количество должно быть > 0.",
            listing_id=None,
            quantity=0,
            total_cost=0,
            buyer_balance=None,
        )

    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=buyer_user_id)
    if scope is None:
        return MarketBuyResult(
            accepted=False,
            reason=error or "Не удалось определить режим экономики",
            listing_id=None,
            quantity=0,
            total_cost=0,
            buyer_balance=None,
        )

    listing = await repo.get_market_listing(listing_id=listing_id)
    if listing is None:
        return MarketBuyResult(
            accepted=False,
            reason="Лот не найден.",
            listing_id=None,
            quantity=0,
            total_cost=0,
            buyer_balance=None,
        )

    if listing.scope_id != scope.scope_id:
        return MarketBuyResult(
            accepted=False,
            reason="Лот относится к другому рынку.",
            listing_id=listing_id,
            quantity=0,
            total_cost=0,
            buyer_balance=None,
        )

    if listing.status != "open":
        return MarketBuyResult(
            accepted=False,
            reason="Лот уже недоступен.",
            listing_id=listing_id,
            quantity=0,
            total_cost=0,
            buyer_balance=None,
        )

    if listing.expires_at <= now:
        await repo.update_market_listing_qty_and_status(listing_id=listing.id, qty_left=listing.qty_left, status="expired")
        return MarketBuyResult(
            accepted=False,
            reason="Срок лота истёк.",
            listing_id=listing_id,
            quantity=0,
            total_cost=0,
            buyer_balance=None,
        )

    if listing.seller_user_id == buyer_user_id:
        return MarketBuyResult(
            accepted=False,
            reason="Нельзя купить собственный лот.",
            listing_id=listing_id,
            quantity=0,
            total_cost=0,
            buyer_balance=None,
        )

    if quantity > listing.qty_left:
        return MarketBuyResult(
            accepted=False,
            reason=f"В лоте доступно только {listing.qty_left}.",
            listing_id=listing_id,
            quantity=0,
            total_cost=0,
            buyer_balance=None,
        )

    buyer_account, _ = await get_account_or_error(repo, scope=scope, user_id=buyer_user_id)
    seller_account, _ = await get_account_or_error(repo, scope=scope, user_id=listing.seller_user_id)

    buyer_inventory = await repo.list_inventory(account_id=buyer_account.id)
    buyer_item = next((item for item in buyer_inventory if item.item_code == listing.item_code), None)
    if buyer_item is None and len(buyer_inventory) >= inventory_stack_limit(buyer_account.storage_level):
        return MarketBuyResult(
            accepted=False,
            reason="У покупателя переполнен склад.",
            listing_id=listing_id,
            quantity=0,
            total_cost=0,
            buyer_balance=None,
        )

    total_cost = listing.unit_price * quantity
    if buyer_account.balance < total_cost:
        return MarketBuyResult(
            accepted=False,
            reason="Недостаточно монет.",
            listing_id=listing_id,
            quantity=0,
            total_cost=total_cost,
            buyer_balance=None,
        )

    seller_tax = int(total_cost * max(0, seller_tax_percent) / 100)
    seller_income = max(0, total_cost - seller_tax)

    buyer_balance = await repo.add_balance(account_id=buyer_account.id, delta=-total_cost)
    await repo.add_balance(account_id=seller_account.id, delta=seller_income)
    await repo.add_inventory_item(account_id=buyer_account.id, item_code=listing.item_code, delta=quantity)

    qty_left = listing.qty_left - quantity
    status = "closed" if qty_left <= 0 else "open"
    await repo.update_market_listing_qty_and_status(listing_id=listing.id, qty_left=max(0, qty_left), status=status)

    await repo.add_ledger(
        account_id=buyer_account.id,
        direction="out",
        amount=total_cost,
        reason="market_buy",
        meta_json=to_meta_json({"listing_id": listing.id, "qty": quantity}),
    )
    await repo.add_ledger(
        account_id=seller_account.id,
        direction="in",
        amount=seller_income,
        reason="market_sell",
        meta_json=to_meta_json({"listing_id": listing.id, "qty": quantity}),
    )

    return MarketBuyResult(
        accepted=True,
        reason=None,
        listing_id=listing.id,
        quantity=quantity,
        total_cost=total_cost,
        buyer_balance=buyer_balance,
    )
