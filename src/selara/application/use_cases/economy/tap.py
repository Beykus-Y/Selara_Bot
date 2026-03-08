from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error, to_meta_json
from selara.application.use_cases.economy.results import TapResult


MAX_STREAK_BONUS_PCT = 50
STREAK_WINDOW_SECONDS = 10 * 60
MIN_TAP_COOLDOWN_SECONDS = 25


async def execute(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
    tap_cooldown_seconds: int,
    event_at: datetime | None = None,
) -> TapResult:
    now = event_at or datetime.now(timezone.utc)

    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=user_id)
    if scope is None:
        return TapResult(
            accepted=False,
            reason=error or "Не удалось определить режим экономики",
            reward=0,
            proc_x4=False,
            new_balance=None,
            next_available_at=None,
            tap_streak=0,
        )

    account, _ = await get_account_or_error(repo, scope=scope, user_id=user_id)

    effective_cd = max(MIN_TAP_COOLDOWN_SECONDS, int(tap_cooldown_seconds) - int(account.tap_glove_level) * 5)
    if account.last_tap_at is not None:
        next_allowed = account.last_tap_at + timedelta(seconds=effective_cd)
        if now < next_allowed:
            remaining = int((next_allowed - now).total_seconds())
            return TapResult(
                accepted=False,
                reason=f"Кулдаун тапа: ещё {remaining} сек.",
                reward=0,
                proc_x4=False,
                new_balance=None,
                next_available_at=next_allowed,
                tap_streak=account.tap_streak,
            )

    new_streak = 1
    if account.last_tap_at is not None and (now - account.last_tap_at).total_seconds() <= STREAK_WINDOW_SECONDS:
        new_streak = account.tap_streak + 1

    base_reward = random.randint(8, 18)
    bonus_pct = min(MAX_STREAK_BONUS_PCT, max(0, new_streak - 1) * 5)
    reward = int(round(base_reward * (1 + bonus_pct / 100)))

    proc_x4 = random.random() < 0.03
    if proc_x4:
        reward *= 4

    balance = await repo.add_balance(account_id=account.id, delta=reward)
    await repo.update_tap_state(account_id=account.id, tap_streak=new_streak, last_tap_at=now)
    await repo.add_ledger(
        account_id=account.id,
        direction="in",
        amount=reward,
        reason="tap",
        meta_json=to_meta_json({"streak": new_streak, "bonus_pct": bonus_pct, "proc_x4": proc_x4}),
    )

    return TapResult(
        accepted=True,
        reason=None,
        reward=reward,
        proc_x4=proc_x4,
        new_balance=balance,
        next_available_at=now + timedelta(seconds=effective_cd),
        tap_streak=new_streak,
    )
