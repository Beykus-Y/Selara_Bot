from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.catalog import localize_item_code
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error
from selara.application.use_cases.economy.growth import effective_growth_stress_pct, stored_growth_stress_pct
from selara.application.use_cases.economy.results import UseItemResult


def _find_target_plot_no(plot_no: int | None, active_plot_nos: list[int]) -> int | None:
    if not active_plot_nos:
        return None
    if plot_no is None:
        return active_plot_nos[0]
    if plot_no in active_plot_nos:
        return plot_no
    return None


async def execute(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
    item_code: str,
    plot_no: int | None,
    event_at: datetime | None = None,
) -> UseItemResult:
    now = event_at or datetime.now(timezone.utc)

    normalized = item_code.strip().lower()
    if not normalized:
        return UseItemResult(accepted=False, reason="Укажите код предмета.", item_code=None, details=None)

    if not normalized.startswith("item:"):
        normalized = f"item:{normalized}"

    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=user_id)
    if scope is None:
        return UseItemResult(accepted=False, reason=error or "Не удалось определить режим экономики", item_code=None, details=None)

    account, _ = await get_account_or_error(repo, scope=scope, user_id=user_id)
    item = await repo.get_inventory_item(account_id=account.id, item_code=normalized)
    if item is None or item.quantity <= 0:
        return UseItemResult(accepted=False, reason="Такого предмета нет в инвентаре.", item_code=None, details=None)

    action = normalized.removeprefix("item:")
    current_effective_stress = effective_growth_stress_pct(
        last_growth_at=account.last_growth_at,
        stress_pct=account.growth_stress_pct,
        as_of=now,
    )
    current_stored_stress = stored_growth_stress_pct(
        last_growth_at=account.last_growth_at,
        effective_stress_pct=current_effective_stress,
        as_of=now,
    )

    if action in {"lottery_ticket", "market_fee_coupon", "permanent_token"}:
        return UseItemResult(
            accepted=False,
            reason="Этот предмет используется автоматически в соответствующей команде.",
            item_code=normalized,
            details=None,
        )

    if action == "energy_drink":
        new_stress = max(0, current_effective_stress - 25)
        await repo.update_growth_state(
            account_id=account.id,
            growth_size_mm=account.growth_size_mm,
            growth_stress_pct=stored_growth_stress_pct(
                last_growth_at=account.last_growth_at,
                effective_stress_pct=new_stress,
                as_of=now,
            ),
            growth_actions=account.growth_actions,
            last_growth_at=account.last_growth_at,
            growth_boost_pct=account.growth_boost_pct,
            growth_cooldown_discount_seconds=account.growth_cooldown_discount_seconds,
        )
        await repo.add_inventory_item(account_id=account.id, item_code=normalized, delta=-1)
        return UseItemResult(
            accepted=True,
            reason=None,
            item_code=normalized,
            details=f"Стресс снижен до {new_stress}%.",
        )

    if action == "veggie_salad":
        new_stress = max(0, current_effective_stress - 15)
        await repo.update_growth_state(
            account_id=account.id,
            growth_size_mm=account.growth_size_mm,
            growth_stress_pct=stored_growth_stress_pct(
                last_growth_at=account.last_growth_at,
                effective_stress_pct=new_stress,
                as_of=now,
            ),
            growth_actions=account.growth_actions,
            last_growth_at=account.last_growth_at,
            growth_boost_pct=account.growth_boost_pct,
            growth_cooldown_discount_seconds=account.growth_cooldown_discount_seconds,
        )
        await repo.add_inventory_item(account_id=account.id, item_code=normalized, delta=-1)
        return UseItemResult(
            accepted=True,
            reason=None,
            item_code=normalized,
            details=f"Салат освежил персонажа: стресс снижен до {new_stress}%.",
        )

    if action == "growth_gel":
        new_boost = min(200, account.growth_boost_pct + 40)
        await repo.update_growth_state(
            account_id=account.id,
            growth_size_mm=account.growth_size_mm,
            growth_stress_pct=current_stored_stress,
            growth_actions=account.growth_actions,
            last_growth_at=account.last_growth_at,
            growth_boost_pct=new_boost,
            growth_cooldown_discount_seconds=account.growth_cooldown_discount_seconds,
        )
        await repo.add_inventory_item(account_id=account.id, item_code=normalized, delta=-1)
        return UseItemResult(
            accepted=True,
            reason=None,
            item_code=normalized,
            details=f"Буст роста подготовлен: +{new_boost}% к следующему действию.",
        )

    if action == "cooling_pack":
        new_discount = min(3600, account.growth_cooldown_discount_seconds + 1200)
        await repo.update_growth_state(
            account_id=account.id,
            growth_size_mm=account.growth_size_mm,
            growth_stress_pct=current_stored_stress,
            growth_actions=account.growth_actions,
            last_growth_at=account.last_growth_at,
            growth_boost_pct=account.growth_boost_pct,
            growth_cooldown_discount_seconds=new_discount,
        )
        await repo.add_inventory_item(account_id=account.id, item_code=normalized, delta=-1)
        return UseItemResult(
            accepted=True,
            reason=None,
            item_code=normalized,
            details="Следующий кулдаун в механике роста будет уменьшен.",
        )

    if action == "corn_chips":
        new_boost = min(200, account.growth_boost_pct + 20)
        await repo.update_growth_state(
            account_id=account.id,
            growth_size_mm=account.growth_size_mm,
            growth_stress_pct=current_stored_stress,
            growth_actions=account.growth_actions,
            last_growth_at=account.last_growth_at,
            growth_boost_pct=new_boost,
            growth_cooldown_discount_seconds=account.growth_cooldown_discount_seconds,
        )
        await repo.add_inventory_item(account_id=account.id, item_code=normalized, delta=-1)
        return UseItemResult(
            accepted=True,
            reason=None,
            item_code=normalized,
            details=f"Чипсы добавили +20% буста к следующему росту. Теперь буст: {new_boost}%.",
        )

    if action == "stimulant_shot":
        new_boost = min(250, account.growth_boost_pct + 70)
        new_stress = min(100, current_effective_stress + 10)
        await repo.update_growth_state(
            account_id=account.id,
            growth_size_mm=account.growth_size_mm,
            growth_stress_pct=stored_growth_stress_pct(
                last_growth_at=account.last_growth_at,
                effective_stress_pct=new_stress,
                as_of=now,
            ),
            growth_actions=account.growth_actions,
            last_growth_at=account.last_growth_at,
            growth_boost_pct=new_boost,
            growth_cooldown_discount_seconds=account.growth_cooldown_discount_seconds,
        )
        await repo.add_inventory_item(account_id=account.id, item_code=normalized, delta=-1)
        return UseItemResult(
            accepted=True,
            reason=None,
            item_code=normalized,
            details=f"Стимулятор активирован: буст +{new_boost}%, стресс {new_stress}%.",
        )

    if action == "pizza":
        await repo.update_growth_state(
            account_id=account.id,
            growth_size_mm=account.growth_size_mm,
            growth_stress_pct=current_stored_stress,
            growth_actions=account.growth_actions,
            last_growth_at=None,
            growth_boost_pct=account.growth_boost_pct,
            growth_cooldown_discount_seconds=account.growth_cooldown_discount_seconds,
        )
        await repo.add_inventory_item(account_id=account.id, item_code=normalized, delta=-1)
        return UseItemResult(
            accepted=True,
            reason=None,
            item_code=normalized,
            details="Пицца сработала: кулдаун роста сброшен, действие можно делать сразу.",
        )

    if action in {"fertilizer_fast", "fertilizer_rich", "pesticide", "crop_insurance"}:
        plots = await repo.list_plots(account_id=account.id)
        active = [plot for plot in plots if plot.crop_code is not None and plot.ready_at is not None and plot.ready_at > now]
        target_no = _find_target_plot_no(plot_no, [plot.plot_no for plot in active])
        if target_no is None:
            return UseItemResult(
                accepted=False,
                reason="Нет подходящей активной грядки. Передайте номер грядки.",
                item_code=normalized,
                details=None,
            )

        target = next(plot for plot in active if plot.plot_no == target_no)

        if action == "fertilizer_fast":
            remaining = max(1, int((target.ready_at - now).total_seconds()))
            new_remaining = max(60, int(round(remaining * 0.85)))
            await repo.upsert_plot(
                account_id=account.id,
                plot_no=target.plot_no,
                crop_code=target.crop_code,
                planted_at=target.planted_at,
                ready_at=now + timedelta(seconds=new_remaining),
                yield_boost_pct=target.yield_boost_pct,
                shield_active=target.shield_active,
            )
            await repo.add_inventory_item(account_id=account.id, item_code=normalized, delta=-1)
            return UseItemResult(
                accepted=True,
                reason=None,
                item_code=normalized,
                details=f"Грядка #{target.plot_no} ускорена на 15%.",
            )

        if action == "fertilizer_rich":
            await repo.upsert_plot(
                account_id=account.id,
                plot_no=target.plot_no,
                crop_code=target.crop_code,
                planted_at=target.planted_at,
                ready_at=target.ready_at,
                yield_boost_pct=min(200, target.yield_boost_pct + 25),
                shield_active=target.shield_active,
            )
            await repo.add_inventory_item(account_id=account.id, item_code=normalized, delta=-1)
            return UseItemResult(
                accepted=True,
                reason=None,
                item_code=normalized,
                details=f"Грядка #{target.plot_no} получит +25% урожая.",
            )

        await repo.upsert_plot(
            account_id=account.id,
            plot_no=target.plot_no,
            crop_code=target.crop_code,
            planted_at=target.planted_at,
            ready_at=target.ready_at,
            yield_boost_pct=target.yield_boost_pct,
            shield_active=True,
        )
        await repo.add_inventory_item(account_id=account.id, item_code=normalized, delta=-1)
        return UseItemResult(
            accepted=True,
            reason=None,
            item_code=normalized,
            details=f"На грядке #{target.plot_no} активирована защита от негативного события.",
        )

    if action == "mystery_pack":
        await repo.add_inventory_item(account_id=account.id, item_code=normalized, delta=-1)
        if random.random() < 0.6:
            coins = random.randint(120, 380)
            await repo.add_balance(account_id=account.id, delta=coins)
            await repo.add_ledger(
                account_id=account.id,
                direction="in",
                amount=coins,
                reason="mystery_pack_coins",
                meta_json="{}",
            )
            return UseItemResult(
                accepted=True,
                reason=None,
                item_code=normalized,
                details=f"Из набора выпало {coins} монет.",
            )

        reward_item = random.choice(["item:fertilizer_fast", "item:fertilizer_rich", "item:pesticide"])
        await repo.add_inventory_item(account_id=account.id, item_code=reward_item, delta=1)
        return UseItemResult(
            accepted=True,
            reason=None,
            item_code=normalized,
            details=f"Из набора выпал предмет «{localize_item_code(reward_item)}».",
        )

    return UseItemResult(
        accepted=False,
        reason="Этот предмет пока нельзя применить вручную.",
        item_code=normalized,
        details=None,
    )
