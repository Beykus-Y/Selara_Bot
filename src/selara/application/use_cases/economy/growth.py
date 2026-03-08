from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error, to_meta_json
from selara.application.use_cases.economy.results import GrowthActionResult, GrowthProfileResult

BASE_COOLDOWN_SECONDS = 60 * 60
MIN_COOLDOWN_SECONDS = 15 * 60
STRESS_STEP = 20
STRESS_PENALTY_SECONDS = 5 * 60
STRESS_DECAY_PER_HOUR = 10

STRESS_GAIN_MIN = 7
STRESS_GAIN_MAX = 15
STRESS_FUMBLE_EXTRA = 6

SIZE_GAIN_MM_MIN = 2
SIZE_GAIN_MM_MAX = 9
SIZE_LOSS_MM_MIN = 1
SIZE_LOSS_MM_MAX = 5

REWARD_MIN = 14
REWARD_MAX = 32
REWARD_FUMBLE_MIN = 5
REWARD_FUMBLE_MAX = 16

FUMBLE_BASE_CHANCE = 0.08
FUMBLE_STRESS_BONUS_PER_1 = 0.0035
FUMBLE_MAX_CHANCE = 0.45


def growth_stress_decay_pct(*, last_growth_at: datetime | None, as_of: datetime) -> int:
    if last_growth_at is None:
        return 0
    elapsed_seconds = int((as_of - last_growth_at).total_seconds())
    if elapsed_seconds <= 0:
        return 0
    return max(0, elapsed_seconds * STRESS_DECAY_PER_HOUR // (60 * 60))


def effective_growth_stress_pct(
    *,
    last_growth_at: datetime | None,
    stress_pct: int,
    as_of: datetime,
) -> int:
    normalized = max(0, min(100, int(stress_pct)))
    decay = growth_stress_decay_pct(last_growth_at=last_growth_at, as_of=as_of)
    return max(0, normalized - decay)


def stored_growth_stress_pct(
    *,
    last_growth_at: datetime | None,
    effective_stress_pct: int,
    as_of: datetime,
) -> int:
    normalized = max(0, min(100, int(effective_stress_pct)))
    if normalized <= 0 or last_growth_at is None:
        return normalized
    decay = growth_stress_decay_pct(last_growth_at=last_growth_at, as_of=as_of)
    return min(100, normalized + decay)


def effective_growth_cooldown_seconds(*, stress_pct: int, cooldown_discount_seconds: int) -> int:
    stress_penalty_steps = max(0, int(stress_pct)) // STRESS_STEP
    stress_penalty = stress_penalty_steps * STRESS_PENALTY_SECONDS
    raw = BASE_COOLDOWN_SECONDS + stress_penalty - max(0, int(cooldown_discount_seconds))
    return max(MIN_COOLDOWN_SECONDS, raw)


def next_growth_available_at(
    *,
    last_growth_at: datetime | None,
    stress_pct: int,
    cooldown_discount_seconds: int,
) -> tuple[datetime | None, int]:
    cooldown_seconds = effective_growth_cooldown_seconds(
        stress_pct=stress_pct,
        cooldown_discount_seconds=cooldown_discount_seconds,
    )
    if last_growth_at is None:
        return None, cooldown_seconds
    return last_growth_at + timedelta(seconds=cooldown_seconds), cooldown_seconds


async def get_profile(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
    event_at: datetime | None = None,
) -> GrowthProfileResult:
    now = event_at or datetime.now(timezone.utc)

    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=user_id)
    if scope is None:
        return GrowthProfileResult(
            accepted=False,
            reason=error or "Не удалось определить режим экономики.",
            size_mm=0,
            stress_pct=0,
            actions=0,
            balance=None,
            next_available_at=None,
            cooldown_seconds=BASE_COOLDOWN_SECONDS,
        )

    account, _ = await get_account_or_error(repo, scope=scope, user_id=user_id)
    effective_stress_pct = effective_growth_stress_pct(
        last_growth_at=account.last_growth_at,
        stress_pct=account.growth_stress_pct,
        as_of=now,
    )
    next_available, cooldown_seconds = next_growth_available_at(
        last_growth_at=account.last_growth_at,
        stress_pct=effective_stress_pct,
        cooldown_discount_seconds=account.growth_cooldown_discount_seconds,
    )
    if next_available is not None and next_available <= now:
        next_available = None

    return GrowthProfileResult(
        accepted=True,
        reason=None,
        size_mm=account.growth_size_mm,
        stress_pct=effective_stress_pct,
        actions=account.growth_actions,
        balance=account.balance,
        next_available_at=next_available,
        cooldown_seconds=cooldown_seconds,
    )


async def perform_action(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
    event_at: datetime | None = None,
) -> GrowthActionResult:
    now = event_at or datetime.now(timezone.utc)

    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=user_id)
    if scope is None:
        return GrowthActionResult(
            accepted=False,
            reason=error or "Не удалось определить режим экономики.",
            size_delta_mm=0,
            new_size_mm=0,
            stress_delta_pct=0,
            new_stress_pct=0,
            reward=0,
            new_balance=None,
            cooldown_seconds=BASE_COOLDOWN_SECONDS,
            next_available_at=None,
            fumble=False,
        )

    account, _ = await get_account_or_error(repo, scope=scope, user_id=user_id)
    effective_stress_pct = effective_growth_stress_pct(
        last_growth_at=account.last_growth_at,
        stress_pct=account.growth_stress_pct,
        as_of=now,
    )

    next_available, cooldown_seconds = next_growth_available_at(
        last_growth_at=account.last_growth_at,
        stress_pct=effective_stress_pct,
        cooldown_discount_seconds=account.growth_cooldown_discount_seconds,
    )
    if next_available is not None and now < next_available:
        remain = next_available - now
        remain_seconds = max(1, int(remain.total_seconds()))
        mins, secs = divmod(remain_seconds, 60)
        return GrowthActionResult(
            accepted=False,
            reason=f"Слишком рано. До следующей попытки {mins}м {secs:02d}с.",
            size_delta_mm=0,
            new_size_mm=account.growth_size_mm,
            stress_delta_pct=0,
            new_stress_pct=effective_stress_pct,
            reward=0,
            new_balance=account.balance,
            cooldown_seconds=cooldown_seconds,
            next_available_at=next_available,
            fumble=False,
        )

    fumble_chance = min(
        FUMBLE_MAX_CHANCE,
        FUMBLE_BASE_CHANCE + max(0, effective_stress_pct) * FUMBLE_STRESS_BONUS_PER_1,
    )
    fumble = random.random() < fumble_chance

    if fumble:
        size_delta_mm = -random.randint(SIZE_LOSS_MM_MIN, SIZE_LOSS_MM_MAX)
        reward = random.randint(REWARD_FUMBLE_MIN, REWARD_FUMBLE_MAX)
    else:
        base_gain = random.randint(SIZE_GAIN_MM_MIN, SIZE_GAIN_MM_MAX)
        boost_mult = 1.0 + max(0, account.growth_boost_pct) / 100
        size_delta_mm = max(1, int(round(base_gain * boost_mult)))
        reward = random.randint(REWARD_MIN, REWARD_MAX) + max(0, size_delta_mm // 2)

    stress_delta = random.randint(STRESS_GAIN_MIN, STRESS_GAIN_MAX)
    if fumble:
        stress_delta += STRESS_FUMBLE_EXTRA

    new_size_mm = max(0, account.growth_size_mm + size_delta_mm)
    new_stress_pct = max(0, min(100, effective_stress_pct + stress_delta))
    new_actions = account.growth_actions + 1

    new_balance = await repo.add_balance(account_id=account.id, delta=reward)
    await repo.update_growth_state(
        account_id=account.id,
        growth_size_mm=new_size_mm,
        growth_stress_pct=new_stress_pct,
        growth_actions=new_actions,
        last_growth_at=now,
        growth_boost_pct=0,
        growth_cooldown_discount_seconds=0,
    )
    await repo.add_ledger(
        account_id=account.id,
        direction="in",
        amount=reward,
        reason="growth_action",
        meta_json=to_meta_json({"fumble": fumble, "size_delta_mm": size_delta_mm, "stress_delta": stress_delta}),
    )

    new_next_available, next_cd = next_growth_available_at(
        last_growth_at=now,
        stress_pct=new_stress_pct,
        cooldown_discount_seconds=0,
    )

    return GrowthActionResult(
        accepted=True,
        reason=None,
        size_delta_mm=size_delta_mm,
        new_size_mm=new_size_mm,
        stress_delta_pct=stress_delta,
        new_stress_pct=new_stress_pct,
        reward=reward,
        new_balance=new_balance,
        cooldown_seconds=next_cd,
        next_available_at=new_next_available,
        fumble=fumble,
    )
