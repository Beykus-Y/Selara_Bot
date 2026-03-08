from __future__ import annotations

from datetime import datetime, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.common import get_account_or_error
from selara.application.use_cases.economy.results import AuctionFinalizeResult
from selara.domain.economy_entities import EconomyScope


def _scope_from_auction(chat_id: int, scope_id: str, scope_type: str) -> EconomyScope:
    return EconomyScope(scope_id=scope_id, scope_type=scope_type, chat_id=chat_id if scope_type == "chat" else None)


async def execute(
    repo: EconomyRepository,
    *,
    auction_id: int,
    cancel: bool = False,
    event_at: datetime | None = None,
    message_id: int | None = None,
) -> AuctionFinalizeResult:
    now = event_at or datetime.now(timezone.utc)
    auction = await repo.get_chat_auction(auction_id=auction_id)
    if auction is None:
        return AuctionFinalizeResult(accepted=False, reason="Аукцион не найден.", auction=None, winner_user_id=None)
    if auction.status != "open":
        return AuctionFinalizeResult(
            accepted=False,
            reason="Аукцион уже был закрыт.",
            auction=auction,
            winner_user_id=auction.highest_bid_user_id,
        )

    scope = _scope_from_auction(auction.chat_id, auction.scope_id, auction.scope_type)
    seller_account, _ = await get_account_or_error(repo, scope=scope, user_id=auction.seller_user_id)

    winner_user_id = auction.highest_bid_user_id
    if cancel:
        if winner_user_id is not None and auction.current_bid > 0:
            winner_account, _ = await get_account_or_error(repo, scope=scope, user_id=winner_user_id)
            await repo.add_balance(account_id=winner_account.id, delta=auction.current_bid)
            await repo.add_ledger(
                account_id=winner_account.id,
                direction="in",
                amount=auction.current_bid,
                reason="auction_cancel_refund",
                meta_json=f'{{"auction_id": {auction.id}}}',
            )
        await repo.add_inventory_item(account_id=seller_account.id, item_code=auction.item_code, delta=auction.quantity)
        closed = await repo.close_chat_auction(
            auction_id=auction.id,
            status="cancelled",
            closed_at=now,
            message_id=message_id,
        )
        return AuctionFinalizeResult(accepted=closed is not None, reason=None, auction=closed, winner_user_id=None)

    if winner_user_id is None or auction.current_bid <= 0:
        await repo.add_inventory_item(account_id=seller_account.id, item_code=auction.item_code, delta=auction.quantity)
        closed = await repo.close_chat_auction(
            auction_id=auction.id,
            status="closed",
            closed_at=now,
            message_id=message_id,
        )
        return AuctionFinalizeResult(accepted=closed is not None, reason=None, auction=closed, winner_user_id=None)

    winner_account, _ = await get_account_or_error(repo, scope=scope, user_id=winner_user_id)
    await repo.add_inventory_item(account_id=winner_account.id, item_code=auction.item_code, delta=auction.quantity)
    await repo.add_balance(account_id=seller_account.id, delta=auction.current_bid)
    await repo.add_ledger(
        account_id=seller_account.id,
        direction="in",
        amount=auction.current_bid,
        reason="auction_sale",
        meta_json=f'{{"auction_id": {auction.id}, "winner_user_id": {winner_user_id}}}',
    )
    closed = await repo.close_chat_auction(
        auction_id=auction.id,
        status="closed",
        closed_at=now,
        message_id=message_id,
    )
    return AuctionFinalizeResult(accepted=closed is not None, reason=None, auction=closed, winner_user_id=winner_user_id)
