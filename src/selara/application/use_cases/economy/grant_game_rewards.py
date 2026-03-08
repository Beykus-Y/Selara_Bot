from __future__ import annotations

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error, to_meta_json
from selara.domain.economy_entities import GameRewardResult


async def execute(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int,
    game_kind: str,
    game_id: str,
    rewards_by_user: dict[int, int],
) -> tuple[GameRewardResult | None, str | None]:
    if not rewards_by_user:
        return (
            GameRewardResult(game_kind=game_kind, rewarded_users=(), total_distributed=0),
            None,
        )

    sample_user_id = next(iter(rewards_by_user.keys()))
    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=sample_user_id)
    if scope is None:
        return None, error or "Не удалось определить режим экономики для наград"

    rewarded: list[tuple[int, int]] = []
    total_distributed = 0
    for user_id, amount in sorted(rewards_by_user.items()):
        if amount <= 0:
            continue
        account, _ = await get_account_or_error(repo, scope=scope, user_id=user_id)
        await repo.add_balance(account_id=account.id, delta=amount)
        await repo.add_ledger(
            account_id=account.id,
            direction="in",
            amount=amount,
            reason="game_reward",
            meta_json=to_meta_json({"game_kind": game_kind, "game_id": game_id}),
        )
        rewarded.append((user_id, amount))
        total_distributed += amount

    return (
        GameRewardResult(
            game_kind=game_kind,
            rewarded_users=tuple(rewarded),
            total_distributed=total_distributed,
        ),
        None,
    )
