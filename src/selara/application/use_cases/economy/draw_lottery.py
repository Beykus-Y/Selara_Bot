from __future__ import annotations

import random
from datetime import datetime, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error, to_meta_json
from selara.application.use_cases.economy.results import LotteryResultView


LOTTERY_SINGLE_ITEMS = [
    "item:fertilizer_fast",
    "item:fertilizer_rich",
    "item:pesticide",
    "item:crop_insurance",
    "item:market_fee_coupon",
    "item:energy_drink",
    "item:growth_gel",
    "item:cooling_pack",
    "item:stimulant_shot",
]


async def execute(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
    ticket_type: str,
    lottery_ticket_price: int,
    lottery_paid_daily_limit: int,
    event_at: datetime | None = None,
) -> LotteryResultView:
    now = event_at or datetime.now(timezone.utc)
    today = now.date()

    mode = ticket_type.strip().lower()
    if mode not in {"free", "paid", "item"}:
        return LotteryResultView(
            accepted=False,
            reason="Тип билета: free, paid или item.",
            ticket_type=None,
            coin_reward=0,
            item_rewards=(),
            new_balance=None,
            used_paid_today=0,
        )

    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=user_id)
    if scope is None:
        return LotteryResultView(
            accepted=False,
            reason=error or "Не удалось определить режим экономики",
            ticket_type=None,
            coin_reward=0,
            item_rewards=(),
            new_balance=None,
            used_paid_today=0,
        )

    account, _ = await get_account_or_error(repo, scope=scope, user_id=user_id)
    used_paid_today = account.paid_lottery_used_today if account.paid_lottery_used_on == today else 0
    balance = account.balance

    if mode == "free":
        if account.free_lottery_claimed_on == today:
            return LotteryResultView(
                accepted=False,
                reason="Бесплатный билет сегодня уже использован.",
                ticket_type=None,
                coin_reward=0,
                item_rewards=(),
                new_balance=None,
                used_paid_today=used_paid_today,
            )
        await repo.mark_free_lottery_claimed(account_id=account.id, claimed_on=today)

    elif mode == "item":
        ticket_item = await repo.get_inventory_item(account_id=account.id, item_code="item:lottery_ticket")
        if ticket_item is None or ticket_item.quantity <= 0:
            return LotteryResultView(
                accepted=False,
                reason="В инвентаре нет лотерейного билета.",
                ticket_type=None,
                coin_reward=0,
                item_rewards=(),
                new_balance=None,
                used_paid_today=used_paid_today,
            )
        await repo.add_inventory_item(account_id=account.id, item_code="item:lottery_ticket", delta=-1)

    else:
        if used_paid_today >= lottery_paid_daily_limit:
            return LotteryResultView(
                accepted=False,
                reason="Дневной лимит платных билетов исчерпан.",
                ticket_type=None,
                coin_reward=0,
                item_rewards=(),
                new_balance=None,
                used_paid_today=used_paid_today,
            )
        if balance < lottery_ticket_price:
            return LotteryResultView(
                accepted=False,
                reason=f"Недостаточно монет. Билет стоит {lottery_ticket_price}.",
                ticket_type=None,
                coin_reward=0,
                item_rewards=(),
                new_balance=None,
                used_paid_today=used_paid_today,
            )
        balance = await repo.add_balance(account_id=account.id, delta=-lottery_ticket_price)
        used_paid_today = await repo.increment_paid_lottery_used(account_id=account.id, used_on=today)
        await repo.add_ledger(
            account_id=account.id,
            direction="out",
            amount=lottery_ticket_price,
            reason="lottery_ticket_paid",
            meta_json=to_meta_json({}),
        )

    roll = random.random() * 100.0
    coin_reward = 0
    item_rewards: list[tuple[str, int]] = []

    if roll < 40.0:
        coin_reward = random.randint(80, 140)
    elif roll < 63.0:
        coin_reward = random.randint(150, 280)
    elif roll < 78.0:
        coin_reward = random.randint(300, 600)
    elif roll < 88.0:
        item_rewards.append((random.choice(LOTTERY_SINGLE_ITEMS), 1))
    elif roll < 96.0:
        first = random.choice(LOTTERY_SINGLE_ITEMS)
        second = random.choice(LOTTERY_SINGLE_ITEMS)
        if first == second:
            item_rewards.append((first, 2))
        else:
            item_rewards.append((first, 1))
            item_rewards.append((second, 1))
    elif roll < 99.0:
        item_rewards.append(("item:permanent_token", 1))
    else:
        coin_reward = 2500

    if coin_reward > 0:
        balance = await repo.add_balance(account_id=account.id, delta=coin_reward)
        await repo.add_ledger(
            account_id=account.id,
            direction="in",
            amount=coin_reward,
            reason="lottery_coins",
            meta_json=to_meta_json({"ticket_type": mode}),
        )

    for item_code, qty in item_rewards:
        await repo.add_inventory_item(account_id=account.id, item_code=item_code, delta=qty)

    return LotteryResultView(
        accepted=True,
        reason=None,
        ticket_type="paid" if mode in {"paid", "item"} else "free",
        coin_reward=coin_reward,
        item_rewards=tuple(item_rewards),
        new_balance=balance,
        used_paid_today=used_paid_today,
    )
