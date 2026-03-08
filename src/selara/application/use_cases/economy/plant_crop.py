from __future__ import annotations

from datetime import datetime, timedelta, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.catalog import get_crop, get_plot_slots, get_size_tier
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error, to_meta_json
from selara.application.use_cases.economy.results import PlantResult


async def execute(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
    crop_code: str,
    plot_no: int | None,
    event_at: datetime | None = None,
) -> PlantResult:
    now = event_at or datetime.now(timezone.utc)

    crop = get_crop(crop_code)
    if crop is None:
        return PlantResult(
            accepted=False,
            reason="Неизвестная культура. Используйте /farm для списка.",
            crop_code=None,
            plot_no=None,
            ready_at=None,
            new_balance=None,
        )

    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=user_id)
    if scope is None:
        return PlantResult(
            accepted=False,
            reason=error or "Не удалось определить режим экономики",
            crop_code=None,
            plot_no=None,
            ready_at=None,
            new_balance=None,
        )

    account, farm = await get_account_or_error(repo, scope=scope, user_id=user_id)

    slots = get_plot_slots(farm.farm_level)
    selected_plot = plot_no
    if selected_plot is None:
        for candidate in range(1, slots + 1):
            candidate_plot = await repo.get_plot(account_id=account.id, plot_no=candidate)
            if candidate_plot is None or candidate_plot.crop_code is None:
                selected_plot = candidate
                break

    if selected_plot is None:
        return PlantResult(
            accepted=False,
            reason="Нет свободных грядок.",
            crop_code=None,
            plot_no=None,
            ready_at=None,
            new_balance=None,
        )

    if selected_plot < 1 or selected_plot > slots:
        return PlantResult(
            accepted=False,
            reason=f"Для текущего уровня фермы доступны грядки 1..{slots}.",
            crop_code=None,
            plot_no=None,
            ready_at=None,
            new_balance=None,
        )

    plot = await repo.get_plot(account_id=account.id, plot_no=selected_plot)
    if plot is not None and plot.crop_code is not None:
        return PlantResult(
            accepted=False,
            reason="Эта грядка уже занята.",
            crop_code=None,
            plot_no=None,
            ready_at=None,
            new_balance=None,
        )

    if account.balance < crop.seed_cost:
        return PlantResult(
            accepted=False,
            reason=f"Недостаточно монет. Нужно {crop.seed_cost}.",
            crop_code=None,
            plot_no=None,
            ready_at=None,
            new_balance=None,
        )

    size_tier = get_size_tier(farm.size_tier)
    time_mult = size_tier.time_mult if size_tier is not None else 1.0
    grow_seconds = max(60, int(round(crop.grow_seconds * time_mult)))
    ready_at = now + timedelta(seconds=grow_seconds)

    balance = await repo.add_balance(account_id=account.id, delta=-crop.seed_cost)
    await repo.upsert_plot(
        account_id=account.id,
        plot_no=selected_plot,
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
        meta_json=to_meta_json({"crop": crop.code, "plot": selected_plot}),
    )

    return PlantResult(
        accepted=True,
        reason=None,
        crop_code=crop.code,
        plot_no=selected_plot,
        ready_at=ready_at,
        new_balance=balance,
    )
