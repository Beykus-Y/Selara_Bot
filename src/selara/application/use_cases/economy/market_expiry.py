from __future__ import annotations

from datetime import datetime, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.common import (
    account_lock_key,
    get_account_or_error,
    lock_economy_resources,
    market_listing_lock_key,
)
from selara.domain.economy_entities import EconomyScope, MarketListing


async def settle_expired_escrow(
    repo: EconomyRepository,
    *,
    scope: EconomyScope,
    listing: MarketListing,
    now: datetime,
) -> int:
    """Give the seller's unsold units of an expired listing back, exactly once.

    The caller must hold the seller's account lock and market_listing_lock_key(listing.id),
    and must have read `listing` under them, so this check-then-act cannot race a buy, a
    cancel or another sweep. The refund and the status change share the caller's transaction.
    The row ends as `expired` with qty_left 0, so a repeated call finds nothing to return.
    Returns the number of units given back.
    """
    if listing.status == "open" and listing.expires_at > now:
        return 0
    if listing.status not in ("open", "expired"):
        # Closed and cancelled listings have already released their escrow.
        return 0

    returned = max(0, listing.qty_left)
    if returned > 0:
        seller_account, _ = await get_account_or_error(repo, scope=scope, user_id=listing.seller_user_id)
        await repo.add_inventory_item(account_id=seller_account.id, item_code=listing.item_code, delta=returned)
    if listing.status == "open" or returned > 0:
        await repo.update_market_listing_qty_and_status(listing_id=listing.id, qty_left=0, status="expired")
    return returned


async def execute(
    repo: EconomyRepository,
    *,
    listing_id: int,
    event_at: datetime | None = None,
) -> int:
    """Settle one listing for the periodic sweep. Returns the number of units given back."""
    owner = await repo.get_market_listing_owner(listing_id=listing_id)
    if owner is None:
        return 0
    scope, seller_user_id = owner
    await lock_economy_resources(
        repo,
        account_lock_key(scope=scope, user_id=seller_user_id),
        market_listing_lock_key(listing_id),
    )
    now = event_at or datetime.now(timezone.utc)
    listing = await repo.get_market_listing(listing_id=listing_id)
    if listing is None:
        return 0
    return await settle_expired_escrow(repo, scope=scope, listing=listing, now=now)
