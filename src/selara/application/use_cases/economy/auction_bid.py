from __future__ import annotations

from datetime import datetime, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.common import get_account_or_error
from selara.application.use_cases.economy.results import AuctionBidResult
from selara.domain.economy_entities import EconomyScope


def _scope_from_auction(chat_id: int, scope_id: str, scope_type: str) -> EconomyScope:
    return EconomyScope(scope_id=scope_id, scope_type=scope_type, chat_id=chat_id if scope_type == "chat" else None)


async def execute(
    repo: EconomyRepository,
    *,
    auction_id: int,
    bidder_user_id: int,
    bid_amount: int,
    event_at: datetime | None = None,
    message_id: int | None = None,
) -> AuctionBidResult:
    now = event_at or datetime.now(timezone.utc)
    auction = await repo.get_chat_auction(auction_id=auction_id)
    if auction is None:
        return AuctionBidResult(accepted=False, reason="Аукцион не найден.", auction=None)
    if auction.status != "open":
        return AuctionBidResult(accepted=False, reason="Аукцион уже закрыт.", auction=auction)
    if auction.ends_at <= now:
        return AuctionBidResult(accepted=False, reason="Аукцион уже завершён по времени.", auction=auction)
    if bidder_user_id == auction.seller_user_id:
        return AuctionBidResult(accepted=False, reason="Нельзя ставить на свой аукцион.", auction=auction)
    if auction.highest_bid_user_id == bidder_user_id:
        return AuctionBidResult(accepted=False, reason="Вы уже лидируете в аукционе.", auction=auction)

    min_bid = auction.start_price if auction.current_bid <= 0 else auction.current_bid + auction.min_increment
    if bid_amount < min_bid:
        return AuctionBidResult(
            accepted=False,
            reason=f"Минимальная ставка сейчас: {min_bid}.",
            auction=auction,
        )

    scope = _scope_from_auction(auction.chat_id, auction.scope_id, auction.scope_type)
    bidder_account, _ = await get_account_or_error(repo, scope=scope, user_id=bidder_user_id)
    if bidder_account.balance < bid_amount:
        return AuctionBidResult(accepted=False, reason="Недостаточно монет для этой ставки.", auction=auction)

    if auction.highest_bid_user_id is not None and auction.current_bid > 0:
        previous_account, _ = await get_account_or_error(repo, scope=scope, user_id=auction.highest_bid_user_id)
        await repo.add_balance(account_id=previous_account.id, delta=auction.current_bid)
        await repo.add_ledger(
            account_id=previous_account.id,
            direction="in",
            amount=auction.current_bid,
            reason="auction_refund",
            meta_json=f'{{"auction_id": {auction.id}}}',
        )

    await repo.add_balance(account_id=bidder_account.id, delta=-bid_amount)
    await repo.add_ledger(
        account_id=bidder_account.id,
        direction="out",
        amount=bid_amount,
        reason="auction_bid_hold",
        meta_json=f'{{"auction_id": {auction.id}}}',
    )

    updated = await repo.update_chat_auction_bid(
        auction_id=auction.id,
        current_bid=bid_amount,
        highest_bid_user_id=bidder_user_id,
        message_id=message_id,
    )
    return AuctionBidResult(accepted=updated is not None, reason=None if updated is not None else "Не удалось обновить ставку.", auction=updated)
