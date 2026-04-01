from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.catalog import get_crop
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error, to_meta_json
from selara.application.use_cases.economy.harvest import calculate_harvest_outcome
from selara.application.use_cases.economy.results import HarvestAllResult


async def execute(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
    negative_event_chance_percent: int,
    negative_event_loss_percent: int,
    event_at: datetime | None = None,
) -> HarvestAllResult:
    now = event_at or datetime.now(timezone.utc)

    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=user_id)
    if scope is None:
        return HarvestAllResult(
            accepted=False,
            reason=error or "Не удалось определить режим экономики",
            harvested_plots=(),
            total_amount=0,
            crop_totals=(),
        )

    account, farm = await get_account_or_error(repo, scope=scope, user_id=user_id)
    plots = await repo.list_plots(account_id=account.id)
    ready_plots = sorted(
        (
            plot
            for plot in plots
            if plot.crop_code is not None and plot.ready_at is not None and plot.ready_at <= now
        ),
        key=lambda item: item.plot_no,
    )
    if not ready_plots:
        return HarvestAllResult(
            accepted=False,
            reason="Нет готовых грядок.",
            harvested_plots=(),
            total_amount=0,
            crop_totals=(),
        )

    harvested: list[int] = []
    crop_totals: dict[str, int] = defaultdict(int)
    total_amount = 0
    last_error: str | None = None
    current_negative_event_streak = farm.negative_event_streak

    for plot in ready_plots:
        crop = get_crop(plot.crop_code)
        if crop is None:
            last_error = "Неизвестный тип культуры в грядке."
            continue

        amount, event, current_negative_event_streak = calculate_harvest_outcome(
            crop=crop,
            size_tier_code=farm.size_tier,
            sprinkler_level=account.sprinkler_level,
            yield_boost_pct=plot.yield_boost_pct,
            shield_active=plot.shield_active,
            negative_event_streak=current_negative_event_streak,
            negative_event_chance_percent=negative_event_chance_percent,
            negative_event_loss_percent=negative_event_loss_percent,
        )
        await repo.set_negative_event_streak(account_id=account.id, value=current_negative_event_streak)
        await repo.add_inventory_item(account_id=account.id, item_code=f"crop:{crop.code}", delta=amount)
        await repo.upsert_plot(
            account_id=account.id,
            plot_no=plot.plot_no,
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
            meta_json=to_meta_json({"crop": crop.code, "plot": plot.plot_no, "event": event or "none"}),
        )
        harvested.append(plot.plot_no)
        crop_totals[crop.code] += amount
        total_amount += amount

    if not harvested:
        return HarvestAllResult(
            accepted=False,
            reason=last_error or "Не удалось собрать урожай.",
            harvested_plots=(),
            total_amount=0,
            crop_totals=(),
        )

    return HarvestAllResult(
        accepted=True,
        reason=last_error,
        harvested_plots=tuple(harvested),
        total_amount=total_amount,
        crop_totals=tuple(sorted(crop_totals.items())),
    )
