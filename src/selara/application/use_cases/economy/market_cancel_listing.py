from __future__ import annotations

from datetime import datetime, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error
from selara.application.use_cases.economy.results import MarketCancelResult


async def execute(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    seller_user_id: int,
    listing_id: int,
    event_at: datetime | None = None,
) -> MarketCancelResult:
    now = event_at or datetime.now(timezone.utc)

    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=seller_user_id)
    if scope is None:
        return MarketCancelResult(accepted=False, reason=error or "Не удалось определить режим экономики", listing_id=None)

    listing = await repo.get_market_listing(listing_id=listing_id)
    if listing is None:
        return MarketCancelResult(accepted=False, reason="Лот не найден.", listing_id=None)

    if listing.scope_id != scope.scope_id:
        return MarketCancelResult(accepted=False, reason="Лот из другого рынка.", listing_id=listing.id)

    if listing.seller_user_id != seller_user_id:
        return MarketCancelResult(accepted=False, reason="Можно отменить только свой лот.", listing_id=listing.id)

    if listing.status != "open":
        return MarketCancelResult(accepted=False, reason="Лот уже закрыт.", listing_id=listing.id)

    if listing.expires_at <= now:
        await repo.update_market_listing_qty_and_status(listing_id=listing.id, qty_left=listing.qty_left, status="expired")
        return MarketCancelResult(accepted=False, reason="Срок лота уже истёк.", listing_id=listing.id)

    seller_account, _ = await get_account_or_error(repo, scope=scope, user_id=seller_user_id)
    if listing.qty_left > 0:
        await repo.add_inventory_item(account_id=seller_account.id, item_code=listing.item_code, delta=listing.qty_left)

    await repo.update_market_listing_qty_and_status(listing_id=listing.id, qty_left=listing.qty_left, status="cancelled")

    return MarketCancelResult(accepted=True, reason=None, listing_id=listing.id)
