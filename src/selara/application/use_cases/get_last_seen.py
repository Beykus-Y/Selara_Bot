from datetime import datetime

from selara.application.interfaces import ActivityRepository


async def execute(repo: ActivityRepository, *, chat_id: int, user_id: int) -> datetime | None:
    return await repo.get_last_seen(chat_id=chat_id, user_id=user_id)
