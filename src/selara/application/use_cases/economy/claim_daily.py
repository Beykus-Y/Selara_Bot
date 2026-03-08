from __future__ import annotations

from datetime import datetime, timedelta, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error, to_meta_json
from selara.application.use_cases.economy.results import DailyResult


DAILY_INTERVAL_HOURS = 24
DAILY_STREAK_RESET_HOURS = 36


async def execute(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
    daily_base_reward: int,
    daily_streak_cap: int,
    event_at: datetime | None = None,
) -> DailyResult:
    now = event_at or datetime.now(timezone.utc)

    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=user_id)
    if scope is None:
        return DailyResult(
            accepted=False,
            reason=error or "Не удалось определить режим экономики",
            reward=0,
            streak=0,
            new_balance=None,
            granted_lottery_ticket=False,
            next_available_at=None,
        )

    account, _ = await get_account_or_error(repo, scope=scope, user_id=user_id)

    if account.last_daily_claimed_at is not None:
        next_allowed = account.last_daily_claimed_at + timedelta(hours=DAILY_INTERVAL_HOURS)
        if now < next_allowed:
            return DailyResult(
                accepted=False,
                reason="Дейлик уже получен. Попробуйте позже.",
                reward=0,
                streak=account.daily_streak,
                new_balance=None,
                granted_lottery_ticket=False,
                next_available_at=next_allowed,
            )

    streak = 1
    if account.last_daily_claimed_at is not None:
        elapsed = now - account.last_daily_claimed_at
        if elapsed <= timedelta(hours=DAILY_STREAK_RESET_HOURS):
            streak = min(max(1, daily_streak_cap), account.daily_streak + 1)

    reward = int(daily_base_reward + (streak - 1) * 20)
    balance = await repo.add_balance(account_id=account.id, delta=reward)
    await repo.update_daily_state(account_id=account.id, daily_streak=streak, last_daily_claimed_at=now)

    granted_lottery_ticket = False
    if streak == max(1, daily_streak_cap):
        await repo.add_inventory_item(account_id=account.id, item_code="item:lottery_ticket", delta=1)
        granted_lottery_ticket = True

    await repo.add_ledger(
        account_id=account.id,
        direction="in",
        amount=reward,
        reason="daily",
        meta_json=to_meta_json({"streak": streak}),
    )

    return DailyResult(
        accepted=True,
        reason=None,
        reward=reward,
        streak=streak,
        new_balance=balance,
        granted_lottery_ticket=granted_lottery_ticket,
        next_available_at=now + timedelta(hours=DAILY_INTERVAL_HOURS),
    )
