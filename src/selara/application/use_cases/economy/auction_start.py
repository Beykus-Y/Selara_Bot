from __future__ import annotations

from datetime import datetime, timedelta, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error
from selara.application.use_cases.economy.results import AuctionStartResult


async def execute(
    repo: EconomyRepository,
    *,
    chat_id: int,
    economy_mode: str,
    seller_user_id: int,
    item_code: str,
    quantity: int,
    start_price: int,
    min_increment: int,
    duration_minutes: int,
    message_id: int | None = None,
    event_at: datetime | None = None,
) -> AuctionStartResult:
    now = event_at or datetime.now(timezone.utc)
    normalized_item_code = (item_code or "").strip().lower()
    if quantity <= 0:
        return AuctionStartResult(accepted=False, reason="Количество должно быть > 0.", auction=None)
    if start_price <= 0:
        return AuctionStartResult(accepted=False, reason="Стартовая цена должна быть > 0.", auction=None)
    if min_increment <= 0:
        return AuctionStartResult(accepted=False, reason="Шаг аукциона должен быть > 0.", auction=None)
    if duration_minutes <= 0:
        return AuctionStartResult(accepted=False, reason="Длительность аукциона должна быть > 0.", auction=None)

    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=seller_user_id)
    if scope is None:
        return AuctionStartResult(accepted=False, reason=error or "Не удалось определить режим экономики.", auction=None)

    if await repo.get_active_chat_auction(chat_id=chat_id) is not None:
        return AuctionStartResult(accepted=False, reason="В этом чате уже идёт активный аукцион.", auction=None)

    account, _ = await get_account_or_error(repo, scope=scope, user_id=seller_user_id)
    item = await repo.get_inventory_item(account_id=account.id, item_code=normalized_item_code)
    if item is None or item.quantity < quantity:
        return AuctionStartResult(accepted=False, reason="Недостаточно предметов для выставления на аукцион.", auction=None)

    await repo.add_inventory_item(account_id=account.id, item_code=normalized_item_code, delta=-quantity)
    auction = await repo.create_chat_auction(
        chat_id=chat_id,
        scope=scope,
        seller_user_id=seller_user_id,
        item_code=normalized_item_code,
        quantity=quantity,
        start_price=start_price,
        min_increment=min_increment,
        ends_at=now + timedelta(minutes=duration_minutes),
        message_id=message_id,
    )
    return AuctionStartResult(accepted=True, reason=None, auction=auction)
