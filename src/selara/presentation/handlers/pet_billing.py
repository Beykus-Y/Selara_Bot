"""AIL billing of a pet's model lines: reserve the per-request cap, settle at the real cost."""

from __future__ import annotations

import logging
from decimal import Decimal

from selara.application.feature_access import AIL_UNIT, ail_units_from_cost_usd
from selara.presentation.handlers.personal_ai import chat_turn_cost_usd, failed_turn_cost_usd

log = logging.getLogger(__name__)


def pet_reserve_units(settings) -> Decimal:
    """The per-request cap: what one pet line reserves before the provider call (used in AI Limits mode only)."""
    return Decimal(getattr(settings, "pet_request_ail_cap", Decimal("3")))


async def settle_pet_line(access_service, *, config, decision, usages, failed: bool = False) -> None:
    """Replace the cap reservation by the line's actual cost in AI Limits mode; never fails the line.

    Without a priced response (or in requests mode) the reservation simply stays as the charge.
    """
    invocation_id = getattr(decision, "invocation_id", None)
    if invocation_id is None or config is None or getattr(decision, "quota_unit", None) != AIL_UNIT:
        return
    if not config.ail_settles_actual_cost:
        return
    cost = failed_turn_cost_usd(usages) if failed else chat_turn_cost_usd(usages)
    if cost is None:
        log.warning("pet billing: cost unknown, keeping the cap reservation invocation_id=%s", invocation_id)
        return
    try:
        await access_service.adjust(
            invocation_id=invocation_id, actual_units=ail_units_from_cost_usd(cost, config.ail_usd_value)
        )
    except Exception:
        log.exception("pet billing: AIL settlement failed invocation_id=%s", invocation_id)
