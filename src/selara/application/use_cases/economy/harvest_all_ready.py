from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error
from selara.application.use_cases.economy.harvest import execute as harvest_crop
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

    account, _ = await get_account_or_error(repo, scope=scope, user_id=user_id)
    plots = await repo.list_plots(account_id=account.id)
    ready_plots = sorted(
        plot.plot_no
        for plot in plots
        if plot.crop_code is not None and plot.ready_at is not None and plot.ready_at <= now
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

    for plot_no in ready_plots:
        result = await harvest_crop(
            repo,
            economy_mode=economy_mode,
            chat_id=chat_id,
            user_id=user_id,
            plot_no=plot_no,
            negative_event_chance_percent=negative_event_chance_percent,
            negative_event_loss_percent=negative_event_loss_percent,
            event_at=now,
        )
        if not result.accepted or result.crop_code is None:
            last_error = result.reason or "Не удалось собрать часть урожая."
            continue
        harvested.append(plot_no)
        crop_totals[result.crop_code] += result.amount
        total_amount += result.amount

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
