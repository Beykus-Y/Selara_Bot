from __future__ import annotations

from datetime import date

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.catalog import UPGRADE_MAX_LEVEL, build_daily_shop_offers, inventory_stack_limit
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error, to_meta_json
from selara.application.use_cases.economy.results import BuyShopResult
from selara.domain.economy_entities import EconomyScope, ShopOffer


async def list_shop_offers(
    repo: EconomyRepository,
    *,
    scope: EconomyScope,
    user_id: int,
    current_day: date,
) -> tuple[list[ShopOffer], str | None]:
    account, _ = await get_account_or_error(repo, scope=scope, user_id=user_id)
    offers = build_daily_shop_offers(
        scope_id=scope.scope_id,
        account_user_id=user_id,
        current_day=current_day,
        sprinkler_level=account.sprinkler_level,
        tap_glove_level=account.tap_glove_level,
        storage_level=account.storage_level,
    )
    return offers, None


async def execute(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
    offer_code: str,
    current_day: date,
) -> BuyShopResult:
    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=user_id)
    if scope is None:
        return BuyShopResult(accepted=False, reason=error or "Не удалось определить режим экономики", offer=None, new_balance=None)

    account, _ = await get_account_or_error(repo, scope=scope, user_id=user_id)
    offers = build_daily_shop_offers(
        scope_id=scope.scope_id,
        account_user_id=user_id,
        current_day=current_day,
        sprinkler_level=account.sprinkler_level,
        tap_glove_level=account.tap_glove_level,
        storage_level=account.storage_level,
    )

    offer = next((item for item in offers if item.offer_code == offer_code), None)
    if offer is None:
        return BuyShopResult(accepted=False, reason="Такого предложения сегодня нет в магазине.", offer=None, new_balance=None)

    if account.balance < offer.price:
        return BuyShopResult(
            accepted=False,
            reason=f"Недостаточно монет. Нужно {offer.price}.",
            offer=offer,
            new_balance=None,
        )

    if offer.category == "upgrades":
        parts = offer.item_code.split(":")
        if len(parts) != 3 or parts[0] != "upgrade":
            return BuyShopResult(accepted=False, reason="Некорректный апгрейд.", offer=offer, new_balance=None)

        upgrade_code = parts[1]
        next_level = int(parts[2])
        if upgrade_code not in UPGRADE_MAX_LEVEL:
            return BuyShopResult(accepted=False, reason="Неизвестный тип апгрейда.", offer=offer, new_balance=None)

        current_level = 0
        if upgrade_code == "sprinkler":
            current_level = account.sprinkler_level
        elif upgrade_code == "tap_glove":
            current_level = account.tap_glove_level
        elif upgrade_code == "storage_rack":
            current_level = account.storage_level

        if current_level >= UPGRADE_MAX_LEVEL[upgrade_code]:
            return BuyShopResult(accepted=False, reason="Этот апгрейд уже на максимальном уровне.", offer=offer, new_balance=None)
        if next_level != current_level + 1:
            return BuyShopResult(accepted=False, reason="Сначала нужно купить предыдущий уровень.", offer=offer, new_balance=None)

        balance = await repo.add_balance(account_id=account.id, delta=-offer.price)
        await repo.set_upgrade_level(account_id=account.id, upgrade_code=upgrade_code, new_level=next_level)
        await repo.add_ledger(
            account_id=account.id,
            direction="out",
            amount=offer.price,
            reason="shop_upgrade",
            meta_json=to_meta_json({"upgrade": upgrade_code, "level": next_level}),
        )
        return BuyShopResult(accepted=True, reason=None, offer=offer, new_balance=balance)

    inventory = await repo.list_inventory(account_id=account.id)
    existing = {item.item_code: item for item in inventory}
    if offer.item_code not in existing:
        stack_limit = inventory_stack_limit(account.storage_level)
        if len(existing) >= stack_limit:
            return BuyShopResult(
                accepted=False,
                reason="Склад переполнен. Улучшите storage_rack.",
                offer=offer,
                new_balance=None,
            )

    balance = await repo.add_balance(account_id=account.id, delta=-offer.price)
    await repo.add_inventory_item(account_id=account.id, item_code=offer.item_code, delta=offer.quantity)
    await repo.add_ledger(
        account_id=account.id,
        direction="out",
        amount=offer.price,
        reason="shop_buy",
        meta_json=to_meta_json({"item_code": offer.item_code, "qty": offer.quantity}),
    )
    return BuyShopResult(accepted=True, reason=None, offer=offer, new_balance=balance)
