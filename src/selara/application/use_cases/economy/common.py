from __future__ import annotations

import json
from datetime import datetime, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.results import EconomyDashboard
from selara.domain.economy_entities import EconomyAccount, EconomyScope, FarmState, InventoryItem, PlotState


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def resolve_scope_or_error(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
) -> tuple[EconomyScope | None, str | None]:
    return await repo.resolve_scope(mode=economy_mode, chat_id=chat_id, user_id=user_id)


async def get_account_or_error(
    repo: EconomyRepository,
    *,
    scope: EconomyScope,
    user_id: int,
) -> tuple[EconomyAccount, FarmState]:
    return await repo.get_or_create_account(scope=scope, user_id=user_id)


def scope_label(scope: EconomyScope) -> str:
    if scope.scope_type == "global":
        return "global"
    return f"chat:{scope.chat_id}"


def scope_from_snapshot(*, chat_id: int, scope_id: str, scope_type: str) -> EconomyScope:
    return EconomyScope(scope_id=scope_id, scope_type=scope_type, chat_id=chat_id if scope_type == "chat" else None)


def build_dashboard(
    *,
    scope: EconomyScope,
    account: EconomyAccount,
    farm: FarmState,
    plots: list[PlotState],
    inventory: list[InventoryItem],
) -> EconomyDashboard:
    return EconomyDashboard(
        scope=scope,
        account=account,
        farm=farm,
        plots=tuple(sorted(plots, key=lambda item: item.plot_no)),
        inventory=tuple(sorted(inventory, key=lambda item: item.item_code)),
    )


async def load_dashboard_for_scope(
    repo: EconomyRepository,
    *,
    scope: EconomyScope,
    user_id: int,
    create_account: bool,
) -> tuple[EconomyDashboard | None, str | None]:
    if create_account:
        account, farm = await repo.get_or_create_account(scope=scope, user_id=user_id)
    else:
        account = await repo.get_account(scope=scope, user_id=user_id)
        if account is None:
            return None, "Аккаунт не найден"
        farm = await repo.get_farm_state(account_id=account.id)
        if farm is None:
            farm = FarmState(account_id=account.id, farm_level=1, size_tier="small", negative_event_streak=0)

    plots = await repo.list_plots(account_id=account.id)
    inventory = await repo.list_inventory(account_id=account.id)
    return build_dashboard(scope=scope, account=account, farm=farm, plots=plots, inventory=inventory), None


def to_meta_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False)
