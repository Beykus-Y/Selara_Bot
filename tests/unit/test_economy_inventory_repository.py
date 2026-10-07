from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import EconomyAccountModel, EconomyInventoryModel
from selara.infrastructure.db.repositories import SqlAlchemyEconomyRepository


async def _repository():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()
    # SQLite cannot autoincrement the BigInteger economy_accounts.id, so the
    # account row is seeded with an explicit id.
    session.add(EconomyAccountModel(id=1, scope_id="global", scope_type="global", user_id=50))
    await session.commit()
    repo = SqlAlchemyEconomyRepository(session)
    return engine, repo, session, 1


@pytest.mark.asyncio
async def test_add_inventory_item_creates_missing_row() -> None:
    engine, repo, session, account_id = await _repository()

    item = await repo.add_inventory_item(account_id=account_id, item_code="crop:radish", delta=2)
    await session.commit()

    assert item == (await repo.get_inventory_item(account_id=account_id, item_code="crop:radish"))
    assert item.quantity == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_add_inventory_item_increments_existing_row() -> None:
    engine, repo, session, account_id = await _repository()
    await repo.add_inventory_item(account_id=account_id, item_code="crop:radish", delta=2)
    await session.commit()

    item = await repo.add_inventory_item(account_id=account_id, item_code="crop:radish", delta=3)
    await session.commit()

    assert item.quantity == 5
    assert (await repo.get_inventory_item(account_id=account_id, item_code="crop:radish")).quantity == 5
    await engine.dispose()


@pytest.mark.asyncio
async def test_add_inventory_item_cannot_subtract_missing_item() -> None:
    engine, repo, session, account_id = await _repository()

    with pytest.raises(ValueError, match="Cannot subtract missing inventory item"):
        await repo.add_inventory_item(account_id=account_id, item_code="crop:radish", delta=-1)
    assert await repo.get_inventory_item(account_id=account_id, item_code="crop:radish") is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_add_inventory_item_cannot_go_below_zero() -> None:
    engine, repo, session, account_id = await _repository()
    await repo.add_inventory_item(account_id=account_id, item_code="item:lottery_ticket", delta=1)
    await session.commit()

    with pytest.raises(ValueError, match="Inventory quantity cannot be negative"):
        await repo.add_inventory_item(account_id=account_id, item_code="item:lottery_ticket", delta=-2)

    stored = await session.scalar(
        select(EconomyInventoryModel.quantity).where(
            EconomyInventoryModel.account_id == account_id,
            EconomyInventoryModel.item_code == "item:lottery_ticket",
        )
    )
    assert stored == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_add_inventory_item_to_zero_removes_the_row() -> None:
    engine, repo, session, account_id = await _repository()
    await repo.add_inventory_item(account_id=account_id, item_code="item:lottery_ticket", delta=1)
    await session.commit()

    item = await repo.add_inventory_item(account_id=account_id, item_code="item:lottery_ticket", delta=-1)
    await session.commit()

    assert item.quantity == 0
    assert await repo.get_inventory_item(account_id=account_id, item_code="item:lottery_ticket") is None
    assert await repo.list_inventory(account_id=account_id) == []
    stored = await session.scalar(
        select(EconomyInventoryModel.quantity).where(
            EconomyInventoryModel.account_id == account_id,
            EconomyInventoryModel.item_code == "item:lottery_ticket",
        )
    )
    assert stored is None
    await engine.dispose()
