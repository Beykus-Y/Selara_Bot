from __future__ import annotations

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.common import load_dashboard_for_scope, resolve_scope_or_error
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
    return await load_dashboard_for_scope(repo, scope=scope, user_id=user_id, create_account=True)
