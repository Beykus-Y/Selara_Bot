from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository, SqlAlchemyEconomyRepository


@dataclass
class SqlAlchemyUnitOfWork:
    session: AsyncSession

    def __post_init__(self) -> None:
        self.activity = SqlAlchemyActivityRepository(self.session)
        self.economy = SqlAlchemyEconomyRepository(self.session)
