from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError


async def safe_rollback(target: object | None) -> None:
    session = target
    if session is not None and not hasattr(session, "rollback"):
        session = getattr(target, "_session", None)
    if session is None or not hasattr(session, "rollback"):
        return

    try:
        await session.rollback()
    except SQLAlchemyError:
        pass
