from __future__ import annotations

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error
from selara.application.use_cases.economy.results import EconomyDashboard


async def execute(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
) -> tuple[EconomyDashboard | None, str | None]:
    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=user_id)
    if scope is None:
        return None, error or "Не удалось определить режим экономики"

    account, farm = await get_account_or_error(repo, scope=scope, user_id=user_id)
    plots = await repo.list_plots(account_id=account.id)
    inventory = await repo.list_inventory(account_id=account.id)

    return (
        EconomyDashboard(
            scope=scope,
            account=account,
            farm=farm,
            plots=tuple(sorted(plots, key=lambda p: p.plot_no)),
            inventory=tuple(sorted(inventory, key=lambda i: i.item_code)),
        ),
        None,
    )
