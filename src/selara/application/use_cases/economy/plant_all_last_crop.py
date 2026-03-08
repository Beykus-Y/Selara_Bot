from __future__ import annotations

from datetime import datetime, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.catalog import get_plot_slots
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error
from selara.application.use_cases.economy.plant_crop import execute as plant_crop
from selara.application.use_cases.economy.results import PlantAllResult


async def execute(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
    crop_code: str | None = None,
    event_at: datetime | None = None,
) -> PlantAllResult:
    now = event_at or datetime.now(timezone.utc)

    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=user_id)
    if scope is None:
        return PlantAllResult(
            accepted=False,
            reason=error or "Не удалось определить режим экономики",
            crop_code=None,
            planted_plots=(),
            new_balance=None,
        )

    account, farm = await get_account_or_error(repo, scope=scope, user_id=user_id)
    selected_crop = crop_code or farm.last_planted_crop_code
    if not selected_crop:
        return PlantAllResult(
            accepted=False,
            reason="Сначала посадите хотя бы одну культуру вручную.",
            crop_code=None,
            planted_plots=(),
            new_balance=account.balance,
        )

    plots = await repo.list_plots(account_id=account.id)
    slots = get_plot_slots(farm.farm_level)
    empty_plots = sorted(
        plot.plot_no
        for plot in plots
        if plot.plot_no <= slots and plot.crop_code is None
    )
    if not empty_plots:
        return PlantAllResult(
            accepted=False,
            reason="Свободных грядок нет.",
            crop_code=selected_crop,
            planted_plots=(),
            new_balance=account.balance,
        )

    planted_plots: list[int] = []
    balance = account.balance
    last_error: str | None = None

    for plot_no in empty_plots:
        result = await plant_crop(
            repo,
            economy_mode=economy_mode,
            chat_id=chat_id,
            user_id=user_id,
            crop_code=selected_crop,
            plot_no=plot_no,
            event_at=now,
        )
        if not result.accepted or result.plot_no is None:
            last_error = result.reason or "Не удалось засадить все грядки."
            break
        planted_plots.append(result.plot_no)
        if result.new_balance is not None:
            balance = result.new_balance

    if not planted_plots:
        return PlantAllResult(
            accepted=False,
            reason=last_error or "Не удалось посадить культуру.",
            crop_code=selected_crop,
            planted_plots=(),
            new_balance=balance,
        )

    return PlantAllResult(
        accepted=True,
        reason=last_error,
        crop_code=selected_crop,
        planted_plots=tuple(planted_plots),
        new_balance=balance,
    )
