from __future__ import annotations

import random
from datetime import datetime, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.catalog import get_crop, get_size_tier
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error, to_meta_json
from selara.application.use_cases.economy.results import HarvestResult

POSITIVE_EVENT_CHANCE_PCT = 14
POSITIVE_EVENT_BONUS_PCT = 25


async def execute(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
    plot_no: int,
    negative_event_chance_percent: int,
    negative_event_loss_percent: int,
    event_at: datetime | None = None,
) -> HarvestResult:
    now = event_at or datetime.now(timezone.utc)

    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=user_id)
    if scope is None:
        return HarvestResult(
            accepted=False,
            reason=error or "Не удалось определить режим экономики",
            crop_code=None,
            amount=0,
            event=None,
        )

    account, farm = await get_account_or_error(repo, scope=scope, user_id=user_id)

    plot = await repo.get_plot(account_id=account.id, plot_no=plot_no)
    if plot is None or plot.crop_code is None:
        return HarvestResult(
            accepted=False,
            reason="На этой грядке нечего собирать.",
            crop_code=None,
            amount=0,
            event=None,
        )

    if plot.ready_at is None or plot.ready_at > now:
        return HarvestResult(
            accepted=False,
            reason="Урожай ещё не созрел.",
            crop_code=None,
            amount=0,
            event=None,
        )

    crop = get_crop(plot.crop_code)
    if crop is None:
        return HarvestResult(
            accepted=False,
            reason="Неизвестный тип культуры в грядке.",
            crop_code=None,
            amount=0,
            event=None,
        )

    size = get_size_tier(farm.size_tier)
    size_yield_mult = size.yield_mult if size is not None else 1.0
    sprinkler_mult = 1.0 + max(0, account.sprinkler_level) * 0.08
    boost_mult = 1.0 + max(0, plot.yield_boost_pct) / 100.0

    base_amount = random.randint(crop.min_yield, crop.max_yield)
    amount = int(round(base_amount * size_yield_mult * sprinkler_mult * boost_mult))
    amount = max(1, amount)

    event: str | None = None
    roll = random.random() * 100.0

    if farm.negative_event_streak < 2 and roll < max(0, negative_event_chance_percent):
        if plot.shield_active:
            event = "shielded"
            await repo.set_negative_event_streak(account_id=account.id, value=0)
        else:
            event = "negative"
            amount = max(1, int(round(amount * (1.0 - max(0, negative_event_loss_percent) / 100.0))))
            await repo.set_negative_event_streak(account_id=account.id, value=farm.negative_event_streak + 1)
    elif roll < max(0, negative_event_chance_percent) + POSITIVE_EVENT_CHANCE_PCT:
        event = "positive"
        amount = max(1, int(round(amount * (1.0 + POSITIVE_EVENT_BONUS_PCT / 100.0))))
        await repo.set_negative_event_streak(account_id=account.id, value=0)
    else:
        await repo.set_negative_event_streak(account_id=account.id, value=0)

    await repo.add_inventory_item(account_id=account.id, item_code=f"crop:{crop.code}", delta=amount)
    await repo.upsert_plot(
        account_id=account.id,
        plot_no=plot_no,
        crop_code=None,
        planted_at=None,
        ready_at=None,
        yield_boost_pct=0,
        shield_active=False,
    )

    await repo.add_ledger(
        account_id=account.id,
        direction="in",
        amount=amount,
        reason="harvest",
        meta_json=to_meta_json({"crop": crop.code, "plot": plot_no, "event": event or "none"}),
    )

    return HarvestResult(
        accepted=True,
        reason=None,
        crop_code=crop.code,
        amount=amount,
        event=event,
    )
