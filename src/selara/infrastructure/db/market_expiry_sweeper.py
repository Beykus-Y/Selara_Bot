from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.use_cases.economy import market_expiry
from selara.infrastructure.db.repositories import SqlAlchemyEconomyRepository

logger = logging.getLogger(__name__)


async def sweep_due_market_listings(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    batch_size: int = 100,
) -> int:
    """Settle one batch of due listings, one transaction per listing. Returns the units given back."""
    async with session_factory() as session:
        repo = SqlAlchemyEconomyRepository(session)
        due = await repo.list_market_listing_ids_due_for_settlement(
            now=datetime.now(timezone.utc),
            limit=batch_size,
        )

    returned_total = 0
    for listing_id, status in due:
        try:
            async with session_factory() as session:
                repo = SqlAlchemyEconomyRepository(session)
                returned = await market_expiry.execute(repo, listing_id=listing_id)
                await session.commit()
        except Exception:
            # One bad listing must not stop the rest of the batch; it is retried on the next tick.
            logger.exception("Market listing expiry failed", extra={"listing_id": listing_id})
            continue
        if not returned:
            continue
        returned_total += returned
        if status == "expired":
            # Escrow still held by a listing that was already marked expired: the pre-fix
            # code could leave these behind, so every repayment is worth an alert.
            logger.warning(
                "Returned escrow held by an already expired market listing",
                extra={"listing_id": listing_id, "units": returned},
            )
        else:
            logger.info(
                "Returned unsold units of an expired market listing",
                extra={"listing_id": listing_id, "units": returned},
            )
    return returned_total


async def run_market_expiry_scheduler(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    interval_seconds: float = 60.0,
) -> None:
    while True:
        try:
            await sweep_due_market_listings(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Market listing expiry sweep failed")
        await asyncio.sleep(interval_seconds)
