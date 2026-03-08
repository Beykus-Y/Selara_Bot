from selara.application.interfaces import ActivityRepository
from selara.domain.entities import ActivityStats


async def execute(repo: ActivityRepository, *, chat_id: int, user_id: int) -> ActivityStats | None:
    return await repo.get_user_stats(chat_id=chat_id, user_id=user_id)
