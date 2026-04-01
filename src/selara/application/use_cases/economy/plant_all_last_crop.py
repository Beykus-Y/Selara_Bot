from __future__ import annotations

from datetime import datetime, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.catalog import get_crop, get_plot_slots
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error, to_meta_json
from selara.application.use_cases.economy.plant_crop import calculate_ready_at
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

    crop = get_crop(selected_crop)
    if crop is None:
        return PlantAllResult(
            accepted=False,
            reason="Неизвестная культура. Используйте /farm для списка.",
            crop_code=None,
            planted_plots=(),
            new_balance=account.balance,
        )

    plots = await repo.list_plots(account_id=account.id)
    slots = get_plot_slots(farm.farm_level)
    plots_by_no = {plot.plot_no: plot for plot in plots}
    empty_plots = [
        plot_no
        for plot_no in range(1, slots + 1)
        if plots_by_no.get(plot_no) is None or plots_by_no[plot_no].crop_code is None
    ]
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
    ready_at = calculate_ready_at(crop=crop, size_tier_code=farm.size_tier, now=now)

    for plot_no in empty_plots:
        if balance < crop.seed_cost:
            last_error = f"Недостаточно монет. Нужно {crop.seed_cost}."
            break
        balance = await repo.add_balance(account_id=account.id, delta=-crop.seed_cost)
        await repo.upsert_plot(
            account_id=account.id,
            plot_no=plot_no,
            crop_code=crop.code,
            planted_at=now,
            ready_at=ready_at,
            yield_boost_pct=0,
            shield_active=False,
        )
        await repo.add_ledger(
            account_id=account.id,
            direction="out",
            amount=crop.seed_cost,
            reason="plant_seed",
            meta_json=to_meta_json({"crop": crop.code, "plot": plot_no}),
        )
        planted_plots.append(plot_no)

    if not planted_plots:
        return PlantAllResult(
            accepted=False,
            reason=last_error or "Не удалось посадить культуру.",
            crop_code=selected_crop,
            planted_plots=(),
            new_balance=balance,
        )

    await repo.set_last_planted_crop_code(account_id=account.id, crop_code=crop.code)
    return PlantAllResult(
        accepted=True,
        reason=last_error,
        crop_code=crop.code,
        planted_plots=tuple(planted_plots),
        new_balance=balance,
    )
